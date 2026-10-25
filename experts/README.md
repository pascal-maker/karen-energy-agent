# Expertregister voor Karens benchmark

Vlaamse energie-experts en hun publieke bronnen, gebruikt om Karen eerlijk te testen.
Komt uit agentscomparison (branch `expert-registry`, 8cec466) en wordt vanaf nu hier bijgehouden.

- `config/experts.yaml`: experts en bronnen met `approval_status`. Alleen een mens zet een bron op `approved`.
- `data/benchmark/holdout.json`: `benchmark_only_urls` (Karen mag die nooit als kennis krijgen) en `karen_search_domains` (gewone domeinen).
- `scripts/expert_benchmark.py`: controleert vraag-antwoordparen (lijst of `{"cases": [...]}`) tegen het register en schrijft `holdout.json`.

Draaien vanuit deze map:

    pip install -r requirements.txt
    PYTHONPATH=scripts python scripts/expert_benchmark.py --benchmark data/benchmark/expert_cases.json
    python -m pytest -q tests

Karen gebruikt `../luminus_harness/benchmark_holdout.json` (kopie van `benchmark_only_urls`; een test bewaakt dat ze gelijk blijven).
`scope.ingestion_approved` en alle `enabled_for_ingestion` blijven `false`. Deze map gaat niet mee naar de Space.
