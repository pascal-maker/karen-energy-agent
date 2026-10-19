"""Fixed or variable? A decision with a probability, from the published price forecast.

market.json has no fixed-contract prices, so Karen asks for the fixed price the
user was offered (cent per kWh, energy price incl. VAT). The variable side is the
median of today's verified variable tariff cards, moved with the 12-month
day-ahead forecast: Belgian variable cards are indexed monthly on Belpex, at
about 0.11 cent per kWh for each EUR/MWh before 6% VAT (Frank Energie 0.1068,
Aspiravi 0.116). The forecast's 10-90% band per month is read as a lognormal
around the point forecast, with errors moving together over the year, so the
chance that variable ends up cheaper is a single normal probability. Standing
charges, network costs and taxes are left out because both contracts pay them.
"""

from __future__ import annotations

import math
import re
from statistics import NormalDist, median
from typing import Any

from . import market_prices
from .market_prices import _cents, question_language

PASS_THROUGH_CENT_PER_EUR_MWH = 0.11 * 1.06
DEFAULT_USAGE_KWH = 3500
HORIZON_MONTHS = 12
Z_10_90 = NormalDist().inv_cdf(0.9)

_FIXED = re.compile(r"\b(?:vast\w*|fixed|fixe)\b", re.IGNORECASE)
_VARIABLE = re.compile(r"\b(?:variabel\w*|variable|dynami\w*)\b", re.IGNORECASE)
_ADVICE = re.compile(
    r"\b(?:beter|best\w*|kiezen|kies|nemen|afsluiten|moet|loont|worth|should|better|choose|take|pick|vaut|choisir)\b",
    re.IGNORECASE,
)
_CONTRACT = re.compile(r"\b(?:contract\w*|prijs|price|tarief|tariff|formule|offer|aanbod)\b", re.IGNORECASE)
_CENTS = re.compile(r"(\d{1,2}(?:[.,]\d{1,3})?)\s*(?:c€|ct|cent\w*|eurocent)", re.IGNORECASE)
_EUROS = re.compile(r"(?:€\s*(0[.,]\d{2,5})|(0[.,]\d{2,5})\s*(?:€|eur\w*))", re.IGNORECASE)
_GAS = re.compile(r"\b\w*gas\w*\b", re.IGNORECASE)
_USAGE = re.compile(r"(\d{1,3}(?:[. ]\d{3})+|\d{3,6})\s*kwh", re.IGNORECASE)


def is_contract_advice_question(question: str) -> bool:
    """'Vast of variabel?', 'Is a fixed contract better?'"""
    if not _FIXED.search(question):
        return False
    return bool(_VARIABLE.search(question) or (_ADVICE.search(question) and _CONTRACT.search(question)))


def offered_fixed_cents(question: str) -> float | None:
    """The fixed price in the message, in cent per kWh, when one is plausible (5 to 80 cent)."""
    match = _CENTS.search(question)
    if match:
        value = float(match.group(1).replace(",", "."))
    else:
        match = _EUROS.search(question)
        if not match:
            return None
        value = float((match.group(1) or match.group(2)).replace(",", ".")) * 100
    return value if 5 <= value <= 80 else None


def yearly_usage_kwh(question: str) -> int | None:
    for match in _USAGE.finditer(question):
        value = int(re.sub(r"[. ]", "", match.group(1)))
        if 300 <= value <= 50_000:
            return value
    return None


def _variable_reference_cents(data: dict[str, Any]) -> tuple[float, list[str]] | None:
    suppliers = [
        supplier for supplier in (data.get("contracts") or {}).get("suppliers") or []
        if supplier.get("status") == "verified" and isinstance(supplier.get("price_per_kwh_eur"), (int, float))
    ]
    if len(suppliers) < 3:
        return None
    return median(s["price_per_kwh_eur"] * 100 for s in suppliers), [s["name"] for s in suppliers]


def _forecast(data: dict[str, Any]) -> list[dict[str, float]] | None:
    forecast = data.get("forecast") or {}
    months = [
        month for month in forecast.get("months") or []
        if all(isinstance(month.get(key), (int, float)) and month[key] > 0 for key in ("point", "low", "high"))
        and month["low"] <= month["point"] <= month["high"]
    ][:HORIZON_MONTHS]
    if forecast.get("unit") != "EUR/MWh" or forecast.get("stale") or len(months) < 6:
        return None
    return months


def _average_variable_cents(months: list[dict[str, float]], reference: float, z: float) -> float:
    """Average variable price over the horizon when forecast errors sit z standard deviations out."""
    base = months[0]["point"]
    prices = []
    for month in months:
        point = month["point"]
        spread = math.log(month["high"] / point) if z >= 0 else math.log(point / month["low"])
        wholesale = point * math.exp(z * spread / Z_10_90)
        prices.append(reference + PASS_THROUGH_CENT_PER_EUR_MWH * (wholesale - base))
    return sum(prices) / len(prices)


def chance_variable_is_cheaper(months: list[dict[str, float]], reference: float, fixed: float) -> float:
    """P(average variable price < fixed). The average rises with z, so solve for the break-even z."""
    low, high = -6.0, 6.0
    if _average_variable_cents(months, reference, low) >= fixed:
        return 0.0
    if _average_variable_cents(months, reference, high) < fixed:
        return 1.0
    for _ in range(60):
        middle = (low + high) / 2
        if _average_variable_cents(months, reference, middle) < fixed:
            low = middle
        else:
            high = middle
    return NormalDist().cdf(low)


def _missing_price_reply(lang: str) -> dict[str, Any]:
    answer = (
        "I can work that out, but I need the fixed price you were offered. Ask again with it, for example: "
        "\"Is a fixed contract at 21 cents per kWh better than variable?\" Add your yearly usage in kWh if you know it."
        if lang == "en" else
        "Dat reken ik graag voor je uit, maar ik heb de vaste prijs nodig die je aangeboden kreeg. Vraag het opnieuw "
        "met die prijs erbij, bijvoorbeeld: \"Is een vast contract aan 21 cent per kWh beter dan variabel?\" "
        "Zet er je jaarverbruik in kWh bij als je dat kent."
    )
    return {"answer": answer, "sources": [], "live_source_status": "not-needed", "processing": "contract-advice"}


def contract_advice_reply(question: str, data: dict[str, Any]) -> dict[str, Any] | None:
    """Decision and probability, a request for the fixed price, or None when market.json can't support it."""
    lang = question_language(question)
    months = _forecast(data)
    variable = _variable_reference_cents(data)
    if months is None or variable is None:
        return None
    fixed = offered_fixed_cents(question)
    if fixed is None:
        return _missing_price_reply(lang)
    reference, names = variable
    usage = yearly_usage_kwh(question) or DEFAULT_USAGE_KWH
    expected = _average_variable_cents(months, reference, 0.0)
    chance = chance_variable_is_cheaper(months, reference, fixed)
    percent = round(chance * 100)
    yearly = abs(fixed - expected) * usage / 100
    if chance >= 0.65:
        decision = "variable"
    elif chance <= 0.35:
        decision = "fixed"
    else:
        decision = "toss-up"

    if lang == "en":
        verdict = {
            "variable": "Variable looks like the better choice.",
            "fixed": "The fixed contract looks like the better choice.",
            "toss-up": "It's close: neither is clearly cheaper, so pick fixed if you value a predictable bill.",
        }[decision]
        answer = (
            f"{verdict} There's about a {percent}% chance that variable works out cheaper than "
            f"{_cents(fixed, lang)} cents/kWh fixed over the next 12 months. Expected variable price: "
            f"{_cents(expected, lang)} cents/kWh, so about €{yearly:,.0f} a year "
            f"{'less' if expected < fixed else 'more'} than fixed at {usage:,} kWh.\n"
            f"This is an estimate from the day-ahead price forecast and today's variable tariff cards "
            f"({len(names)} suppliers), energy price incl. 6% VAT only. Standing charges, network costs and taxes "
            "are not included, and it is not financial advice."
        )
    else:
        verdict = {
            "variable": "Variabel lijkt de betere keuze.",
            "fixed": "Het vaste contract lijkt de betere keuze.",
            "toss-up": "Het is nipt: geen van beide is duidelijk goedkoper, dus kies vast als je zekerheid belangrijk vindt.",
        }[decision]
        answer = (
            f"{verdict} De kans dat variabel de komende 12 maanden goedkoper uitvalt dan vast aan "
            f"{_cents(fixed, lang)} cent/kWh is ongeveer {percent}%. Verwachte variabele prijs: "
            f"{_cents(expected, lang)} cent/kWh, dus ongeveer € {f'{yearly:,.0f}'.replace(',', '.')} per jaar "
            f"{'minder' if expected < fixed else 'meer'} dan vast bij {f'{usage:,}'.replace(',', '.')} kWh.\n"
            f"Dit is een schatting op basis van de day-ahead-prijsvoorspelling en de huidige variabele "
            f"tariefkaarten ({len(names)} leveranciers), alleen de energieprijs incl. 6% btw. Vaste vergoeding, "
            "netkosten en heffingen zijn niet meegeteld, en het is geen financieel advies."
        )
    market_source = next(iter(data.get("sources") or []), {})
    sources = [{
        "title": "Karen's prijsvoorspelling (12 maanden)",
        "detail": str((data.get("forecast") or {}).get("band", "10-90%-band uit backtest")),
        "url": market_source.get("url", "https://www.energy-charts.info/charts/price_spot_market/chart.htm?l=nl&c=BE"),
        "excerpt": f"{months[0]['month']} tot {months[-1]['month']}: "
                   + ", ".join(f"{m['month']} {m['point']:.0f} ({m['low']:.0f}-{m['high']:.0f})" for m in months[:3])
                   + " EUR/MWh …",
    }]
    return {
        "answer": answer,
        "sources": sources,
        "live_source_status": "checked",
        "processing": "contract-advice",
        "decision": decision,
        "probability_variable_cheaper": round(chance, 3),
    }


async def answer_contract_advice(question: str, *, recognised: bool = False) -> dict[str, Any] | None:
    """None when this isn't a fixed-or-variable question or market.json can't support an answer.

    `recognised` skips the word-list check when Jev already routed the question here.
    """
    if _GAS.search(question) or (not recognised and not is_contract_advice_question(question)):
        # Only electricity forecasts and tariff cards are published, so gas contracts take the source path.
        return None
    data = await market_prices.load_market_data()
    return None if data is None else contract_advice_reply(question, data)
