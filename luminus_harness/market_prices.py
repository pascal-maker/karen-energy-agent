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
MAX_FIXED = 2
BRUSSELS = ZoneInfo("Europe/Brussels")

_PRICE = re.compile(
    r"\b\w*(?:prijs|prijzen|tarief|tarieven)\b|\b(?:kost|kosten|goedkoop\w*|price|prices|pricing|cost|costs|cheap\w*|prix|tarifs?|co[uû]t\w*|cher|ch[eè]re)\b",
    re.IGNORECASE,
)
_ELECTRICITY = re.compile(r"(?:stroom|elektriciteit|electricit|électricit|energie|énergie|energy|power|kwh)", re.IGNORECASE)
# Gas, network costs, social tariffs, invoices, contracts and explicit CREG questions keep their own routes.
_OTHER_SUBJECT = re.compile(
    r"\b\w*(?:gas|capaciteit|nettarief|netkost|distributie|sociaal|social|injectie|prosument|"
    r"factuur|voorschot|afrekening|invoice|bill|contract|contrat|factur|acompte|r[ée]sili|opzeg|zeggen|stopzet|verbreking|cancel|"
    r"warmtepomp|ketel|creg)\w*\b",
    re.IGNORECASE,
)
# An explicit comparison request, or household inputs, goes to the comparison flow.
_COMPARE = re.compile(r"\b(?:vergelijk\w*|compar(?:e|ing|ison|er)|v-test|brusim|compacwape)\b", re.IGNORECASE)
_HOUSEHOLD_INPUT = re.compile(r"\b(?!20\d{2}\b)[1-9]\d{3}\b|\bkwh\s*(?:per|/)\s*(?:jaar|year)\b|\b\d[\d. ]*\s*kwh\b", re.IGNORECASE)
_ENGLISH = re.compile(
    r"\b(?:the|what|what's|whats|is|are|look|up|can|you|today|price|prices|energy|electricity|how|much|current|please|"
    r"show|me|i|should|take|or|than|better|my|it|do|does|at|for|and|cheaper|which|will|fixed|cents?|use)\b",
    re.IGNORECASE,
)
_DUTCH = re.compile(
    r"\b(?:de|het|wat|zijn|is|zoek|op|kan|je|jij|vandaag|prijs|prijzen|stroom|energie|hoeveel|huidige|actuele|toon|mij|een|"
    r"ik|moet|dan|beter|mijn|aan|voor|en|vast|vaste|variabel|gebruik|welke|goedkoper|nemen|kiezen)\b",
    re.IGNORECASE,
)
_FRENCH = re.compile(
    r"\b(?:le|la|les|est|quel|quelle|quels|prix|électricité|electricite|aujourd|je|j|mon|ma|mes|un|une|des|du|pour|"
    r"combien|coûte|coute|fixe|contrat|vous|tu|que|qui|bonjour|ça|c|pourquoi|mieux|dois|prendre|moins|cher|chère|"
    r"facture|acompte|chauffage|chaudière|consommation|merci|avec|sur|pas)\b",
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
    """'en' or 'fr' when the question reads as English or French, otherwise Karen's default 'nl'."""
    english = len(_ENGLISH.findall(question))
    dutch = len(_DUTCH.findall(question))
    french = len(_FRENCH.findall(question))
    if french > max(english, dutch):
        return "fr"
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


def verified_cards(data: dict[str, Any], contract_type: str = "variable") -> list[dict[str, Any]]:
    """Verified tariff cards with a kWh price, cheapest first. Cards without a contract_type are variable."""
    return sorted(
        (
            card for card in (data.get("contracts") or {}).get("suppliers") or []
            if card.get("status") == "verified" and isinstance(card.get("price_per_kwh_eur"), (int, float))
            and card.get("contract_type", "variable") == contract_type
        ),
        key=lambda card: card["price_per_kwh_eur"],
    )


def card_label(card: dict[str, Any]) -> str:
    name, product = card["name"], str(card.get("product_name") or "")
    return product if product.casefold().startswith(name.casefold()) else f"{name} {product}".strip()


def _cents(value: float, lang: str) -> str:
    text = f"{value:.1f}"
    return text if lang == "en" else text.replace(".", ",")  # nl and fr use a decimal comma


def _join(items: list[str], lang: str) -> str:
    if len(items) < 2:
        return "".join(items)
    return ", ".join(items[:-1]) + {"en": " and ", "fr": " et "}.get(lang, " en ") + items[-1]


def _day_label(day: str, lang: str) -> str:
    today = datetime.now(BRUSSELS).date().isoformat()
    if day == today:
        return {"en": "today", "fr": "aujourd'hui"}.get(lang, "vandaag")
    return {"en": f"on {day}", "fr": f"le {day}"}.get(lang, f"op {day}")


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
    elif lang == "fr":
        lines = [
            f"Le prix de gros de l'électricité en Belgique (day-ahead) est {when} de {avg} centimes par kWh en moyenne, "
            f"entre {low} et {high} centimes."
            + (f" C'est le moins cher vers {_join([f'{h}h' for h in hours], lang)}." if hours else "")
            + " C'est hors coûts de réseau, taxes et TVA."
        ]
    else:
        lines = [
            f"De groothandelsprijs voor stroom in België (day-ahead) is {when} gemiddeld {avg} cent per kWh, "
            f"tussen {low} en {high} cent."
            + (f" Het goedkoopst is het rond {_join([f'{h}u' for h in hours], lang)}." if hours else "")
            + " Dat is zonder netkosten, heffingen en btw."
        ]

    contracts = data.get("contracts") or {}
    suppliers = verified_cards(data)[:MAX_SUPPLIERS]
    fixed_cards = verified_cards(data, "fixed")[:MAX_FIXED]
    if suppliers:
        lines.append({
            "en": "Variable contracts on the suppliers' current tariff cards (energy price incl. 6% VAT, without network costs):",
            "fr": "Contrats variables sur les fiches tarifaires actuelles des fournisseurs (prix de l'énergie TVA 6% incl., hors coûts de réseau) :",
        }.get(lang, "Variabele contracten op de huidige tariefkaarten van leveranciers (energieprijs incl. 6% btw, zonder netkosten):"))
        for supplier in suppliers:
            cents = _cents(supplier["price_per_kwh_eur"] * 100, lang)
            unit = {"en": "cents/kWh", "fr": "c€/kWh"}.get(lang, "cent/kWh")
            lines.append(f"• {card_label(supplier)}: {cents} {unit}")
    if fixed_cards:
        lines.append({
            "en": "Cheapest fixed contracts on the current tariff cards:",
            "fr": "Contrats fixes les moins chers sur les fiches tarifaires actuelles :",
        }.get(lang, "Goedkoopste vaste contracten op de huidige tariefkaarten:"))
        unit = {"en": "cents/kWh", "fr": "c€/kWh"}.get(lang, "cent/kWh")
        for card in fixed_cards:
            lines.append(f"• {card_label(card)}: {_cents(card['price_per_kwh_eur'] * 100, lang)} {unit}")
    lines.append({
        "en": "Want to know what this means for your home? Say \"compare offers\" and I'll ask for your region, postcode and usage.",
        "fr": "Vous voulez savoir ce que cela représente pour votre logement ? Dites \"comparer les offres\" et je vous "
              "demanderai votre région, code postal et consommation.",
    }.get(lang, "Wil je weten wat dat voor jou betekent? Zeg \"vergelijk aanbiedingen\", dan vraag ik je gewest, postcode en verbruik."))

    market_source = next(iter(data.get("sources") or []), {})
    sources = [{
        "title": market_source.get("title", "Energy-Charts (Fraunhofer ISE) · day-ahead BE"),
        "detail": f"Day-ahead {day.get('date', '')} · CC BY 4.0",
        "url": market_source.get("url", "https://www.energy-charts.info/charts/price_spot_market/chart.htm?l=nl&c=BE"),
        "excerpt": f"Gemiddeld {day['avg']} EUR/MWh, min {day['min']}, max {day['max']}.",
    }]
    for supplier in suppliers + fixed_cards:
        if supplier.get("source_url"):
            sources.append({
                "title": f"Tariefkaart {card_label(supplier)}",
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

    `recognised` skips the price-word check when Jev already routed the question here; gas,
    network-cost and household-detail questions still go elsewhere, since market.json can't answer them.
    """
    if recognised:
        if _OTHER_SUBJECT.search(question) or _HOUSEHOLD_INPUT.search(question):
            return None
    elif not is_market_price_question(question):
        return None
    data = await load_market_data()
    if data is None:
        return None
    return market_price_reply(data, question_language(question))
