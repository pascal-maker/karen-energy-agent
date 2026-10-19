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
from .market_prices import _cents, card_label, question_language, verified_cards

PASS_THROUGH_CENT_PER_EUR_MWH = 0.11 * 1.06
DEFAULT_USAGE_KWH = 3500
HORIZON_MONTHS = 12
Z_10_90 = NormalDist().inv_cdf(0.9)

_FIXED = re.compile(r"\b(?:vast\w*|fixed|fixe)\b", re.IGNORECASE)
_VARIABLE = re.compile(r"\b(?:variabel\w*|variable|dynami\w*)\b", re.IGNORECASE)
_ADVICE = re.compile(
    r"\b(?:beter|best\w*|kiezen|kies|nemen|afsluiten|moet|loont|worth|should|better|choose|take|pick|vaut|choisir|mieux|prendre|dois)\b",
    re.IGNORECASE,
)
_CONTRACT = re.compile(r"\b(?:contract\w*|prijs|price|tarief|tariff|formule|offer|aanbod|contrat|prix|offre)\b", re.IGNORECASE)
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
    suppliers = verified_cards(data)
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
        "Je peux le calculer, mais il me faut le prix fixe qu'on vous a proposé. Reposez la question avec ce prix, par "
        "exemple : \"Un contrat fixe à 21 centimes par kWh, c'est mieux que variable ?\" Ajoutez votre consommation "
        "annuelle en kWh si vous la connaissez."
        if lang == "fr" else
        "Dat reken ik graag voor je uit, maar ik heb de vaste prijs nodig die je aangeboden kreeg. Vraag het opnieuw "
        "met die prijs erbij, bijvoorbeeld: \"Is een vast contract aan 21 cent per kWh beter dan variabel?\" "
        "Zet er je jaarverbruik in kWh bij als je dat kent."
    )
    return {"answer": answer, "sources": [], "live_source_status": "not-needed", "processing": "contract-advice"}


def _fixed_card_note(compared: dict[str, Any] | None, cheapest: dict[str, Any] | None, fixed: float, lang: str) -> str:
    """Which fixed card Karen compared with, or a tip when a verified card beats the offered price."""
    if compared is not None:
        label, cents = card_label(compared), _cents(compared["price_per_kwh_eur"] * 100, lang)
        return {
            "en": f"I compared with the cheapest fixed contract on today's tariff cards, {label} at {cents} cents/kWh. "
                  "Were you offered a different price? Tell me and I'll work it out again.",
            "fr": f"J'ai comparé avec le contrat fixe le moins cher des fiches tarifaires actuelles, {label} à {cents} c€/kWh. "
                  "On vous a proposé un autre prix ? Dites-le-moi et je refais le calcul.",
        }.get(lang, f"Ik vergeleek met het goedkoopste vaste contract op de huidige tariefkaarten, {label} aan {cents} cent/kWh. "
                    "Kreeg je een andere prijs aangeboden? Zeg het, dan reken ik het opnieuw uit.")
    if cheapest is not None and cheapest["price_per_kwh_eur"] * 100 < fixed - 0.5:
        label, cents = card_label(cheapest), _cents(cheapest["price_per_kwh_eur"] * 100, lang)
        return {
            "en": f"Tip: {label} lists a fixed price of {cents} cents/kWh on its current tariff card, lower than your offer.",
            "fr": f"Astuce : {label} affiche un prix fixe de {cents} c€/kWh sur sa fiche tarifaire actuelle, moins que votre offre.",
        }.get(lang, f"Tip: {label} vermeldt op de huidige tariefkaart een vaste prijs van {cents} cent/kWh, lager dan je aanbod.")
    return ""


def contract_advice_reply(question: str, data: dict[str, Any]) -> dict[str, Any] | None:
    """Decision and probability, a request for the fixed price, or None when market.json can't support it."""
    lang = question_language(question)
    months = _forecast(data)
    variable = _variable_reference_cents(data)
    if months is None or variable is None:
        return None
    fixed = offered_fixed_cents(question)
    fixed_cards = verified_cards(data, "fixed")
    cheapest_fixed = fixed_cards[0] if fixed_cards else None
    if fixed is None:
        # No offer named: compare with the cheapest verified fixed card whose price is fixed for the
        # same 12 months the forecast covers (a 3-year price is not a like-for-like benchmark).
        benchmark = next((card for card in fixed_cards if card.get("duration_months") == HORIZON_MONTHS), None)
        if benchmark is None:
            return _missing_price_reply(lang)
        fixed = benchmark["price_per_kwh_eur"] * 100
        compared_card = benchmark
    else:
        compared_card = None
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
    elif lang == "fr":
        verdict = {
            "variable": "Le variable semble être le meilleur choix.",
            "fixed": "Le contrat fixe semble être le meilleur choix.",
            "toss-up": "C'est serré : aucun des deux n'est clairement moins cher, donc choisissez le fixe si une facture prévisible compte pour vous.",
        }[decision]
        answer = (
            f"{verdict} Il y a environ {percent} % de chances que le variable revienne moins cher qu'un fixe à "
            f"{_cents(fixed, lang)} c€/kWh sur les 12 prochains mois. Prix variable attendu : "
            f"{_cents(expected, lang)} c€/kWh, soit environ {f'{yearly:,.0f}'.replace(',', ' ')} € par an "
            f"{'de moins' if expected < fixed else 'de plus'} que le fixe pour {f'{usage:,}'.replace(',', ' ')} kWh.\n"
            f"C'est une estimation basée sur la prévision des prix day-ahead et les fiches tarifaires variables actuelles "
            f"({len(names)} fournisseurs), prix de l'énergie TVA 6% incl. uniquement. Redevance fixe, coûts de réseau et "
            "taxes ne sont pas inclus, et ce n'est pas un conseil financier."
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
    note = _fixed_card_note(compared_card, cheapest_fixed, fixed, lang)
    if note:
        answer = answer.replace("\n", f"\n{note}\n", 1)
    market_source = next(iter(data.get("sources") or []), {})
    sources = [{
        "title": "Karen's prijsvoorspelling (12 maanden)",
        "detail": str((data.get("forecast") or {}).get("band", "10-90%-band uit backtest")),
        "url": market_source.get("url", "https://www.energy-charts.info/charts/price_spot_market/chart.htm?l=nl&c=BE"),
        "excerpt": f"{months[0]['month']} tot {months[-1]['month']}: "
                   + ", ".join(f"{m['month']} {m['point']:.0f} ({m['low']:.0f}-{m['high']:.0f})" for m in months[:3])
                   + " EUR/MWh …",
    }]
    shown_card = compared_card or (cheapest_fixed if note else None)
    if shown_card and shown_card.get("source_url"):
        sources.append({
            "title": f"Tariefkaart {card_label(shown_card)}",
            "detail": f"Vast contract · opgehaald {shown_card.get('retrieved_at', '')}".strip(),
            "url": shown_card["source_url"],
            "excerpt": str(shown_card.get("price_per_kwh_note", ""))[:360],
        })
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
