from fastapi.testclient import TestClient

import energy_voice_app as app


def test_component_route_serves_the_built_module(tmp_path, monkeypatch):
    built = tmp_path / "karen-component.js"
    built.write_text("export default function Karen() {}")
    monkeypatch.setattr(app, "KAREN_COMPONENT_JS", built)

    response = TestClient(app.api_app).get("/karen-component.js", headers={"Origin": "https://ask-karen.framer.website"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
    assert response.headers["access-control-allow-origin"] in {"*", "https://ask-karen.framer.website"}
    assert "max-age=60" in response.headers["cache-control"]
    assert response.text == "export default function Karen() {}"


def test_component_route_reports_a_missing_build(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "KAREN_COMPONENT_JS", tmp_path / "missing.js")

    assert TestClient(app.api_app).get("/karen-component.js").status_code == 404


def test_framer_canvas_gets_the_module_too(tmp_path, monkeypatch):
    built = tmp_path / "karen-component.js"
    built.write_text("export default 1")
    monkeypatch.setattr(app, "KAREN_COMPONENT_JS", built)

    response = TestClient(app.api_app).get("/karen-component.js", headers={"Origin": "https://project-abc.framercanvas.com"})

    assert response.headers["access-control-allow-origin"] == "*"
