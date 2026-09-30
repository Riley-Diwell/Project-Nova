"""
tools/canvas/text.py - the words out of course material

Lecture slides, tutorial sheets, lab manuals and Canvas pages, as plain text
the model can answer a question from: PDF (pypdf), Word and PowerPoint (their
XML, read straight out of the zip), HTML and plain text.

Documents are long and the model's context is not, so excerpt() keeps the
opening (the title, the week, the overview - what "what's it about" needs) and
then the passages that share the most words with the user's question ("what
are the prelab questions" finds the prelab section).
"""
from __future__ import annotations

import io
import re
import zipfile
from html import unescape
from html.parser import HTMLParser
from typing import Optional

# About 4-5k tokens: room for the answer inside a voice turn's context.
EXCERPT_CHARS = 18_000
OPENING_CHARS = 4_000
MAX_PDF_PAGES = 80


class Unreadable(Exception):
    """A file type Nova can't pull text out of."""


class _HTMLText(HTMLParser):
    _BLOCKS = {"p", "div", "br", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "table"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in self._BLOCKS:
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n- ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: Optional[str]) -> str:
    if not html:
        return ""
    parser = _HTMLText()
    parser.feed(html)
    return tidy("".join(parser.parts))


def tidy(text: str) -> str:
    text = unescape(text).replace("\r", "")
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n\s*\n\s*(\n\s*)+", "\n\n", text)
    return text.strip()


def _pdf(data: bytes) -> str:
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            reader.decrypt("")
        pages = [page.extract_text() or "" for page in reader.pages[:MAX_PDF_PAGES]]
    except Exception as e:
        raise Unreadable(f"couldn't read the PDF ({type(e).__name__})")
    return tidy("\n\n".join(f"[page {i + 1}]\n{p}" for i, p in enumerate(pages) if p.strip()))


def _office(data: bytes, members: str, text_tag: str, paragraph_tag: str) -> str:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise Unreadable("the file is damaged")
    names = sorted(
        (n for n in archive.namelist() if re.fullmatch(members, n)),
        key=lambda n: [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", n)],
    )
    out: list[str] = []
    for index, name in enumerate(names):
        xml = archive.read(name).decode("utf-8", "ignore")
        xml = re.sub(rf"</{paragraph_tag}>", "\n", xml)
        words = re.findall(rf"<{text_tag}(?:\s[^>]*)?>([^<]*)</{text_tag}>|\n", xml)
        text = "".join(w if w else "\n" for w in words)
        if text.strip():
            out.append(f"[slide {index + 1}]\n{text}" if "slide" in name else text)
    return tidy("\n\n".join(out))


def extract(data: bytes, content_type: str, filename: str) -> str:
    """Plain text from a downloaded file. Raises Unreadable for anything else."""
    name = filename.lower()
    kind = content_type.split(";")[0].strip().lower()
    if name.endswith(".pdf") or kind == "application/pdf":
        return _pdf(data)
    if name.endswith(".docx") or "wordprocessingml" in kind:
        return _office(data, r"word/document\.xml", "w:t", "w:p")
    if name.endswith(".pptx") or "presentationml" in kind:
        return _office(data, r"ppt/slides/slide\d+\.xml", "a:t", "a:p")
    if name.endswith((".html", ".htm")) or kind == "text/html":
        return html_to_text(data.decode("utf-8", "ignore"))
    if name.endswith((".txt", ".md", ".csv", ".py", ".java", ".c", ".tex")) or kind.startswith("text/"):
        return tidy(data.decode("utf-8", "ignore"))
    raise Unreadable(f"Nova can't read {filename.rsplit('.', 1)[-1].upper() or 'this kind of'} files")


_WORD = re.compile(r"[a-z0-9]{3,}")
_STOP = {"the", "and", "for", "are", "what", "whats", "about", "this", "that", "with",
         "does", "have", "any", "there", "from", "into", "how", "you", "your", "can",
         "will", "which", "when", "who", "why", "tell", "today", "was", "were", "has"}


def excerpt(text: str, question: Optional[str], limit: int = EXCERPT_CHARS) -> tuple[str, bool]:
    """(text to hand the model, whether anything was left out)."""
    if len(text) <= limit:
        return text, False
    opening = text[:OPENING_CHARS]
    rest = text[OPENING_CHARS:]
    terms = {w for w in _WORD.findall((question or "").lower()) if w not in _STOP}
    passages = [p for p in re.split(r"\n\s*\n", rest) if p.strip()]
    if not terms:
        return text[:limit], True
    scored = sorted(
        range(len(passages)),
        key=lambda i: -sum(passages[i].lower().count(t) for t in terms),
    )
    budget = limit - len(opening)
    keep: set[int] = set()
    for i in scored:
        if sum(1 for t in terms if t in passages[i].lower()) == 0:
            break
        # A matching passage and the one after it - a heading's content
        # usually follows the heading.
        for j in (i, i + 1):
            if j < len(passages) and j not in keep and len(passages[j]) <= budget:
                keep.add(j)
                budget -= len(passages[j]) + 6
        if budget <= 200:
            break
    chosen = "\n\n".join(passages[i] for i in sorted(keep))
    return (opening + ("\n\n[...]\n\n" + chosen if chosen else "")), True
