"""The owner's overview of what people ask Karen and what she answered."""

from __future__ import annotations

import csv
import html
import io
from collections import Counter
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

_BRUSSELS = ZoneInfo("Europe/Brussels")
_PATHS = {
    "general-knowledge": "Algemene uitleg",
    "hosted-public-knowledge": "Met bron",
    "verified-public-sources": "Met bron",
    "small-talk": "Small talk",
    "offer-comparison": "Vergelijken",
    "market-data": "Marktprijzen",
    "contract-advice": "Contractadvies",
}
_ROUTES = {
    "price": "Prijs",
    "compare": "Vergelijken",
    "contract_advice": "Contractadvies",
    "energy_other": "Energievraag",
    "small_talk": "Small talk",
    "off_topic": "Buiten energie",
}


def _local_time(stamp: str) -> str:
    try:
        return datetime.fromisoformat(stamp).astimezone(_BRUSSELS).strftime("%d/%m %H:%M")
    except ValueError:
        return stamp


def _no_source(record: dict[str, Any]) -> bool:
    return record.get("answer", "").startswith(("Dat weet ik niet zeker", "I'm not sure", "Je ne sais pas"))


def _path(record: dict[str, Any]) -> str:
    if _no_source(record):
        return "Geen antwoord"
    processing = record.get("processing") or "?"
    if processing == "hosted-public-knowledge" and not record.get("sources"):
        return "Zonder bron"
    return _PATHS.get(processing, processing)


def _ratings(feedback: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_log: dict[str, dict[str, Any]] = {}
    for item in feedback:
        if item.get("log_id") and item["log_id"] not in by_log:
            by_log[item["log_id"]] = item
    return by_log


def render_admin_page(conversations: list[dict[str, Any]], feedback: list[dict[str, Any]], days: int) -> str:
    ratings = _ratings(feedback)
    paths = Counter(_path(record) for record in conversations)
    no_source = sum(1 for record in conversations if _no_source(record))
    up = sum(1 for item in feedback if item.get("rating") == "up")
    down = sum(1 for item in feedback if item.get("rating") == "down")
    e = html.escape
    tiles = [
        ("Vragen", str(len(conversations))),
        ("Geen antwoord", str(no_source)),
        ("👍 / 👎", f"{up} / {down}"),
    ] + [(name, str(count)) for name, count in paths.most_common(4)]
    rows = []
    for record in conversations:
        rated = ratings.get(record.get("id", ""))
        rating = {"up": "👍", "down": "👎"}.get(rated.get("rating", "") if rated else "", "")
        comment = f"<div class='muted'>{e(rated['comment'])}</div>" if rated and rated.get("comment") else ""
        route = _ROUTES.get(record.get("route") or "", "")
        confidence = record.get("route_confidence")
        if route and isinstance(confidence, (int, float)):
            route = f"{route} {round(confidence * 100)}%"
        sources = "".join(
            f"<a href='{e(source['url'])}' target='_blank' rel='noopener'>{e(source.get('title') or source['url'])}</a>"
            for source in record.get("sources", [])
        )
        errors = "".join(f"<div class='warn'>{e(message)}</div>" for message in record.get("search_errors", []))
        flag = " class='miss'" if _no_source(record) else ""
        rows.append(
            f"<tr{flag}><td class='nowrap'>{e(_local_time(record.get('created_at', '')))}</td>"
            f"<td>{e(record.get('question', ''))}</td>"
            f"<td class='nowrap'>{e(route)}</td>"
            f"<td class='nowrap'>{e(_path(record))}</td>"
            f"<td><details><summary>{e(record.get('answer', '')[:140])}</summary><p>{e(record.get('answer', ''))}</p>"
            f"<div class='sources'>{sources}</div>{errors}</details></td>"
            f"<td class='nowrap'>{e(str(record.get('seconds', '')))} s</td>"
            f"<td>{rating}{comment}</td></tr>"
        )
    tile_html = "".join(f"<div class='tile'><b>{e(value)}</b><span>{e(name)}</span></div>" for name, value in tiles)
    empty = "<p class='muted'>Nog geen vragen in deze periode.</p>" if not rows else ""
    return f"""<!doctype html><html lang="nl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Karen · gesprekken</title><style>
body{{font:14px/1.45 Inter,system-ui,sans-serif;margin:0;padding:16px;color:#1d2a22;background:#f6f8f5}}
h1{{font-size:20px;margin:0 0 4px}} .muted{{color:#6b776f;font-size:12px}} .warn{{color:#a24b1d;font-size:12px}}
.bar{{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:12px}} .bar a{{color:#234f38}}
.tiles{{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}} .tile{{background:#fff;border:1px solid #dde4dc;border-radius:10px;padding:8px 12px;min-width:92px}}
.tile b{{display:block;font-size:20px}} .tile span{{color:#6b776f;font-size:12px}}
.wrap{{overflow-x:auto;background:#fff;border:1px solid #dde4dc;border-radius:10px}}
table{{border-collapse:collapse;width:100%}} th,td{{text-align:left;vertical-align:top;padding:8px 10px;border-bottom:1px solid #eef1ed}}
th{{font-size:12px;color:#6b776f;font-weight:600;background:#fafbf9;position:sticky;top:0}} tr.miss td{{background:#fff7f0}}
.nowrap{{white-space:nowrap}} summary{{cursor:pointer}} details p{{white-space:pre-wrap;margin:6px 0}}
.sources a{{display:inline-block;margin:2px 6px 0 0;font-size:12px;color:#234f38}}
</style></head><body>
<h1>Wat mensen Karen vragen</h1>
<div class="bar"><span class="muted">Laatste {days} dagen · tijden in Brussel · oranje = geen antwoord</span>
<a href="?days=1">vandaag</a><a href="?days=7">7 dagen</a><a href="?days=30">30 dagen</a><a href="/admin/conversations.csv?days={days}">CSV</a></div>
<div class="tiles">{tile_html}</div>{empty}
<div class="wrap"><table><thead><tr><th>Tijd</th><th>Vraag</th><th>Jev</th><th>Pad</th><th>Antwoord</th><th>Duur</th><th>Oordeel</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div></body></html>"""


def conversations_csv(conversations: list[dict[str, Any]], feedback: list[dict[str, Any]]) -> str:
    ratings = _ratings(feedback)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["tijd", "vraag", "jev_route", "jev_zekerheid", "pad", "antwoord", "bronnen", "seconden", "oordeel", "opmerking"])
    for record in conversations:
        rated = ratings.get(record.get("id", ""), {})
        writer.writerow([
            record.get("created_at", ""), record.get("question", ""), record.get("route") or "",
            record.get("route_confidence") if record.get("route_confidence") is not None else "",
            record.get("processing", ""), record.get("answer", ""),
            " ".join(source.get("url", "") for source in record.get("sources", [])),
            record.get("seconds", ""), rated.get("rating", ""), rated.get("comment", ""),
        ])
    return out.getvalue()
