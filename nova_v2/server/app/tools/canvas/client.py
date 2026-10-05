"""
tools/canvas/client.py - Canvas LMS REST API, read-only

Everything the canvas tool needs from https://<school>/api/v1, as the student
whose personal access token it holds. Only GETs: Nova reads Canvas, it never
submits, posts or changes anything there.

THE ADDRESS IS THE USER'S
The Canvas address comes from the user, and this server sits on a private
network next to the database, auth, the model and SearXNG. So every request -
and every redirect a file download follows - must go to a public HTTPS host:
public_host() resolves the name and refuses loopback, private, link-local and
CGNAT (tailnet) addresses. The token is only ever sent to the Canvas host
itself; a redirect to a file store elsewhere goes without it.
"""
from __future__ import annotations

import ipaddress
import socket
import time
from typing import Any, Iterator, Optional
from urllib.parse import urljoin, urlsplit

import requests

TIMEOUT_S = 8.0
MAX_PAGES = 5
MAX_REDIRECTS = 5
# Lecture slides get big; past this the text isn't worth the wait on a voice turn.
MAX_DOWNLOAD_BYTES = 30 * 1024 * 1024

# Per-process, per-connection cache of GETs that don't change within a turn -
# a "what's my next tutorial about" turn reads the course list and a course's
# modules more than once.
CACHE_TTL_S = 300
_cache: dict[tuple[str, str, str], tuple[float, Any]] = {}



def clear_cache() -> None:
    """Forget every cached response. Keyed by token, not user, so a deleted
    account (store/account.py) clears the lot - it refills within a turn."""
    _cache.clear()

class CanvasError(Exception):
    """A Canvas request that failed in a way worth telling the user about."""

    def __init__(self, message: str, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


def normalise_base_url(raw: str) -> str:
    """"canvas.uni.edu.au", "https://canvas.uni.edu.au/courses/12" ->
    "https://canvas.uni.edu.au". Raises ValueError for anything that isn't a
    plain public HTTPS host."""
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("Enter your Canvas address.")
    if "://" not in raw:
        raw = "https://" + raw
    parts = urlsplit(raw)
    if parts.scheme != "https":
        raise ValueError("The Canvas address must start with https://")
    host = (parts.hostname or "").lower()
    if not host or "." not in host or parts.username or parts.password:
        raise ValueError("That doesn't look like a Canvas address.")
    port = f":{parts.port}" if parts.port and parts.port != 443 else ""
    public_host(host)
    return f"https://{host}{port}"


def public_host(host: str) -> None:
    """Raise ValueError unless every address `host` resolves to is public."""
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise ValueError(f"Couldn't find {host} - check the Canvas address.")
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split("%")[0])
        if not address.is_global:
            raise ValueError(f"{host} isn't a public address.")


class CanvasClient:
    def __init__(self, base_url: str, token: str, session: Optional[requests.Session] = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._host = urlsplit(self.base_url).hostname or ""
        self._token = token
        self._session = session or requests.Session()

    # --- plumbing -----------------------------------------------------------

    def _request(self, url: str, params: Any = None, stream: bool = False) -> requests.Response:
        """GET with redirects followed by hand, so each hop's host is checked and
        the token only goes to the Canvas host."""
        for _ in range(MAX_REDIRECTS + 1):
            parts = urlsplit(url)
            if parts.scheme != "https" or not parts.hostname:
                raise CanvasError("Canvas sent a link Nova won't follow.")
            try:
                public_host(parts.hostname)
            except ValueError as e:
                raise CanvasError(str(e))
            headers = {"Accept": "application/json+canvas-string-ids, application/json"}
            if parts.hostname == self._host:
                headers["Authorization"] = f"Bearer {self._token}"
            try:
                response = self._session.get(
                    url, params=params, headers=headers, timeout=TIMEOUT_S,
                    allow_redirects=False, stream=stream,
                )
            except requests.RequestException as e:
                raise CanvasError(f"Couldn't reach Canvas ({type(e).__name__}).")
            if response.is_redirect or response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("Location")
                response.close()
                if not location:
                    raise CanvasError("Canvas redirected nowhere.")
                url, params = urljoin(url, location), None
                continue
            if response.status_code == 401:
                raise CanvasError("Canvas didn't accept the access token - it may have "
                                  "expired or been deleted. Reconnect Canvas in Settings.", 401)
            if response.status_code == 403:
                raise CanvasError("Canvas doesn't let this account see that.", 403)
            if response.status_code == 404:
                raise CanvasError("Canvas couldn't find that.", 404)
            if response.status_code >= 400:
                raise CanvasError(f"Canvas returned an error ({response.status_code}).",
                                  response.status_code)
            return response
        raise CanvasError("Canvas redirected too many times.")

    def get(self, path: str, params: Any = None, cache: bool = False) -> Any:
        key = (self.base_url, self._token[-8:], f"{path}?{params!r}")
        if cache:
            hit = _cache.get(key)
            if hit and time.monotonic() - hit[0] < CACHE_TTL_S:
                return hit[1]
        response = self._request(f"{self.base_url}/api/v1{path}", params)
        try:
            data = response.json()
        except ValueError:
            raise CanvasError("Canvas sent something that wasn't JSON.")
        if cache:
            _cache[key] = (time.monotonic(), data)
        return data

    def get_all(self, path: str, params: Any = None, cache: bool = False,
                max_pages: int = MAX_PAGES) -> list[Any]:
        """A paginated list endpoint, following Link rel="next" up to max_pages."""
        key = (self.base_url, self._token[-8:], f"all:{path}?{params!r}")
        if cache:
            hit = _cache.get(key)
            if hit and time.monotonic() - hit[0] < CACHE_TTL_S:
                return hit[1]
        items: list[Any] = []
        url: Optional[str] = f"{self.base_url}/api/v1{path}"
        page_params = params
        for _ in range(max_pages):
            if url is None:
                break
            response = self._request(url, page_params)
            try:
                page = response.json()
            except ValueError:
                raise CanvasError("Canvas sent something that wasn't JSON.")
            if isinstance(page, list):
                items.extend(page)
            url = response.links.get("next", {}).get("url")
            page_params = None  # the next link carries its own query string
        if cache:
            _cache[key] = (time.monotonic(), items)
        return items

    def download(self, url: str) -> tuple[bytes, str]:
        """A file's bytes and content type, capped at MAX_DOWNLOAD_BYTES."""
        response = self._request(url, stream=True)
        with response:
            declared = int(response.headers.get("Content-Length") or 0)
            if declared > MAX_DOWNLOAD_BYTES:
                raise CanvasError("That file is too big to read on the fly.")
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_content(64 * 1024):
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    raise CanvasError("That file is too big to read on the fly.")
                chunks.append(chunk)
            return b"".join(chunks), response.headers.get("Content-Type", "")

    # --- endpoints ----------------------------------------------------------

    def me(self) -> dict[str, Any]:
        return self.get("/users/self")

    def courses(self) -> list[dict[str, Any]]:
        """Active enrolments, with term dates and current scores."""
        courses = self.get_all("/courses", params=[
            ("enrollment_state", "active"), ("include[]", "term"),
            ("include[]", "total_scores"), ("per_page", "50"),
        ], cache=True)
        # A course the term has closed off comes back as a bare
        # {id, access_restricted_by_date} - nothing to show.
        return [c for c in courses if c.get("name") and not c.get("access_restricted_by_date")]

    def planner_items(self, start_iso: str, end_iso: str) -> list[dict[str, Any]]:
        return self.get_all("/planner/items", params=[
            ("start_date", start_iso), ("end_date", end_iso), ("per_page", "100"),
        ])

    def calendar_events(self, course_ids: list[str], start_iso: str, end_iso: str) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        # Canvas takes at most 10 contexts per request.
        for i in range(0, len(course_ids), 10):
            params = [("type", "event"), ("start_date", start_iso), ("end_date", end_iso),
                      ("per_page", "100")]
            params += [("context_codes[]", f"course_{cid}") for cid in course_ids[i:i + 10]]
            events.extend(self.get_all("/calendar_events", params=params))
        return events

    def assignments(self, course_id: str) -> list[dict[str, Any]]:
        return self.get_all(f"/courses/{course_id}/assignments", params=[
            ("include[]", "submission"), ("order_by", "due_at"), ("per_page", "100"),
        ], cache=True)

    def assignment(self, course_id: str, assignment_id: str) -> dict[str, Any]:
        return self.get(f"/courses/{course_id}/assignments/{assignment_id}",
                        params=[("include[]", "submission")])

    def my_submission(self, course_id: str, assignment_id: str) -> dict[str, Any]:
        return self.get(f"/courses/{course_id}/assignments/{assignment_id}/submissions/self",
                        params=[("include[]", "submission_comments"), ("include[]", "rubric_assessment")])

    def modules(self, course_id: str) -> list[dict[str, Any]]:
        modules = self.get_all(f"/courses/{course_id}/modules", params=[
            ("include[]", "items"), ("per_page", "50"),
        ], cache=True)
        for module in modules:
            # Canvas leaves items out of a module with too many to inline.
            if "items" not in module and module.get("items_url"):
                try:
                    module["items"] = self.get_all(
                        f"/courses/{course_id}/modules/{module['id']}/items",
                        params=[("per_page", "100")], cache=True, max_pages=2)
                except CanvasError:
                    module["items"] = []
        return modules

    def search_files(self, course_id: str, term: str) -> list[dict[str, Any]]:
        return self.get_all(f"/courses/{course_id}/files", params=[
            ("search_term", term), ("per_page", "30"), ("sort", "updated_at"), ("order", "desc"),
        ], max_pages=1)

    def file(self, file_id: str) -> dict[str, Any]:
        return self.get(f"/files/{file_id}")

    def page(self, course_id: str, page_url: str) -> dict[str, Any]:
        return self.get(f"/courses/{course_id}/pages/{page_url}")

    def pages(self, course_id: str, term: str) -> list[dict[str, Any]]:
        return self.get_all(f"/courses/{course_id}/pages", params=[
            ("search_term", term), ("per_page", "30"),
        ], max_pages=1)


def iter_items(modules: list[dict[str, Any]]) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    for module in modules:
        for item in module.get("items") or []:
            yield module, item
