import pymupdf
import pytest

from app.services.pdf_analyzer import (
    MAX_PAGES,
    PdfError,
    Span,
    analyze_pdf,
    build_rows,
    find_gutter,
    order_rows,
)

W, H = 612.0, 792.0


def span(text, x0, y0, width=None, size=11.0, **kw) -> Span:
    width = width if width is not None else len(text) * size * 0.5
    return Span(text, x0, y0, x0 + width, y0 + size * 1.15, size=size, **kw)


def _pdf(pages: int = 1, text: str = "", encrypt: bool = False) -> bytes:
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text)
    kwargs = {}
    if encrypt:
        kwargs = {"encryption": pymupdf.PDF_ENCRYPT_AES_256, "user_pw": "secret"}
    return doc.tobytes(**kwargs)


# ---------------------------------------------------------------- rows


def test_bullet_glyph_drawn_higher_joins_its_text_row():
    # Word's Symbol-font bullet sits ~1pt above the text baseline.
    rows = build_rows([span("•", 54, 229, width=4), span("Developed a program", 72, 228)])
    assert [r.text for r in rows] == ["• Developed a program"]


def test_right_aligned_date_stays_on_its_row():
    rows = build_rows([span("Pricing Analyst", 36, 252), span("May 2024-Aug. 2024", 470, 252)])
    assert len(rows) == 1
    assert rows[0].text == "Pricing Analyst May 2024-Aug. 2024"


def test_rows_ordered_top_to_bottom():
    rows = build_rows([span("second", 36, 120), span("first", 36, 100)])
    assert [r.text for r in rows] == ["first", "second"]


def test_row_style_flags():
    (mixed,) = build_rows(
        [span("Company", 36, 100, bold=True), span(", City", 90, 100), span("2024", 500, 100, bold=True)]
    )
    assert mixed.starts_bold and not mixed.bold
    (caps,) = build_rows([span("EXPERIENCE", 36, 100, bold=True)])
    assert caps.bold and caps.all_caps


# ---------------------------------------------------------------- columns


def _two_column_page() -> list[Span]:
    spans = [span("Jane Doe", 250, 40, size=18)]
    # Left sidebar and main column with independent baselines (offset by 5pt).
    for i in range(12):
        spans.append(span(f"Sidebar item {i}", 36, 150 + i * 30, width=120))
        spans.append(span(f"Main column line number {i} with more text", 230, 155 + i * 30, width=330))
    return spans


def test_two_column_layout_detected_and_read_column_by_column():
    spans = _two_column_page()
    assert find_gutter(spans, W, H) is not None
    texts = [r.text for r in order_rows(spans, W, H)]
    assert texts[0] == "Jane Doe"
    assert texts[1:13] == [f"Sidebar item {i}" for i in range(12)]
    assert texts[13].startswith("Main column line number 0")


def test_columns_with_aligned_baselines_still_detected():
    # Same font and spacing in both columns: every line shares a row with the other side.
    spans = []
    for i in range(12):
        y = 150 + i * 30
        spans.append(span(f"Sidebar item {i}", 36, y, width=120))
        spans.append(span(f"Main column line number {i} with more text", 230, y, width=330))
    assert find_gutter(spans, W, H) is not None


def test_right_aligned_dates_are_not_a_second_column():
    spans = []
    for i in range(12):
        y = 150 + i * 30
        spans.append(span(f"Company {i}, Job Title", 36, y, width=200))
        spans.append(span("Jan 2020 - Present", 460, y, width=110))
    assert find_gutter(spans, W, H) is None


def test_single_column_has_no_gutter():
    spans = [span("Full width line of body text " * 3, 36, 150 + i * 14, width=530) for i in range(20)]
    assert find_gutter(spans, W, H) is None


# ---------------------------------------------------------------- rejection


def test_non_pdf_rejected():
    with pytest.raises(PdfError, match="isn't a PDF"):
        analyze_pdf(b"hello, this is a text file")


def test_corrupt_pdf_rejected():
    with pytest.raises(PdfError, match="couldn't be opened"):
        analyze_pdf(b"%PDF-1.7\n" + b"\x00garbage" * 50)


def test_image_only_pdf_rejected_with_clear_message():
    with pytest.raises(PdfError, match="scanned or image-only"):
        analyze_pdf(_pdf(text=""))


def test_password_protected_pdf_rejected():
    with pytest.raises(PdfError, match="password-protected"):
        analyze_pdf(_pdf(text="x" * 300, encrypt=True))


def test_too_many_pages_rejected():
    with pytest.raises(PdfError, match=f"longer than {MAX_PAGES} pages"):
        analyze_pdf(_pdf(pages=MAX_PAGES + 1, text="page"))


# ---------------------------------------------------------------- real resumes


def test_fixture_pdfs_extract(fixture_pdfs):
    for path in fixture_pdfs:
        doc = analyze_pdf(path.read_bytes())
        assert len(doc.text) >= 200, path.name
        assert doc.page_count >= 1
        assert all(r.text for r in doc.rows)
        # No raw glyph-only rows: bullets must be merged with their text.
        assert not any(r.text in {"•", ""} for r in doc.rows), path.name
