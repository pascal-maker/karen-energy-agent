"""Read any invoice layout with the language model, on masked text, and keep only values Karen can check.

The model sees the invoice text after names, addresses, IBAN, EAN, phone numbers and customer, contract
and invoice numbers are replaced by [X], and after every word that is not on Karen's list of invoice words
(invoice_words.txt) is replaced too, so a name without a title or label never reaches the model. It copies
each value exactly as printed; Karen keeps a value only when that exact text is on the invoice next to words
that fit its meaning (its unit, a refund or payment label), and drops amounts that do not add up. The regex reader in
invoice_analysis stays first: the model only fills what the rules did not find.
"""

from __future__ import annotations

import json
import os
import re
from decimal import Decimal
from pathlib import Path
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
    return _keep_invoice_words(masked)


_WORDS = frozenset(
    line.strip() for line in (Path(__file__).with_name("invoice_words.txt")).read_text(encoding="utf-8").splitlines()
    if line.strip() and not line.startswith("#")
)
# Supplier names whose words are also first names; kept only together.
_SUPPLIER_PHRASES = re.compile(r"(?i)\b(?:frank energie|dats 24|octa\+|wase wind)\b")
# "jan 2026" is a month; "Jan" alone may be a name.
_MONTH_BEFORE_YEAR = re.compile(r"(?i)\bjan(?=\.?\s+\d{2,4}\b)")
_TOKEN_RE = re.compile(r"\[X\]|[^\W\d_](?:[\w’'.+-]*\w)?|\w*\d\w*(?:[-./]\w+)*")


def _known(token: str) -> bool:
    if token == MASK or token.isdigit() or re.fullmatch(r"[\d.,/-]+", token):
        return True
    letters = [run.lower() for run in re.findall(r"[^\W\d_]+", token)]
    # Numbers with a unit or a short marker ("8u30", "21%", "1.234,56") stay; other mixes are codes.
    if re.search(r"\d", token):
        return all(len(run) <= 3 and (run in _WORDS or len(run) == 1) for run in letters)
    return all(run in _WORDS or (len(run) == 1 and run in "aàeéiou") for run in letters)


def _keep_invoice_words(text: str) -> str:
    """Every word that is not an invoice word becomes [X]; runs of [X] become one."""
    keep: list[tuple[int, int]] = [m.span() for m in _SUPPLIER_PHRASES.finditer(text)]
    keep += [m.span() for m in _MONTH_BEFORE_YEAR.finditer(text)]

    def replace(match: re.Match[str]) -> str:
        if any(start <= match.start() < end for start, end in keep) or _known(match.group(0)):
            return match.group(0)
        return MASK

    masked = _TOKEN_RE.sub(replace, text)
    # Whatever is left of an account number after its label: "IBAN [X] 3350 5545 [X]".
    masked = re.sub(r"(?i)\b(iban|bic)\b[\s:]*(?:(?:\[X\]|[A-Z]{2}\d{2}|\d{2,5})\s*)+", r"\1 [X] ", masked)
    return re.sub(r"\[X\](?:[\s,./-]*\[X\])+", MASK, masked)


def model_enabled() -> bool:
    """On when the OpenAI model is configured; KAREN_INVOICE_MODEL=0 keeps invoices on the regex reader only."""
    if os.getenv("KAREN_INVOICE_MODEL", "1").strip() == "0":
        return False
    # Only the paid OpenAI API: the free-tier Gemma route may keep what it is sent.
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


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
    from .gemma_answer import _generate_backup

    return _generate_backup(f"Factuurtekst:\n{masked[:MAX_MODEL_CHARS]}", _INSTRUCTION)


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
    """The original (unmasked) chunks, to check that a value is printed, next to what it means, and find its page."""

    def __init__(self, chunks: list[KnowledgeChunk]):
        self.chunks = sorted(chunks, key=lambda chunk: chunk.page)
        self.texts = [_squash(chunk.text) for chunk in self.chunks]

    def find(
        self, printed: Any, before: re.Pattern[str] | None = None, after: re.Pattern[str] | None = None,
    ) -> dict[str, Any] | None:
        """The first place `printed` stands whole, with `before` in the 120 characters before it and `after` right behind it."""
        if not isinstance(printed, str) or not printed.strip() or MASK in printed:
            return None
        needle = _squash(printed.strip())
        # Whole numbers only: "63,21" must not match inside "163,21" or "63,215".
        pattern = re.compile(r"(?<![\w.,])" + re.escape(needle) + r"(?![\w]|[.,]\d)", re.IGNORECASE)
        for chunk, text in zip(self.chunks, self.texts):
            for match in pattern.finditer(text):
                lead, tail = text[max(0, match.start() - 120):match.start()], text[match.end():match.end() + 16]
                if before is not None and not before.search(lead):
                    continue
                trailing = after.match(tail) if after is not None else None
                if after is not None and trailing is None:
                    continue
                excerpt = text[max(0, match.start() - 80):min(len(text), match.end() + 80)].strip()[:360]
                return {"value": needle, "page": chunk.page, "source": chunk.source, "excerpt": excerpt, "read_by": "model",
                        "lead": lead, "unit": trailing.group(1).lower() if trailing and trailing.groups() else None}
        return None


_UNIT_RE = re.compile(r"(?i)\s*(kwh|mwh|m³|m3)\b")
_DUE_WORDS = re.compile(r"(?i)betal|saldo|terug|tegoed|voordeel|ontvang|verschuldigd|totaal|bedrag|payer|solde|montant|rembours|faveur|total")
_REFUND_WORDS = re.compile(r"(?i)terug|tegoed|in uw voordeel|te ontvangen|creditnota|rembours|en votre faveur|à recevoir|crédit")
_PAY_WORDS = re.compile(r"(?i)te betalen|nog te betalen|verschuldigd|à payer|montant dû|reste à payer")
_COST_WORDS = re.compile(r"(?i)kost|totaal|total|consommation|verbruik|bedrag|montant|coût")
_ADVANCE_WORDS = re.compile(r"(?i)voorschot|acompte|décompte|intermédiaire|gefactureerd|aangerekend|déjà|reeds")
_NEW_ADVANCE_WORDS = re.compile(r"(?i)voorschot|acompte|intermédiaire|nieuw|nouveau|nouvelle|maandelijks|mensuel")
_DUE_DATE_WORDS = re.compile(r"(?i)verval|vóór|voor|uiterlijk|échéance|avant|domicili|bank|overschrijv|prélev|betal")
_DEBIT_WORDS = re.compile(r"(?i)domicili|uw bank zal|automatisch|prélèvement|afgehouden")
_ELECTRICITY_WORDS = re.compile(r"(?i)elektr|stroom|électr|electr|dag|nacht|piek|dal|pleines|creuses|verbruik|consommation|kwh|mwh")
_GAS_WORDS = re.compile(r"(?i)gas|gaz")


# Money is never followed by a unit: "600 kWh" is not an amount of € 600.
_MONEY_AFTER = re.compile(r"(?i)(?!\s*(?:kwh|mwh|kw|m³|m3|%|kva)\b)")


def _amount(invoice: _Invoice, printed: Any, before: re.Pattern[str] | None = None, after: re.Pattern[str] | None = None) -> dict[str, Any] | None:
    from .invoice_analysis import _decimal_value

    after = after or _MONEY_AFTER
    fact = invoice.find(printed, before, after)
    if fact is None:
        return None
    value = _decimal_value(fact["value"].replace("€", "").replace("EUR", "").strip().lstrip("-").strip())
    if value is None:
        return None
    fact["value"] = value
    return fact


def _energy(value: Any) -> str | None:
    text = str(value or "").lower()
    if "gas" in text or "gaz" in text:
        return "gas"
    if "elek" in text or "élec" in text or "elec" in text or "stroom" in text:
        return "elektriciteit"
    return None


def _rows(value: Any) -> list[dict[str, Any]]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _clean(fact: dict[str, Any]) -> dict[str, Any]:
    fact.pop("lead", None)
    if fact.get("unit") is None:
        fact.pop("unit", None)
    return fact


def _unit(value: Any) -> str:
    return str(value or "").strip().lower().replace("3", "³")


def verified_model_facts(chunks: list[KnowledgeChunk], data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Keep only the model's values that are printed on the invoice next to words that fit, linked to their page."""
    from .invoice_analysis import _whole

    invoice = _Invoice(chunks)
    facts: dict[str, dict[str, Any]] = {}

    supplier = invoice.find(data.get("supplier"))
    if supplier and re.search(r"[A-Za-zÀ-ÿ]{3}", supplier["value"]):
        facts["supplier"] = supplier

    kind = str(data.get("invoice_type") or "").lower()
    if kind in ("jaarafrekening", "voorschotfactuur", "slotfactuur", "tussentijdse afrekening") and invoice.chunks:
        facts["invoice_type"] = {"value": kind, "page": invoice.chunks[0].page, "source": invoice.chunks[0].source,
                                 "excerpt": "", "read_by": "model"}

    period = data.get("billing_period")
    if isinstance(period, dict) and isinstance(period.get("to"), str):
        # Both dates of one period, printed together: "01/01/2025 tot 31/12/2025".
        start = invoice.find(period.get("from"), after=re.compile(r"\s*(?:tot(?: en met)?|t/m|au|à|-|–)\s*" + re.escape(_squash(period["to"].strip()))))
        if start:
            start["value"] = f"{start['value']} tot {_squash(period['to'].strip())}"
            facts["billing_period"] = start

    due = data.get("amount_due")
    if isinstance(due, dict) and isinstance(due.get("refund", False), bool):
        fact = _amount(invoice, due.get("amount"), _DUE_WORDS)
        if fact:
            # The direction comes from the invoice: a minus sign or a refund word before the amount.
            printed_refund = bool(re.search(r"-\s*(?:€|EUR)?\s*$", fact["lead"]) or fact["value"] < 0 or _REFUND_WORDS.search(fact["lead"][-70:]))
            printed_pay = bool(_PAY_WORDS.search(fact["lead"][-70:]))
            refund = due.get("refund") is True
            # A refund the invoice does not show, or a payment where it shows a refund, is not taken.
            if refund == printed_refund and (refund or printed_pay or not _REFUND_WORDS.search(fact["lead"])):
                fact["value"] = -abs(fact["value"]) if refund else abs(fact["value"])
                facts["amount_due"] = fact

    for key, words in (("total_energy_cost", _COST_WORDS), ("advances_paid", _ADVANCE_WORDS)):
        fact = _amount(invoice, data.get(key), words)
        if fact:
            facts[key] = fact

    electricity = data.get("electricity")
    if isinstance(electricity, dict):
        unit = _unit(electricity.get("unit") or "kwh")
        fact = _amount(invoice, electricity.get("amount"), _ELECTRICITY_WORDS, _UNIT_RE)
        if fact is None and len(_parts := electricity.get("parts") or []) >= 2 and isinstance(_parts, list):
            parts = [_amount(invoice, part, _ELECTRICITY_WORDS, _UNIT_RE) for part in _parts]
            if all(parts) and len({part["unit"] for part in parts}) == 1:
                fact = dict(parts[0], value=sum(part["value"] for part in parts))
        # The unit printed behind the number must be the unit the model gave.
        if fact and unit in ("kwh", "mwh") and fact["unit"] == unit:
            fact["value"], fact["unit"] = (_whole(fact["value"] * 1000), "kwh") if unit == "mwh" else (fact["value"], "kwh")
            facts["electricity_kwh"] = fact

    gas = data.get("gas")
    if isinstance(gas, dict):
        unit = _unit(gas.get("unit"))
        fact = _amount(invoice, gas.get("amount"), _GAS_WORDS, _UNIT_RE)
        if fact and unit in ("kwh", "m³") and _unit(fact["unit"]) == unit:
            fact["unit"] = "m³" if unit == "m³" else "kwh"
            facts["gas_kwh"] = fact

    rows, first = [], None
    for row in _rows(data.get("new_advance")):
        fact = _amount(invoice, row.get("amount"), _NEW_ADVANCE_WORDS)
        if fact:
            frequency = row.get("frequency") if isinstance(row.get("frequency"), str) else None
            rows.append({"energy": _energy(row.get("energy")), "frequency": frequency, "old": None, "new": fact["value"]})
            first = first or fact
    if rows:
        facts["new_advance"] = dict(first, value=rows)

    due_date = invoice.find(data.get("due_date"), _DUE_DATE_WORDS)
    if due_date and re.search(r"\d", due_date["value"]):
        if data.get("direct_debit") is True and _DEBIT_WORDS.search(due_date["lead"] + due_date["excerpt"]):
            due_date["direct_debit"] = True
        facts["due_date"] = due_date

    if kind == "voorschotfactuur":
        lines, first = [], None
        for row in _rows(data.get("advance_lines")):
            energy = _energy(row.get("energy"))
            words = _GAS_WORDS if energy == "gas" else _ELECTRICITY_WORDS
            fact = _amount(invoice, row.get("amount"), words) if energy else None
            if fact:
                lines.append({"energy": energy, "period": str(row.get("period") or ""), "amount": fact["value"]})
                first = first or fact
        if lines:
            facts["advance_lines"] = dict(first, value=lines, vat=None)
    return {key: _clean(fact) for key, fact in facts.items()}


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
        return verified_model_facts(chunks, _parse_json(_ask_model(mask_invoice_text(text, names))))
    except Exception:
        # A model that is down, slow or answers in an unexpected shape leaves the invoice to the rules.
        return {}


def adds_up(total: Decimal, advances: Decimal, due: Decimal) -> bool:
    """Costs minus advances is the amount to pay (positive) or to get back (negative)."""
    return abs((total - advances) - due) <= Decimal("0.02")
