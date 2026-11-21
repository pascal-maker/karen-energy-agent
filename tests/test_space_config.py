"""The Space installs `gradio==<sdk_version>` next to requirements.txt, so the two must agree or the build fails."""
import re
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]


def test_space_sdk_version_satisfies_the_gradio_requirement():
    readme = (ROOT / "energy_voice_space" / "README.md").read_text()
    sdk_version = re.search(r"^sdk_version:\s*(\S+)\s*$", readme, re.M).group(1)
    lines = (ROOT / "requirements" / "energy-voice-demo.txt").read_text().splitlines()
    gradio = next(Requirement(line) for line in lines if re.match(r"gradio\b", line.strip()))
    assert Version(sdk_version) in gradio.specifier, f"sdk_version {sdk_version} is outside {gradio.specifier}"
