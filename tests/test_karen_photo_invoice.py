import io
import shutil

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont

from luminus_harness import photo_invoice
from luminus_harness.rate_limit import SlidingWindowRateLimiter


@pytest.fixture
def client(monkeypatch):
    import energy_voice_app as app

    monkeypatch.setattr(app, "_api_rate_limiter", SlidingWindowRateLimiter(requests=100, window_seconds=60, max_clients=100))
    with TestClient(app.api_app) as test_client:
        yield test_client, app


def jpeg(lines):
    image = Image.new("RGB", (900, 500), (238, 236, 228))
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 22)
    except OSError:
        font = ImageFont.load_default()
    for i, line in enumerate(lines):
        draw.text((50, 40 + i * 48), line, fill=(30, 30, 30), font=font)
    out = io.BytesIO()
    image.rotate(2, expand=True, fillcolor=(120, 110, 100)).save(out, "JPEG", quality=75)
    return out.getvalue()


def test_only_real_images_count_as_photos():
    assert photo_invoice.is_image_upload("factuur.jpg", "image/jpeg", b"\xff\xd8\xff\xe0rest")
    assert not photo_invoice.is_image_upload("factuur.jpg", "image/jpeg", b"%PDF-1.7")
    assert not photo_invoice.is_image_upload("factuur.pdf", "application/pdf", b"%PDF-1.7")


def test_photo_text_becomes_overlapping_chunks():
    chunks = photo_invoice.chunks_from_text("woord " * 400, source="f.jpg")
    assert len(chunks) == 3 and all(chunk.source == "f.jpg" for chunk in chunks)


def test_photo_without_ocr_asks_for_a_pdf(client, monkeypatch):
    test_client, app = client

    def unavailable(_data):
        raise photo_invoice.PhotoUnavailable

    monkeypatch.setattr(app, "ocr_image", unavailable)
    response = test_client.post("/api/upload", files={"file": ("f.jpg", jpeg(["x"]), "image/jpeg")})
    assert response.status_code == 503 and "PDF" in response.json()["detail"]


def test_other_files_are_refused(client):
    test_client, _ = client
    response = test_client.post("/api/upload", files={"file": ("f.txt", b"hallo", "text/plain")})
    assert response.status_code == 415


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract is not installed")
def test_bill_photo_is_read_locally(client):
    test_client, _ = client
    photo = jpeg([
        "Jaarafrekening elektriciteit Eneco",
        "Factuurperiode: 01.09.2025 tot 31.08.2026",
        "Elektriciteitsverbruik: 3.245 kWh",
        "Totale energiekosten 1.284,56 EUR",
        "Totaal voorschotten reeds aangerekend 1.140,00 EUR",
        "Saldo te betalen 144,56 EUR",
    ])
    reply = test_client.post("/api/upload", files={"file": ("IMG_0001.jpg", photo, "image/jpeg")}).json()
    assert reply["processing"] == "local-only" and reply["session_id"]
    assert "van de foto" in reply["answer"] and "PDF-pagina" not in reply["answer"]
    assert "144,56" in reply["answer"]
