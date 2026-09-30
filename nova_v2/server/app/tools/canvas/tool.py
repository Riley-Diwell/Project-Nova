"""
tools/canvas/tool.py - the canvas context tool

Reads the user's Canvas: what's due, their grades, course calendar events, and
the course material itself - so "what's my next tutorial about", "does my lab
have a prelab" and "how did I go on my physics assignment" have an answer.

A context tool, like web_search: read-only, never gated, no gain, no Action
recorded. Offered to the model only on turns whose user has connected Canvas
(Settings -> Canvas; store/canvas.py), so everyone else's prompt is unchanged.

ACTIONS
  upcoming   what's due in the next `days` (default 14): the Canvas planner -
             assignments, quizzes, discussions - with whether each is submitted.
  grades     course totals; with `assignment`, that one assignment's score,
             grade and the marker's comments.
  classes    events on the courses' Canvas calendars. Most timetables live in
             the phone calendar instead; this is for what only Canvas has.
  materials  a course's modules and what's in them, optionally filtered by
             `query`, with an estimate of the teaching week.
  read       one item's text - a file (PDF, Word, PowerPoint), page or
             assignment - for the model to answer `question` from.

Courses are named the way the user or their calendar names them: a code
("COMP2100"), part of one ("phys"), or words from the title ("physics").

Times go out as the user's local wall clock (due_local, start_local), like the
calendar, so the model quotes them without converting anything.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from app.store.canvas import CanvasConnection
from app.tools.canvas import text as doc_text
from app.tools.canvas.client import CanvasClient, CanvasError, iter_items

ACTIONS = ("upcoming", "grades", "classes", "materials", "read")

MAX_LIST = 25
MAX_MATERIALS = 60
MAX_COMMENTS = 5

CANVAS_TOOL: dict[str, Any] = {
    "name": "canvas",
    "description": (
        "Reads the user's university Canvas (read-only). Actions: "
        "'upcoming' - assignments, quizzes and other work due soon, with whether "
        "each is submitted; 'grades' - course grades, or with 'assignment' one "
        "assignment's mark and the marker's comments; 'classes' - events on the "
        "Canvas course calendars; 'materials' - a course's modules and files; "
        "'read' - the text of one file, page or assignment so you can answer "
        "'question' from it. "
        "For 'what's my next class/tutorial/lab about', 'look at the notes', "
        "'is there a prelab': take the class from current_events/upcoming_events "
        "(or get_calendar_range) - its title usually holds the course code - "
        "then call 'materials' for that course with a query such as the class "
        "type and week, and 'read' the best match with the user's question. "
        "Answer in a few spoken sentences from what the tool returns; never read "
        "out URLs or ids, and never invent content the text doesn't contain."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(ACTIONS)},
            "course": {
                "type": "string",
                "description": (
                    "Course code or name, e.g. 'COMP2100', 'PHYS1101', 'physics'. "
                    "Required for materials and read; optional elsewhere (all courses)."
                ),
            },
            "days": {
                "type": "integer",
                "description": "How far ahead for upcoming/classes. Default 14 / 7.",
            },
            "assignment": {
                "type": "string",
                "description": (
                    "grades: the assignment's name or words from it. For 'my "
                    "assignment' with nothing more specific, pass 'assignment' - "
                    "that means the one marked most recently."
                ),
            },
            "query": {
                "type": "string",
                "description": "materials: words to filter by, e.g. 'week 9 tutorial', 'lab 4 prelab'.",
            },
            "item": {
                "type": "string",
                "description": (
                    "read: the item's ref from a materials result (e.g. 'File:123') "
                    "or its title."
                ),
            },
            "question": {
                "type": "string",
                "description": "read: what the user wants to know, in their words.",
            },
        },
        "required": ["action"],
    },
}


# --- time -------------------------------------------------------------------

def _parse(iso: Optional[str]) -> Optional[datetime]:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None


def _local(when: Optional[datetime], offset_minutes: int) -> Optional[str]:
    if when is None:
        return None
    return (when.astimezone(timezone.utc) + timedelta(minutes=offset_minutes)).strftime("%Y-%m-%dT%H:%M")


def _relative(when: Optional[datetime], now: datetime) -> Optional[str]:
    if when is None:
        return None
    minutes = (when - now).total_seconds() / 60
    past = minutes < 0
    minutes = abs(minutes)
    if minutes < 90:
        span = f"{round(minutes)} minutes"
    elif minutes < 48 * 60:
        span = f"{round(minutes / 60)} hours"
    else:
        span = f"{round(minutes / 1440)} days"
    return f"{span} ago" if past else f"in {span}"


# --- matching ---------------------------------------------------------------

_TOKEN = re.compile(r"[a-z]+|\d+")
_NOISE = {"the", "my", "a", "an", "of", "and", "for", "to", "in", "on", "course", "class",
          "unit", "subject", "notes", "what", "whats", "is", "about", "next", "today"}


def _words(text: str) -> set[str]:
    return {w for w in _TOKEN.findall((text or "").lower()) if w not in _NOISE}


def _codes(text: str) -> set[str]:
    """Course codes in free text: 'COMP2100 Tutorial 3', 'phys 1101' -> {'comp2100'}, {'phys1101'}."""
    return {m.lower().replace(" ", "") for m in re.findall(r"\b[A-Za-z]{3,5} ?\d{3,4}[A-Za-z]?\b", text or "")}


def _course_score(course: dict[str, Any], query: str) -> int:
    code = (course.get("course_code") or "").lower().replace(" ", "")
    name = (course.get("name") or "").lower()
    wanted_codes = _codes(query)
    if code and any(c in code or code in c for c in wanted_codes):
        return 100
    if any(c in name.replace(" ", "") for c in wanted_codes):
        return 90
    q = _words(query)
    if not q:
        return 0
    score = 0
    haystack = _words(f"{course.get('course_code', '')} {course.get('name', '')}")
    for w in q:
        if w in haystack:
            score += 10
        elif len(w) >= 3 and any(h.startswith(w) or (len(h) >= 4 and w.startswith(h[:4])) for h in haystack):
            score += 6
    return score


def _match_courses(courses: list[dict[str, Any]], query: Optional[str]) -> list[dict[str, Any]]:
    if not query:
        return courses
    scored = [(c, _course_score(c, query)) for c in courses]
    best = max((s for _, s in scored), default=0)
    if best <= 0:
        return []
    return [c for c, s in scored if s == best]


# Words that say "an assignment" without saying which one.
_GENERIC_ASSIGNMENT = {"assignment", "assignments", "assessment", "assessments", "task",
                       "last", "latest", "recent", "most", "recently", "marked", "graded"}


def _course_label(course: dict[str, Any]) -> str:
    code, name = course.get("course_code") or "", course.get("name") or ""
    return name if not code or code in name else f"{code} {name}"


def _title_score(title: str, context: str, wanted: str) -> float:
    words = _words(wanted)
    if not words:
        return 0.0
    have = _words(f"{title} {context}")
    title_words = _words(title)
    score = 0.0
    for w in words:
        if w in title_words:
            score += 2
        elif w in have:
            score += 1
    # "lab 4" must not match "lab 14": numbers only count as whole tokens,
    # which _words already does; reward an exact phrase on top.
    if wanted.lower().strip() and wanted.lower().strip() in title.lower():
        score += 3
    return score / (len(words) * 2)


# --- the tool ---------------------------------------------------------------

class _Run:
    def __init__(self, client: CanvasClient, offset_minutes: int, now: datetime) -> None:
        self.client = client
        self.offset = offset_minutes
        self.now = now
        self._courses: Optional[list[dict[str, Any]]] = None

    def courses(self) -> list[dict[str, Any]]:
        if self._courses is None:
            self._courses = self.client.courses()
        return self._courses

    def course_names(self) -> list[str]:
        return [_course_label(c) for c in self.courses()]

    def one_course(self, query: Optional[str]) -> dict[str, Any]:
        """The course `query` names, or an error dict ({"success": False, ...}) for the model."""
        if not query:
            return {"success": False, "error": "say which course", "courses": self.course_names()}
        found = _match_courses(self.courses(), query)
        if not found:
            return {"success": False, "error": f"no current course matches {query!r}",
                    "courses": self.course_names()}
        if len(found) > 1:
            return {"success": False, "error": f"{query!r} matches more than one course - ask which",
                    "courses": [_course_label(c) for c in found]}
        return found[0]

    def local(self, iso: Optional[str]) -> Optional[str]:
        return _local(_parse(iso), self.offset)

    # upcoming ---------------------------------------------------------------

    def upcoming(self, course: Optional[str], days: int) -> dict[str, Any]:
        days = max(1, min(int(days or 14), 90))
        courses = self.courses()
        chosen = _match_courses(courses, course) if course else courses
        if course and not chosen:
            return {"success": False, "error": f"no current course matches {course!r}",
                    "courses": self.course_names()}
        ids = {str(c["id"]) for c in chosen}
        by_id = {str(c["id"]): c for c in courses}
        end = self.now + timedelta(days=days)
        try:
            items = self.client.planner_items(
                self.now.strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ"))
            due = self._from_planner(items, ids, by_id)
        except CanvasError as e:
            if e.status not in (403, 404):
                raise
            due = self._from_assignments(chosen, end)
        due.sort(key=lambda d: d["_at"])
        for d in due:
            d.pop("_at")
        return {
            "success": True,
            "from_local": _local(self.now, self.offset),
            "days": days,
            "due": due[:MAX_LIST],
            "more": max(0, len(due) - MAX_LIST),
        }

    def _from_planner(self, items: list[dict[str, Any]], ids: set[str],
                      by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for item in items:
            kind = item.get("plannable_type")
            if kind in ("calendar_event", "planner_note", "announcement"):
                continue
            if item.get("course_id") is not None and str(item["course_id"]) not in ids:
                continue
            plannable = item.get("plannable") or {}
            at = _parse(plannable.get("due_at") or item.get("plannable_date") or plannable.get("todo_date"))
            if at is None:
                continue
            submissions = item.get("submissions") or {}
            course = by_id.get(str(item.get("course_id")))
            out.append({
                "_at": at,
                "title": plannable.get("title") or plannable.get("name"),
                "course": _course_label(course) if course else item.get("context_name"),
                "type": (kind or "").replace("_", " "),
                "due_local": _local(at, self.offset),
                "due_in": _relative(at, self.now),
                "points": plannable.get("points_possible"),
                "submitted": bool(submissions.get("submitted")) if submissions else None,
                "missing": bool(submissions.get("missing")) if submissions else None,
                "graded": bool(submissions.get("graded")) if submissions else None,
            })
        return out

    def _from_assignments(self, courses: list[dict[str, Any]], end: datetime) -> list[dict[str, Any]]:
        out = []
        for course, assignments in self._assignments_for(courses):
            for a in assignments:
                at = _parse(a.get("due_at"))
                if at is None or not (self.now <= at <= end):
                    continue
                sub = a.get("submission") or {}
                out.append({
                    "_at": at, "title": a.get("name"), "course": _course_label(course),
                    "type": "assignment", "due_local": _local(at, self.offset),
                    "due_in": _relative(at, self.now), "points": a.get("points_possible"),
                    "submitted": sub.get("workflow_state") in ("submitted", "graded", "pending_review")
                    or bool(sub.get("submitted_at")),
                    "missing": bool(sub.get("missing")), "graded": sub.get("workflow_state") == "graded",
                })
        return out

    def _assignments_for(self, courses: list[dict[str, Any]]):
        def fetch(course):
            try:
                return course, self.client.assignments(str(course["id"]))
            except CanvasError:
                return course, []
        with ThreadPoolExecutor(max_workers=4) as pool:
            return list(pool.map(fetch, courses))

    # grades -----------------------------------------------------------------

    def grades(self, course: Optional[str], assignment: Optional[str]) -> dict[str, Any]:
        courses = self.courses()
        chosen = _match_courses(courses, course) if course else courses
        if course and not chosen:
            return {"success": False, "error": f"no current course matches {course!r}",
                    "courses": self.course_names()}
        if assignment:
            return self._one_assignment(chosen, assignment)

        totals = []
        for c in chosen:
            enrollment = next((e for e in c.get("enrollments") or [] if e.get("type") == "student"), None) \
                or next(iter(c.get("enrollments") or []), {})
            totals.append({
                "course": _course_label(c),
                "current_score": enrollment.get("computed_current_score"),
                "current_grade": enrollment.get("computed_current_grade"),
            })
        result: dict[str, Any] = {"success": True, "courses": totals}
        if course:
            graded = []
            for c, assignments in self._assignments_for(chosen):
                for a in assignments:
                    sub = a.get("submission") or {}
                    if sub.get("score") is None and not sub.get("grade"):
                        continue
                    graded.append((sub.get("graded_at") or "", {
                        "assignment": a.get("name"), "course": _course_label(c),
                        "score": sub.get("score"), "out_of": a.get("points_possible"),
                        "grade": sub.get("grade"), "graded_local": self.local(sub.get("graded_at")),
                    }))
            graded.sort(key=lambda g: g[0], reverse=True)
            result["recently_graded"] = [g for _, g in graded[:8]]
        return result

    def _one_assignment(self, courses: list[dict[str, Any]], wanted: str) -> dict[str, Any]:
        fetched = self._assignments_for(courses)
        best: Optional[tuple[float, dict[str, Any], dict[str, Any]]] = None
        if not (_words(wanted) - _GENERIC_ASSIGNMENT):
            # "how did I go on my assignment" names none: the one marked last.
            for course, assignments in fetched:
                for a in assignments:
                    graded_at = (a.get("submission") or {}).get("graded_at") or ""
                    if graded_at and (best is None or graded_at > best[0]):
                        best = (graded_at, course, a)
            if best is None:
                return {"success": False, "error": "nothing has been marked yet"}
        else:
            for course, assignments in fetched:
                for a in assignments:
                    score = _title_score(a.get("name") or "", _course_label(course), wanted)
                    if best is None or score > best[0]:
                        best = (score, course, a)
            if best is None or best[0] < 0.3:
                return {"success": False, "error": f"no assignment matching {wanted!r}"}
        _, course, a = best
        try:
            sub = self.client.my_submission(str(course["id"]), str(a["id"]))
        except CanvasError:
            sub = a.get("submission") or {}
        graded = sub.get("score") is not None or bool(sub.get("grade"))
        comments = [
            {"from": (c.get("author_name") or "marker"), "comment": doc_text.html_to_text(c.get("comment") or "")[:600]}
            for c in (sub.get("submission_comments") or [])[-MAX_COMMENTS:]
        ]
        return {
            "success": True,
            "assignment": a.get("name"),
            "course": _course_label(course),
            "due_local": self.local(a.get("due_at")),
            "submitted_local": self.local(sub.get("submitted_at")),
            "graded": graded,
            "score": sub.get("score"),
            "out_of": a.get("points_possible"),
            "grade": sub.get("grade"),
            "late": sub.get("late"),
            "missing": sub.get("missing"),
            "comments": comments,
            **({} if graded else {"note": "not marked yet (or the mark isn't released)"}),
        }

    # classes ----------------------------------------------------------------

    def classes(self, course: Optional[str], days: int) -> dict[str, Any]:
        days = max(1, min(int(days or 7), 60))
        courses = self.courses()
        chosen = _match_courses(courses, course) if course else courses
        if course and not chosen:
            return {"success": False, "error": f"no current course matches {course!r}",
                    "courses": self.course_names()}
        by_code = {f"course_{c['id']}": c for c in chosen}
        end = self.now + timedelta(days=days)
        events = self.client.calendar_events(
            [str(c["id"]) for c in chosen],
            self.now.strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ"))
        out = []
        for e in events:
            start = _parse(e.get("start_at"))
            if start is None:
                continue
            c = by_code.get(e.get("context_code") or "")
            out.append((start, {
                "title": e.get("title"),
                "course": _course_label(c) if c else e.get("context_name"),
                "start_local": _local(start, self.offset),
                "end_local": self.local(e.get("end_at")),
                "location": e.get("location_name") or e.get("location_address"),
            }))
        out.sort(key=lambda x: x[0])
        return {
            "success": True,
            "events": [e for _, e in out[:MAX_LIST]],
            "note": "Canvas course calendars only - the timetable is usually in the phone calendar.",
        }

    # materials --------------------------------------------------------------

    def teaching_week(self, course: dict[str, Any]) -> Optional[int]:
        start = _parse((course.get("term") or {}).get("start_at")) or _parse(course.get("start_at"))
        if start is None:
            return None
        week = (self.now - start).days // 7 + 1
        return week if 1 <= week <= 20 else None

    def materials(self, course_query: Optional[str], query: Optional[str]) -> dict[str, Any]:
        course = self.one_course(course_query)
        if course.get("success") is False:
            return course
        cid = str(course["id"])
        week = self.teaching_week(course)
        try:
            modules = self.client.modules(cid)
        except CanvasError as e:
            if e.status not in (403, 404):
                raise
            modules = []

        items = []
        for module, item in iter_items(modules):
            ref = _ref(item)
            if ref is None and item.get("type") != "SubHeader":
                continue
            items.append({
                "module": module.get("name"),
                "title": item.get("title"),
                "type": item.get("type"),
                **({"ref": ref} if ref else {}),
            })
        if query:
            scored = [(_title_score(i["title"] or "", i["module"] or "", query), i) for i in items]
            items = [i for s, i in sorted(scored, key=lambda x: -x[0]) if s > 0]

        if not items and query:
            items = self._search(cid, query)

        return {
            "success": True,
            "course": _course_label(course),
            "teaching_week_estimate": week,
            "items": items[:MAX_MATERIALS],
            "more": max(0, len(items) - MAX_MATERIALS),
            **({} if items else {"note": "nothing matching in this course's modules or files"}),
        }

    def _search(self, cid: str, query: str) -> list[dict[str, Any]]:
        """Files and pages by name, for a course that doesn't use modules."""
        found = []
        terms = sorted(_words(query), key=len, reverse=True)[:2] or [query]
        for term in terms:
            for search, kind, key in ((self.client.search_files, "File", "id"),
                                      (self.client.pages, "Page", "url")):
                try:
                    for hit in search(cid, term):
                        title = hit.get("display_name") or hit.get("title")
                        found.append({"module": None, "title": title, "type": kind,
                                      "ref": f"{kind}:{hit.get(key)}"})
                except CanvasError:
                    continue
        seen, unique = set(), []
        for f in found:
            if f["ref"] not in seen:
                seen.add(f["ref"])
                unique.append(f)
        return sorted(unique, key=lambda i: -_title_score(i["title"] or "", "", query))

    # read -------------------------------------------------------------------

    def read(self, course_query: Optional[str], item: Optional[str], question: Optional[str]) -> dict[str, Any]:
        course = self.one_course(course_query)
        if course.get("success") is False:
            return course
        if not item:
            return {"success": False, "error": "say which item - call materials first to find it"}
        cid = str(course["id"])
        ref, title, others = self._resolve(cid, item, question)
        if ref is None:
            return {"success": False, "error": f"nothing called {item!r} in {_course_label(course)}"}
        kind, key = ref.split(":", 1)

        try:
            if kind == "File":
                meta = self.client.file(key)
                title = meta.get("display_name") or title
                data, content_type = self.client.download(meta["url"])
                body = doc_text.extract(data, meta.get("content-type") or content_type, meta.get("filename") or title or "")
            elif kind == "Page":
                page = self.client.page(cid, key)
                title = page.get("title") or title
                body = doc_text.html_to_text(page.get("body"))
            elif kind == "Assignment":
                a = self.client.assignment(cid, key)
                title = a.get("name") or title
                sub = a.get("submission") or {}
                lines = [f"Due: {self.local(a.get('due_at')) or 'no due date'}",
                         f"Points: {a.get('points_possible')}",
                         f"Submitted: {'yes' if sub.get('submitted_at') else 'no'}"]
                body = "\n".join(lines) + "\n\n" + doc_text.html_to_text(a.get("description"))
            elif kind == "Quiz":
                q = self.client.get(f"/courses/{cid}/quizzes/{key}")
                title = q.get("title") or title
                body = (f"Due: {self.local(q.get('due_at')) or 'no due date'}\n"
                        f"Questions: {q.get('question_count')}\n\n" + doc_text.html_to_text(q.get("description")))
            else:
                return {"success": False, "title": title,
                        "error": "that's a link to another site, which Nova can't open"}
        except doc_text.Unreadable as e:
            return {"success": False, "title": title, "error": str(e)}

        if not body.strip():
            return {"success": False, "title": title,
                    "error": "no text in it - it may be scanned images or a video"}
        excerpt, truncated = doc_text.excerpt(body, question)
        return {
            "success": True,
            "course": _course_label(course),
            "title": title,
            "type": kind,
            "text": excerpt,
            "truncated": truncated,
            **({"other_matches": others} if others else {}),
        }

    def _resolve(self, cid: str, item: str, question: Optional[str]) -> tuple[Optional[str], Optional[str], list[str]]:
        """(ref, title, other close titles) for what the model named."""
        direct = re.fullmatch(r"\s*(File|Page|Assignment|Quiz|External)\s*:\s*(\S.*?)\s*", item)
        if direct:
            return f"{direct.group(1)}:{direct.group(2)}", None, []
        try:
            modules = self.client.modules(cid)
        except CanvasError:
            modules = []
        candidates = [(module.get("name") or "", item_) for module, item_ in iter_items(modules) if _ref(item_)]
        scored = sorted(
            ((_title_score(i.get("title") or "", m, item), m, i) for m, i in candidates),
            key=lambda x: -x[0],
        )
        scored = [s for s in scored if s[0] > 0]
        if scored:
            best = scored[0]
            others = [s[2].get("title") for s in scored[1:4] if s[0] >= best[0] * 0.8]
            return _ref(best[2]), best[2].get("title"), others
        hits = self._search(cid, item)
        if hits:
            return hits[0]["ref"], hits[0]["title"], [h["title"] for h in hits[1:3]]
        return None, None, []


def _ref(item: dict[str, Any]) -> Optional[str]:
    kind = item.get("type")
    if kind == "Page" and item.get("page_url"):
        return f"Page:{item['page_url']}"
    if kind in ("File", "Assignment", "Quiz") and item.get("content_id") is not None:
        return f"{kind}:{item['content_id']}"
    if kind in ("ExternalUrl", "ExternalTool"):
        return f"External:{item.get('id')}"
    return None


def run_canvas(
    tool_input: dict[str, Any],
    connection: Optional[CanvasConnection],
    utc_offset_minutes: int = 0,
    now: Optional[datetime] = None,
    client_factory: Callable[[str, str], CanvasClient] = CanvasClient,
) -> dict[str, Any]:
    if connection is None:
        return {"success": False, "error": "Canvas isn't connected - the user can connect it in Settings."}
    action = tool_input.get("action")
    if action not in ACTIONS:
        return {"success": False, "error": f"action must be one of {', '.join(ACTIONS)}"}
    run = _Run(client_factory(connection.base_url, connection.token), utc_offset_minutes,
               now or datetime.now(timezone.utc))
    try:
        if action == "upcoming":
            return run.upcoming(tool_input.get("course"), tool_input.get("days") or 14)
        if action == "grades":
            return run.grades(tool_input.get("course"), tool_input.get("assignment"))
        if action == "classes":
            return run.classes(tool_input.get("course"), tool_input.get("days") or 7)
        if action == "materials":
            return run.materials(tool_input.get("course"), tool_input.get("query"))
        return run.read(tool_input.get("course"), tool_input.get("item"), tool_input.get("question"))
    except CanvasError as e:
        print(f"[canvas] {action} failed: {e}")
        return {"success": False, "error": str(e)}
    except (TypeError, ValueError) as e:
        print(f"[canvas] {action} bad input: {e}")
        return {"success": False, "error": "those arguments didn't make sense for this action"}
