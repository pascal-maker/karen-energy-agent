"""Read a photo of an energy bill on Karen's own backend with Tesseract OCR, no external model."""

from __future__ import annotations

from io import BytesIO

from .knowledge import URL_RE, KnowledgeChunk

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
MAX_IMAGE_PIXELS = 40_000_000
_MAGIC = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"RIFF")


class PhotoUnavailable(RuntimeError):
    """OCR is not installed on this server."""


def is_image_upload(filename: str, content_type: str, data: bytes) -> bool:
    named = filename.lower().endswith(IMAGE_EXTENSIONS) or content_type.lower().startswith("image/")
    return named and data.startswith(_MAGIC)


def _languages(available: set[str]) -> str:
    wanted = [lang for lang in ("nld", "fra", "eng") if lang in available]
    return "+".join(wanted) or "eng"


def ocr_image(data: bytes) -> str:
    """Text on the photo, with the photo turned upright and its contrast evened out first."""
    try:
        import pytesseract
        from PIL import Image, ImageOps
    except ImportError as error:
        raise PhotoUnavailable from error
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    with Image.open(BytesIO(data)) as opened:
        image = ImageOps.exif_transpose(opened).convert("L")
    # Tesseract reads small print best at about 300 dpi; phone photos of an A4 page are often smaller.
    if max(image.size) < 2200:
        scale = 2200 / max(image.size)
        image = image.resize((round(image.width * scale), round(image.height * scale)), Image.LANCZOS)
    image = ImageOps.autocontrast(image, cutoff=1)
    try:
        languages = _languages(set(pytesseract.get_languages(config="")))
        return pytesseract.image_to_string(image, lang=languages, timeout=60)
    except pytesseract.TesseractNotFoundError as error:
        raise PhotoUnavailable from error


def chunks_from_text(text: str, *, source: str, chunk_size: int = 1000, overlap: int = 160) -> list[KnowledgeChunk]:
    clean = " ".join(text.split())
    chunks = []
    start = 0
    while start < len(clean):
        piece = clean[start:start + chunk_size]
        urls = tuple(dict.fromkeys(url.rstrip(".,;:") for url in URL_RE.findall(piece)))
        chunks.append(KnowledgeChunk(page=1, text=piece, urls=urls, source=source))
        if start + chunk_size >= len(clean):
            break
        start += chunk_size - overlap
    return chunks
