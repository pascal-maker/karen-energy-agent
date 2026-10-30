"""Read any invoice layout with the language model, on masked text, and keep only values Karen can check.

The model sees the invoice text after names, addresses, IBAN, EAN, phone numbers and customer, contract
and invoice numbers are replaced by [X]. It copies each value exactly as printed; Karen keeps a value only
when that exact text is on the invoice, and drops amounts that do not add up. The regex reader in
invoice_analysis stays first: the model only fills what the rules did not find.
"""

from __future__ import annotations

import json
import os
import re
from decimal import Decimal
from typing import Any

from .knowledge import KnowledgeChunk

MASK = "[X]"
MAX_MODEL_CHARS = 12000

_IDENTIFIER_LABELS = (
    r"klantnummer|klantnr\.?|klant nr\.?|klantreferentie|contractnummer|contract nr\.?|factuurnummer|factuurnr\.?"
    r"|referentie|uw referentie|ondernemingsnummer|btw-nummer|btw nr\.?|rekeningnummer|meternummer|meter nr\.?"
    r"|gestructureerde mededeling|mededeling|ean(?:-code)?|iban|bic"
    r"|numéro de client|n° (?:de )?client|numéro de contrat|numéro de facture|n° (?:de )?facture"
    r"|r[ée]f[ée]rence|numéro d[’']entreprise|numéro de tva|numéro de compte|numéro de compteur"
    r"|communication structurée|communication|code ean"
)
# The value after a label must hold a digit: "Klantnummer: TEST-KLANT-001", "EAN 541448..." but not "Factuurdatum".
_IDENTIFIER_RE = re.compile(
    rf"(?i)\b(?:{_IDENTIFIER_LABELS})\s*[:#]?\s*((?:[A-Z]{{0,4}}[ -]?)?[A-Z0-9][A-Z0-9./+-]*\d[A-Z0-9./+-]*)"
)
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}(?:\s?[A-Z0-9]{1,3})?\b")
# Nine digits or more in one run (account, customer, meter numbers), or an EAN written in groups;
# not a table row of separate numbers such as "42400 44608 2208".
_LONG_NUMBER_RE = re.compile(r"(?<![\d.,])\d{9,}(?![\d,]|\.\d)|\b54(?:\s?\d){16}\b")
_STRUCTURED_RE = re.compile(r"\+{3}[\d/ ]+\+{3}|\*{3}[\d/ ]+\*{3}")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
# A labelled number ("Tel. 078 15 05 05") or one with the Belgian prefix; never a bare date such as 01.10.2026.
_PHONE_RE = re.compile(r"(?i)(?<=\b(?:tel|fax|gsm)[.:] )[+\d][\d ./]{7,16}\d|(?:(?<=\btel )|(?<=\btél )|(?<=\btél\. )|(?<=\btelefoon )|(?<=\btelefoon: )|(?<=\bmobile ))[+\d][\d ./]{7,16}\d|(?:\+32|0032)\s?\d(?:[ ./]?\d){7,9}")
_NAME_WORD = r"(?:[A-ZÀ-Ý][\wà-ÿ'’-]*\.?|van|de|der|den|le|la|du|von)"
_NAME_RE = re.compile(
    r"\b(?:Klant|Klantnaam|Naam(?: klant)?|Titularis|Klanthouder|Client|Nom|Titulaire|T\.a\.v\.|t\.a\.v\.|A l[’']attention de|À l[’']attention de"
    r"|Dhr\.?|Mevr\.?|Mr\.?|Mw\.?|De heer|Mevrouw|Meneer|Madame|Monsieur|Mme\.?|Beste|Geachte|Cher|Chère)\s*:?\s+"
    rf"((?:{_NAME_WORD}\s+){{0,3}}{_NAME_WORD})"
)
_STREET_WORD = (
    r"[\wÀ-ÿ'’-]*(?:straat|laan|weg|plein|lei|dreef|steenweg|baan|kaai|hof|dijk|markt|singel|vest|wal|pad|berg|veld)"
)
_FR_STREET = r"(?:rue|avenue|av\.|chaussée|boulevard|bd|place|chemin|allée|square|clos|impasse|quai|drève|sentier)"
_POSTCODE = r"(?:[,\s]+(?:B-)?(?<![\d/.-])\d{4}\s+[A-ZÀ-Ý][\wÀ-ÿ'’-]+(?:[ -][A-ZÀ-Ý][\wÀ-ÿ'’-]+)?)?"
_ADDRESS_RE = re.compile(
    rf"(?i:\b{_STREET_WORD})\s+\d+[A-Za-z]?(?:\s*(?:bus|bte|boîte)\s*\w+)?{_POSTCODE}"
    rf"|(?i:\b{_FR_STREET})\s+(?:[\wÀ-ÿ'’-]+\s+){{0,4}}?\d+[A-Za-z]?(?:\s*(?:bus|bte|boîte)\s*\w+)?{_POSTCODE}"
)
_ADDRESS_LABEL_RE = re.compile(
    r"(?i)\b(?:adres|adresse|leveringsadres|verbruiksadres|afnameadres|facturatieadres|adresse de (?:livraison|fourniture|facturation))\s*:?\s*"
    r"([^:\n]{0,80}?\b(?:B-)?(?<![\d/.-])\d{4}\s+[A-ZÀ-Ý][\wÀ-ÿ'’-]+)"
)
# What is left of an address once its street is masked: "[X]/6R 2600 BERCHEM", "[X] - 2640 Mortsel".
_ADDRESS_REST_RE = re.compile(r"\[X\][^\s:]*(?:\s*[-,]\s*|\s+)(?:B-)?\d{4}\s+[A-ZÀ-Ý][\wÀ-ÿ'’-]+")
_CAPS_CITY_RE = re.compile(r"(?<![\d/.,-])\b\d{4}\s+[A-ZÀ-Ý]{3,}(?:[ -][A-ZÀ-Ý]{3,})?\b(?![a-zà-ÿ])")
# Query strings in links can carry customer or contract codes.
_URL_QUERY_RE = re.compile(r"(?<=[\w/])\?[^\s]+")


def mask_invoice_text(text: str, names: tuple[str, ...] = ()) -> str:
    """Replace what identifies a person or a contract by [X]; amounts, dates, kWh and supplier stay."""
    masked = text
    for name in names:
        if name and len(name) > 2:
            masked = re.sub(re.escape(name), MASK, masked, flags=re.IGNORECASE)
    masked = _EMAIL_RE.sub(MASK, masked)
    masked = _STRUCTURED_RE.sub(MASK, masked)
    masked = _IBAN_RE.sub(MASK, masked)
    masked = _LONG_NUMBER_RE.sub(MASK, masked)
    masked = _IDENTIFIER_RE.sub(lambda match: match.group(0).replace(match.group(1), MASK), masked)
    masked = _ADDRESS_LABEL_RE.sub(lambda match: match.group(0).replace(match.group(1), MASK), masked)
    masked = _ADDRESS_RE.sub(MASK, masked)
    masked = _ADDRESS_REST_RE.sub(MASK, masked)
    masked = _CAPS_CITY_RE.sub(MASK, masked)
    masked = _URL_QUERY_RE.sub("", masked)
    masked = _NAME_RE.sub(lambda match: match.group(0).replace(match.group(1), MASK), masked)
    masked = _PHONE_RE.sub(MASK, masked)
    return masked


def model_enabled() -> bool:
    """On when a language model is configured; KAREN_INVOICE_MODEL=0 keeps invoices on the regex reader only."""
    if os.getenv("KAREN_INVOICE_MODEL", "1").strip() == "0":
        return False
    return any(os.getenv(name, "").strip() for name in ("GEMINI_API_KEY", "OPENAI_API_KEY"))


_INSTRUCTION = """Je leest een Belgische energiefactuur (Nederlands of Frans). Persoonsgegevens zijn vervangen door [X].
Geef alleen JSON terug, zonder uitleg, met precies deze sleutels. Kopieer elk bedrag, getal en elke datum
LETTERLIJK zoals het op de factuur staat (bv. "1.234,56", "2.708", "31/10/2026", "7 novembre 2018").
Staat iets er niet duidelijk op, zet dan null. Reken niets uit en gok niets.

{
 "supplier": naam van de energieleverancier of null,
 "invoice_type": "jaarafrekening" | "voorschotfactuur" | "slotfactuur" | "tussentijdse afrekening" | null,
 "billing_period": {"from": "...", "to": "..."} of null,
 "amount_due": {"amount": "...", "refund": true als de klant geld terugkrijgt} of null,
 "total_energy_cost": totale kosten van deze afrekening incl. btw, of null,
 "advances_paid": som van de al aangerekende of betaalde voorschotten, of null,
 "electricity": {"amount": "...", "unit": "kWh" | "MWh", "parts": ["...", "..."] als het totaal niet gedrukt staat maar wel de deelverbruiken (dag/nacht)} of null,
 "gas": {"amount": "...", "unit": "kWh" | "m³"} of null,
 "new_advance": [{"energy": "elektriciteit" | "gas" | null, "amount": "...", "frequency": "maandelijks" | "tweemaandelijks" | "per kwartaal" | null}] of null,
 "due_date": de vervaldatum zoals gedrukt, of null,
 "direct_debit": true als het bedrag via domiciliëring wordt afgehouden, anders false,
 "advance_lines": [{"energy": "elektriciteit" | "gas", "period": "...", "amount": "..."}] alleen op een voorschotfactuur, anders null
}"""


def _ask_model(masked: str) -> str:
    from .gemma_answer import _generate

    return _generate(f"Factuurtekst:\n{masked[:MAX_MODEL_CHARS]}", _INSTRUCTION)


def _parse_json(raw: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text)


class _Invoice:
    """The original (unmasked) chunks, to check that a value is printed and find its page."""

    def __init__(self, chunks: list[KnowledgeChunk]):
        self.chunks = sorted(chunks, key=lambda chunk: chunk.page)
        self.texts = [_squash(chunk.text) for chunk in self.chunks]

    def find(self, printed: Any) -> dict[str, Any] | None:
        if not isinstance(printed, str) or not printed.strip() or MASK in printed:
            return None
        needle = _squash(printed.strip())
        if len(needle) < 1:
            return None
        # Whole numbers only: "63,21" must not match inside "163,21" or "63,215".
        pattern = re.compile(r"(?<![\w.,])" + re.escape(needle) + r"(?![\w]|[.,]\d)", re.IGNORECASE)
        for chunk, text in zip(self.chunks, self.texts):
            match = pattern.search(text)
            if match:
                start, end = max(0, match.start() - 80), min(len(text), match.end() + 80)
                return {"value": needle, "page": chunk.page, "source": chunk.source, "excerpt": text[start:end].strip()[:360],
                        "read_by": "model"}
        return None


def _amount(invoice: _Invoice, printed: Any) -> dict[str, Any] | None:
    from .invoice_analysis import _decimal_value

    fact = invoice.find(printed)
    if fact is None:
        return None
    value = _decimal_value(fact["value"].replace("€", "").strip().lstrip("-").strip())
    if value is None:
        return None
    fact["value"] = value
    return fact


def _energy(value: Any) -> str | None:
    text = str(value or "").lower()
    if "gas" in text:
        return "gas"
    if "elek" in text or "élec" in text or "elec" in text or "stroom" in text:
        return "elektriciteit"
    return None


def verified_model_facts(chunks: list[KnowledgeChunk], data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Keep only the model's values that are printed on the invoice, linked to their page."""
    from .invoice_analysis import _whole

    invoice = _Invoice(chunks)
    facts: dict[str, dict[str, Any]] = {}

    supplier = invoice.find(data.get("supplier"))
    if supplier and re.search(r"[A-Za-zÀ-ÿ]{3}", supplier["value"]):
        facts["supplier"] = supplier

    kind = str(data.get("invoice_type") or "").lower()
    if kind in ("jaarafrekening", "voorschotfactuur", "slotfactuur", "tussentijdse afrekening"):
        facts["invoice_type"] = {"value": kind, "page": invoice.chunks[0].page if invoice.chunks else 1,
                                 "source": invoice.chunks[0].source if invoice.chunks else "", "excerpt": "", "read_by": "model"}

    period = data.get("billing_period")
    if isinstance(period, dict):
        start, end = invoice.find(period.get("from")), invoice.find(period.get("to"))
        if start and end and start["page"] == end["page"]:
            start["value"] = f"{start['value']} tot {end['value']}"
            facts["billing_period"] = start

    due = data.get("amount_due")
    if isinstance(due, dict):
        fact = _amount(invoice, due.get("amount"))
        if fact:
            if due.get("refund") is True and fact["value"] > 0:
                fact["value"] = -fact["value"]
            facts["amount_due"] = fact

    for key in ("total_energy_cost", "advances_paid"):
        fact = _amount(invoice, data.get(key))
        if fact:
            facts[key] = fact

    electricity = data.get("electricity")
    if isinstance(electricity, dict):
        unit = str(electricity.get("unit") or "kWh").lower()
        fact = _amount(invoice, electricity.get("amount"))
        if fact is None and isinstance(electricity.get("parts"), list) and len(electricity["parts"]) >= 2:
            parts = [_amount(invoice, part) for part in electricity["parts"]]
            if all(parts):
                fact = dict(parts[0], value=sum(part["value"] for part in parts))
        if fact and unit in ("kwh", "mwh"):
            fact["value"], fact["unit"] = (_whole(fact["value"] * 1000), "kwh") if unit == "mwh" else (fact["value"], "kwh")
            facts["electricity_kwh"] = fact

    gas = data.get("gas")
    if isinstance(gas, dict):
        fact = _amount(invoice, gas.get("amount"))
        unit = str(gas.get("unit") or "").lower().replace("3", "³")
        if fact and unit in ("kwh", "m³"):
            fact["unit"] = "m³" if unit == "m³" else "kwh"
            facts["gas_kwh"] = fact

    rows = []
    first = None
    for row in data.get("new_advance") or []:
        if isinstance(row, dict):
            fact = _amount(invoice, row.get("amount"))
            if fact:
                rows.append({"energy": _energy(row.get("energy")), "frequency": row.get("frequency"), "old": None, "new": fact["value"]})
                first = first or fact
    if rows:
        facts["new_advance"] = dict(first, value=rows)

    due_date = invoice.find(data.get("due_date"))
    if due_date and re.search(r"\d", due_date["value"]):
        if data.get("direct_debit") is True:
            due_date["direct_debit"] = True
        facts["due_date"] = due_date

    if kind == "voorschotfactuur":
        lines = []
        first = None
        for row in data.get("advance_lines") or []:
            if isinstance(row, dict) and _energy(row.get("energy")):
                fact = _amount(invoice, row.get("amount"))
                if fact:
                    lines.append({"energy": _energy(row.get("energy")), "period": str(row.get("period") or ""), "amount": fact["value"]})
                    first = first or fact
        if lines:
            facts["advance_lines"] = dict(first, value=lines, vat=None)
    return facts


def needs_model(facts: dict[str, Any]) -> bool:
    """Only when the rules missed something the first answer needs: the text then leaves the server, masked."""
    advance_bill = (facts.get("invoice_type") or {}).get("value") == "voorschotfactuur"
    wanted = ("supplier", "amount_due", "advance_lines") if advance_bill else (
        "supplier", "billing_period", "amount_due", "total_energy_cost", "advances_paid", "electricity_kwh"
    )
    return any(facts.get(key) is None for key in wanted)


def read_invoice_with_model(chunks: list[KnowledgeChunk], names: tuple[str, ...] = ()) -> dict[str, dict[str, Any]]:
    """Masked text to the model, verified facts back; {} when the model is off, fails or answers nonsense."""
    if not chunks or not model_enabled():
        return {}
    text = " ".join(chunk.text for chunk in sorted(chunks, key=lambda chunk: chunk.page))
    try:
        data = _parse_json(_ask_model(mask_invoice_text(text, names)))
    except Exception:
        return {}
    return verified_model_facts(chunks, data)


def adds_up(total: Decimal, advances: Decimal, due: Decimal) -> bool:
    """Costs minus advances is the amount to pay (positive) or to get back (negative)."""
    return abs((total - advances) - due) <= Decimal("0.02")
