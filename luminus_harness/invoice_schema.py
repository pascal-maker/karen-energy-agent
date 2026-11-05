"""The analyzed invoice as one versioned, validated object, and the summary Karen gives from it.

Built from the facts invoice_analysis reads at upload; nothing here reads the PDF again or calls a model.
Money is an exact decimal string in euro ("234.56"), a missing or unreliable value is null, and every
value read from the invoice carries the file, page and passage it came from. Calculated values are kept
apart under `calculated`.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

SCHEMA_VERSION = "1.0"

DocumentType = Literal["annual_settlement", "final_invoice", "advance_invoice", "unknown"]
Energy = Literal["electricity", "gas"]
Frequency = Literal["monthly", "bimonthly", "quarterly", "yearly"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(_Strict):
    """Where a value was read: file, PDF page and the passage around it; `read_by` says rules or (opt-in) model."""

    source: str
    page: int
    excerpt: str
    read_by: Literal["rules", "language_model"] = "rules"


class TextValue(Evidence):
    value: str


class Period(Evidence):
    printed: str
    start: str | None  # ISO date, null when the printed date cannot be read as one
    end: str | None


class Quantity(Evidence):
    value: str  # exact decimal string
    unit: Literal["kWh", "m³"]


class Money(Evidence):
    # Exact decimal string. amount_due and credit are never negative (the field says which way the money
    # goes); total_energy_cost and advances_paid keep the sign printed on the bill.
    value_eur: str


class DueDate(Evidence):
    printed: str
    date: str | None  # ISO date when the printed date can be read as one
    direct_debit: bool


class NewAdvance(Evidence):
    energy: Energy | None
    frequency: Frequency | None
    amount_eur: str
    previous_amount_eur: str | None


class AdvanceLine(Evidence):
    energy: Energy
    period: str
    amount_eur: str
    vat_included: bool | None  # null when the bill does not say whether the lines include btw


class Consumption(_Strict):
    electricity: Quantity | None = None
    gas: Quantity | None = None


class Amounts(_Strict):
    total_energy_cost: Money | None = None
    advances_paid: Money | None = None
    amount_due: Money | None = None  # to pay; "0.00" when the bill says nothing is left to pay
    credit: Money | None = None  # money back


class Calculated(_Strict):
    """Worked out by Karen, not printed: total energy cost minus advances paid."""

    costs_minus_advances_eur: str | None = None
    inputs: list[Literal["total_energy_cost", "advances_paid"]] = []


class Invoice(_Strict):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    source: str
    document_type: DocumentType
    document_type_evidence: Evidence | None = None
    supplier: TextValue | None = None
    customer: TextValue | None = None
    billing_period: Period | None = None
    consumption: Consumption = Consumption()
    amounts: Amounts = Amounts()
    due_date: DueDate | None = None
    new_advances: list[NewAdvance] = []
    advance_lines: list[AdvanceLine] = []
    calculated: Calculated = Calculated()
    warnings: list[str] = []


def _evidence(fact: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": fact["source"],
        "page": fact["page"],
        "excerpt": fact.get("excerpt", ""),
        "read_by": "language_model" if fact.get("read_by") == "model" else "rules",
    }


def _decimal(value: Decimal) -> str:
    return str(value)


def _money(value: Decimal) -> str:
    return str(abs(value).quantize(Decimal("0.01")))


def _signed_money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))


_FRENCH_MONTHS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre")
_DUTCH_MONTHS = ("januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus", "september", "oktober", "november", "december")


def _iso_date(printed: str) -> str | None:
    """'31/12/2025', '31.12.25', '7 novembre 2018' -> ISO; anything else -> None."""
    match = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})[./-](\d{2}|\d{4})", printed.strip())
    if match:
        day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
    else:
        words = re.fullmatch(r"(?i)(\d{1,2})(?:er)?\s+(\w+)\s+(\d{4})", printed.strip())
        if not words:
            return None
        name = words.group(2).lower()
        months = _DUTCH_MONTHS if name in _DUTCH_MONTHS else _FRENCH_MONTHS
        if name not in months:
            return None
        day, month, year = int(words.group(1)), months.index(name) + 1, int(words.group(3))
    year = year + 2000 if year < 100 else year
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def _document_type(fact: dict[str, Any] | None) -> DocumentType:
    printed = str((fact or {}).get("value") or "").lower()
    if not printed:
        return "unknown"
    if "voorschot" in printed or "acompte" in printed:
        return "advance_invoice"
    if "slot" in printed or "eind" in printed:
        return "final_invoice"
    return "annual_settlement"


_ENERGIES = {"elektriciteit": "electricity", "electricité": "electricity", "électricité": "electricity", "gas": "gas", "aardgas": "gas"}
_FREQUENCIES = {
    "maandelijks": "monthly", "maandelijkse": "monthly", "mensuel": "monthly", "mensuelle": "monthly",
    "tweemaandelijks": "bimonthly", "tweemaandelijkse": "bimonthly",
    "driemaandelijks": "quarterly", "per kwartaal": "quarterly", "trimestriel": "quarterly",
    "jaarlijks": "yearly", "annuel": "yearly",
}


def build_invoice(facts: dict[str, Any], source: str) -> Invoice:
    """The facts read at upload, as one validated object."""
    data: dict[str, Any] = {"source": source, "document_type": _document_type(facts.get("invoice_type"))}
    if facts.get("invoice_type"):
        data["document_type_evidence"] = _evidence(facts["invoice_type"])
    for key in ("supplier", "customer"):
        if facts.get(key):
            data[key] = {**_evidence(facts[key]), "value": str(facts[key]["value"])}

    period = facts.get("billing_period")
    if period:
        printed = str(period["value"])
        parts = re.split(r"\s+(?:tot(?: en met)?|t/m|au|[-–])\s+", printed, maxsplit=1)
        data["billing_period"] = {
            **_evidence(period), "printed": printed,
            "start": _iso_date(parts[0]), "end": _iso_date(parts[1]) if len(parts) == 2 else None,
        }

    consumption: dict[str, Any] = {}
    for key, energy in (("electricity_kwh", "electricity"), ("gas_kwh", "gas")):
        fact = facts.get(key)
        if fact and isinstance(fact.get("value"), Decimal):
            unit = "m³" if fact.get("unit") in ("m3", "m³") else "kWh"
            consumption[energy] = {**_evidence(fact), "value": _decimal(fact["value"]), "unit": unit}
    data["consumption"] = consumption

    amounts: dict[str, Any] = {}
    for key in ("total_energy_cost", "advances_paid"):
        if facts.get(key):
            amounts[key] = {**_evidence(facts[key]), "value_eur": _signed_money(facts[key]["value"])}
    due = facts.get("amount_due")
    if due:
        amounts["credit" if due["value"] < 0 else "amount_due"] = {**_evidence(due), "value_eur": _money(due["value"])}
    data["amounts"] = amounts

    date = facts.get("due_date")
    if date:
        printed = str(date["value"])
        data["due_date"] = {**_evidence(date), "printed": printed, "date": _iso_date(printed), "direct_debit": bool(date.get("direct_debit"))}

    advance = facts.get("new_advance")
    if advance:
        data["new_advances"] = [
            {
                **_evidence(advance),
                "energy": _ENERGIES.get(str(row.get("energy") or "").lower()),
                "frequency": _FREQUENCIES.get(str(row.get("frequency") or "").lower()),
                "amount_eur": _money(row["new"]),
                "previous_amount_eur": _money(row["old"]) if row.get("old") is not None else None,
            }
            for row in advance["value"] if row.get("new") is not None
        ]
    lines = facts.get("advance_lines")
    if lines:
        vat_included = {"incl. btw": True, "excl. btw": False}.get(lines.get("vat") or "")
        data["advance_lines"] = [
            {
                **_evidence(lines), "energy": _ENERGIES[row["energy"]], "period": str(row["period"]),
                "amount_eur": _money(row["amount"]), "vat_included": vat_included,
            }
            for row in lines["value"] if row.get("amount") is not None and row.get("energy") in _ENERGIES
        ]

    difference = facts.get("calculated_difference")
    if difference:
        data["calculated"] = {
            "costs_minus_advances_eur": str(difference["value"].quantize(Decimal("0.01"))),
            "inputs": ["total_energy_cost", "advances_paid"],
        }

    warnings = []
    if data["document_type"] != "advance_invoice":
        for key in ("total_energy_cost", "advances_paid"):
            if key not in amounts:
                warnings.append(f"missing:{key}")
        if not consumption:
            warnings.append("missing:consumption")
    if due is None:
        warnings.append("missing:amount_due")
    elif difference is not None and abs(difference["value"] - due["value"]) > Decimal("0.01"):
        warnings.append("calculated_difference_differs_from_amount_due")
    data["warnings"] = warnings
    return Invoice.model_validate(data)


# --- Summary requests -----------------------------------------------------------------------------

_SUMMARY_WORDS = ("samenvatting", "samenvatten", "samenvat", "summary", "summarize", "summarise", "resume", "resumer", "resumez")
_SUMMARY_PHRASE = re.compile(
    r"\bvat\b.*\bsamen\b|\bkort\s+samen|\bin\s+het\s+kort\b|\bin\s+een\s+notendop\b|\ben\s+resume\b|\bsamenvatting\b"
)


def _plain(text: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(char))


def is_summary_request(question: str) -> bool:
    """'Vat deze factuur samen', 'wat is de samenvatting', and typos such as 'smaenvatting' or 'samenvattng'."""
    plain = _plain(question)
    if _SUMMARY_PHRASE.search(plain):
        return True
    words = re.findall(r"[a-z]+", plain)
    return any(len(word) >= 6 and difflib.get_close_matches(word, _SUMMARY_WORDS, n=1, cutoff=0.8) for word in words)


def _euro(value: str) -> str:
    return f"€ {Decimal(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _page(item: Evidence) -> str:
    return f" (PDF-pagina {item.page})"


_DUTCH_TYPES = {
    "annual_settlement": "jaarafrekening", "final_invoice": "slotfactuur", "advance_invoice": "voorschotfactuur", "unknown": "factuur",
}
_DUTCH_FREQUENCIES = {"monthly": "per maand", "bimonthly": "per twee maanden", "quarterly": "per kwartaal", "yearly": "per jaar"}
_DUTCH_ENERGIES = {"electricity": "elektriciteit", "gas": "gas"}


def summarize_invoice(invoice: Invoice) -> dict[str, Any]:
    """A fixed-format summary of what was read, each line with its page; what was not found is said, never guessed."""
    lines = [f"Samenvatting van je {'factuur' if invoice.document_type == 'unknown' else _DUTCH_TYPES[invoice.document_type]}"
             + (f" van {invoice.supplier.value}" if invoice.supplier else "") + ":"]
    evidence: list[Evidence] = []

    def add(text: str, item: Evidence) -> None:
        lines.append(f"- {text}{_page(item)}")
        evidence.append(item)

    if invoice.billing_period:
        add(f"Periode: {invoice.billing_period.printed}", invoice.billing_period)
    for energy in ("electricity", "gas"):
        quantity = getattr(invoice.consumption, energy)
        if quantity:
            add(f"Verbruik {_DUTCH_ENERGIES[energy]}: {quantity.value} {quantity.unit}", quantity)
    for line in invoice.advance_lines:
        vat = {True: " incl. btw", False: " excl. btw", None: ""}[line.vat_included]
        add(f"Voorschot {_DUTCH_ENERGIES[line.energy]} {line.period}: {_euro(line.amount_eur)}{vat}".replace("  ", " "), line)
    amounts = invoice.amounts
    if amounts.total_energy_cost:
        add(f"Totale energiekosten: {_euro(amounts.total_energy_cost.value_eur)}", amounts.total_energy_cost)
    if amounts.advances_paid:
        add(f"Al aangerekende voorschotten: {_euro(amounts.advances_paid.value_eur)}", amounts.advances_paid)
    if amounts.credit:
        add(f"Je krijgt terug: {_euro(amounts.credit.value_eur)}", amounts.credit)
    elif amounts.amount_due and Decimal(amounts.amount_due.value_eur) == 0:
        add("Nog te betalen: niets", amounts.amount_due)
    elif amounts.amount_due:
        add(f"Nog te betalen: {_euro(amounts.amount_due.value_eur)}", amounts.amount_due)
    if invoice.due_date:
        how = "wordt via domiciliëring afgehouden op" if invoice.due_date.direct_debit else "betalen vóór"
        add(f"{how[0].upper()}{how[1:]} {invoice.due_date.printed}", invoice.due_date)
    for advance in invoice.new_advances:
        parts = [part for part in (
            _DUTCH_ENERGIES.get(advance.energy or ""), _euro(advance.amount_eur), _DUTCH_FREQUENCIES.get(advance.frequency or "")
        ) if part]
        add(f"Nieuw voorschot: {' '.join(parts)}", advance)

    missing = []
    if not amounts.amount_due and not amounts.credit:
        missing.append("het bedrag te betalen of terug te krijgen")
    if invoice.document_type != "advance_invoice":
        if not invoice.consumption.electricity and not invoice.consumption.gas:
            missing.append("het verbruik")
        if not invoice.billing_period:
            missing.append("de periode")
    if missing:
        lines.append(f"Niet duidelijk gevonden op de factuur: {', '.join(missing)}.")
    if invoice.calculated.costs_minus_advances_eur is not None and "calculated_difference_differs_from_amount_due" in invoice.warnings:
        lines.append(
            f"Let op: kosten min voorschotten is {_euro(str(abs(Decimal(invoice.calculated.costs_minus_advances_eur))))}, "
            "dat is niet het vermelde bedrag; er staan dus nog andere posten op de factuur."
        )

    sources: list[dict[str, str]] = []
    for item in evidence:
        source = {"title": item.source, "detail": f"PDF · pagina {item.page}", "excerpt": item.excerpt}
        if source not in sources:
            sources.append(source)
    if not evidence:
        lines = ["Ik kon op deze factuur geen leverancier, periode, verbruik of bedragen herkennen. "
                 "Vraag me gerust naar een specifiek onderdeel, dan zoek ik de passage op."]
    return {"answer": "\n".join(lines), "sources": sources}


# --- Explaining the invoice -----------------------------------------------------------------------

_MEANING_WORDS = ("betekent", "betekenen", "betekenis", "beteken", "mean", "means", "meaning", "signifie")
_TREND_WORDS = ("trend", "foresight", "forecast", "toekomst", "voorspel", "prognose", "verwacht", "vooruitzicht", "evolutie")
_TREND_PHRASE = re.compile(r"\b(?:volgend|vorig|komend)\w*\s+jaar\b")
# Words that point at the invoice or the answer so far, not at a subject of their own.
_GENERIC_WORDS = frozenset("""
    wat wats wa dit dat deze die het de een oke ok okay oki maar en me mij ik voor nu dan dus eigenlijk precies juist
    factuur rekening afrekening resultaten resultaat cijfers getallen bedragen alles allemaal hier hiervan daarvan
    zeggen zegt zeg wil kan kun je jij u geef eens nog of over is zijn al wel what does do this that these those
    results result invoice bill tell me qu que ca cela veut dire
""".split())
# Close to "betekent" but a different question ("hoe berekent Luminus …").
_NOT_MEANING = frozenset(("berekent", "berekend", "berekenen", "bekend"))


def _fuzzy(word: str, targets: tuple[str, ...] | frozenset[str], cutoff: float) -> bool:
    return word in targets or (len(word) >= 5 and bool(difflib.get_close_matches(word, list(targets), n=1, cutoff=cutoff)))


def _is_trend_request(plain: str) -> bool:
    return any(word in plain for word in _TREND_WORDS) or bool(_TREND_PHRASE.search(plain))


def is_explanation_request(question: str) -> bool:
    """'Wat betekent dit', 'wat beteknen die resulataten', or a question about trends and next year.

    A meaning word only counts when the rest of the question points at the invoice itself:
    'wat betekent capaciteitstarief' asks about a term and keeps the passage search."""
    plain = _plain(question)
    if _is_trend_request(plain):
        return True
    # Tokens with digits are typos ("mqq4 3at"), not subjects.
    words = [word for word in re.findall(r"[a-z0-9]+", plain) if not re.search(r"\d", word)]
    meaning = [word for word in words if word not in _NOT_MEANING and _fuzzy(word, _MEANING_WORDS, 0.8)]
    if not meaning:
        return False
    rest = [word for word in words if word not in meaning and len(word) > 3 and not _fuzzy(word, _GENERIC_WORDS, 0.85)]
    return not rest


def _covers_a_year(period: Period | None) -> bool:
    """A billing period of about a year (330 to 400 days), read from its printed start and end dates."""
    if period is None or not period.start or not period.end:
        return False
    from datetime import date

    days = (date.fromisoformat(period.end) - date.fromisoformat(period.start)).days + 1
    return 330 <= days <= 400


_PER_YEAR = {"monthly": 12, "bimonthly": 6, "quarterly": 4, "yearly": 1}


def explain_invoice(invoice: Invoice, question: str = "") -> dict[str, Any]:
    """What the bill means, from the values read on it; anything worked out is said to be Karen's calculation."""
    kind = "factuur" if invoice.document_type == "unknown" else _DUTCH_TYPES[invoice.document_type]
    lines = [f"Wat deze {kind} betekent:"]
    evidence: list[Evidence] = []

    def add(text: str, *items: Evidence | None) -> None:
        found = [item for item in items if item is not None]
        lines.append(f"- {text}{_page(found[0]) if found else ''}")
        evidence.extend(found)

    amounts = invoice.amounts
    if invoice.document_type == "advance_invoice":
        lines.append("- Dit is een vast bedrag vooraf, geen afrekening van je echte verbruik. Dat gebeurt pas op je jaarafrekening.")
        if amounts.amount_due:
            add(f"Je betaalt nu {_euro(amounts.amount_due.value_eur)}", amounts.amount_due)
    else:
        cost, paid = amounts.total_energy_cost, amounts.advances_paid
        differs = "calculated_difference_differs_from_amount_due" in invoice.warnings
        if cost and paid and not differs and (amounts.amount_due or amounts.credit):
            amount_text = f"je voorschotten ({_euro(paid.value_eur)})"
            cost_text = f"je werkelijke energiekosten ({_euro(cost.value_eur)})"
            if amounts.credit:
                add(f"Je krijgt {_euro(amounts.credit.value_eur)} terug, omdat {amount_text} hoger waren dan {cost_text}", amounts.credit, cost, paid)
            elif Decimal(amounts.amount_due.value_eur) == 0:
                add(f"Je voorschotten dekten je energiekosten precies: je betaalt niets bij en krijgt niets terug", amounts.amount_due, cost, paid)
            else:
                add(f"Je betaalt {_euro(amounts.amount_due.value_eur)} bij, omdat {amount_text} lager waren dan {cost_text}", amounts.amount_due, cost, paid)
        elif cost and paid and differs:
            add(
                f"Je energiekosten min je voorschotten is {_euro(invoice.calculated.costs_minus_advances_eur or '0')}, maar de factuur "
                "vermeldt een ander bedrag: er staan dus nog andere posten op. Kijk die na of vraag het je leverancier", cost, paid,
            )
    used = [
        f"{quantity.value} {quantity.unit} {_DUTCH_ENERGIES[energy]}"
        for energy in ("electricity", "gas") if (quantity := getattr(invoice.consumption, energy))
    ]
    if used:
        add(f"In deze periode verbruikte je {' en '.join(used)}", invoice.consumption.electricity or invoice.consumption.gas)

    advances = invoice.new_advances
    covered = {advance.energy for advance in advances}
    consumed = {energy for energy in ("electricity", "gas") if getattr(invoice.consumption, energy)}
    if advances and all(advance.frequency for advance in advances) and (covered == {None} and len(advances) == 1 or consumed <= covered):
        yearly = sum(Decimal(advance.amount_eur) * _PER_YEAR[advance.frequency] for advance in advances)
        text = f"Je nieuwe voorschot is {_euro(str(yearly))} per jaar"
        cost = amounts.total_energy_cost
        if cost and invoice.document_type != "advance_invoice" and not _covers_a_year(invoice.billing_period):
            # Twelve months of advances against six months of costs would promise money that isn't there.
            text += ". Deze factuur dekt geen volledig jaar, dus een schatting voor volgend jaar maak ik niet"
        elif cost and invoice.document_type != "advance_invoice":
            gap = yearly - Decimal(cost.value_eur)
            outcome = (f"krijg je volgend jaar ongeveer {_euro(str(gap))} terug" if gap > 0
                       else f"betaal je volgend jaar ongeveer {_euro(str(-gap))} bij" if gap < 0
                       else "betaal je volgend jaar ongeveer niets bij")
            text += f". Blijven je verbruik en de prijzen gelijk, dan {outcome}. Dat is een berekening van Karen, geen bedrag van de factuur"
        add(text, advances[0])

    if len(lines) == 1:
        return summarize_invoice(invoice)
    if _is_trend_request(_plain(question)):
        lines.append(
            "Trends kan ik uit één factuur niet halen: ze toont maar één periode. Daarvoor heb je de facturen van "
            "vorige jaren nodig, of je verbruiksgeschiedenis bij je netbeheerder (in Vlaanderen: Mijn Fluvius)."
        )
    sources: list[dict[str, str]] = []
    for item in evidence:
        source = {"title": item.source, "detail": f"PDF · pagina {item.page}", "excerpt": item.excerpt}
        if source not in sources:
            sources.append(source)
    return {"answer": "\n".join(lines), "sources": sources}


# --- Follow-up questions about the uploaded invoice ----------------------------------------------
# Who pays whom, what a year of advances costs, and what the bill lets Karen conclude. Everything is
# worked out from the invoice object, never by a model; a calculation is always called Karen's.

FOLLOW_UP_SUGGESTIONS = ["Wie moet betalen?", "Wat betekent dit?", "Wat betaal ik op jaarbasis?", "Zie je aandachtspunten?"]

_PAYER_WORDS = ("verschuldigd", "schuldig", "verschuldigde", "owe", "owes", "owed", "doit", "redevable")
# "Wie" alone is not enough: "wie krijgt een sociaal tarief" or "wie moet mijn gasmeter afsluiten" ask something else.
_WHO = re.compile(r"\b(?:wie|who|whom|qui)\b")
_PAY_WORD = re.compile(r"\b(?:betal\w*|betaal\w*|betaalt|betaald|geld|bedrag|stort\w*|overschrij\w*|pay\w*|paid|money|pai\w*|argent|montant)\b")
_PAYER_SUBJECT = re.compile(r"\bhet\s+geld\b|\bwie\s+(?:moet|moeten)\s+(?:er\s+)?(?:dit|dat|het\s+)?\s*betalen\b|\b(?:aan|naar|to|a)\s+(?:wie|whom|qui)\b|\b(?:ik|je|jij|we|wij|i)\b|\bbegunstigde\b")
_YEAR_PHRASE = re.compile(
    r"\bjaarbasis\b|\bper\s+jaar\b|\bop\s+(?:een|1)\s+jaar\b|\bin\s+(?:een|1)\s+jaar\b|\bjaarlijks\w*\b|\b12\s+maanden\b"
    r"|\btwaalf\s+maanden\b|\bper\s+year\b|\ba\s+year\b|\byearly\b|\bpar\s+an\b"
)
_INSIGHT_WORDS = (
    "afleiden", "afgeleid", "conclusie", "conclusies", "concluderen", "inzicht", "inzichten", "aandachtspunt", "aandachtspunten",
    "opvallend", "opvalt", "controleren", "controle", "nakijken", "fouten", "conclude", "insights", "conclusion", "anomalies",
)
_INSIGHT_PHRASE = re.compile(
    r"\bvalt\s+(?:je|u|er)\b.*\bop\b|\bzie\s+je\s+(?:iets|problemen|fouten)\b|\bklopt\s+(?:dit|deze|het|alles|de\s+factuur)\b"
    r"|\bis\s+(?:dit|deze\s+factuur|alles)\s+(?:juist|correct|ok)\b|\bwhat\s+can\s+(?:we|you|i)\s+(?:learn|tell|conclude)\b"
)
# "dit", "die", "hiervan", "mijn factuur": the question is about the uploaded bill, not a general one.
_ABOUT_THE_BILL = re.compile(
    r"\b(?:dit|die|deze|dat|hiervan|hierop|hierin|daarvan|daarop|ervan|mijn|m'n|mn|factuur|rekening|afrekening|bedrag|bedragen"
    r"|this|that|my|bill|invoice|cette|ce|ma|facture)\b"
)


def _words(question: str) -> list[str]:
    return re.findall(r"[a-z]+", _plain(question))


def is_payer_request(question: str) -> bool:
    """'Wie is dit verschuldigd' (also 'vershuldigd'), 'wie moet betalen', 'aan wie betaal ik dit'."""
    plain = _plain(question)
    if any(_fuzzy(word, _PAYER_WORDS, 0.8) for word in _words(question)):
        return True
    return bool(_WHO.search(plain) and _PAY_WORD.search(plain) and (is_about_the_bill(question) or _PAYER_SUBJECT.search(plain)))


def is_yearly_request(question: str) -> bool:
    return bool(_YEAR_PHRASE.search(_plain(question)))


def is_insight_request(question: str) -> bool:
    """'Wat kunnen we uit deze factuur afleiden', 'zie je aandachtspunten', 'klopt deze factuur'."""
    plain = _plain(question)
    return bool(_INSIGHT_PHRASE.search(plain)) or any(
        len(word) >= 6 and _fuzzy(word, _INSIGHT_WORDS, 0.82) for word in _words(question)
    )


def is_about_the_bill(question: str) -> bool:
    return bool(_ABOUT_THE_BILL.search(_plain(question)))


def _sources(evidence: list[Evidence | None]) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    for item in evidence:
        if item is None:
            continue
        source = {"title": item.source, "detail": f"PDF · pagina {item.page}", "excerpt": item.excerpt}
        if source not in sources:
            sources.append(source)
    return sources


def _advance_frequency(invoice: Invoice) -> int | None:
    """Advances per year on an advance bill, from its heading ("Uw maandelijks voorschot")."""
    if invoice.document_type != "advance_invoice" or invoice.document_type_evidence is None:
        return None
    heading = _plain(invoice.document_type_evidence.excerpt)
    if re.search(r"\btweemaandelijks", heading):
        return 6
    if re.search(r"\bdriemaandelijks|\bkwartaal", heading):
        return 4
    if re.search(r"\bmaandelijks|\bmensuel", heading):
        return 12
    return None


def _payment_line(invoice: Invoice, today: Any = None) -> str | None:
    due = invoice.due_date
    if due is None:
        return None
    if not due.direct_debit:
        return f"Volgens de factuur betaal je vóór {due.printed}{_page(due)}."
    from datetime import date

    line = f"De factuur vermeldt een automatische betaling (domiciliëring) op {due.printed}{_page(due)}."
    if due.date and date.fromisoformat(due.date) < (today or date.today()):
        return line + " Die datum is voorbij. Of de betaling echt gelukt is, kan ik niet zien: kijk het na in je bankapp."
    return line + " Of die betaling later echt uitgevoerd wordt, kan ik niet controleren."


def payer_answer(invoice: Invoice, today: Any = None) -> dict[str, Any]:
    """Who the bill is addressed to, how much is owed to whom, and how it is paid: as printed, never as a legal verdict."""
    lines: list[str] = []
    amounts, supplier = invoice.amounts, invoice.supplier
    to = f" aan {supplier.value}" if supplier else ""
    if invoice.customer:
        lines.append(f"Deze factuur staat op naam van {invoice.customer.value}{_page(invoice.customer)}.")
    if amounts.credit:
        lines.append(f"Hier moet jij niets betalen: {supplier.value if supplier else 'je leverancier'} betaalt je "
                     f"{_euro(amounts.credit.value_eur)} terug{_page(amounts.credit)}.")
    elif amounts.amount_due and Decimal(amounts.amount_due.value_eur) == 0:
        lines.append(f"Volgens de factuur is er niets meer verschuldigd{to}{_page(amounts.amount_due)}.")
    elif amounts.amount_due:
        lines.append(f"Volgens de factuur is {_euro(amounts.amount_due.value_eur)} verschuldigd{to}{_page(amounts.amount_due)}.")
    elif supplier:
        lines.append(f"De factuur komt van {supplier.value}{_page(supplier)}, maar ik vond er geen duidelijk bedrag te betalen op.")
    if not amounts.credit and (payment := _payment_line(invoice, today)):
        lines.append(payment)
    if not lines:
        lines.append("Ik vond op deze factuur geen naam, leverancier of bedrag te betalen.")
    return {"answer": " ".join(lines), "sources": _sources([invoice.customer, amounts.amount_due or amounts.credit, invoice.due_date, supplier])}


_GAS_WORD = re.compile(r"\b(?:aard)?gas\w*")
_ELECTRICITY_WORD = re.compile(r"\b(?:elektric\w*|electric\w*|stroom\w*|kwh)")


def _asked_energy(question: str) -> str | None:
    """The one energy a question names ("wat kost gas per jaar"), or None for both or neither."""
    plain = _plain(question)
    gas, power = bool(_GAS_WORD.search(plain)), bool(_ELECTRICITY_WORD.search(plain))
    return "gas" if gas and not power else "electricity" if power and not gas else None


def yearly_answer(invoice: Invoice, question: str = "") -> dict[str, Any]:
    """A year of the same advance, or the yearly total of the new advance, for the energy asked; never a predicted
    settlement, and never a total when the bill shows the advance of only one of the energies it covers."""
    amounts = invoice.amounts
    energy = _asked_energy(question)
    name = _DUTCH_ENERGIES[energy] if energy else ""
    per_year = _advance_frequency(invoice)
    caveat = ("Dat is een berekening van Karen, geen voorspelling van je echte energiekosten: of je bijbetaalt of geld "
              "terugkrijgt, zie je pas op je jaarafrekening. Upload die gerust, dan vergelijk ik ze.")
    if per_year and energy:
        line = next((row for row in invoice.advance_lines if row.energy == energy), None)
        if line is None:
            return {"answer": f"Op deze factuur vond ik geen apart voorschot voor {name}, dus een bedrag per jaar daarvoor kan ik niet geven.",
                    "sources": []}
        amount = Decimal(line.amount_eur)
        vat = {True: " incl. btw", False: " excl. btw", None: ""}[line.vat_included]
        extra = " Op de factuur komt daar nog btw bij." if line.vat_included is False else ""
        return {
            "answer": (f"Betaal je een jaar lang hetzelfde voorschot voor {name}, dan is dat {per_year} × {_euro(str(amount))} = "
                       f"**{_euro(str(amount * per_year))}{vat} per jaar**{_page(line)}.{extra} {caveat}"),
            "sources": _sources([line, invoice.document_type_evidence]),
        }
    if per_year and amounts.amount_due and Decimal(amounts.amount_due.value_eur) > 0:
        amount = Decimal(amounts.amount_due.value_eur)
        return {
            "answer": (f"Betaal je een jaar lang hetzelfde voorschot, dan is dat {per_year} × {_euro(str(amount))} = "
                       f"**{_euro(str(amount * per_year))} aan voorschotten per jaar**{_page(amounts.amount_due)}. {caveat}"),
            "sources": _sources([amounts.amount_due, invoice.document_type_evidence]),
        }
    advances = [advance for advance in invoice.new_advances if advance.frequency]
    if advances and len(advances) == len(invoice.new_advances):
        if energy:
            chosen = [advance for advance in advances if advance.energy == energy]
            if not chosen:
                return {"answer": f"Op deze factuur vond ik geen apart nieuw voorschot voor {name}, dus een bedrag per jaar daarvoor kan ik niet geven.",
                        "sources": []}
        else:
            covered = {advance.energy for advance in advances}
            consumed = {kind for kind in ("electricity", "gas") if getattr(invoice.consumption, kind)}
            if not (covered == {None} and len(advances) == 1 or consumed <= covered):
                # Only one energy's advance was read: its yearly amount is not what the bill costs per year.
                found = advances[0]
                part = _DUTCH_ENERGIES.get(found.energy or "", "één energie")
                missing = " en ".join(_DUTCH_ENERGIES[kind] for kind in sorted(consumed - covered))
                return {
                    "answer": (f"Ik vond op deze factuur alleen het nieuwe voorschot voor {part}: "
                               f"{_euro(found.amount_eur)} {_DUTCH_FREQUENCIES[found.frequency]}{_page(found)}. "
                               f"Dat voor {missing or 'de andere energie'} vond ik niet, dus een totaal per jaar kan ik niet geven."),
                    "sources": _sources([found]),
                }
            chosen = advances
        yearly = sum(Decimal(advance.amount_eur) * _PER_YEAR[advance.frequency] for advance in chosen)
        what = f"Je nieuwe voorschot voor {name}" if energy else "Je nieuwe voorschot"
        return {
            "answer": (f"{what} komt op **{_euro(str(yearly))} per jaar**{_page(chosen[0])}. "
                       "Dat is een berekening van Karen uit het voorschot op de factuur."),
            "sources": _sources(list(chosen)),
        }
    cost = amounts.total_energy_cost
    if cost and _covers_a_year(invoice.billing_period) and not energy:
        return {
            "answer": (f"Over de periode {invoice.billing_period.printed} kostte je energie {_euro(cost.value_eur)}{_page(cost)}. "
                       "Dat is ongeveer een jaar; blijven verbruik en prijzen gelijk, dan is dat een redelijke schatting per jaar."),
            "sources": _sources([cost, invoice.billing_period]),
        }
    if energy:
        return {"answer": f"Op deze factuur staat geen apart bedrag voor {name}, dus een bedrag per jaar daarvoor kan ik niet geven.",
                "sources": []}
    return {
        "answer": ("Met deze factuur alleen kan ik geen bedrag per jaar berekenen: ze toont geen vast voorschot per maand en "
                   "geen volledig jaar. Upload je jaarafrekening, dan reken ik het uit."),
        "sources": [],
    }


def insights_answer(invoice: Invoice, today: Any = None) -> dict[str, Any]:
    """What the bill shows, Karen's checks on its numbers, and what it cannot tell; no verdict on prices or fraud."""
    kind = "factuur" if invoice.document_type == "unknown" else _DUTCH_TYPES[invoice.document_type]
    lines = [f"Wat ik uit deze {kind} afleid:"]
    evidence: list[Evidence | None] = []
    amounts = invoice.amounts
    explained = explain_invoice(invoice)["answer"].split("\n")
    if explained[0].startswith("Wat deze"):
        lines.extend(explained[1:])
    per_year = _advance_frequency(invoice)
    if per_year and amounts.amount_due and Decimal(amounts.amount_due.value_eur) > 0:
        amount = Decimal(amounts.amount_due.value_eur)
        lines.append(f"- Een jaar lang hetzelfde voorschot is {per_year} × {_euro(str(amount))} = {_euro(str(amount * per_year))} "
                     "(berekening van Karen)")
    rows = invoice.advance_lines
    if len(rows) >= 2:
        total = sum(Decimal(row.amount_eur) for row in rows)
        if total > 0:
            shares = ", ".join(f"{_DUTCH_ENERGIES[row.energy]} {round(Decimal(row.amount_eur) / total * 100)}%" for row in rows)
            lines.append(f"- Verdeling van het voorschot: {shares}{_page(rows[0])}")
        due = amounts.amount_due
        if due and rows[0].vat_included is False:
            vat = Decimal(due.value_eur) - total
            rate = next((r for r in (6, 21) if abs(total * r / 100 - vat) <= Decimal("0.02")), None)
            if rate:
                lines.append(f"- De bedragen kloppen: {_euro(str(total))} + {rate}% btw ({_euro(str(vat))}) = {_euro(due.value_eur)}")
            elif vat:
                lines.append(f"- Let op: {_euro(str(total))} zonder btw en {_euro(due.value_eur)} met btw passen niet bij 6% of 21% btw. "
                             "Kijk na of er nog andere posten op de factuur staan")
        evidence.extend([*rows, due])
    payment = _payment_line(invoice, today)
    if payment and not amounts.credit:
        lines.append(f"- {payment}")
        evidence.append(invoice.due_date)
    if invoice.document_type == "advance_invoice":
        lines.append("- Wat deze factuur niet vertelt: je echte verbruik en of je voorschot genoeg is. Daarvoor heb ik je jaarafrekening nodig.")
    elif not invoice.consumption.electricity and not invoice.consumption.gas:
        lines.append("- Je verbruik vond ik niet op deze factuur, dus vergelijken met andere jaren kan ik niet.")
    if not any("Let op" in line for line in lines):
        lines.append("Andere aandachtspunten vond ik niet in deze controles.")
    explained_sources = explain_invoice(invoice)["sources"]
    sources = explained_sources + [source for source in _sources(evidence) if source not in explained_sources]
    return {"answer": "\n".join(lines), "sources": sources}


def clarify_answer() -> dict[str, Any]:
    """No fitting fact or passage for a question about the bill: ask, instead of searching the web with it."""
    return {
        "answer": "Ik weet niet zeker wat je over deze factuur wilt weten. Kies een van de vragen hieronder of stel je vraag iets concreter.",
        "sources": [],
        "suggestions": FOLLOW_UP_SUGGESTIONS,
    }
