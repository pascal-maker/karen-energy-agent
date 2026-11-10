"""Answer plain electricity and gas price questions from the published market.json.

The daily `market-daily` workflow already publishes today's Belgian day-ahead
prices and verified supplier tariff cards for the Prijzen tab. A question like
"wat zijn de stroomprijzen vandaag?" is answered from that file directly, so
Karen shows real numbers instead of starting the offer-comparison form. Gas
questions get the verified gas tariff cards. Only values present in the file are
shown; nothing is estimated or filled in.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator
import time
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .browserbase_sources import named_supplier

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
# Only plain gas and gas-price words; a gasfactuur or gasketel is a different question.
_GAS = re.compile(r"\b(?:(?:aard)?gas(?:prijs|prijzen|tarief|tarieven|contract|contracten)?|gaz)\b", re.IGNORECASE)
# A gas price list can't answer a boiler question or a "why" question.
_GAS_ELSEWHERE = re.compile(r"\b(?:\w*ketel\w*|boiler|chaudi[eè]re|waarom|why|pourquoi)\b", re.IGNORECASE)
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
    r"combien|coûte|coute|fixe|contrat|gaz|vous|tu|que|qui|bonjour|ça|c|pourquoi|mieux|dois|prendre|moins|cher|chère|"
    r"facture|acompte|chauffage|chaudière|consommation|merci|avec|sur|pas|oui|non|français|francais|explique\w*|"
    r"s'il|stp|svp|aussi|alors|mais|où|comment|parle\w*|et|ton|tes|ta|réponds|reponds|seulement|contenu|règles|regles|"
    r"il|elle|nous|ils|sont|peux|peut|dans|chez|au|aux|ce|cette|ces|j'ai|c'est|qu'est)\b",
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


def is_gas_price_question(question: str, *, recognised: bool = False) -> bool:
    """A general gas-price question that the gas tariff cards can answer."""
    text = question.strip()
    other = _OTHER_SUBJECT.search(_GAS.sub(" ", text))
    return bool(
        _GAS.search(text)
        and (recognised or _PRICE.search(text))
        and not _GAS_ELSEWHERE.search(text)
        and not other
        and not _COMPARE.search(text)
        and not _HOUSEHOLD_INPUT.search(text)
    )


# The reply language for the current question, fixed once from the visitor's own words (see
# answer_language); a rewritten follow-up or a Dutch search query can't change it.
_reply_language: ContextVar[str | None] = ContextVar("reply_language", default=None)
_ASKED_LANGUAGE = [
    ("fr", re.compile(r"\b(?:en\s+fran[cç]ais|in\s+(?:het\s+)?frans|in\s+french|parle[sz]?\s+fran[cç]ais|french,?\s+please)\b", re.IGNORECASE)),
    ("en", re.compile(r"\b(?:in\s+english|en\s+anglais|in\s+(?:het\s+)?engels|english,?\s+please|speak\s+english)\b", re.IGNORECASE)),
    ("nl", re.compile(r"\b(?:in\s+(?:het\s+)?nederlands|en\s+n[ée]erlandais|in\s+dutch|in\s+het\s+vlaams)\b", re.IGNORECASE)),
]


def _asked_language(text: str) -> str | None:
    return next((lang for lang, pattern in _ASKED_LANGUAGE if pattern.search(text)), None)


def answer_language(question: str, earlier_questions: list[str]) -> str:
    """The language to answer in, from the visitor's own words only.

    An explicit request ("en français", "in English, please") wins and holds for the next short
    messages; a question of four words or more is answered in its own language; a short one
    ("Explique.", "Oké.") in the language of the conversation so far."""
    asked = _asked_language(question)
    if asked:
        return asked
    if len(question.split()) >= 4:
        return _detect_language(question)
    for earlier in reversed(earlier_questions[-3:]):
        asked = _asked_language(earlier)
        if asked:
            return asked
        if len(earlier.split()) >= 4:
            return _detect_language(earlier)
    return _detect_language(question)


@contextmanager
def reply_language(language: str) -> Iterator[None]:
    token = _reply_language.set(language)
    try:
        yield
    finally:
        _reply_language.reset(token)


def question_language(question: str) -> str:
    """The reply language fixed for this question, or else the language the question reads as."""
    return _reply_language.get() or _detect_language(question)


def _detect_language(question: str) -> str:
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


def verified_cards(data: dict[str, Any], contract_type: str = "variable", energy: str = "electricity") -> list[dict[str, Any]]:
    """Verified tariff cards with a kWh price, cheapest first. Cards without a contract_type are variable."""
    key = "gas_suppliers" if energy == "gas" else "suppliers"
    return sorted(
        (
            card for card in (data.get("contracts") or {}).get(key) or []
            if card.get("status") == "verified" and isinstance(card.get("price_per_kwh_eur"), (int, float))
            and card.get("contract_type", "variable") == contract_type
        ),
        key=lambda card: card["price_per_kwh_eur"],
    )


def card_label(card: dict[str, Any]) -> str:
    name, product = card["name"], str(card.get("product_name") or "")
    return product if product.casefold().startswith(name.casefold()) else f"{name} {product}".strip()


def _cents(value: float, lang: str, digits: int = 1) -> str:
    text = f"{value:.{digits}f}"
    return text if lang == "en" else text.replace(".", ",")  # nl and fr use a decimal comma


def _join(items: list[str], lang: str) -> str:
    if len(items) < 2:
        return "".join(items)
    return ", ".join(items[:-1]) + {"en": " and ", "fr": " et "}.get(lang, " en ") + items[-1]


# Labels for the chart the chat draws next to a price answer.
_VISUAL_LABELS = {
    "nl": {"variable": "Variabel", "fixed": "Vast", "day": "Groothandelsprijs per uur", "unit": "cent/kWh", "months": "{} mnd vast", "cheap": "goedkoopste uren"},
    "en": {"variable": "Variable", "fixed": "Fixed", "day": "Wholesale price per hour", "unit": "cents/kWh", "months": "{} months fixed", "cheap": "cheapest hours"},
    "fr": {"variable": "Variable", "fixed": "Fixe", "day": "Prix de gros par heure", "unit": "c€/kWh", "months": "fixe {} mois", "cheap": "heures les moins chères"},
}


def _hourly_cents(points: Any) -> list[list[Any]]:
    """Quarter-hour EUR/MWh points as hourly averages in eurocent per kWh: [["14:00", 21.3], ...]."""
    hours: dict[str, list[float]] = {}
    for point in points or []:
        if isinstance(point, (list, tuple)) and len(point) == 2 and isinstance(point[1], (int, float)):
            hours.setdefault(f"{str(point[0])[:2]}:00", []).append(point[1] / 10)
    return [[hour, round(sum(values) / len(values), 2)] for hour, values in sorted(hours.items())]


def _visual_group(kind: str, cards: list[dict[str, Any]], lang: str, digits: int) -> dict[str, Any]:
    labels = _VISUAL_LABELS.get(lang, _VISUAL_LABELS["nl"])
    items = []
    for card in cards:
        months = card.get("duration_months") if kind == "fixed" else None
        items.append({
            "label": card_label(card),
            "value": round(card["price_per_kwh_eur"] * 100, digits),
            **({"note": labels["months"].format(months)} if months else {}),
        })
    return {"title": labels[kind], "items": items}


def _current_spot_day(spot: dict[str, Any]) -> dict[str, Any]:
    """The spot day dated today; the daily job runs in the evening, so after midnight its "tomorrow" is today."""
    today = datetime.now(BRUSSELS).date().isoformat()
    later = spot.get("tomorrow") or {}
    return later if later.get("date") == today and (spot.get("today") or {}).get("date") != today else spot.get("today") or {}


def _day_label(day: str, lang: str) -> str:
    today = datetime.now(BRUSSELS).date().isoformat()
    if day == today:
        return {"en": "today", "fr": "aujourd'hui"}.get(lang, "vandaag")
    return {"en": f"on {day}", "fr": f"le {day}"}.get(lang, f"op {day}")


def market_price_reply(data: dict[str, Any], lang: str = "nl", region: str = "flanders") -> dict[str, Any] | None:
    """Karen's reply from market.json, or None when the file has no usable spot day.

    The wholesale price is Belgian; the supplier cards are residential Flanders, so a visitor in
    Brussels or Wallonia gets the wholesale price and their region's comparator instead of the cards."""
    spot = data.get("spot") or {}
    day = _current_spot_day(spot)
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
    flemish = region not in ("brussels", "wallonia")
    suppliers = verified_cards(data)[:MAX_SUPPLIERS] if flemish else []
    fixed_cards = verified_cards(data, "fixed")[:MAX_FIXED] if flemish else []
    if not flemish:
        name, url = _COMPARATORS[region]
        place = {"brussels": {"nl": "Brussel", "fr": "Bruxelles", "en": "Brussels"},
                 "wallonia": {"nl": "Wallonië", "fr": "la Wallonie", "en": "Wallonia"}}[region][lang]
        lines.append({
            "en": f"The supplier tariff cards I have are for Flanders, not {place}. Compare prices for your usage with {name}: {url}",
            "fr": f"Les fiches tarifaires que j'ai valent pour la Flandre, pas pour {place}. Compare les prix pour ta consommation avec {name} : {url}",
        }.get(lang, f"De tariefkaarten van leveranciers die ik heb, gelden voor Vlaanderen, niet voor {place}. Vergelijk de prijzen voor jouw verbruik met {name}: {url}"))
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

    market_source = next(iter(data.get("sources") or []), {})
    sources = [{
        "title": market_source.get("title", "Energy-Charts (Fraunhofer ISE) · day-ahead BE"),
        "detail": f"Day-ahead {day.get('date', '')} · CC BY 4.0",
        "url": market_source.get("url", "https://www.energy-charts.info/charts/price_spot_market/chart.htm?l=nl&c=BE"),
        "excerpt": f"Gemiddeld {day['avg']} EUR/MWh, min {day['min']}, max {day['max']}.",
    }]
    sources += _card_sources(suppliers + fixed_cards, contracts)
    labels = _VISUAL_LABELS.get(lang, _VISUAL_LABELS["nl"])
    visual: dict[str, Any] = {
        "kind": "prices", "lead": lines[0], "unit": labels["unit"], "digits": 1,
        "groups": [_visual_group(kind, cards, lang, 1) for kind, cards in (("variable", suppliers), ("fixed", fixed_cards)) if cards],
    }
    hourly = _hourly_cents(day.get("points"))
    if len(hourly) >= 6:
        visual["day"] = {
            "title": f"{labels['day']} · {day.get('date', '')}", "points": hourly, "cheapest_hours": hours, "cheap_label": labels["cheap"],
            "low": round(day["min"] / 10, 1), "high": round(day["max"] / 10, 1),
        }
    return {
        "answer": "\n".join(lines),
        "visual": visual,
        "sources": sources,
        "live_source_status": "checked",
        "processing": "market-data",
    }


def _card_sources(cards: list[dict[str, Any]], contracts: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "title": f"Tariefkaart {card_label(card)}",
            "detail": f"Opgehaald {card.get('retrieved_at', contracts.get('as_of', ''))}",
            "url": card["source_url"],
            "excerpt": str(card.get("price_per_kwh_note", ""))[:360],
        }
        for card in cards if card.get("source_url")
    ]


def gas_price_reply(data: dict[str, Any], lang: str = "nl") -> dict[str, Any] | None:
    """Karen's gas reply from the verified gas tariff cards, or None when the file has none."""
    variable = verified_cards(data, energy="gas")[:MAX_SUPPLIERS]
    fixed = verified_cards(data, "fixed", energy="gas")[:MAX_FIXED + 1]
    if not variable and not fixed:
        return None
    unit = {"en": "cents/kWh", "fr": "c€/kWh"}.get(lang, "cent/kWh")
    lines = [{
        "en": "Gas prices on the suppliers' current tariff cards in Flanders (energy price incl. 6% VAT, "
              "without network costs and levies, which add a few cents per kWh):",
        "fr": "Prix du gaz sur les fiches tarifaires actuelles des fournisseurs en Flandre (prix de l'énergie TVA 6% incl., "
              "hors coûts de réseau et taxes, qui ajoutent quelques centimes par kWh) :",
    }.get(lang, "Gasprijzen op de huidige tariefkaarten van leveranciers in Vlaanderen (energieprijs incl. 6% btw, "
                "zonder netkosten en heffingen, die er nog enkele cent per kWh bijtellen):")]
    if variable:
        lines.append({"en": "Variable:", "fr": "Variables :"}.get(lang, "Variabel:"))
        # Gas prices are close together, so they keep the cards' two decimals.
        lines += [f"• {card_label(card)}: {_cents(card['price_per_kwh_eur'] * 100, lang, 2)} {unit}" for card in variable]
    if fixed:
        lines.append({"en": "Cheapest fixed:", "fr": "Fixes les moins chers :"}.get(lang, "Goedkoopste vaste:"))
        for card in fixed:
            months = card.get("duration_months")
            term = {"en": f" (fixed {months} months)", "fr": f" (fixe {months} mois)"}.get(lang, f" ({months} maanden vast)") if months else ""
            lines.append(f"• {card_label(card)}: {_cents(card['price_per_kwh_eur'] * 100, lang, 2)} {unit}{term}")
    return {
        "answer": "\n".join(lines),
        "visual": {
            "kind": "prices", "unit": unit, "digits": 2,
            "lead": {"en": "Gas prices in Flanders (energy price incl. 6% VAT, without network costs and levies):",
                     "fr": "Prix du gaz en Flandre (prix de l'énergie TVA 6% incl., hors coûts de réseau et taxes) :",
                     }.get(lang, "Gasprijzen in Vlaanderen (energieprijs incl. 6% btw, zonder netkosten en heffingen):"),
            "groups": [_visual_group(kind, cards, lang, 2) for kind, cards in (("variable", variable), ("fixed", fixed)) if cards],
        },
        "sources": _card_sources(variable + fixed, data.get("contracts") or {}),
        "live_source_status": "checked",
        "processing": "market-data",
    }


async def answer_market_price_question(question: str, *, recognised: bool = False, region: str = "flanders") -> dict[str, Any] | None:
    """None when this isn't a general price question or market.json is unavailable.

    `recognised` skips the price-word check when Jev already routed the question here; network-cost
    and household-detail questions still go elsewhere, since market.json can't answer them.
    """
    if is_gas_price_question(question, recognised=recognised):
        if region in ("brussels", "wallonia"):
            # The gas cards are Flemish too: the region's comparator instead.
            from .question_kind import regional_tariff_answer

            return regional_tariff_answer(region, question_language(question))
        data = await load_market_data()
        return gas_price_reply(data, question_language(question)) if data is not None else None
    if recognised:
        if _OTHER_SUBJECT.search(question) or _HOUSEHOLD_INPUT.search(question):
            return None
    elif not is_market_price_question(question):
        return None
    data = await load_market_data()
    if data is None:
        return None
    return market_price_reply(data, question_language(question), region or "flanders")


_COMPARATORS = {
    "flanders": ("V-test (Vlaamse Nutsregulator)", "https://www.vtest.be/"),
    "brussels": ("BruSim (BRUGEL)", "https://brugel.brussels/nl_BE/outils/brusim-2"),
    "wallonia": ("CompaCWaPE (CWaPE)", "https://www.compacwape.be/"),
}
_COMPARATOR_REGION = (
    ("brussels", re.compile(r"\b(?:brussel|bruxelles|brussels)\b", re.IGNORECASE)),
    ("wallonia", re.compile(r"\b(?:walloni[eë]|wallonie|wallonia|waals\w*)\b", re.IGNORECASE)),
)
_COMPARISON_LEAD = {
    "nl": "Dit zijn de goedkoopste contracten van vandaag. Wat jij precies betaalt, hangt af van je verbruik: reken het na op {name}.",
    "en": "These are today's cheapest contracts. What you pay depends on your usage: check it on {name}.",
    "fr": "Voici les contrats les moins chers du jour. Ce que tu paies dépend de ta consommation : vérifie-le sur {name}.",
}
# "How do I compare?" asks for the tool first; the cards follow as an example.
_HOW_TO_COMPARE = re.compile(r"\b(?:hoe|manier|waar kan ik|how|way|where can i|comment|o[uù] puis)\b", re.IGNORECASE)
_CREG_SCAN = ("CREG-scan", "https://www.cregscan.be/nl")
_HOW_TO_LEAD = {
    "nl": "De makkelijkste manier is een officiële vergelijker: op {name} vul je je verbruik in en zie je alle contracten naast elkaar. "
          "Met de CREG-scan zie je meteen of je huidige contract duur is. Dit zijn alvast de goedkoopste contracten van vandaag.",
    "en": "The easiest way is an official comparator: on {name} you enter your usage and see every contract side by side. "
          "The CREG-scan shows straight away whether your current contract is expensive. These are today's cheapest contracts.",
    "fr": "Le plus simple est un comparateur officiel : sur {name}, tu indiques ta consommation et tu vois tous les contrats côte à côte. "
          "Le CREG-scan te montre tout de suite si ton contrat actuel est cher. Voici déjà les contrats les moins chers du jour.",
}


async def answer_offer_comparison(question: str) -> dict[str, Any] | None:
    """A comparison question gets today's supplier cards and the official comparator, no form."""
    data = await load_market_data()
    if data is None:
        return None
    lang = question_language(question)
    gas = bool(_GAS.search(question))
    reply = gas_price_reply(data, lang) if gas else market_price_reply(data, lang)
    if reply is None:
        return None
    region = next((name for name, pattern in _COMPARATOR_REGION if pattern.search(question)), "flanders")
    name, url = _COMPARATORS[region]
    how_to = bool(_HOW_TO_COMPARE.search(question))
    leads = _HOW_TO_LEAD if how_to else _COMPARISON_LEAD
    lead = leads.get(lang, leads["nl"]).format(name=name)
    comparators = [{"title": name, "url": url, "detail": "Officiële vergelijker · zelf je verbruik invullen"}]
    if how_to:
        comparators.append({"title": _CREG_SCAN[0], "url": _CREG_SCAN[1], "detail": "Officiële scan van je huidige contract"})
    visual = {key: value for key, value in reply["visual"].items() if key != "day"}
    visual["lead"] = lead
    return {
        **reply,
        "answer": f"{lead}\n{reply['answer']}",
        "visual": visual,
        "suggested_sources": comparators,
        "processing": "offer-comparison",
    }


def today_summary(data: dict[str, Any], now: datetime | None = None) -> dict[str, Any] | None:
    """The wholesale price this hour and today's cheapest hours, for Karen's greeting."""
    now = now or datetime.now(BRUSSELS)
    today = now.date().isoformat()
    spot = data.get("spot") or {}
    day = next((d for d in (spot.get("today") or {}, spot.get("tomorrow") or {}) if d.get("date") == today), None)
    if day is None or spot.get("unit") != "EUR/MWh":
        return None
    hourly = dict(_hourly_cents(day.get("points")))
    current = hourly.get(f"{now.hour:02d}:00")
    if current is None:
        return None
    later = sorted((h for h in day.get("cheapest_hours") or [] if isinstance(h, int) and h >= now.hour))
    return {"date": today, "hour": now.hour, "now_cents": current, "cheapest_hour": later[0] if later else None}


# A price question about one named supplier: that supplier's own verified tariff cards.
_SUPPLIER_CARD_NAMES = {
    "luminus": "Luminus", "eneco": "Eneco", "engie": "Engie", "totalenergies": "TotalEnergies", "mega": "Mega",
    "bolt": "Bolt", "elegant": "Elegant", "energyvision": "Energyvision", "octaplus": "OCTA+",
    "aspiravi": "Aspiravi", "frank": "Frank Energie",
}
_SUPPLIER_PRICE = re.compile(
    r"\b(?:\w*prijs\w*|kost\w*|tarie\w*|vergoeding|abonnement\w*|betaal\w*|kwh|cent|price\w*|costs?|fees?|pay|how much|prix|co[uû]t\w*|redevance|combien|paie\w*)\b",
    re.IGNORECASE,
)
# Cancelling, invoices, complaints and comparisons keep their own routes.
_SUPPLIER_ELSEWHERE = re.compile(
    r"\b\w*(?:opzeg|stopzet|verbreking|annul|r[ée]sili|cancel|factu|invoice|voorschot|acompte|afrekening|klacht|plainte|"
    r"complaint|contact|vergelijk|compar|goedkoper|duurder|cheaper|ketel|boiler|waarom|why|pourquoi)\w*\b",
    re.IGNORECASE,
)
_FIXED_WORD = re.compile(r"\b(?:vast\w*|fix\w*|fixe)\b", re.IGNORECASE)
_STANDING_FEE = re.compile(r"\b(?:vaste\s+vergoeding|fixed\s+fee|redevance\s+fixe)\b", re.IGNORECASE)
_VARIABLE_WORD = re.compile(r"\b(?:variab\w*|flex\w*)\b", re.IGNORECASE)
_CONTRACT_TYPE = {"fixed": {"nl": "vast", "en": "fixed", "fr": "fixe"}, "variable": {"nl": "variabel", "en": "variable", "fr": "variable"}}
_STANDING_UNIT = {"per_year": {"nl": "per jaar", "en": "a year", "fr": "par an"}, "per_month": {"nl": "per maand", "en": "a month", "fr": "par mois"}}


def _card_line(card: dict[str, Any], lang: str) -> str:
    cents = _cents(card["price_per_kwh_eur"] * 100, lang)
    kind = _CONTRACT_TYPE[card.get("contract_type", "variable")][lang]
    amount, unit = card.get("standing_charge_amount"), _STANDING_UNIT.get(card.get("standing_charge_unit"), {}).get(lang)
    fee = ""
    if isinstance(amount, (int, float)) and unit:
        euros = _cents(amount, lang, 2).removesuffix(",00").removesuffix(".00")
        fee = {"en": f", fixed fee € {euros} {unit}", "fr": f", redevance fixe {euros} € {unit}"}.get(lang, f", vaste vergoeding € {euros} {unit}")
    unit_kwh = {"en": "cents/kWh", "fr": "c€/kWh"}.get(lang, "cent/kWh")
    return f"{card_label(card)} ({kind}): {cents} {unit_kwh}{fee}"


async def answer_supplier_price_question(question: str) -> dict[str, Any] | None:
    """The named supplier's prices from its verified tariff cards; None when the question isn't that."""
    supplier = named_supplier(question)
    name = _SUPPLIER_CARD_NAMES.get(supplier or "")
    if not name or not _SUPPLIER_PRICE.search(question) or _SUPPLIER_ELSEWHERE.search(question):
        return None
    data = await load_market_data()
    if data is None:
        return None
    lang = question_language(question)
    energy = "gas" if _GAS.search(question) else "electricity"
    # "vaste vergoeding" is the standing charge, not a fixed contract.
    kind_text = _STANDING_FEE.sub(" ", question)
    fixed, variable = bool(_FIXED_WORD.search(kind_text)), bool(_VARIABLE_WORD.search(kind_text))
    types = ["fixed"] if fixed and not variable else ["variable"] if variable and not fixed else ["variable", "fixed"]
    cards = [card for kind in types for card in verified_cards(data, kind, energy) if card["name"].casefold() == name.casefold()]
    if not cards:
        return None
    what = {"gas": {"nl": "gas", "en": "gas", "fr": "le gaz"}, "electricity": {"nl": "stroom", "en": "electricity", "fr": "l'électricité"}}[energy][lang]
    lead = {
        "en": f"On {name}'s current tariff card, {what} costs:",
        "fr": f"Sur la fiche tarifaire actuelle de {name}, {what} coûte :",
    }.get(lang, f"Op de huidige tariefkaart van {name} kost {what}:")
    note = {
        "en": "Energy price incl. 6% VAT for Flanders, without network costs and levies. Your own price depends on your usage: check it on V-test.",
        "fr": "Prix de l'énergie TVA 6% incl. pour la Flandre, hors coûts de réseau et taxes. Ton prix dépend de ta consommation : vérifie-le sur V-test.",
    }.get(lang, "Energieprijs incl. 6% btw in Vlaanderen, zonder netkosten en heffingen. Wat jij betaalt hangt af van je verbruik: reken het na op V-test.")
    lines = [lead, *(f"- {_card_line(card, lang)}" for card in cards), note]
    return {
        "answer": "\n".join(lines),
        "sources": [{"title": card_label(card), "url": card["source_url"], "detail": "Tariefkaart van de leverancier"}
                    for card in cards if card.get("source_url")],
        "suggested_sources": [{"title": _COMPARATORS["flanders"][0], "url": _COMPARATORS["flanders"][1],
                               "detail": "Officiële vergelijker · zelf je verbruik invullen"}],
        "live_source_status": "checked",
        "processing": "supplier-tariff-card",
    }


# --- Questions the price lists used to swallow ---------------------------------------------------

_WHEN = re.compile(
    r"\b(?:wanneer|welk\w*\s+(?:uur|uren|moment)|hoe\s+laat|om\s+hoeveel\s+uur|when|what\s+time|quand|[àa]\s+quelle\s+heure)\b",
    re.IGNORECASE,
)
_CHEAP = re.compile(r"\b(?:goedko\w*|voordelig\w*|cheap\w*|moins\s+ch[eè]re?)\b", re.IGNORECASE)


_ELECTRICITY = re.compile(
    r"\b(?:stroom\w*|elektri\w*|electri\w*|[ée]lectricit[ée]|energie\w*|[ée]nergie|energy|kwh|wasmachine|vaatwas\w*|droogkast|"
    r"(?:op)?laden|laadpaal|warmtepomp|power)\b", re.IGNORECASE,
)


def is_cheapest_time_question(question: str) -> bool:
    """'Wanneer is stroom het goedkoopst?': the hours, not the supplier cards. Not flights, not fuel, not gas."""
    return bool(_WHEN.search(question) and _CHEAP.search(question) and _ELECTRICITY.search(question)) and not _GAS.search(question)


_CHEAPEST_TIME = {
    "nl": (
        "Vandaag is stroom op de groothandelsmarkt het goedkoopst rond {hours} (de dag loopt van {low} tot {high} cent per kWh).",
        "- **Dynamisch contract:** dan volgt je prijs deze uren, dus loont het om je wasmachine of auto dan te laten draaien.",
        "- **Vast of variabel contract:** dan betaal je elk uur van de dag hetzelfde, behalve met een dag- en nachtmeter: "
        "dan is stroom 's nachts en in het weekend goedkoper.",
    ),
    "en": (
        "Today wholesale electricity is cheapest around {hours} (the day runs from {low} to {high} cents per kWh).",
        "- **Dynamic contract:** your price follows these hours, so run the washing machine or charge the car then.",
        "- **Fixed or variable contract:** you pay the same every hour, unless you have a day/night meter: "
        "then nights and weekends are cheaper.",
    ),
    "fr": (
        "Aujourd'hui, l'électricité est la moins chère sur le marché de gros vers {hours} (la journée va de {low} à {high} centimes par kWh).",
        "- **Contrat dynamique :** ton prix suit ces heures, lance donc la machine à laver ou la recharge de la voiture à ce moment-là.",
        "- **Contrat fixe ou variable :** tu paies le même prix à toute heure, sauf avec un compteur bihoraire : "
        "la nuit et le week-end sont alors moins chers.",
    ),
}


# "Vandaag is stroom … het goedkoopst" -> "Op 2026-11-01 was stroom … het goedkoopst".
_PAST_DAY = {
    "nl": ("Vandaag is ", "{when} was "),
    "en": ("Today wholesale electricity is", "{when} wholesale electricity was"),
    "fr": ("Aujourd'hui, l'électricité est", "{when}, l'électricité était"),
}


def cheapest_time_reply(data: dict[str, Any], lang: str = "nl") -> dict[str, Any] | None:
    """Today's cheapest hours first, then what they mean for the kind of contract; None without spot data."""
    spot = data.get("spot") or {}
    day = _current_spot_day(spot)
    hours = [hour for hour in day.get("cheapest_hours") or [] if isinstance(hour, int)][:3]
    if spot.get("unit") != "EUR/MWh" or not hours or not all(isinstance(day.get(key), (int, float)) for key in ("min", "max")):
        return None
    labels = [f"{h}:00" if lang == "en" else f"{h}{'h' if lang == 'fr' else 'u'}" for h in hours]
    lines = _CHEAPEST_TIME.get(lang, _CHEAPEST_TIME["nl"])
    first = lines[0].format(hours=_join(labels, lang), low=_cents(day["min"] / 10, lang), high=_cents(day["max"] / 10, lang))
    if day.get("date") != datetime.now(BRUSSELS).date().isoformat():
        # Old data is never called today.
        label = _day_label(str(day.get("date", "")), lang)
        now_word, then = _PAST_DAY.get(lang, _PAST_DAY["nl"])
        first = first.replace(now_word, then.format(when=label[0].upper() + label[1:]), 1)
    return {
        "answer": "\n".join([first, *lines[1:]]),
        "sources": [source for source in data.get("sources") or [] if isinstance(source, dict)][:1],
        "live_source_status": "checked",
        "processing": "market-data",
    }


# VREG's averages for electricity a year by household size (as quoted by Selectra, checked 2 Nov 2026).
HOUSEHOLD_KWH = {2: 2350, 3: 3200, 4: 4000}
# "3.500 kWh", "3500 kWh", "20 kWh", "8,5 kWh": thousands with a dot or space, decimals with a comma.
_USAGE_NUMBER = re.compile(r"(?<![\d,.])(\d{1,2}(?:[.\s]\d{3})+|\d{1,6}(?:,\d{1,2})?)\s*kwh\b", re.IGNORECASE)
_HOW_MUCH = re.compile(r"\b(?:veel|weinig|normaal|gemiddeld\w*|hoog|laag|a\s+lot|much|normal|average|beaucoup|normale?)\b", re.IGNORECASE)
_USAGE_ELSEWHERE = re.compile(r"\b(?:kost\w*|prijs\w*|betaal\w*|cost\w*|price|prix|co[uû]t\w*|combien)\b", re.IGNORECASE)


# How the usage is counted, and how many of that period make a year.
_PERIODS = {
    "per jaar": (re.compile(r"\b(?:per\s+jaar|jaarlijks\w*|op\s+een\s+jaar|a\s+year|per\s+year|par\s+an)\b", re.IGNORECASE), 1),
    "per maand": (re.compile(r"\b(?:per\s+maand|maandelijks\w*|in\s+een\s+maand|a\s+month|per\s+month|par\s+mois)\b", re.IGNORECASE), 12),
    "per twee maanden": (re.compile(r"\b(?:per\s+twee\s+maanden|tweemaandelijks\w*)\b", re.IGNORECASE), 6),
    "per kwartaal": (re.compile(r"\b(?:per\s+kwartaal|per\s+trimester|driemaandelijks\w*|a\s+quarter|par\s+trimestre)\b", re.IGNORECASE), 4),
    "per week": (re.compile(r"\b(?:per\s+week|wekelijks\w*|a\s+week|par\s+semaine)\b", re.IGNORECASE), 52),
    "per dag": (re.compile(r"\b(?:per\s+dag|dagelijks\w*|a\s+day|per\s+day|par\s+jour)\b", re.IGNORECASE), 365),
}


def _kwh(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def _usage_amount(printed: str) -> Decimal:
    if re.fullmatch(r"\d{1,2}(?:[.\s]\d{3})+", printed):
        return Decimal(re.sub(r"\D", "", printed))
    return Decimal(printed.replace(",", "."))


def usage_reply(question: str) -> dict[str, Any] | None:
    """'Ik verbruik 3500 kWh, is dat veel?' for electricity: set against VREG's household averages; else None."""
    number = _USAGE_NUMBER.search(question)
    if not number or not _HOW_MUCH.search(question) or _GAS.search(question) or _USAGE_ELSEWHERE.search(question):
        return None
    amount = _usage_amount(number.group(1))
    shown = number.group(1) if amount != amount.to_integral_value() else _kwh(int(amount))
    period = next((name for name, (pattern, _) in _PERIODS.items() if pattern.search(question)), None)
    if period is None and amount < 1000:
        # 400 kWh is a month or two, not a year: ask rather than call it low.
        return {
            "answer": f"Is dat {shown} kWh per maand of per jaar? Dan zet ik het naast het gemiddelde van een gezin.",
            "sources": [], "live_source_status": "not-needed", "processing": "usage-yardstick",
        }
    kwh = int((amount * (_PERIODS[period][1] if period else 1)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if kwh < HOUSEHOLD_KWH[2] * 0.85:
        verdict = "minder dan een gemiddeld gezin van 2 personen"
    elif kwh > HOUSEHOLD_KWH[4] * 1.15:
        verdict = "meer dan een gemiddeld gezin van 4 personen"
    else:
        size = min(HOUSEHOLD_KWH, key=lambda people: abs(HOUSEHOLD_KWH[people] - kwh))
        verdict = f"ongeveer wat een gemiddeld gezin van {size} personen verbruikt"
    averages = ", ".join(f"{people} personen {_kwh(value)} kWh" for people, value in HOUSEHOLD_KWH.items())
    return {
        "answer": "\n".join([
            (f"{shown} kWh stroom {period} is ongeveer {_kwh(kwh)} kWh per jaar: {verdict}." if period and period != "per jaar"
             else f"{_kwh(kwh)} kWh stroom per jaar is {verdict}."),
            f"- **Gemiddeld volgens de VREG:** {averages}.",
            "- **Wat meetelt:** een warmtepomp, een elektrische auto of elektrisch koken doen je verbruik flink stijgen.",
            "- **Zonnepanelen:** dan telt wat je van het net haalt, niet wat je zelf opwekt.",
        ]),
        "sources": [{"title": "Selectra · verbruik per gezinsgrootte (cijfers VREG)", "url": "https://selectra.be/nl/energie/verbruik/elektriciteit",
                     "detail": "Gemiddeld elektriciteitsverbruik per gezinsgrootte"}],
        "live_source_status": "not-needed", "processing": "usage-yardstick",
    }


def _named_suppliers(question: str) -> list[str]:
    text = question.casefold()
    found = [(match.start(), name) for key, name in _SUPPLIER_CARD_NAMES.items() if (match := re.search(rf"\b{re.escape(key)}\b", text))]
    return [name for _, name in sorted(found)]


# A price comparison only: "is Luminus of Engie beter voor klantendienst?" is not one.
_WHICH_BETTER = re.compile(r"\b(?:goedko\w*|duurder|voordelig\w*|vergelijk\w*|prijs\w*|cheap\w*|compar\w*|moins\s+cher)\b", re.IGNORECASE)


def named_comparison_reply(question: str, data: dict[str, Any], lang: str = "nl") -> dict[str, Any] | None:
    """'Wat is goedkoper, Luminus of Engie?': those suppliers' own verified electricity cards; else None."""
    names = _named_suppliers(question)
    if len(names) < 2 or not _WHICH_BETTER.search(question) or _GAS.search(question):
        return None
    names = names[:3]
    lines = [f"Zo verhouden {' en '.join(names)} zich op hun huidige tariefkaarten (energieprijs incl. 6% btw, zonder netkosten):"]
    missing, cards = [], []
    for name in names:
        for kind in ("variable", "fixed"):
            own = [card for card in verified_cards(data, kind) if card.get("name") == name]
            if own:
                cards.append(own[0])
                lines.append(f"• {_card_line(own[0], 'nl')}")
            else:
                missing.append(f"{name} heeft geen nagekeken {'variabele' if kind == 'variable' else 'vaste'} tariefkaart.")
    if not cards:
        return None
    cheapest = min(cards, key=lambda card: card["price_per_kwh_eur"])
    lines += missing
    lines.append(
        f"Per kWh is {card_label(cheapest)} het goedkoopst. Wat je in totaal betaalt, hangt ook af van de vaste vergoeding "
        "en je verbruik: reken het na op V-test (Vlaamse Nutsregulator)."
    )
    return {
        "answer": "\n".join(lines),
        "sources": _card_sources(cards, data.get("contracts") or {}),
        "suggested_sources": [{"title": _COMPARATORS["flanders"][0], "url": _COMPARATORS["flanders"][1],
                               "detail": "Officiële vergelijker · zelf je verbruik invullen"}],
        "live_source_status": "checked", "processing": "offer-comparison",
    }


async def answer_specific_price_question(question: str) -> dict[str, Any] | None:
    """The cheapest hours, a usage yardstick or two named suppliers, before any general price list."""
    usage = usage_reply(question)
    if usage is not None:
        return usage
    if not is_cheapest_time_question(question) and len(_named_suppliers(question)) < 2:
        return None
    data = await load_market_data()
    if data is None:
        return None
    if is_cheapest_time_question(question):
        return cheapest_time_reply(data, question_language(question))
    return named_comparison_reply(question, data, question_language(question))
