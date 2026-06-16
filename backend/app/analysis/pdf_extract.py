"""Download and extract text from a BSE announcement PDF attachment.

The real numbers (order value, profit, guidance, stake %) usually live inside
the attached PDF, not the one-line headline. Extracting that text and feeding it
to the LLM is the single biggest lever on analysis quality.

Text-based PDFs extract cleanly with pypdf. Scanned/image PDFs yield little or
no text (OCR is out of scope) and we fall back to the headline/body upstream.
"""
from __future__ import annotations

import io
import logging
import re

import httpx

from app.sources.bse import HEADERS

logger = logging.getLogger(__name__)

MAX_PDF_BYTES = 25 * 1024 * 1024  # skip pathologically large files
MAX_PAGES = 20
MAX_TEXT_CHARS = 12000  # cap stored/forwarded text

# BSE serves recent filings from AttachLive and older ones from AttachHis;
# the stored URL may point at either, so we try the alternate on a 404.
_ATTACH_LIVE = "/corpfiling/AttachLive/"
_ATTACH_HIS = "/corpfiling/AttachHis/"


def _alternate_url(url: str) -> str | None:
    if _ATTACH_LIVE in url:
        return url.replace(_ATTACH_LIVE, _ATTACH_HIS)
    if _ATTACH_HIS in url:
        return url.replace(_ATTACH_HIS, _ATTACH_LIVE)
    return None


def _clean(text: str) -> str:
    text = text.replace("\x00", " ")
    # Collapse runs of whitespace but keep paragraph breaks readable.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fetch_pdf_text(url: str, client: httpx.Client | None = None) -> str | None:
    """Return extracted text from a PDF URL, or None if unavailable/empty.

    Pass a cookie-warmed `client` to reuse the BSE session across many fetches.
    """
    if not url:
        return None

    owns_client = client is None
    if owns_client:
        client = httpx.Client(headers=HEADERS, timeout=30.0, follow_redirects=True)
        try:
            client.get("https://www.bseindia.com/")
        except httpx.HTTPError:
            pass

    try:
        resp = client.get(url)
        if resp.status_code == 404:
            alt = _alternate_url(url)
            if alt:
                resp = client.get(alt)
        resp.raise_for_status()
        content = resp.content
        ctype = resp.headers.get("content-type", "").lower()
        if "pdf" not in ctype and not str(resp.url).lower().endswith(".pdf"):
            return None
        if len(content) > MAX_PDF_BYTES:
            logger.info("Skipping oversized PDF (%d bytes): %s", len(content), url)
            return None

        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content))
        parts: list[str] = []
        for page in reader.pages[:MAX_PAGES]:
            try:
                parts.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001 - a single bad page shouldn't fail the doc
                continue
        text = _clean("\n".join(parts))
        if len(text) < 30:  # effectively empty / scanned image
            return None
        return text[:MAX_TEXT_CHARS]
    except Exception as exc:  # noqa: BLE001
        logger.warning("PDF extraction failed for %s: %s", url, exc)
        return None
    finally:
        if owns_client:
            client.close()
