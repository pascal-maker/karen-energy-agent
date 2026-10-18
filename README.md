# EnergyAgent Karen

A Dutch voice and chat assistant for Belgian household energy questions
(Luminus, Eneco, Elegant and others). Karen answers from a bundled energy FAQ PDF,
curated official supplier and regulator pages, and optional visitor PDF uploads,
and can run regional offer comparisons through the official comparators (V-test, BruSim, CompaCWaPE).

This repository was split out of
[`pascal-maker/agentscomparison`](https://github.com/pascal-maker/agentscomparison)
with its git history preserved.

## Layout

| Path | What it is |
| --- | --- |
| `energy_voice_app.py` | Gradio + FastAPI backend (`/api/answer`, PDF upload, comparison sessions) |
| `luminus_harness/` | Retrieval, Gemma answers, invoice analysis, offer comparison, rate limiting, TTS |
| `energyagent-karen-prototype/` | Framer/React chat component that calls the backend |
| `energy_voice_space/` | Hugging Face Space README and configuration |
| `scripts/` | Space deploy/upload scripts and a live deployment smoke check |
| `experiments/karen_search_ab/`, `experiments/karen_vane/` | Search provider A/B tests and claim-verification research |
| `docs/` | Evaluation write-ups |
| `tests/` | Test suite |

## Running the tests

```bash
pip install -r requirements/energy-voice-demo.txt pytest pydantic httpx
PYTHONPATH=. pytest -q tests
```

**Known gap:** `energy_voice_app.py` imports `luminus_harness/perplexity_agent.py`,
which has not been committed yet. Until it is added, the app does not start and
the tests that import it fail.

## Deployment

The backend runs as a Hugging Face Space; see `energy_voice_space/README.md` for
the required secrets and `scripts/deploy_energy_voice_space.sh` to deploy. The
website embeds the chat component and points it at the Space's API.
