"""PDF → visual rows with layout information (plan §5, step 1).

PyMuPDF's own lines/blocks follow the PDF's content stream, which is often not reading order
(right-aligned dates, bullet glyphs drawn separately, designed two-column templates). So we
flatten to spans and rebuild rows and reading order ourselves.
"""

import math
import re
import statistics
import unicodedata
from dataclasses import dataclass, field

import pymupdf

MAX_PAGES = 10
PAGES_TO_READ = 5
MIN_TEXT_CHARS = 200

# Default dict flags minus images (their decoded bytes would sit in memory) and minus
# ligatures (so "Certiﬁcations" comes back as "Certifications").
_TEXT_FLAGS = (
    pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES & ~pymupdf.TEXT_PRESERVE_LIGATURES
)
_INVISIBLE = dict.fromkeys(map(ord, "­​‌‍⁠﻿"), None)
_PAGE_NUMBER = re.compile(r"^(page\s*)?\d{1,3}(\s*(of|/)\s*\d{1,3})?$", re.IGNORECASE)

# Tables/drawings scans get expensive on vector-heavy designer exports.
_MAX_DRAWINGS_FOR_SCAN = 1500

pymupdf.TOOLS.mupdf_display_errors(False)
pymupdf.no_recommend_layout()  # find_tables() otherwise prints an advert to stdout


class PdfError(Exception):
    """The upload can't be processed. The message is shown to the user."""


@dataclass(frozen=True)
class Span:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float = 11.0
    bold: bool = False
    italic: bool = False
    font: str = ""

    @property
    def height(self) -> float:
        return max(self.y1 - self.y0, 0.1)

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass
class Row:
    """One visual line of text: spans sharing a baseline, left to right."""

    spans: list[Span]
    page: int = 0

    @property
    def text(self) -> str:
        parts: list[str] = []
        prev: Span | None = None
        for s in self.spans:
            if prev is not None and s.x0 - prev.x1 > 0.15 * s.size:
                parts.append(" ")
            parts.append(s.text)
            prev = s
        return re.sub(r"\s+", " ", "".join(parts)).strip()

    @property
    def x0(self) -> float:
        return self.spans[0].x0

    @property
    def x1(self) -> float:
        return max(s.x1 for s in self.spans)

    @property
    def y0(self) -> float:
        return min(s.y0 for s in self.spans)

    @property
    def y1(self) -> float:
        return max(s.y1 for s in self.spans)

    @property
    def size(self) -> float:
        return max(s.size for s in self.spans)

    def _styled(self, attr: str) -> bool:
        worded = [s for s in self.spans if any(c.isalpha() for c in s.text)]
        return bool(worded) and all(getattr(s, attr) for s in worded)

    @property
    def bold(self) -> bool:
        return self._styled("bold")

    @property
    def italic(self) -> bool:
        return self._styled("italic")

    @property
    def starts_bold(self) -> bool:
        """First worded span is bold, e.g. "**Company**, City  **May 2024**"."""
        worded = [s for s in self.spans if any(c.isalpha() for c in s.text)]
        return bool(worded) and worded[0].bold

    @property
    def all_caps(self) -> bool:
        letters = [c for c in self.text if c.isalpha()]
        return len(letters) >= 2 and all(c.isupper() for c in letters)


@dataclass
class PageInfo:
    number: int
    width: float
    height: float
    char_count: int = 0
    # (fraction of page area, characters of text drawn over it) for images >= 25% of the page
    big_images: list[tuple[float, int]] = field(default_factory=list)
    table_chars: int = 0


@dataclass
class PdfDocument:
    rows: list[Row]
    pages: list[PageInfo]
    page_count: int
    links: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(r.text for r in self.rows)


# ---------------------------------------------------------------- public entry point


def analyze_pdf(data: bytes) -> PdfDocument:
    if b"%PDF-" not in data[:1024]:
        raise PdfError("This file isn't a PDF.")
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as e:  # PyMuPDF raises several unrelated types on corrupt input
        raise PdfError("This PDF couldn't be opened. It may be damaged.") from e

    try:
        if doc.needs_pass:
            raise PdfError("This PDF is password-protected. Please upload an unlocked copy.")
        if doc.page_count > MAX_PAGES:
            raise PdfError(f"Resumes longer than {MAX_PAGES} pages aren't supported.")

        rows: list[Row] = []
        pages: list[PageInfo] = []
        links: list[str] = []
        scan_tables = doc.page_count <= 3
        for number in range(min(doc.page_count, PAGES_TO_READ)):
            page = doc[number]
            page_rows, info = _read_page(page, number, scan_tables)
            rows.extend(page_rows)
            pages.append(info)
            links.extend(link["uri"] for link in page.get_links() if link.get("uri"))
        page_count = doc.page_count
    finally:
        doc.close()

    rows = _drop_running_headers(rows, pages)
    for info in pages:
        info.char_count = sum(len(r.text) for r in rows if r.page == info.number)

    result = PdfDocument(rows=rows, pages=pages, page_count=page_count, links=links)
    _check_quality(result.text)
    return result


# ---------------------------------------------------------------- page reading


def _read_page(page: pymupdf.Page, number: int, scan_tables: bool) -> tuple[list[Row], PageInfo]:
    width, height = page.rect.width, page.rect.height
    drawings = page.get_cdrawings()
    many_drawings = len(drawings) >= _MAX_DRAWINGS_FOR_SCAN
    dark_fills = [] if many_drawings else _dark_filled_rects(drawings)

    spans = []
    for block in page.get_text("dict", flags=_TEXT_FLAGS)["blocks"]:
        for line in block.get("lines", []):
            if abs(line["dir"][1]) > 0.1:  # rotated/vertical text (sidebar labels)
                continue
            for raw in line["spans"]:
                span = _make_span(raw)
                if span is None:
                    continue
                if _is_near_white(raw["color"]) and not (
                    many_drawings or _inside_any(span, dark_fills)
                ):
                    continue  # white-on-white text: hidden keyword stuffing
                spans.append(span)

    rows = [Row(r.spans, number) for r in order_rows(spans, width, height)]
    info = PageInfo(number=number, width=width, height=height)
    info.big_images = _big_images(page, spans)
    if scan_tables and not many_drawings:
        info.table_chars = _table_chars(page)
    return rows, info


def _clean(text: str) -> str:
    return unicodedata.normalize("NFKC", text).translate(_INVISIBLE).replace("\t", " ")


def _make_span(raw: dict) -> Span | None:
    text = _clean(raw["text"])
    stripped = text.strip()
    if not stripped or raw["size"] < 4 or raw.get("alpha", 255) == 0:
        return None
    x0, y0, x1, y1 = raw["bbox"]
    # The bbox includes padding spaces (Word right-aligns dates with runs of spaces).
    # Shrink it proportionally so geometry reflects the visible text.
    n = len(text)
    lead, trail = n - len(text.lstrip()), n - len(text.rstrip())
    w = x1 - x0
    x0, x1 = x0 + w * lead / n, x1 - w * trail / n
    flags = raw["flags"]
    font = raw.get("font", "")
    return Span(
        text=stripped,
        x0=x0,
        y0=y0,
        x1=x1,
        y1=y1,
        size=round(raw["size"], 1),
        bold=bool(flags & 16) or "bold" in font.lower(),
        italic=bool(flags & 2) or "italic" in font.lower() or "oblique" in font.lower(),
        font=font,
    )


def _is_near_white(color: int) -> bool:
    r, g, b = (color >> 16) & 255, (color >> 8) & 255, color & 255
    return min(r, g, b) >= 0xF0


def _dark_filled_rects(drawings: list[dict]) -> list[pymupdf.Rect]:
    rects = []
    for d in drawings:
        fill = d.get("fill")
        if fill and max(fill[:3]) < 0.85:
            rects.append(pymupdf.Rect(d["rect"]))
    return rects


def _inside_any(span: Span, rects: list[pymupdf.Rect]) -> bool:
    cx = (span.x0 + span.x1) / 2
    return any(r.x0 <= cx <= r.x1 and r.y0 <= span.cy <= r.y1 for r in rects)


def _big_images(page: pymupdf.Page, spans: list[Span]) -> list[tuple[float, int]]:
    area = page.rect.width * page.rect.height
    seen, result = set(), []
    for info in page.get_image_info():
        rect = pymupdf.Rect(info["bbox"]) & page.rect
        key = tuple(round(v) for v in rect)
        if rect.is_empty or key in seen:
            continue
        seen.add(key)
        frac = rect.width * rect.height / area
        if frac < 0.25:
            continue  # icons, logos, headshots
        over = sum(
            len(s.text)
            for s in spans
            if rect.x0 <= (s.x0 + s.x1) / 2 <= rect.x1 and rect.y0 <= s.cy <= rect.y1
        )
        result.append((frac, over))
    return result


def _table_chars(page: pymupdf.Page) -> int:
    """Characters inside real grid tables (≥2×2 with ≥4 filled cells)."""
    try:
        tables = page.find_tables().tables
    except Exception:
        return 0
    total = 0
    for t in tables:
        if t.row_count < 2 or t.col_count < 2:
            continue
        cells = [c for row in t.extract() for c in row if c and c.strip()]
        if len(cells) >= 4:
            total += sum(len(c) for c in cells)
    return total


# ---------------------------------------------------------------- rows and reading order


def _v_overlap(a: Span | Row, b: Span | Row) -> float:
    """Vertical overlap as a fraction of the shorter of the two heights."""
    inter = min(a.y1, b.y1) - max(a.y0, b.y0)
    shorter = min(a.y1 - a.y0, b.y1 - b.y0) or 0.1
    return max(inter, 0) / shorter


def build_rows(spans: list[Span]) -> list[Row]:
    """Group spans into visual rows. Uses bbox overlap, not baseline equality, because
    bullet glyphs from Symbol fonts sit a point or two higher than their text."""
    rows: list[Row] = []
    for span in sorted(spans, key=lambda s: (s.cy, s.x0)):
        for row in reversed(rows[-4:]):
            if _v_overlap(span, row) >= 0.5:
                row.spans.append(span)
                break
        else:
            rows.append(Row([span]))
    for row in rows:
        row.spans.sort(key=lambda s: s.x0)
    rows.sort(key=lambda r: (r.y0, r.x0))
    return rows


def find_gutter(spans: list[Span], width: float, height: float) -> float | None:
    """x position of a two-column gutter, or None for single-column pages.

    A gutter is a vertical strip in the middle of the page that almost no text crosses,
    with substantial text on both sides. What it must not confuse with columns: right-aligned
    dates/locations next to job titles. Those are short fragments that each share a row with
    left-side text. Real columns can share rows too (same font and spacing), but their lines
    are long.
    """
    body = [s for s in spans if s.y0 > height * 0.12]  # ignore the name/contact band
    if len(body) < 10:
        return None

    cover = [0] * (int(width) + 2)
    for s in body:
        for x in range(max(int(s.x0), 0), min(math.ceil(s.x1), int(width)) + 1):
            cover[x] += 1
    tolerance = max(1, int(len(body) * 0.03))
    lo, hi = int(width * 0.15), int(width * 0.85)

    best_start, best_len, run_start = 0, 0, None
    for x in range(lo, hi + 2):
        clear = x <= hi and cover[x] <= tolerance
        if clear and run_start is None:
            run_start = x
        elif not clear and run_start is not None:
            if x - run_start > best_len:
                best_start, best_len = run_start, x - run_start
            run_start = None
    if best_len < 8:
        return None
    gutter = best_start + best_len / 2

    left = [s for s in body if s.x1 <= gutter]
    right = [s for s in body if s.x0 >= gutter]
    total = sum(len(s.text) for s in body)
    left_chars, right_chars = sum(len(s.text) for s in left), sum(len(s.text) for s in right)
    if left_chars < 0.15 * total or right_chars < 0.15 * total:
        return None

    shared = sum(len(r.text) for r in right if any(_v_overlap(r, l) >= 0.5 for l in left))
    short_fragments = statistics.median(len(s.text) for s in right) < 25
    if shared / right_chars >= 0.4 and short_fragments:
        return None
    return gutter


def order_rows(spans: list[Span], width: float, height: float) -> list[Row]:
    """Rows in reading order: single column top to bottom; two columns as
    (full-width header) → left column → right column."""
    gutter = find_gutter(spans, width, height)
    if gutter is None:
        return build_rows(spans)

    # Everything above where the columns start is the header (name, contact), whichever side
    # of the gutter it happens to sit on.
    column_top = min(
        (s.y0 for s in spans if s.y0 > height * 0.12 and (s.x1 <= gutter or s.x0 >= gutter)),
        default=0,
    )
    top = [s for s in spans if s.y1 <= column_top]
    rest = [s for s in spans if s.y1 > column_top]
    right = [s for s in rest if s.x0 >= gutter]
    left = [s for s in rest if s.x0 < gutter]  # includes stray full-width lines
    return build_rows(top) + build_rows(left) + build_rows(right)


def _drop_running_headers(rows: list[Row], pages: list[PageInfo]) -> list[Row]:
    """Remove page numbers and headers/footers repeated on 2+ pages."""
    band = {p.number: (p.height * 0.08, p.height * 0.92) for p in pages}

    def in_band(r: Row) -> bool:
        top, bottom = band[r.page]
        return r.y1 <= top or r.y0 >= bottom

    def key(r: Row) -> str:
        return re.sub(r"\d+", "#", r.text.lower())

    pages_seen: dict[str, set[int]] = {}
    for r in rows:
        if in_band(r):
            pages_seen.setdefault(key(r), set()).add(r.page)
    repeated = {k for k, p in pages_seen.items() if len(p) >= 2}

    return [
        r
        for r in rows
        if not _PAGE_NUMBER.match(r.text) and not (in_band(r) and key(r) in repeated)
    ]


def _check_quality(text: str) -> None:
    stripped = text.strip()
    if len(stripped) < MIN_TEXT_CHARS:
        raise PdfError(
            "This PDF looks like a scanned or image-only document. "
            "Please upload a PDF with selectable text."
        )
    nonspace = [c for c in stripped if not c.isspace()]
    bad = sum(1 for c in nonspace if c == "�" or unicodedata.category(c) == "Co")
    letters = sum(1 for c in nonspace if c.isalpha())
    if bad > 0.05 * len(nonspace) or letters < 0.5 * len(nonspace):
        raise PdfError(
            "We couldn't read the text in this PDF (it may use an unusual font encoding). "
            "Try exporting it again from your editor."
        )
