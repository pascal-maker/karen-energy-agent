"""Jev as a judge: does Karen answer expert questions as well as the experts do?

Each case in expert_benchmark.json is a question with an expert's published answer.
Karen answers the question, then Jev compares the two answers. The comparison runs
twice with the answers swapped, so the order they are shown in does not decide the
verdict. Jev also checks whether Karen answers right away and whether she contradicts
the expert. Runs only on the Space, where TYPESAFE_API_KEY is set; the owner starts a
run from /admin/benchmark.
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .jev import DEFAULT_BASE_URL, DEFAULT_MODEL

CASES_PATH = Path(__file__).with_name("expert_benchmark.json")
# Sources kept for the benchmark only (from the expert registry's holdout.json): Karen may not read them during a run.
HOLDOUT_PATH = Path(__file__).with_name("benchmark_holdout.json")
REQUIRED_FIELDS = ("id", "expert_id", "question", "expert_answer", "source_url")
JUDGE_TIMEOUT_SECONDS = 30.0
_BRUSSELS = ZoneInfo("Europe/Brussels")

COMPARE_INSTRUCTIONS = (
    "The state is a question from a Belgian household about energy and two answers to it, A and B. "
    "Which answer is better for that household: more correct, more complete on what matters, "
    "answering the actual question, and clear?"
)
COMPARE = {
    "a_better": "Answer A is clearly better.",
    "equal": "Both answers are about equally good.",
    "b_better": "Answer B is clearly better.",
}
DIRECT_INSTRUCTIONS = (
    "The state is a question and Karen's answer. Does the first sentence of Karen's answer already answer "
    "the question that was asked (for a yes/no question: yes or no; otherwise the main point)?"
)
CONTRADICT_INSTRUCTIONS = (
    "The state is a question, an expert's answer and Karen's answer. Does Karen's answer state a fact that "
    "contradicts the expert's answer? Leaving something out is not a contradiction."
)
YES_NO = {"yes": "Yes.", "no": "No."}


def load_cases() -> list[dict[str, Any]]:
    """One entry per question, each with its own source: id, expert_id, question, expert_answer,
    quote (shown on the page only), source_url and published_at."""
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    for case in cases:
        missing = [field for field in REQUIRED_FIELDS if not str(case.get(field, "")).strip()]
        if missing:
            raise ValueError(f"Benchmarkvraag {case.get('id', '?')} mist {', '.join(missing)}.")
    return cases


def blocked_sources(cases: list[dict[str, Any]]) -> tuple[str, ...]:
    """Every case's own source plus the registry's benchmark-only sources."""
    urls = {case["source_url"] for case in cases}
    if HOLDOUT_PATH.is_file():
        urls |= set(json.loads(HOLDOUT_PATH.read_text(encoding="utf-8")).get("benchmark_only_urls") or [])
    # Without the trailing slash, so ".../artikel" also blocks ".../artikel/".
    return tuple(sorted({url.rstrip("/") for url in urls if url}))


def judge_available() -> bool:
    return bool(os.getenv("TYPESAFE_API_KEY", "").strip())


def _post(body: dict[str, Any]) -> dict[str, Any]:
    base_url = os.getenv("TYPESAFE_BASE_URL", "").strip() or DEFAULT_BASE_URL
    request = Request(
        f"{base_url.rstrip('/')}/v1/systemone",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {os.getenv('TYPESAFE_API_KEY', '').strip()}",
            "Content-Type": "application/json",
            "User-Agent": "karen-energy-agent",
        },
        method="POST",
    )
    with urlopen(request, timeout=JUDGE_TIMEOUT_SECONDS) as response:
        return json.load(response)


def _probabilities(answer: Any, options: dict[str, str]) -> dict[str, float] | None:
    if not isinstance(answer, dict):
        return None
    raw = answer.get("probabilities")
    items: list[tuple[Any, Any]] = []
    if isinstance(raw, dict):
        items = list(raw.items())
    elif isinstance(raw, list):
        items = [(item.get("option") or item.get("choice"), item.get("probability")) for item in raw if isinstance(item, dict)]
    probabilities = {str(key): float(value) for key, value in items if key in options and isinstance(value, (int, float))}
    if not probabilities and answer.get("choice") in options and isinstance(answer.get("confidence"), (int, float)):
        probabilities = {answer["choice"]: float(answer["confidence"])}
    return probabilities or None


async def _ask(state: str, questions: dict[str, tuple[str, dict[str, str]]]) -> dict[str, dict[str, float] | None]:
    body = {
        "model": os.getenv("TYPESAFE_DEFAULT_MODEL", "").strip() or DEFAULT_MODEL,
        "state": state,
        "questions": {
            name: {"type": "choice", "instructions": instructions, "criteria": options}
            for name, (instructions, options) in questions.items()
        },
    }
    result = await asyncio.to_thread(_post, body)
    answers = result.get("answers") or {} if isinstance(result, dict) else {}
    return {name: _probabilities(answers.get(name), options) for name, (_, options) in questions.items()}


MAX_ANSWER_CHARS = 3000


def _pair(question: str, first: str, second: str) -> str:
    return f"Question: {question}\n\nAnswer A:\n{first[:MAX_ANSWER_CHARS]}\n\nAnswer B:\n{second[:MAX_ANSWER_CHARS]}"


async def judge(question: str, expert_answer: str, karen_answer: str) -> dict[str, Any]:
    """Jev's verdict on Karen against the expert, averaged over both orders."""
    karen_first, expert_first, checks = await asyncio.gather(
        _ask(_pair(question, karen_answer, expert_answer), {"compare": (COMPARE_INSTRUCTIONS, COMPARE)}),
        _ask(_pair(question, expert_answer, karen_answer), {"compare": (COMPARE_INSTRUCTIONS, COMPARE)}),
        _ask(
            f"Question: {question}\n\nExpert's answer:\n{expert_answer[:MAX_ANSWER_CHARS]}\n\nKaren's answer:\n{karen_answer[:MAX_ANSWER_CHARS]}",
            {"direct": (DIRECT_INSTRUCTIONS, YES_NO), "contradicts": (CONTRADICT_INSTRUCTIONS, YES_NO)},
        ),
    )
    orders = []
    if karen_first["compare"]:
        p = karen_first["compare"]
        orders.append({"karen": p.get("a_better", 0.0), "equal": p.get("equal", 0.0), "expert": p.get("b_better", 0.0)})
    if expert_first["compare"]:
        p = expert_first["compare"]
        orders.append({"karen": p.get("b_better", 0.0), "equal": p.get("equal", 0.0), "expert": p.get("a_better", 0.0)})
    if not orders:
        raise RuntimeError("Jev gaf geen oordeel.")
    scores = {key: round(sum(order[key] for order in orders) / len(orders), 3) for key in ("karen", "equal", "expert")}
    return {
        **scores,
        "verdict": max(scores, key=scores.get),
        "orders_agree": len(orders) == 2 and max(orders[0], key=orders[0].get) == max(orders[1], key=orders[1].get),
        "direct": round((checks["direct"] or {}).get("yes", 0.0), 3) if checks["direct"] else None,
        "contradicts": round((checks["contradicts"] or {}).get("yes", 0.0), 3) if checks["contradicts"] else None,
    }


AnswerFn = Callable[[str], Awaitable[dict[str, Any]]]


async def run_benchmark(answer: AnswerFn, cases: list[dict[str, Any]], progress: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    """Ask Karen every case, let Jev judge each answer; `progress` gets the run after every case."""
    run: dict[str, Any] = {
        "id": uuid.uuid4().hex[:12],
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "running",
        "total": len(cases),
        "results": [],
    }
    progress(run)
    for case in cases:
        started = time.monotonic()
        row: dict[str, Any] = {key: case.get(key, "") for key in ("id", "expert_id", "source_url", "question", "expert_answer", "quote")}
        try:
            reply = await answer(case["question"])
            row.update(
                karen_answer=str(reply.get("answer", "")),
                processing=str(reply.get("processing", "")),
                sources=[source.get("url", "") for source in reply.get("sources") or [] if isinstance(source, dict)],
                seconds=round(time.monotonic() - started, 1),
            )
            row["judge"] = await judge(case["question"], case["expert_answer"], row["karen_answer"])
        except Exception as error:  # one failing case should not stop the run
            row["error"] = str(error)[:300] or type(error).__name__
        run["results"].append(row)
        progress(run)
    run["status"] = "done"
    run["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    run["summary"] = summarize(run["results"])
    progress(run)
    return run


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    judged = [row["judge"] for row in results if row.get("judge")]
    count = {key: sum(1 for item in judged if item["verdict"] == key) for key in ("karen", "equal", "expert")}
    return {
        "judged": len(judged),
        "errors": len(results) - len(judged),
        **count,
        "karen_avg": round(sum(item["karen"] for item in judged) / len(judged), 3) if judged else None,
        "direct": sum(1 for item in judged if (item.get("direct") or 0) >= 0.5),
        "contradicts": sum(1 for item in judged if (item.get("contradicts") or 0) >= 0.5),
    }


def save_run(run: dict[str, Any]) -> None:
    """Keep a finished run in the private dataset next to the conversations, when one is set."""
    token = os.getenv("HF_TOKEN", "").strip()
    dataset = os.getenv("KAREN_FEEDBACK_DATASET", "").strip()
    if not token or not dataset:
        return
    from huggingface_hub import HfApi

    HfApi(token=token).upload_file(
        path_or_fileobj=json.dumps(run, ensure_ascii=False, indent=1).encode("utf-8"),
        path_in_repo=f"benchmarks/{run['started_at'][:10]}/{run['id']}.json",
        repo_id=dataset,
        repo_type="dataset",
        commit_message=f"Karen expert benchmark {run['id']}",
    )


_VERDICTS = {"karen": "Karen beter", "equal": "Even goed", "expert": "Expert beter"}


def _pct(value: Any) -> str:
    return f"{round(value * 100)}%" if isinstance(value, (int, float)) else "–"


def render_benchmark_page(run: dict[str, Any] | None, available: bool, case_count: int) -> str:
    e = html.escape
    if not available:
        intro = "<p class='warn'>Jev is niet ingesteld: zet TYPESAFE_API_KEY als Space-secret.</p>"
    elif run and run.get("status") == "running":
        intro = f"<p>Bezig: {len(run['results'])} van {run['total']} vragen. Ververs de pagina over een minuut.</p>"
    else:
        intro = (
            f"<form method='post'><button>Start benchmark ({case_count} vragen)</button></form>"
            "<p class='muted'>Karen beantwoordt elke vraag opnieuw; Jev vergelijkt haar antwoord met dat van de expert, "
            "twee keer met A en B omgewisseld. Duurt een paar minuten.</p>"
        )
    body = ""
    if run and run.get("results"):
        summary = run.get("summary") or summarize(run["results"])
        tiles = [
            ("Karen beter", summary["karen"]), ("Even goed", summary["equal"]), ("Expert beter", summary["expert"]),
            ("Antwoordt meteen", summary["direct"]), ("Spreekt expert tegen", summary["contradicts"]),
        ]
        rows = []
        for row in run["results"]:
            verdict = row.get("judge") or {}
            label = _VERDICTS.get(verdict.get("verdict", ""), "Fout")
            flag = {"karen": "win", "expert": "loss"}.get(verdict.get("verdict", ""), "")
            detail = (
                f"Karen {_pct(verdict.get('karen'))} · gelijk {_pct(verdict.get('equal'))} · expert {_pct(verdict.get('expert'))}"
                + ("" if verdict.get("orders_agree", True) else " · <span class='warn'>volgorde maakt verschil</span>")
                if verdict else e(row.get("error", ""))
            )
            rows.append(
                f"<tr class='{flag}'><td>{e(row['question'])}</td><td class='nowrap'><b>{label}</b><div class='muted'>{detail}</div></td>"
                f"<td class='nowrap'>{_pct(verdict.get('direct'))}</td><td class='nowrap'>{_pct(verdict.get('contradicts'))}</td>"
                f"<td><details><summary>Karen ({e(row.get('processing', ''))})</summary><p>{e(row.get('karen_answer', ''))}</p></details>"
                f"<details><summary>Expert</summary><p>{e(row.get('expert_answer', ''))}</p>"
                + (f"<blockquote>{e(row['quote'])}</blockquote>" if row.get("quote") else "")
                + (f"<a href='{e(row['source_url'])}' target='_blank' rel='noopener'>bron</a>" if row.get("source_url") else "")
                + "</details></td></tr>"
            )
        started = datetime.fromisoformat(run["started_at"]).astimezone(_BRUSSELS).strftime("%d/%m %H:%M")
        body = (
            f"<p class='muted'>Run van {started} · gemiddelde kans dat Karen beter is: {_pct(summary['karen_avg'])}</p>"
            "<div class='tiles'>" + "".join(f"<div class='tile'><b>{value}</b><span>{name}</span></div>" for name, value in tiles) + "</div>"
            "<div class='wrap'><table><thead><tr><th>Vraag</th><th>Jev-oordeel</th><th>Meteen antwoord</th><th>Tegenspraak</th><th>Antwoorden</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></div>"
        )
    return f"""<!doctype html><html lang="nl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Karen · benchmark</title><style>
body{{font:14px/1.45 Inter,system-ui,sans-serif;margin:0;padding:16px;color:#1d2a22;background:#f6f8f5}}
h1{{font-size:20px;margin:0 0 4px}} .muted{{color:#6b776f;font-size:12px}} .warn{{color:#a24b1d}} a{{color:#234f38}}
button{{padding:10px 16px;border:0;border-radius:10px;color:#fff;background:#234f38;font:inherit;cursor:pointer}}
.tiles{{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}} .tile{{background:#fff;border:1px solid #dde4dc;border-radius:10px;padding:8px 12px;min-width:92px}}
.tile b{{display:block;font-size:20px}} .tile span{{color:#6b776f;font-size:12px}}
.wrap{{overflow-x:auto;background:#fff;border:1px solid #dde4dc;border-radius:10px}}
table{{border-collapse:collapse;width:100%}} th,td{{text-align:left;vertical-align:top;padding:8px 10px;border-bottom:1px solid #eef1ed}}
th{{font-size:12px;color:#6b776f;font-weight:600;background:#fafbf9}} tr.win td{{background:#f1f8f1}} tr.loss td{{background:#fff7f0}}
.nowrap{{white-space:nowrap}} summary{{cursor:pointer;color:#234f38}} details p{{white-space:pre-wrap;margin:6px 0}}
</style></head><body>
<h1>Karen tegen de experts</h1><p class="muted"><a href="/admin">← gesprekken</a> · Jev beoordeelt elk antwoord naast dat van een energie-expert</p>
{intro}{body}</body></html>"""
