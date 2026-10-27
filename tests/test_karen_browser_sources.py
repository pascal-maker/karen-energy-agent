import pytest

from luminus_harness import browserbase_sources


@pytest.mark.asyncio
async def test_failed_fetch_is_not_returned_as_a_checked_live_source(monkeypatch) -> None:
    monkeypatch.setattr(
        browserbase_sources,
        "_search",
        lambda query, scope: [{"title": "CREG", "url": "https://www.creg.be/example"}],
    )
    monkeypatch.setattr(
        browserbase_sources,
        "_fetch",
        lambda url: "Browserbase is nog niet geconfigureerd.",
    )

    pages = await browserbase_sources.search_energy_pages("actuele stroomprijs", "creg", max_pages=1)

    assert pages == []


@pytest.mark.asyncio
async def test_successful_fetch_returns_checked_source_content(monkeypatch) -> None:
    monkeypatch.setattr(
        browserbase_sources,
        "_search",
        lambda query, scope: [{"title": "CREG", "url": "https://www.creg.be/example"}],
    )
    monkeypatch.setattr(browserbase_sources, "_fetch", lambda url: "Maandgemiddelde gepubliceerd in mei.")

    pages = await browserbase_sources.search_energy_pages("actuele stroomprijs", "creg", max_pages=1)

    assert pages == [{
        "title": "CREG",
        "url": "https://www.creg.be/example",
        "content": "Maandgemiddelde gepubliceerd in mei.",
    }]


@pytest.mark.asyncio
async def test_curated_source_is_not_returned_when_fetch_fails(monkeypatch) -> None:
    monkeypatch.setattr(browserbase_sources, "_fetch", lambda url: "Browserbase is nog niet geconfigureerd.")

    page = await browserbase_sources.fetch_energy_page("luminus_invoice")

    assert page is None


@pytest.mark.asyncio
async def test_curated_source_returns_page_only_after_successful_fetch(monkeypatch) -> None:
    monkeypatch.setattr(browserbase_sources, "_fetch", lambda url: "Een voorschot is een maandelijkse inschatting.")

    page = await browserbase_sources.fetch_energy_page("luminus_invoice")

    assert page == {
        "title": "Je energiefactuur uitgelegd — Luminus",
        "url": "https://www.luminus.be/nl/prive/energie/energiefactuur-uitgelegd/",
        "content": "Een voorschot is een maandelijkse inschatting.",
    }


def test_search_with_only_off_site_results_says_so(monkeypatch) -> None:
    import io
    import json as json_module
    from luminus_harness import browserbase_sources

    payload = {"results": [{"title": "Elders", "url": "https://example.com/zonnepanelen"}]}
    monkeypatch.setenv("BROWSERBASE_API_KEY", "test")
    monkeypatch.setattr(browserbase_sources, "urlopen", lambda *_args, **_kwargs: io.BytesIO(json_module.dumps(payload).encode()))
    result = browserbase_sources._search("zonnepanelen", "official_energy")
    assert result == "Browserbase Search gaf 1 resultaten, geen op de toegelaten sites (example.com)."


def test_every_search_query_fits_browserbase_limit() -> None:
    long_question = "Hoe werkt het sociaal tarief voor elektriciteit en aardgas in Vlaanderen? " * 6
    for domains in browserbase_sources.ENERGY_SOURCE_SCOPES.values():
        queries = browserbase_sources._scoped_queries(long_question, domains)
        assert all(len(query) <= 200 for query in queries)
        assert all(query.startswith("Hoe werkt het sociaal tarief") for query in queries)
        assert all(f"site:{domain}" in " ".join(queries) for domain in domains)


def test_fetch_reads_page_directly_when_browserbase_has_no_quota(monkeypatch) -> None:
    import io
    from urllib.error import HTTPError

    url = "https://www.creg.be/nl/a-z-index/verbrekingsvergoeding"
    html = "<html><script>x()</script><p>" + "Sinds 2012 betaal je geen verbrekingsvergoeding. " * 10 + "</p></html>"

    class Page(io.BytesIO):
        headers = {"Content-Type": "text/html; charset=utf-8"}

        def geturl(self):
            return url

    def fake_urlopen(request, timeout):
        if "browserbase" in request.full_url:
            raise HTTPError(request.full_url, 402, "Payment Required", {}, None)
        return Page(html.encode())

    monkeypatch.setenv("BROWSERBASE_API_KEY", "test")
    monkeypatch.setattr(browserbase_sources, "urlopen", fake_urlopen)
    browserbase_sources._PAGE_CACHE.clear()
    content = browserbase_sources._fetch(url)
    assert content.startswith("Sinds 2012 betaal je geen verbrekingsvergoeding.")
    assert "x()" not in content


def _tiny_pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)


def test_the_direct_download_reads_an_official_pdf(monkeypatch) -> None:
    import io

    text = "Distributietarief elektriciteit 2026 " * 10
    response = io.BytesIO(_tiny_pdf(text))
    response.headers = {"Content-Type": "application/pdf"}
    response.geturl = lambda: "https://www.creg.be/tarieven.pdf"
    response.__enter__ = lambda self=response: self
    monkeypatch.setattr(browserbase_sources, "urlopen", lambda *_args, **_kwargs: response)
    assert "Distributietarief elektriciteit 2026" in browserbase_sources._fetch_direct("https://www.creg.be/tarieven.pdf")
