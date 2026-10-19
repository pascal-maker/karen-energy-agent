"""Answer plain price questions from the published market.json.

The daily `market-daily` workflow already publishes today's Belgian day-ahead
prices and verified supplier tariff cards for the Prijzen tab. A question like
"wat zijn de stroomprijzen vandaag?" is answered from that file directly, so
Karen shows real numbers instead of starting the offer-comparison form. Only
values present in the file are shown; nothing is estimated or filled in.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from datetime import datetime
from typing import Any
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

DEFAULT_MARKET_DATA_URL = "https://raw.githubusercontent.com/pascal-maker/karen-energy-agent/market-data/market.json"
CACHE_SECONDS = 10 * 60
MAX_SUPPLIERS = 4
BRUSSELS = ZoneInfo("Europe/Brussels")

_PRICE = re.compile(
    r"\b\w*(?:prijs|prijzen|tarief|tarieven)\b|\b(?:kost|kosten|goedkoop\w*|price|prices|pricing|cost|costs|cheap\w*)\b",
    re.IGNORECASE,
)
_ELECTRICITY = re.compile(r"(?:stroom|elektriciteit|electricit|energie|energy|power|kwh)", re.IGNORECASE)
# Gas, network costs, social tariffs, invoices, contracts and explicit CREG questions keep their own routes.
_OTHER_SUBJECT = re.compile(
    r"\b\w*(?:gas|capaciteit|nettarief|netkost|distributie|sociaal|social|injectie|prosument|"
    r"factuur|voorschot|afrekening|invoice|bill|contract|opzeg|zeggen|stopzet|verbreking|cancel|"
    r"warmtepomp|ketel|creg)\w*\b",
    re.IGNORECASE,
)
# An explicit comparison request, or household inputs, goes to the comparison flow.
_COMPARE = re.compile(r"\b(?:vergelijk\w*|compar(?:e|ing|ison)|v-test|brusim|compacwape)\b", re.IGNORECASE)
_HOUSEHOLD_INPUT = re.compile(r"\b(?!20\d{2}\b)[1-9]\d{3}\b|\bkwh\s*(?:per|/)\s*(?:jaar|year)\b|\b\d[\d. ]*\s*kwh\b", re.IGNORECASE)
_ENGLISH = re.compile(
    r"\b(?:the|what|what's|whats|is|are|look|up|can|you|today|price|prices|energy|electricity|how|much|current|please|show|me)\b",
    re.IGNORECASE,
)
_DUTCH = re.compile(
    r"\b(?:de|het|wat|zijn|is|zoek|op|kan|je|jij|vandaag|prijs|prijzen|stroom|energie|hoeveel|huidige|actuele|toon|mij|een)\b",
    re.IGNORECASE,
)

_cache: dict[str, Any] = {"url": None, "at": 0.0, "data": None}


def is_market_price_question(question: str) -> bool:
    """A general electricity-price question that market.json can answer."""
    text = question.strip()
    return bool(
        _PRICE.search(text)
        and _ELECTRICITY.search(text)
        and not _OTHER_SUBJECT.search(text)
        and not _COMPARE.search(text)
        and not _HOUSEHOLD_INPUT.search(text)
    )


def question_language(question: str) -> str:
    """'en' when the question reads as English, otherwise Karen's default 'nl'."""
    english = len(_ENGLISH.findall(question))
    dutch = len(_DUTCH.findall(question))
    return "en" if english > dutch else "nl"


def _download(url: str) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": "karen-energy-agent"})
    with urlopen(request, timeout=8) as response:
        return json.load(response)


async def load_market_data() -> dict[str, Any] | None:
    """market.json, cached for ten minutes; None when it can't be read."""
    url = os.getenv("MARKET_DATA_URL", "").strip() or DEFAULT_MARKET_DATA_URL
    now = time.monotonic()
    if _cache["data"] is not None and _cache["url"] == url and now - _cache["at"] < CACHE_SECONDS:
        return _cache["data"]
    try:
        data = await asyncio.to_thread(_download, url)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    _cache.update(url=url, at=now, data=data)
    return data


def _cents(value: float, lang: str) -> str:
    text = f"{value:.1f}"
    return text if lang == "en" else text.replace(".", ",")


def _join(items: list[str], lang: str) -> str:
    if len(items) < 2:
        return "".join(items)
    return ", ".join(items[:-1]) + (" and " if lang == "en" else " en ") + items[-1]


def _day_label(day: str, lang: str) -> str:
    today = datetime.now(BRUSSELS).date().isoformat()
    if day == today:
        return "today" if lang == "en" else "vandaag"
    return f"on {day}" if lang == "en" else f"op {day}"


def market_price_reply(data: dict[str, Any], lang: str = "nl") -> dict[str, Any] | None:
    """Karen's reply from market.json, or None when the file has no usable spot day."""
    spot = data.get("spot") or {}
    day = spot.get("today") or {}
    if spot.get("unit") != "EUR/MWh" or not all(isinstance(day.get(key), (int, float)) for key in ("avg", "min", "max")):
        return None
    # EUR/MWh divided by 10 is eurocent per kWh.
    avg, low, high = (_cents(day[key] / 10, lang) for key in ("avg", "min", "max"))
    when = _day_label(str(day.get("date", "")), lang)
    hours = [hour for hour in day.get("cheapest_hours") or [] if isinstance(hour, int)][:3]
    if lang == "en":
        lines = [
            f"The Belgian wholesale electricity price (day-ahead) {when} averages {avg} cents per kWh, "
            f"ranging from {low} to {high} cents."
            + (f" It is cheapest around {_join([f'{h}:00' for h in hours], lang)}." if hours else "")
            + " That is before network costs, levies and VAT."
        ]
    else:
        lines = [
            f"De groothandelsprijs voor stroom in België (day-ahead) is {when} gemiddeld {avg} cent per kWh, "
            f"tussen {low} en {high} cent."
            + (f" Het goedkoopst is het rond {_join([f'{h}u' for h in hours], lang)}." if hours else "")
            + " Dat is zonder netkosten, heffingen en btw."
        ]

    contracts = data.get("contracts") or {}
    suppliers = sorted(
        (
            supplier for supplier in contracts.get("suppliers") or []
            if supplier.get("status") == "verified" and isinstance(supplier.get("price_per_kwh_eur"), (int, float))
        ),
        key=lambda supplier: supplier["price_per_kwh_eur"],
    )[:MAX_SUPPLIERS]
    if suppliers:
        lines.append(
            "Variable contracts on the suppliers' current tariff cards (energy price incl. 6% VAT, without network costs):"
            if lang == "en" else
            "Variabele contracten op de huidige tariefkaarten van leveranciers (energieprijs incl. 6% btw, zonder netkosten):"
        )
        for supplier in suppliers:
            cents = _cents(supplier["price_per_kwh_eur"] * 100, lang)
            unit = "cents/kWh" if lang == "en" else "cent/kWh"
            name, product = supplier["name"], str(supplier.get("product_name") or "")
            label = product if product.casefold().startswith(name.casefold()) else f"{name} {product}".strip()
            lines.append(f"• {label}: {cents} {unit}")
    lines.append(
        "Want to know what this means for your home? Say \"compare offers\" and I'll ask for your region, postcode and usage."
        if lang == "en" else
        "Wil je weten wat dat voor jou betekent? Zeg \"vergelijk aanbiedingen\", dan vraag ik je gewest, postcode en verbruik."
    )

    market_source = next(iter(data.get("sources") or []), {})
    sources = [{
        "title": market_source.get("title", "Energy-Charts (Fraunhofer ISE) · day-ahead BE"),
        "detail": f"Day-ahead {day.get('date', '')} · CC BY 4.0",
        "url": market_source.get("url", "https://www.energy-charts.info/charts/price_spot_market/chart.htm?l=nl&c=BE"),
        "excerpt": f"Gemiddeld {day['avg']} EUR/MWh, min {day['min']}, max {day['max']}.",
    }]
    for supplier in suppliers:
        if supplier.get("source_url"):
            sources.append({
                "title": f"Tariefkaart {supplier['name']}",
                "detail": f"Opgehaald {supplier.get('retrieved_at', contracts.get('as_of', ''))}",
                "url": supplier["source_url"],
                "excerpt": str(supplier.get("price_per_kwh_note", ""))[:360],
            })
    return {
        "answer": "\n".join(lines),
        "sources": sources,
        "live_source_status": "checked",
        "processing": "market-data",
    }


async def answer_market_price_question(question: str, *, recognised: bool = False) -> dict[str, Any] | None:
    """None when this isn't a general price question or market.json is unavailable.

    `recognised` skips the word-list check when Jev already routed the question here.
    """
    if not recognised and not is_market_price_question(question):
        return None
    data = await load_market_data()
    if data is None:
        return None
    return market_price_reply(data, question_language(question))
