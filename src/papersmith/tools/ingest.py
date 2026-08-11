"""Fetch, section, chunk, and embed a paper or technical article."""

from __future__ import annotations

import hashlib
import math
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests
from agno.knowledge.embedder.base import Embedder
from agno.knowledge.knowledge import Knowledge
from agno.knowledge.reader.text_reader import TextReader
from agno.vectordb.lancedb import LanceDb

from papersmith.tools import pdf_reader
from papersmith.tools.pdf_reader import Section

_ARXIV_ID = re.compile(r"^(?:arXiv:)?(\d{4}\.\d{4,5}(?:v\d+)?)$", re.IGNORECASE)
_ARXIV_URL = re.compile(
    r"^https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5}(?:v\d+)?)(?:\.pdf)?(?:[?#].*)?$",
    re.IGNORECASE,
)
_HTTP_TIMEOUT = 60


@dataclass
class RawDocument:
    run_id: str
    source: str
    local_path: str
    kind: str  # "arxiv" | "pdf" | "html"


def fetch(source: str, run_id: str | None = None) -> RawDocument:
    """Detect arXiv ID/URL vs generic URL vs local file path, download/read
    accordingly, and save the raw file under data/papers/<run_id>/.

    ``run_id`` is optional for callers that do not already have a workflow run.
    """
    run_id = _safe_run_id(run_id or uuid.uuid4().hex)
    output_dir = Path(os.environ.get("PAPERSMITH_PAPERS_DIR", "data/papers")) / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    arxiv_id = _arxiv_id(source)
    if arxiv_id:
        url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
        destination = output_dir / f"{arxiv_id}.pdf"
        _download(url, destination)
        return RawDocument(run_id, source, str(destination), "arxiv")

    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"}:
        response = requests.get(
            source,
            timeout=_HTTP_TIMEOUT,
            headers={"User-Agent": "PaperSmith/0.1 (paper ingestion)"},
        )
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        is_pdf = "application/pdf" in content_type or response.content.startswith(
            b"%PDF"
        )
        suffix = ".pdf" if is_pdf else ".html"
        name = Path(unquote(parsed.path)).stem or "document"
        destination = output_dir / f"{_safe_filename(name)}{suffix}"
        destination.write_bytes(response.content)
        return RawDocument(
            run_id, source, str(destination), "pdf" if is_pdf else "html"
        )

    local_source = Path(source).expanduser()
    if not local_source.is_file():
        raise FileNotFoundError(f"Document source does not exist: {source}")
    suffix = local_source.suffix.lower()
    if suffix not in {".pdf", ".html", ".htm"}:
        raise ValueError("Local documents must be PDF or HTML files")
    destination = output_dir / _safe_filename(local_source.name)
    if local_source.resolve() != destination.resolve():
        shutil.copy2(local_source, destination)
    kind = "pdf" if suffix == ".pdf" else "html"
    return RawDocument(run_id, source, str(destination), kind)


def extract_sections(raw: RawDocument) -> list[Section]:
    """Route to pdf_reader.extract_sections for PDFs, or an HTML heading
    splitter for blog posts.

    """
    if raw.kind in {"arxiv", "pdf"}:
        return pdf_reader.extract_sections(raw.local_path)
    if raw.kind == "html":
        parser = _HeadingHTMLParser()
        parser.feed(Path(raw.local_path).read_text(encoding="utf-8", errors="replace"))
        return parser.sections()
    raise ValueError(f"Unsupported raw document kind: {raw.kind}")


def embed_and_store(sections: list[Section], run_id: str) -> str:
    """Chunk each section (~500 tokens, slight overlap), embed, and write to
    a LanceDB table at data/vectorstore/<run_id>. Returns the table path.

    """
    if not sections:
        raise ValueError("Cannot embed an empty section list")

    run_id = _safe_run_id(run_id)
    table_path = Path(
        os.environ.get("PAPERSMITH_VECTOR_DB_PATH", "data/vectorstore")
    ) / run_id
    table_path.mkdir(parents=True, exist_ok=True)
    vector_db = LanceDb(
        uri=str(table_path),
        table_name="paper_chunks",
        embedder=LocalHashEmbedder(),
    )
    knowledge = Knowledge(vector_db=vector_db)
    reader = TextReader(chunk=False)

    for section_index, section in enumerate(sections):
        for chunk_index, chunk in enumerate(_chunk_text(section.text)):
            knowledge.insert(
                name=f"{section.title} [{chunk_index}]",
                text_content=chunk,
                metadata={
                    "run_id": run_id,
                    "section": section.title,
                    "section_index": section_index,
                    "chunk_index": chunk_index,
                },
                reader=reader,
                upsert=True,
            )
    return str(table_path)


@dataclass
class LocalHashEmbedder(Embedder):
    """Small, deterministic and cost-free token hashing embedder."""

    dimensions: int = 384

    def get_embedding(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in re.findall(r"[a-z0-9]+", text.lower()):
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            value = int.from_bytes(digest, "little")
            index = value % self.dimensions
            vector[index] += -1.0 if value & (1 << 63) else 1.0
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector

    def get_embedding_and_usage(self, text: str) -> tuple[list[float], None]:
        return self.get_embedding(text), None


def _arxiv_id(source: str) -> str | None:
    match = _ARXIV_ID.match(source.strip()) or _ARXIV_URL.match(source.strip())
    return match.group(1) if match else None


def _download(url: str, destination: Path) -> None:
    response = requests.get(
        url,
        timeout=_HTTP_TIMEOUT,
        headers={"User-Agent": "PaperSmith/0.1 (paper ingestion)"},
    )
    response.raise_for_status()
    if not response.content.startswith(b"%PDF"):
        raise ValueError(f"Expected a PDF response from {url}")
    destination.write_bytes(response.content)


def _safe_run_id(run_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "-", run_id).strip(".-")
    if not safe:
        raise ValueError("run_id must contain at least one letter or digit")
    return safe


def _safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name) or "document"


def _chunk_text(
    text: str, chunk_words: int = 375, overlap_words: int = 40
) -> list[str]:
    words = text.split()
    if not words:
        return []
    chunks: list[str] = []
    step = chunk_words - overlap_words
    for start in range(0, len(words), step):
        chunk = " ".join(words[start : start + chunk_words])
        if chunk:
            chunks.append(chunk)
        if start + chunk_words >= len(words):
            break
    return chunks


class _HeadingHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._ignored_depth = 0
        self._heading_tag: str | None = None
        self._heading_parts: list[str] = []
        self._title = "front_matter"
        self._body: list[str] = []
        self._sections: list[Section] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._ignored_depth += 1
        elif not self._ignored_depth and tag in {"h1", "h2", "h3"}:
            self._flush_section()
            self._heading_tag = tag
            self._heading_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._ignored_depth:
            self._ignored_depth -= 1
        elif tag == self._heading_tag:
            heading = " ".join(self._heading_parts).strip()
            if heading:
                self._title = heading
            self._heading_tag = None

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        text = re.sub(r"\s+", " ", data).strip()
        if not text:
            return
        if self._heading_tag:
            self._heading_parts.append(text)
        else:
            self._body.append(text)

    def sections(self) -> list[Section]:
        self._flush_section()
        return self._sections

    def _flush_section(self) -> None:
        text = "\n".join(self._body).strip()
        if text:
            self._sections.append(Section(self._title, text))
        self._body = []
