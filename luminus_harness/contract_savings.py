"""What your energy contract costs per year, what the cheapest verified tariff card would cost, and the saving.

No model is called: yearly cost = kWh per year × energy price incl. btw + fixed fee per year incl. btw, for the
contract on the bill and for every verified tariff card (market/tariffs-phase2.json, published in market.json
under `contracts`). Network costs and levies are left out on both sides: they are the same for every supplier
in a region. When a fair comparison is not possible (a region or meter the cards don't cover, gas in m³, no
consumption on the bill) the result says why instead of showing a saving.
"""

from __future__ import annotations

import json
import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from .contract_terms import VCHECK_URL, read_contract_terms, region_for_postcode
from .invoice_analysis import analyze_invoice_chunks
from .knowledge import KnowledgeChunk
from .market_prices import card_label, verified_cards

VAT = Decimal("1.06")  # residential electricity and gas in Belgium; the tariff cards are incl. 6% btw
CENT = Decimal("0.01")
TARIFFS_FILE = Path(__file__).resolve().parent.parent / "market" / "tariffs-phase2.json"
COVERED_REGIONS = {"flanders"}  # the verified tariff cards are for residential Flanders, single meter
_ANSWERS_SOURCE = "jouw antwoorden"  # the evidence source of values typed or picked by the visitor

MESSAGES = {
    "compared": "Je huidige contract vergeleken met de geverifieerde tariefkaarten van vandaag.",
    "already_cheapest": "Je zit al bij een van de goedkoopste geverifieerde contracten.",
    "no_current_price": "Ik vond je prijs per kWh niet op de factuur, dus ik kan geen besparing berekenen. Dit kosten de aanbiedingen bij jouw verbruik.",
    "no_usage": "Deze factuur vermeldt geen jaarverbruik. Geef je verbruik in kWh op om te vergelijken.",
    "gas_in_m3": "Je gasverbruik staat in m³. Geef het in kWh op (dat staat op je jaarafrekening) om te vergelijken.",
    "region_not_covered": "Mijn geverifieerde tariefkaarten gelden voor Vlaanderen. Voor Brussel en Wallonië vergelijk je best via BruSim of CompaCWaPE.",
    "day_night_meter": "Je hebt een dag/nachtmeter. Mijn geverifieerde tariefkaarten gelden voor een enkelvoudige meter, dus ik toon nog geen besparing.",
    "exclusive_night": "Je hebt een exclusief-nachtmeter. Daarvoor heb ik nog geen geverifieerde tariefkaarten.",
}
WARNINGS = {
    "vat_assumed_incl": "De factuur zegt niet of de prijs per kWh inclusief btw is; ik ging uit van inclusief, dus de besparing is eerder te laag dan te hoog.",
    "usage_annualised": "Je factuur dekt geen volledig jaar; het verbruik is omgerekend naar een jaar.",
    "region_unknown": "Zonder postcode ging ik uit van Vlaanderen.",
    "standing_assumed_yearly": "Bij de vaste vergoeding staat geen periode; ik ging uit van een jaarbedrag.",
    "price_from_tariff_card": "Je prijs per kWh staat niet op de factuur; ik nam die van de tariefkaart van je product.",
    "fee_not_found": "Ik vond geen vaste vergoeding op de factuur; je huidige kost kan dus hoger liggen dan hier staat.",
    "price_from_chosen_product": "Je prijs komt van de huidige tariefkaart van het product dat je koos.",
}


def local_contracts() -> dict[str, Any]:
    """The tariff cards shipped with this repository, in the shape market.json publishes under `contracts`."""
    return json.loads(TARIFFS_FILE.read_text(encoding="utf-8"))


def _money(value: Decimal) -> str:
    return str(value.quantize(CENT, rounding=ROUND_HALF_UP))


def _parse_date(text: str) -> date | None:
    match = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})", text.strip())
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    try:
        return date(year + (2000 if year < 100 else 0), month, day)
    except ValueError:
        return None


def _billing_days(facts: dict[str, Any]) -> int | None:
    period = facts.get("billing_period")
    if not period or " tot " not in str(period["value"]):
        return None
    start, end = (_parse_date(part) for part in str(period["value"]).split(" tot ", 1))
    if not start or not end or end <= start:
        return None
    return (end - start).days + 1


def _yearly_usage(fact: dict[str, Any] | None, days: int | None, warnings: list[str]) -> tuple[Decimal | None, str | None]:
    """kWh per year from the bill's consumption, or the reason it can't be used."""
    if not fact or not isinstance(fact.get("value"), Decimal):
        return None, "no_usage"
    if fact.get("unit") in ("m3", "m³"):
        return None, "gas_in_m3"
    kwh = fact["value"]
    if days and not 330 <= days <= 400:
        if days < 28:
            return None, "no_usage"
        kwh = (kwh * 365 / days).quantize(Decimal("1"))
        warnings.append("usage_annualised")
    return kwh, None


def _incl_vat(fact: dict[str, Any], warnings: list[str]) -> Decimal:
    if fact.get("vat") == "excl":
        return fact["value"] * VAT
    if fact.get("vat") is None and "vat_assumed_incl" not in warnings:
        warnings.append("vat_assumed_incl")
    return fact["value"]


def _yearly_fee(fact: dict[str, Any] | None, billing_days: int | None, warnings: list[str]) -> Decimal:
    if not fact:
        return Decimal("0")
    amount = _incl_vat(fact, warnings) if fact.get("vat") == "excl" else fact["value"]
    if fact.get("unit") == "per_month":
        return amount * 12
    if fact.get("unit") == "per_year":
        return amount
    days = fact.get("period_days") or billing_days
    if days and days >= 28:
        return amount * 365 / days
    warnings.append("standing_assumed_yearly")
    return amount


def _card_fee(card: dict[str, Any]) -> Decimal:
    amount = Decimal(str(card.get("standing_charge_amount") or 0))
    return amount * 12 if card.get("standing_charge_unit") == "per_month" else amount


def _fold(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def _matching_card(cards: list[dict[str, Any]], supplier: str, product: str) -> dict[str, Any] | None:
    """The tariff card of the product on the bill: same supplier, same product name."""
    if not product:
        return None
    for card in cards:
        same_supplier = _fold(card["name"]) in _fold(supplier) or _fold(card["name"]) in _fold(product)
        if same_supplier and _fold(str(card.get("product_name") or "")) in (_fold(product), _fold(f"{card['name']} {product}")):
            return card
    return None


def _offer(card: dict[str, Any], kwh: Decimal) -> dict[str, Any]:
    price = Decimal(str(card["price_per_kwh_eur"]))
    yearly = kwh * price + _card_fee(card)
    return {
        "supplier": card["name"],
        "product": card.get("product_name"),
        "label": card_label(card),
        "contract_type": card.get("contract_type", "variable"),
        "duration_months": card.get("duration_months"),
        "price_per_kwh_eur": str(price),
        "fixed_fee_eur_per_year": _money(_card_fee(card)),
        "yearly_eur": _money(yearly),
        "source_url": card.get("source_url"),
        "retrieved_at": card.get("retrieved_at"),
        "_yearly": yearly,
    }


def _evidence(*facts: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [
        {"page": fact["page"], "source": fact["source"], "excerpt": fact["excerpt"], "field": fact.get("field", "")}
        for fact in facts if fact
    ]


def _compare_energy(
    energy: str, terms: dict[str, Any], usage: dict[str, Any] | None, supplier: str, contracts: dict[str, Any],
    region: str | None, billing_days: int | None, usage_override: Decimal | None,
) -> dict[str, Any]:
    warnings: list[str] = []
    result: dict[str, Any] = {"status": None, "reason": None, "usage_kwh": None, "current": None, "offers": [],
                              "best": None, "best_by_type": {"variable": None, "fixed": None},
                              "saving_eur_per_year": None, "saving_eur_per_month": None}
    meter = terms.get("meter")
    if energy == "electricity" and meter and meter["value"] in ("day_night", "exclusive_night"):
        result["reason"] = "day_night_meter" if meter["value"] == "day_night" else "exclusive_night"
    elif region and region not in COVERED_REGIONS:
        result["reason"] = "region_not_covered"
    if result["reason"]:
        return _finish(result, warnings)
    if region is None:
        warnings.append("region_unknown")

    if usage_override is not None:
        kwh, reason = usage_override, None
    else:
        kwh, reason = _yearly_usage(usage, billing_days, warnings)
    if kwh is None:
        result["reason"] = reason
        return _finish(result, warnings)
    result["usage_kwh"] = str(kwh)

    data = {"contracts": contracts}
    cards = verified_cards(data, "variable", energy) + verified_cards(data, "fixed", energy)
    entry = terms.get(energy) or {}
    product = (entry.get("product") or {}).get("value") or ""
    current_card = _matching_card(cards, supplier, product)

    current = None
    if entry.get("price"):
        price = _incl_vat(entry["price"], warnings)
        fee = _yearly_fee(entry.get("standing_charge"), billing_days, warnings)
        if not entry.get("standing_charge"):
            warnings.append("fee_not_found")
        current = {"price_source": "invoice", "price_per_kwh_eur": price, "fee": fee,
                   "evidence": _evidence(entry.get("product"), entry["price"], entry.get("standing_charge"))}
    elif current_card:
        chosen = (entry.get("product") or {}).get("source") == _ANSWERS_SOURCE
        warnings.append("price_from_chosen_product" if chosen else "price_from_tariff_card")
        current = {"price_source": "tariff_card", "price_per_kwh_eur": Decimal(str(current_card["price_per_kwh_eur"])),
                   "fee": _card_fee(current_card), "evidence": _evidence(entry.get("product"))}

    offers = sorted((_offer(card, kwh) for card in cards), key=lambda offer: offer["_yearly"])
    for offer in offers:
        offer["is_current"] = current_card is not None and offer["label"] == card_label(current_card)
    others = [offer for offer in offers if not offer["is_current"]]
    result["offers"] = offers
    result["best"] = others[0] if others else None
    # The cheapest of each kind, so a variable contract can be weighed against a (possibly multi-year) fixed one.
    result["best_by_type"] = {
        kind: next((offer for offer in others if offer["contract_type"] == kind), None) for kind in ("variable", "fixed")
    }

    if current is None:
        result["status"], result["reason"] = "offers_only", "no_current_price"
        return _finish(result, warnings)
    yearly = kwh * current["price_per_kwh_eur"] + current["fee"]
    result["current"] = {
        "supplier": supplier or None,
        "product": product or None,
        "price_per_kwh_eur": str(current["price_per_kwh_eur"].quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)),
        "fixed_fee_eur_per_year": _money(current["fee"]),
        "yearly_eur": _money(yearly),
        "price_source": current["price_source"],
        "evidence": current["evidence"],
    }
    if result["best"] is None:  # the visitor's own card is the only one: nothing cheaper to show
        result["status"] = "already_cheapest"
        return _finish(result, warnings)
    saving = yearly - result["best"]["_yearly"]
    if saving <= 0:
        result["status"] = "already_cheapest"
        return _finish(result, warnings)
    result["status"] = "compared"
    result["saving_eur_per_year"] = _money(saving)
    result["saving_eur_per_month"] = _money(saving / 12)
    result["_saving"] = saving
    return _finish(result, warnings)


def _finish(result: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    if result["status"] is None:
        result["status"] = "not_comparable"
    result["message"] = MESSAGES[result["reason"] or result["status"]]
    result["warnings"] = [{"code": code, "message": WARNINGS[code]} for code in dict.fromkeys(warnings)]
    return result


def _v_check(terms: dict[str, Any], energies: dict[str, Any], region: str | None) -> dict[str, Any] | None:
    """The official check next to Karen's: the V-check code from the bill and the yearly kWh to type in the
    V-test, which otherwise compares on an average household. Flanders only."""
    found = terms.get("v_check")
    if not found or region not in (None, "flanders"):
        return None
    only = next(iter(energies)) if len(energies) == 1 else None  # a code on a one-energy bill is that energy's
    return {
        "url": next((link for link in found["links"] if "v-check" in link.lower()), VCHECK_URL),
        "codes": [
            {"code": code["value"], "energy": code.get("energy") or only, "page": code["page"],
             "usage_kwh": (energies.get(code.get("energy") or only or "") or {}).get("usage_kwh")}
            for code in found["codes"]
        ],
    }


def compare_contract(
    facts: dict[str, Any], terms: dict[str, Any], contracts: dict[str, Any], *,
    usage_kwh: dict[str, int | str | Decimal] | None = None,
) -> dict[str, Any]:
    """Compare the contract on an analysed bill with the verified tariff cards.

    `facts` are invoice_analysis facts, `terms` come from read_contract_terms, `contracts` is market.json's
    `contracts` (or local_contracts()). `usage_kwh` overrides the yearly kWh per energy, e.g. typed by the user.
    """
    usage_kwh = usage_kwh or {}
    postcode = terms.get("postcode")
    region = postcode.get("region") if postcode else None
    supplier = str((facts.get("supplier") or {}).get("value") or "")
    billing_days = _billing_days(facts)
    energies: dict[str, Any] = {}
    for energy, key in (("electricity", "electricity_kwh"), ("gas", "gas_kwh")):
        override = Decimal(str(usage_kwh[energy])) if energy in usage_kwh else None
        if not facts.get(key) and override is None and not terms.get(energy):
            continue
        energies[energy] = _compare_energy(energy, terms, facts.get(key), supplier, contracts, region, billing_days, override)

    compared = [entry for entry in energies.values() if entry["status"] == "compared"]
    total = None
    if compared:
        saving = sum((entry.pop("_saving") for entry in compared), Decimal("0"))
        total = {"saving_eur_per_year": _money(saving), "saving_eur_per_month": _money(saving / 12)}
    for entry in energies.values():
        entry.pop("_saving", None)
        for offer in entry["offers"]:
            offer.pop("_yearly", None)
        for offer in (entry["best"], *entry["best_by_type"].values()):
            if offer:
                offer.pop("_yearly", None)
    return {
        "v_check": _v_check(terms, energies, region),
        "tariffs_as_of": contracts.get("as_of"),
        "postcode": postcode["value"] if postcode else None,
        "region": region,
        "energies": energies,
        "total": total,
        "excluded": "Netkosten, heffingen en btw op netkosten zijn voor elke leverancier in je regio gelijk en zitten niet in deze bedragen.",
    }


def check_invoice_contract(
    chunks: list[KnowledgeChunk], contracts: dict[str, Any] | None = None, *,
    usage_kwh: dict[str, int | str | Decimal] | None = None, facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read the bill and compare its contract: the whole contract check in one call. `facts` are the bill's
    invoice_analysis facts when they were read already (an upload session keeps them)."""
    facts = facts if facts is not None else analyze_invoice_chunks(chunks)["facts"]
    energies = tuple(energy for energy, key in (("electricity", "electricity_kwh"), ("gas", "gas_kwh")) if facts.get(key))
    terms = read_contract_terms(chunks, energies or ("electricity", "gas"))
    result = compare_contract(facts, terms, contracts if contracts is not None else local_contracts(), usage_kwh=usage_kwh)
    result["based_on"] = "invoice"
    return result


# --- without a bill: three questions ----------------------------------------------------------------


def _answer_fact(value: Any, field: str, **extra: Any) -> dict[str, Any]:
    return {"value": value, "page": 0, "source": _ANSWERS_SOURCE, "excerpt": field, "field": field, **extra}


def contract_options(contracts: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    """The verified products a visitor can pick as "my contract", per energy, by supplier."""
    data = {"contracts": contracts}
    options = {}
    for energy in ("electricity", "gas"):
        cards = verified_cards(data, "variable", energy) + verified_cards(data, "fixed", energy)
        options[energy] = sorted(
            ({"supplier": card["name"], "product": str(card.get("product_name") or ""), "label": card_label(card),
              "contract_type": card.get("contract_type", "variable")} for card in cards),
            key=lambda option: (option["supplier"].casefold(), option["label"].casefold()),
        )
    return options


def compare_answers(
    contracts: dict[str, Any], *, postcode: str | None, meter: str | None,
    energies: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """The contract check from answers instead of a bill: postcode, meter, and per energy the yearly kWh and the
    product the visitor has now ("supplier" and "product" as contract_options lists them; both empty when unknown).
    The current price is the product's tariff card; without one the offers are shown without a saving."""
    region = region_for_postcode(postcode) if postcode else None
    terms: dict[str, Any] = {
        "postcode": _answer_fact(postcode, f"Postcode: {postcode}", region=region, delivery=True) if postcode else None,
        "meter": _answer_fact(meter, f"Meter: {meter}") if meter in ("single", "day_night", "exclusive_night") else None,
    }
    facts: dict[str, Any] = {"supplier": None}
    usage_kwh: dict[str, int | str | Decimal] = {}
    supplier_names = []
    for energy in ("electricity", "gas"):
        answer = energies.get(energy)
        if not answer:
            terms[energy] = None
            continue
        product = str(answer.get("product") or "").strip()
        supplier = str(answer.get("supplier") or "").strip()
        if supplier:
            supplier_names.append(supplier)
        terms[energy] = {
            "product": _answer_fact(product, f"Product: {supplier} {product}".strip()) if product else None,
            "price": None, "day_price": None, "night_price": None, "standing_charge": None,
        }
        if answer.get("usage_kwh") not in (None, ""):
            usage_kwh[energy] = answer["usage_kwh"]
    if supplier_names:
        facts["supplier"] = {"value": " / ".join(dict.fromkeys(supplier_names))}
    result = compare_contract(facts, terms, contracts, usage_kwh=usage_kwh)
    result["based_on"] = "answers"
    return result
