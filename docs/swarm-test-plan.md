# Swarm test plan for Karen

Goal: replace solo beta testing with ~50 realistic simulated conversations, count and group the
failures, fix them in **separate PRs, safety first** (changed 2026-11-09), and rerun the same conversations to prove
the improvement. Written 2026-11-07.

## Decisions taken

- **PR #86** (invoice questions as typed tasks) is merged **before** the swarm runs, so the swarm
  tests the newest Karen and the fix PRs do not conflict with it.
- **Test users** are played by Claude subagents (one persona each).
- **Codex** is not a second swarm. It is (a) the judge's second opinion, being a different model
  family than Claude and Gemma, and (b) the independent reviewer of the fix PRs.
- **Start small:** a pilot of 2 personas x 2-3 conversations, check `/usage`, then scale up.

## Before starting (checklist)

1. Local repo `~/repos/karen-energy-agent` was 99 commits behind `origin/main` and has an
   uncommitted change in `energyagent-karen-prototype/EnergyMarketDashboard.tsx`. Keep that change
   (WIP commit on a branch, or a stash with a unique tag), then update `main`.
2. Confirm PR #86 is merged.
3. Run Karen **locally**, never against the live Space
   (`https://pascal-maker-belgian-energy-voice-assistant.hf.space`): the live API allows 20
   questions per minute per IP, uses the Gemini quota, and writes to the real feedback dataset and
   Langfuse.
4. Local `.env` with `GEMINI_API_KEY` (ask the user). Leave `HF_TOKEN`, `KAREN_FEEDBACK_DATASET`
   and Langfuse keys unset so test traffic is never stored as real feedback.
5. Check `GET /api/health`, then send a few questions by hand.

## Phase 1: personas and scenarios

About 10 Belgian households, each with 4-6 conversations of 3-8 turns, including follow-ups
(`history` holds at most 6 turns):

| Persona | Tests |
|---|---|
| Elderly person, not good with technology | vague questions, simple language |
| Student renting a room | moving house, contract in their own name |
| Solar panel owner, digital meter | capacity tariff, injection |
| Heat pump / EV owner | fixed vs variable contract, prices |
| Angry customer with a high bill | invoice upload (`tests/fixtures/invoices`, fictional) |
| Budget meter / social tariff | sensitive situation, correct referrals |
| Brussels or Walloon resident, French | language, BruSim / CompaCWaPE |
| Someone comparing offers | V-test comparison flow |
| Someone giving personal data | kept out of answers and logs? |
| Tester trying to break it | prompt injection, off-topic, invented suppliers |

The user reviews the persona list in ~5 minutes before the run.

## Phase 2: the swarm

- Up to ~8 agents in parallel, one persona each, against the local API
  (`/api/answer` or `/api/answer/stream`, `/api/upload`).
- Stay under ~15 questions per minute in total (rate limit 20/min per IP; Gemini quota).
- Save every turn as JSONL: persona, conversation id, question, answer, route, sources,
  latency, HTTP status and errors. Output goes under `experiments/karen_swarm/output/`.

## Phase 3: grading (see the evals approach)

- **Code checks:** HTTP errors and timeouts, empty answers, wrong language, answer too long,
  sources present, prices consistent with `market.json` and `market/tariffs-phase2.json`.
- **Judge**, binary questions with the evidence quoted: invented claim, question not answered,
  wrong route, personal data repeated or leaked. The judge is **not Gemma** (self-preference);
  Codex gives a second opinion.
- **Baseline numbers:** the existing expert benchmark (`experts/`, Jev judge) and
  `scripts/eval_invoice_understanding.py`.

## Phase 4: error analysis and calibration

- Group the failures into types with counts (for example "invented an amount: 9x").
- The user labels ~30 sample conversations as good or bad (~20 minutes). Report the judge's
  agreement with the user (kappa, or caught vs missed bad answers).
- Pick the top 5-8 failure types together.

## Phase 5: separate PRs, safety first

Changed 2026-11-09 on Codex's advice (was: one big PR). Each PR fixes a mechanism, not a list of
example sentences. Details and the chosen conversations: `experiments/karen_swarm/REPORT-full-5175ecf.md`.

1. **Safety:** social tariff, disconnection and emergency numbers. Region, network operator and
   authority are settled before Karen answers. No number from another operator. The IGO,
   increased-reimbursement and winter-protection rules are corrected.
2. **Question router:** first decide what is asked (right, procedure, price, calculation, invoice
   field). A supplier name does not trigger a tariff card. "Geen gas" excludes gas.
3. **Code calculates:** usage x tariff + fixed fee is computed in code. Karen lists what is missing
   for a full bill.
4. The rest: language, invoice follow-ups, unknown entities, error messages.

- Each PR branches from the updated `main`.
- The audit set (`full-5175ecf`, 54 conversations) stays fixed as the regression set. The five
  most serious conversations are replayed in full as PR 1's acceptance criteria, including
  follow-ups and recovery after a correction.
- After the fixes, a new unseen set of conversations is run, so we don't only measure how well
  Karen learned the known examples.
- Each fix gets a regression test built from the real swarm conversation that went wrong.
- Rerun the same scenarios. The PR description shows before/after per failure type, and lists
  the conversations that got **worse**, not just the ones that got better.
- Codex reviews the PR before the user merges it.

## Phase 6: repeatable

- The swarm becomes a script in `experiments/karen_swarm/` that can be rerun before each release.
