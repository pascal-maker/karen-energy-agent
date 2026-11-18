"""The contract on an energy bill: product, energy price per kWh, fixed fee, meter and delivery postcode.

Read with rules from the same text chunks as invoice_analysis; no model is called. Every value carries the
page and passage it was read from, like the other invoice facts. PDF text arrives as one running line per
page, so each value is judged by the words just before it: "Energieprijs 3.500 kWh x 0,2100 €/kWh" is the
energy price, "Distributie 3.500 kWh x 0,0950 €/kWh" is a network cost and is left out.

Prices are kept as printed, with `vat` saying whether the bill calls them incl. or excl. btw (None: not
said). contract_savings turns them into the incl.-btw prices the tariff cards use.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from .invoice_analysis import _decimal_value, _fact
from .knowledge import KnowledgeChunk

Energy = str  # "electricity" or "gas"

_NUMBER = r"(\d+(?:[.,]\d+)?)"
# "0,2100 €/kWh", "21,00 c€/kWh", "0,0950 EUR/kWh", "9,5 cent/kWh", "€ 0,21/kWh"
PRICE_RE = re.compile(
    rf"(?i)(?:€\s*{_NUMBER}\s*/\s*kwh|{_NUMBER}\s*(c€|ct|cent|eurocent|€|eur|euro)\s*(?:per|/)\s*kwh)"
)
# Words that make a per-kWh price a network cost, a levy or a payment for injection, not the energy price.
_NOT_ENERGY_PRICE = re.compile(
    r"(?i)\b(?:distributie\w*|transmissie\w*|transport\w*|net(?:tarie\w*|kost\w*|beheer\w*|vergoeding)|heffing\w*|accijns\w*|"
    r"bijdrage\w*|capaciteit\w*|injectie\w*|teruglever\w*|groene\s+stroom|wkk|certificat\w*|federale|energiefonds|"
    r"openbare\s+dienst|odv|distribution|accise\w*|cotisation\w*|surcharge\w*|prélèvement\w*|réseau|injection)"
)
_ENERGY_PRICE_LABEL = re.compile(r"(?i)\b(?:energie(?:prijs|kost\w*|component)|prix\s+de\s+l'énergie|énergie|verbruik|consommation|prijs)\b")
_ELECTRICITY_WORDS = r"\b(?:elektriciteit\w*|stroom\w*|électricité)\b"
_GAS_WORDS = r"\b(?:aardgas\w*|gas\w*|gaz)\b"
_ELECTRICITY = re.compile(_ELECTRICITY_WORDS, re.IGNORECASE)
_GAS = re.compile(_GAS_WORDS, re.IGNORECASE)
_ENERGY_WORD = re.compile(f"{_ELECTRICITY_WORDS}|{_GAS_WORDS}", re.IGNORECASE)
_DAY = re.compile(r"\b(?:[Dd]ag|[Pp]iek|[Jj]our|[Hh]eures?\s+pleines)\b")
_NIGHT = re.compile(r"\b(?:[Nn]acht|[Dd]al|[Nn]uit|[Hh]eures?\s+creuses)\b")
_EXCL_VAT = re.compile(r"(?i)(?:excl(?:\.|usief)?\s*(?:btw|tva)|zonder\s+btw|hors\s+tva|\bhtva\b)")
_INCL_VAT = re.compile(r"(?i)(?:incl(?:\.|usief)?\s*(?:btw|tva)|met\s+btw|\btvac\b|\bttc\b|tva\s+comprise)")

PRODUCT_LABEL_RE = re.compile(r"(?i)\b(?:productnaam|uw\s+product|product|tariefformule|formule|votre\s+produit|produit)\s*:\s*")
# A product name ends where the next "Label:" starts or at one of these words.
_PRODUCT_STOP = {
    "metertype", "meter", "energieprijs", "vaste", "leveringsadres", "factuurperiode", "periode", "klant", "adres",
    "ean", "contract", "duur", "looptijd", "type", "prijs", "verbruik", "type de compteur", "compteur", "période",
}
CONTRACT_TYPE_RE = re.compile(r"(?i)\b(?:(vast\w*|fixed|fix|fixe)|(variabel\w*|variable|flex|dynamisch\w*|dynamique))\b")

STANDING_LABEL_RE = re.compile(
    r"(?i)\b(?:vaste\s+vergoeding|vaste\s+kost(?:en)?|abonnement(?:skost\w*)?|platformkost\w*|redevance(?:\s+fixe)?)\b"
)
# "€ 40,00" first; "40,00 €" only with decimals, so "31/12/2025 €" is never read as an amount.
_STANDING_AMOUNT_RE = re.compile(r"€\s*(\d{1,3}(?:[ .]\d{3})*(?:[,.]\d{1,2})?)(?![\d/])")
_STANDING_AMOUNT_AFTER_RE = re.compile(r"(?<![\d/.,])(\d{1,3}(?:[ .]\d{3})*[,.]\d{2})\s*(?:€|eur\b|euro\b)", re.IGNORECASE)
_STANDING_UNIT_RE = re.compile(r"(?i)^\s*(?:/|per|par)\s*(jaar|maand|an|année|mois)\b")
_DATE_RANGE_RE = re.compile(r"(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})\s*(?:-|–|tot|t/m|au)\s*(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})")

SINGLE_METER_RE = re.compile(r"(?i)\b(?:enkelvoudige?\s+meter|enkelvoudig\s+tarief|normale\s+meter|monohoraire|compteur\s+simple)\b")
DAY_NIGHT_RE = re.compile(r"(?i)\b(?:tweevoudige?\s+meter|dag[\s/-]*(?:en\s+)?nacht\w*|dubbel\s+tarief|bihoraire|jour\s*/\s*nuit)\b")
# Consumption printed per register ("Elektriciteit dag 1.850 kWh", "Elektriciteit nacht 1.200 kWh") is a day/night meter.
DAY_REGISTER_RE = re.compile(r"(?i)\b(?:elektriciteit|afname|verbruik|électricité)\s+(?:dag|jour)\s+\d")
NIGHT_REGISTER_RE = re.compile(r"(?i)\b(?:elektriciteit|afname|verbruik|électricité)\s+(?:nacht|nuit)\s+\d")
EXCLUSIVE_NIGHT_RE = re.compile(r"(?i)\b(?:exclusie(?:f|ve)\s+nacht\w*|uitsluitend\s+nacht|exclusif\s+nuit)\b")

_DELIVERY_LABEL = re.compile(
    r"(?i)\b(?:leveringsadres|verbruiksadres|afnameadres|afnamepunt|adres\s+van\s+levering|adresse\s+de\s+(?:fourniture|consommation)|point\s+de\s+fourniture)\b"
)
_ADDRESS_LABEL = re.compile(r"(?i)\b(?:adres|adresse)\s*:")
POSTCODE_RE = re.compile(r"\b([1-9]\d{3})\s+[A-ZÀ-Ý]")


def region_for_postcode(postcode: str | None) -> str | None:
    """Belgian region for a postcode: brussels 1000-1299, wallonia 1300-1499 and 4000-7999, else flanders."""
    if not postcode or not postcode.isdigit() or len(postcode) != 4:
        return None
    number = int(postcode)
    if number < 1000:
        return None
    if number < 1300:
        return "brussels"
    if number < 1500 or 4000 <= number < 8000:
        return "wallonia"
    return "flanders"


def _energy_near(text: str, start: int, end: int, lookback: int = 260) -> Energy | None:
    """The energy a value belongs to: an energy word in its own label, else the nearest heading before it."""
    window = text[max(0, start - lookback):end]
    last = None
    for match in _ENERGY_WORD.finditer(window):
        last = match
    if last is None:
        return None
    return "gas" if _GAS.fullmatch(last.group(0)) else "electricity"


def _vat_near(text: str, start: int, end: int) -> str | None:
    """incl or excl when the price's own label, the words after it or a heading shortly before say so."""
    after = text[end:end + 40]
    for window in (text[max(0, start - 70):start] + after, text[max(0, start - 320):start]):
        excl = [m.start() for m in _EXCL_VAT.finditer(window)]
        incl = [m.start() for m in _INCL_VAT.finditer(window)]
        if excl or incl:
            return "excl" if max(excl, default=-1) > max(incl, default=-1) else "incl"
    return None


_PREVIOUS_AMOUNT = re.compile(r"€\s*\d[\d.,]*|\d[\d.,]*\s*€(?!\s*/)")


def _label_before(text: str, start: int, previous_end: int) -> str:
    """This value's own label: the words since the previous value or euro amount, at most 90 characters."""
    window_start = max(previous_end, start - 90)
    window = text[window_start:start]
    amounts = list(_PREVIOUS_AMOUNT.finditer(window))
    return window[amounts[-1].end():] if amounts else window


def _period(label: str) -> str | None:
    """day or night from the words after the price label itself ("Energieprijs dag 1.800 kWh x")."""
    labels = list(_ENERGY_PRICE_LABEL.finditer(label))
    own = label[labels[-1].end():] if labels else label[-35:]
    words = list(re.finditer(f"{_DAY.pattern}|{_NIGHT.pattern}", own))
    if not words:
        return None
    return "night" if _NIGHT.fullmatch(words[-1].group(0)) else "day"


def _prices(chunk: KnowledgeChunk) -> list[dict[str, Any]]:
    text = chunk.text
    found = []
    previous_end = 0
    for match in PRICE_RE.finditer(text):
        label = _label_before(text, match.start(), previous_end)
        previous_end = match.end()
        if _NOT_ENERGY_PRICE.search(label):
            continue
        # A line without "energieprijs" or "verbruik" is accepted only when nothing marks it as another cost.
        raw, unit = (match.group(1), "€") if match.group(1) else (match.group(2), match.group(3).lower())
        value = _decimal_value(raw)
        if value is None:
            continue
        eur = value / 100 if unit in ("c€", "ct", "cent", "eurocent") else value
        if not Decimal("0.01") <= eur <= Decimal("2"):
            continue
        period = _period(label)
        excerpt = text[max(0, match.start() - 90):match.end() + 40]
        fact = _fact(eur, chunk, excerpt, label.strip()[-60:] + " " + match.group(0))
        fact.update(
            energy=_energy_near(text, match.start() - len(label), match.start()),
            period=period,
            vat=_vat_near(text, match.start(), match.end()),
            labelled=bool(_ENERGY_PRICE_LABEL.search(label)),
        )
        found.append(fact)
    return found


def _product_name(text: str, start: int) -> str:
    words = []
    for match in re.finditer(r"[^\s:]+:?", text[start:start + 120]):
        word = match.group(0)
        bare = word.rstrip(":").rstrip(".,;·")
        if word.endswith(":") or bare.casefold() in _PRODUCT_STOP or len(words) == 6:
            break
        if not bare:
            break
        words.append(bare)
        if word.endswith((".", ";", "·")) or word.endswith(","):
            break
    return " ".join(words).strip(" -–—")


def _products(chunk: KnowledgeChunk) -> list[dict[str, Any]]:
    found = []
    for match in PRODUCT_LABEL_RE.finditer(chunk.text):
        name = _product_name(chunk.text, match.end())
        if len(name) < 3:
            continue
        energy = "gas" if _GAS.search(name) else "electricity" if _ELECTRICITY.search(name) else None
        fact = _fact(name, chunk, chunk.text[max(0, match.start() - 60):match.end() + 80], match.group(0) + name)
        fact["energy"] = energy or _energy_near(chunk.text, match.start(), match.start(), lookback=60)
        kind = CONTRACT_TYPE_RE.search(name)
        fact["contract_type"] = None if kind is None else "fixed" if kind.group(1) else "variable"
        found.append(fact)
    return found


def _days(day: str, month: str, year: str) -> int:
    from datetime import date

    full = int(year) + (2000 if len(year) == 2 else 0)
    return date(full, int(month), int(day)).toordinal()


def _standing_charges(chunk: KnowledgeChunk) -> list[dict[str, Any]]:
    text = chunk.text
    found = []
    for label in STANDING_LABEL_RE.finditer(text):
        window = text[label.end():label.end() + 90]
        amount = _STANDING_AMOUNT_RE.search(window) or _STANDING_AMOUNT_AFTER_RE.search(window)
        if amount is None:
            continue
        value = _decimal_value(amount.group(1))
        if value is None or value <= 0:
            continue
        unit_match = _STANDING_UNIT_RE.search(window[amount.end():])
        unit = None
        if unit_match:
            unit = "per_month" if unit_match.group(1).lower() in ("maand", "mois") else "per_year"
        days = None
        span = _DATE_RANGE_RE.search(window[:amount.start()])
        if span:
            try:
                days = _days(*span.group(4, 5, 6)) - _days(*span.group(1, 2, 3)) + 1
            except ValueError:
                days = None
        start, end = label.start(), label.end() + amount.end() + (unit_match.end() if unit_match else 0)
        fact = _fact(value, chunk, text[max(0, start - 40):end + 30], text[start:end])
        fact.update(
            unit=unit,
            period_days=days,
            energy=_energy_near(text, start, start),
            vat=_vat_near(text, start, end),
        )
        found.append(fact)
    return found


def _meter(chunks: list[KnowledgeChunk], prices: list[dict[str, Any]]) -> dict[str, Any] | None:
    for pattern, kind in ((EXCLUSIVE_NIGHT_RE, "exclusive_night"), (DAY_NIGHT_RE, "day_night"), (SINGLE_METER_RE, "single")):
        for chunk in chunks:
            match = pattern.search(chunk.text)
            if match:
                return _fact(kind, chunk, chunk.text[max(0, match.start() - 60):match.end() + 60], match.group(0))
    for chunk in chunks:
        day = DAY_REGISTER_RE.search(chunk.text)
        if day and NIGHT_REGISTER_RE.search(chunk.text):
            return _fact("day_night", chunk, chunk.text[max(0, day.start() - 30):day.end() + 90], day.group(0))
    day_night = [price for price in prices if price["energy"] != "gas" and price["period"]]
    if {price["period"] for price in day_night} >= {"day", "night"}:
        first = day_night[0]
        return {**first, "value": "day_night"}
    return None


def _postcode(chunks: list[KnowledgeChunk]) -> dict[str, Any] | None:
    for label_re in (_DELIVERY_LABEL, _ADDRESS_LABEL):
        for chunk in chunks:
            for label in label_re.finditer(chunk.text):
                match = POSTCODE_RE.search(chunk.text[label.end():label.end() + 90])
                if match:
                    start = label.end() + match.start()
                    fact = _fact(match.group(1), chunk, chunk.text[max(0, label.start() - 10):start + 40],
                                 chunk.text[label.start():start + len(match.group(0)) + 20])
                    fact["region"] = region_for_postcode(match.group(1))
                    fact["delivery"] = label_re is _DELIVERY_LABEL
                    return fact
    return None


def _first(facts: list[dict[str, Any]], energy: Energy, *, single_energy: bool, **wanted: Any) -> dict[str, Any] | None:
    for fact in facts:
        if any(fact.get(key) != value for key, value in wanted.items()):
            continue
        if fact.get("energy") == energy or (fact.get("energy") is None and single_energy):
            return fact
    return None


def read_contract_terms(chunks: list[KnowledgeChunk], energies: tuple[Energy, ...] = ("electricity", "gas")) -> dict[str, Any]:
    """The contract terms on the bill, per energy. `energies` are the energies the bill has consumption for;
    a value without an energy word belongs to the bill's only energy, and to none when there are two."""
    ordered = sorted(chunks, key=lambda chunk: chunk.page)
    seen: set[str] = set()
    unique = []
    for chunk in ordered:  # overlapping chunks repeat text; read every passage once
        key = chunk.text[:200]
        if key not in seen:
            seen.add(key)
            unique.append(chunk)
    prices = [price for chunk in unique for price in _prices(chunk)]
    products = [product for chunk in unique for product in _products(chunk)]
    standing = [charge for chunk in unique for charge in _standing_charges(chunk)]
    single_energy = len(energies) == 1
    terms: dict[str, Any] = {"postcode": _postcode(unique), "meter": _meter(unique, prices)}
    for energy in ("electricity", "gas"):
        if energy not in energies:
            terms[energy] = None
            continue
        alone = single_energy and energy in energies
        energy_prices = [price for price in prices if price["energy"] == energy or (price["energy"] is None and alone)]
        # A labelled energy price wins over an unlabelled one; day and night are kept apart.
        ranked = sorted(energy_prices, key=lambda price: not price["labelled"])
        entry = {
            "product": _first(products, energy, single_energy=alone),
            "price": next((price for price in ranked if price["period"] is None), None),
            "day_price": next((price for price in ranked if price["period"] == "day"), None),
            "night_price": next((price for price in ranked if price["period"] == "night"), None),
            "standing_charge": _first(standing, energy, single_energy=alone),
        }
        if entry["price"] is None and energy == "gas":
            entry["price"] = entry["day_price"]  # gas has no day/night; a "dag" word nearby is noise
        terms[energy] = entry
    return terms
