"""PDF text extraction with pragmatic section-heading heuristics."""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass

from pypdf import PdfReader


@dataclass
class Section:
    title: str
    text: str


def extract_sections(pdf_path: str) -> list[Section]:
    """Split a PDF into rough sections using pypdf + heading heuristics
    (font size / all-caps / numbered headers).

    Fall back to a single ``full_text`` section when no headings are found.
    """
    reader = PdfReader(pdf_path)
    lines: list[tuple[str, float]] = []

    for page in reader.pages:
        page_lines: list[tuple[str, float]] = []

        def collect_text(
            text: str,
            _cm: list[float],
            _tm: list[float],
            _font: dict | None,
            font_size: float,
        ) -> None:
            for line in text.splitlines():
                normalized = _normalize(line)
                if normalized:
                    page_lines.append((normalized, float(font_size or 0)))

        page.extract_text(visitor_text=collect_text)
        lines.extend(page_lines)

    if not lines:
        return []

    body_sizes = [size for text, size in lines if len(text) >= 30 and size > 0]
    body_size = statistics.median(body_sizes) if body_sizes else 10.0
    sections: list[Section] = []
    title = "front_matter"
    body: list[str] = []
    headings_found = 0

    for text, font_size in lines:
        if _is_heading(text, font_size, body_size):
            if body:
                sections.append(Section(title=title, text="\n".join(body).strip()))
            title = text
            body = []
            headings_found += 1
        else:
            body.append(text)

    if body:
        sections.append(Section(title=title, text="\n".join(body).strip()))

    sections = [section for section in sections if section.text]
    if headings_found == 0:
        full_text = "\n".join(text for text, _ in lines).strip()
        return [Section(title="full_text", text=full_text)] if full_text else []
    return sections


_NUMBERED_HEADING = re.compile(
    r"^(?:\d{1,2}(?:\.\d{1,2})*\.?|[IVXLC]+\.?)\s+[A-Z][^.!?]{0,100}$"
)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _is_heading(text: str, font_size: float, body_size: float) -> bool:
    if len(text) < 2 or len(text) > 120 or text.isdigit():
        return False
    words = text.split()
    letters = [char for char in text if char.isalpha()]
    significant_words = [re.sub(r"[^A-Za-z]", "", word) for word in words]
    significant_words = [word for word in significant_words if word]
    title_case_ratio = sum(word[0].isupper() for word in significant_words) / max(
        len(significant_words), 1
    )
    # Some PDFs render section headings in small caps: the first letter comes
    # through as one text run at body size, the rest of the word as a
    # separate, smaller-font run that is still all-uppercase glyphs. Without
    # the font_size floor, that trailing run (e.g. "NTRODUCTION" from
    # "INTRODUCTION") reads as its own valid all-caps heading.
    is_all_caps = (
        len(letters) >= 4
        and not any(char.isdigit() for char in text)
        and len(words) <= 14
        and all(char.isupper() for char in letters)
        and (len(words) > 1 or len(letters) >= 5)
        and font_size >= body_size * 0.95
    )
    is_large = (
        len(letters) >= 4
        and font_size >= body_size * 1.18
        and len(words) <= 16
        and title_case_ratio >= 0.5
    )
    return bool(_NUMBERED_HEADING.match(text) or is_all_caps or is_large)
