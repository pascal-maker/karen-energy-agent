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


def test_mounting_the_ui_starts_no_node_server_on_spaces():
    # Spaces set GRADIO_SSR_MODE=true; with SSR on, Gradio 6 starts a Node server on port 7860 while mounting,
    # and Uvicorn then cannot bind it (the Space exited with "address already in use").
    import os
    import subprocess
    import sys

    env = {**os.environ, "GRADIO_SSR_MODE": "true", "PYTHONPATH": str(ROOT)}
    out = subprocess.run(
        [sys.executable, "-c", "import energy_voice_app as m; print(m.gradio_demo.ssr_mode, getattr(m.gradio_demo, \"node_process\", None))"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=180,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().splitlines()[-1] == "False None"
