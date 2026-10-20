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
