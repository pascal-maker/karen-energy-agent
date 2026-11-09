# Swarm-test Karen — run `full-5175ecf`

Datum: 2026-11-08 · Commit getest: `5175ecf` (main, incl. PR #86 en #87) · Plan: `docs/swarm-test-plan.md`

## Samenvatting

- 11 persona's (Claude-subagents), **54 gesprekken, 272 beurten** tegen een **lokale** Karen. 269 kregen een
  antwoord. 3 gaven een HTTP 503, en een nieuwe poging lukte telkens.
- **Wat goed zit:**
  - Bedragen uit facturen kloppen.
  - De prijzen van de tariefkaarten kloppen met `market/tariffs-phase2.json` en blijven consistent.
  - Er lekt geen systeemprompt.
  - Karen herhaalt nooit persoonsgegevens en doet nooit alsof ze iets uitvoert.
  - Valse premissen worden vaak gecorrigeerd: "sociaal tarief afgeschaft" en "Fluvius in Brussel".
- **Wat fout zit:** de fouten gaan vooral over **de gestelde vraag niet beantwoorden**:
  - vaste weigeringen;
  - herhaalde prijslijsten;
  - verkeerde routes;
  - de **regio van de gebruiker negeren**: Vlaamse bronnen voor Franstaligen, Waalse of Brusselse info voor Vlamingen;
  - **Nederlands in Franse en Engelse gesprekken**.
- Verzonnen feiten komen minder vaak voor, maar ze zijn ernstig:
  - een "Vlaamse premie 2027 = 1.500 euro";
  - een niet-bestaande "Zonnebonus 2026" die als echt wordt behandeld;
  - een Brussels storingsnummer voor een Waalse gebruiker;
  - een "afbetalingsplan bij de Vlaamse Belastingdienst".

## Opzet

| | |
|---|---|
| Karen | lokaal `energy_voice_app.py` op `127.0.0.1:7860`, model `gemma-4-26b-a4b-it`, Browserbase aan |
| Bewust uit | `HF_TOKEN`, `KAREN_FEEDBACK_DATASET`, Langfuse: geen testverkeer in de echte feedback of tracing |
| Live Space | niet geraakt: `ask.py` weigert elk adres dat niet localhost is |
| Snelheid | ten hoogste 8 agents tegelijk, gedeelde limiet van 15 vragen per minuut (lockfile) |
| Facturen | fictieve fixtures uit `tests/fixtures/invoices/` via de nieuwe optie `ask.py --upload` |
| Persoonsgegevens | alleen fictieve waarden (`BE00 0000…`, `@example.invalid`) |
| Kosten | proefrondes plus swarm: ongeveer 54–67k tokens per persona-agent |

**Eerdere runs, niet als basismeting gebruikt:**
- `output/pilot-70e2ce1-stale/` draaide op de oude main, zonder PR #86.
- `output/pilot-5175ecf-no-browserbase/` draaide zonder Browserbase. Daarin gaf Karen 7 vaste weigeringen tegenover 4 met Browserbase. Browserbase hoort er dus bij.

## Cijfers uit de controles in code (`checks.py`, exact)

| Controle | Aantal (op 272) | Opmerking |
|---|---|---|
| `no_sources` | 78 | vaak bij weigeringen, small talk en vervolgvragen op een factuur |
| `slow_over_30s` | 35 | mediaan 9,5 s, max 67,9 s; route `hosted-public-knowledge` (Browserbase) onder 8× parallelle last |
| `stock_refusal` ("Dat weet ik niet zeker…" / "Je n'en suis pas sûre…") | 32 | de meeste bij breaker (9) en personal-data (6) |
| `flemish_source_for_fr_region` | 14 | wallonia-fr 8, brussels-fr 6 (het bijna-dubbel in angry-bill-4, een Brusselse factuur, staat er niet in) |
| `wrong_language_nl_for_fr` | 11 | brussels-fr 7, angry-bill-5 (Franse factuur) 3, wallonia-fr 1 |
| `dutch_words_in_french` | 5 | "netbeheerder", "zeker", "bronnen" in Franse zinnen |
| `http_error` / `empty_answer` | 3 / 3 | 2× "Google heeft GEMINI_API_KEY afgewezen", 1× "Gemma kon nu geen verbinding maken" |

**Routes:**

| Route | Aantal |
|---|---|
| `hosted-public-knowledge` | 152 |
| `general-knowledge` | 31 |
| `local-only` (factuur) | 26 |
| `supplier-tariff-card` | 19 |
| `offer-comparison` | 12 |
| `contract-advice` | 8 |
| `market-data` | 7 |
| `small-talk` | 5 |
| `contract-check` | 4 |
| `usage-yardstick` | 3 |
| `verified-public-sources` | 2 |

**Extra heuristieken (gecontroleerd met regex, met voorbeelden):**

| Heuristiek | Aantal | Voorbeelden |
|---|---|---|
| Hetzelfde antwoord opnieuw gegeven in hetzelfde gesprek | 22 | brussels-fr-2/3, wallonia-fr-2/5, angry-bill-3/6, solar-2/2 |
| Een leverancier genoemd die de gebruiker niet noemde (Elegant, aineijk
