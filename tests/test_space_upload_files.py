import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_every_imported_harness_module_is_uploaded_to_the_space():
    uploaded = set(re.findall(r'"(luminus_harness/\w+\.py)"', (ROOT / "scripts" / "upload_energy_voice_space.py").read_text()))
    sources = [ROOT / "energy_voice_app.py", *(ROOT / path for path in uploaded)]
    imported = set()
    for source in sources:
        text = source.read_text()
        imported |= {f"luminus_harness/{name}.py" for name in re.findall(r"luminus_harness\.(\w+)", text)}
        imported |= {f"luminus_harness/{name}.py" for name in re.findall(r"^\s*from \.(\w+) import", text, re.M)}
    missing = {path for path in imported if (ROOT / path).exists()} - uploaded
    assert not missing, f"Add these to scripts/upload_energy_voice_space.py: {sorted(missing)}"


def test_every_data_file_the_app_reads_is_uploaded_to_the_space():
    """PR 2 read market/tariffs-phase2.json, which only existed locally: every price question crashed on the Space."""
    script = (ROOT / "scripts" / "upload_energy_voice_space.py").read_text()
    uploaded = set(re.findall(r'^\s*"([\w./-]+)":', script, re.M))
    sources = [ROOT / "energy_voice_app.py", *(ROOT / path for path in uploaded if path.endswith(".py"))]
    read = set()
    for source in sources:
        for name in re.findall(r'"([\w.-]+\.(?:json|txt|html|csv|md))"', source.read_text()):
            for folder in ("luminus_harness", "market", "experts"):
                if (ROOT / folder / name).is_file():
                    read.add(f"{folder}/{name}")
    missing = read - uploaded
    assert not missing, f"Add these to scripts/upload_energy_voice_space.py: {sorted(missing)}"


def test_a_missing_tariff_card_file_never_costs_the_visitor_an_answer(monkeypatch):
    from pathlib import Path as RealPath

    from luminus_harness import question_kind

    monkeypatch.setattr(RealPath, "read_text", lambda self, **_: (_ for _ in ()).throw(FileNotFoundError(self)))
    assert question_kind.unknown_supplier("Wat kost stroom bij Mega?") is None
    assert question_kind.unknown_supplier("Wat kost stroom vandaag?") is None
