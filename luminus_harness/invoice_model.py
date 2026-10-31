"""Read any invoice layout with the language model, on masked text, and keep only values Karen can check.

Off unless KAREN_INVOICE_MODEL=1 and OPENAI_API_KEY are set. Only passages around amounts, dates and
quantities leave the server, and in them every word that is not on Karen's list of invoice words
(invoice_words.txt) and every number that is not an amount with cents, a date or a quantity with its unit
becomes [X]. The model copies each value exactly as printed; Karen keeps a value only when that exact text
is on the invoice next to words that fit it (its energy and unit, its sign and refund or payment label, a
total incl. btw), and keeps the model's amounts only when costs minus advances is the amount due. The
regex reader in invoice_analysis stays first: the model only fills what the rules did not find.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

from .knowledge import KnowledgeChunk

MASK = "[X]"
MAX_MODEL_CHARS = 6000
MODEL_DEADLINE_SECONDS = 30
# At most two invoices at the model at once; a third visitor gets the rules' reading.
_MODEL_SLOTS = 2

_IDENTIFIER_LABELS = (
    r"klantnummer|klantnr\.?|klant nr\.?|klantreferentie|contractnummer|contract nr\.?|factuurnummer|factuurnr\.?"
    r"|referentie|uw referentie|ondernemingsnummer|btw-nummer|btw nr\.?|rekeningnummer|meternummer|meter nr\.?"
    r"|gestructureerde mededeling|mededeling|ean(?:-code)?|iban|bic"
    r"|numéro de client|n° (?:de )?client|numéro de contrat|numéro de facture|n° (?:de )?facture"
    r"|r[ée]f[ée]rence|numéro d[’']entreprise|numéro de tva|numéro de compte|numéro de compteur"
    r"|communication structurée|communication|code ean"
    r"|customer (?:id|number|no\.?)|client (?:id|number|no\.?)|account (?:id|number|no\.?)|invoice (?:number|no\.?)"
    r"|contract (?:id|number|no\.?)|order (?:number|no\.?)|reference|ref\.?|id|nr\.?|no\.?|n°|nummer|number|code"
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
# Query strings in links can carry customer or contract codes.
_URL_QUERY_RE = re.compile(r"(?<=[\w/])\?[^\s]+")


def mask_invoice_text(text: str, names: tuple[str, ...] = ()) -> str:
    """Labelled identifiers and titled names first, then everything that is not an invoice word, an amount,
    a date or a quantity: streets, cities, names and numbers of any other shape become [X]."""
    masked = text
    for name in names:
        if name and len(name) > 2:
            masked = re.sub(re.escape(name), MASK, masked, flags=re.IGNORECASE)
    masked = _EMAIL_RE.sub(MASK, masked)
    masked = _STRUCTURED_RE.sub(MASK, masked)
    masked = _IBAN_RE.sub(MASK, masked)
    masked = _LONG_NUMBER_RE.sub(MASK, masked)
    masked = _IDENTIFIER_RE.sub(lambda match: match.group(0).replace(match.group(1), MASK), masked)
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
_MONTH_BEFORE_YEAR = re.compile(r"(?i)\bjan(?=\.?\s+\d{4}\b)")
_MONTHS = (
    r"januari|februari|maart|april|mei|juni|juli|augustus|september|oktober|november|december"
    r"|janvier|février|mars|avril|mai|juin|juillet|août|septembre|octobre|novembre|décembre"
    r"|jan|feb|mrt|maa|apr|jun|jul|aug|sep|sept|okt|nov|dec|janv|févr|avr|juil|déc"
)
# The only numbers that may leave the server: an amount with cents, a date, or a quantity with its unit.
_ALLOWED_NUMBER_RE = re.compile(
    r"(?i)(?<![\w.,/])(?:"
    r"-?\s?(?:\d{1,3}(?:[.\u00a0 ]\d{3})+|\d+)[,.]\d{2}(?![\d.,/])"
    r"|\d{1,2}[./-]\d{1,2}[./-](?:\d{4}|\d{2})(?![\d./-])"
    rf"|(?:\d{{1,2}}\s+)?(?:{_MONTHS})\.?\s+(?:19|20)\d{{2}}(?!\d)"
    r"|(?:\d{1,3}(?:[. ]\d{3})+|\d+)(?:,\d{1,3})?\s?(?:kwh|mwh|kw|kva|m³|m3|%|€/kwh|c€/kwh)(?![\w])"
    r")"
)
_TOKEN_RE = re.compile(r"\[X\]|[^\W\d_](?:[\w’'.+-]*\w)?|[\w.,/+-]*\d[\w.,/+-]*")
# An amount with cents is money only next to a euro sign or a money label; a decimal-looking number after
# any other label ("Customer ID: 1234.56") may be a code and stays masked.
_MONEY_SHAPE_RE = re.compile(r"-?\s?(?:\d{1,3}(?:[.\u00a0 ]\d{3})+|\d+)[,.]\d{2}")
_MONEY_LABEL_RE = re.compile(
    r"(?i)€|\beur\b|euro|bedrag|totaal|totale|total|saldo|btw|tva|voorschot|kost|prijs|montant|acompte|décompte"
    r"|solde|betal|payer|terug|tegoed|vergoeding|korting|incl|excl|hors|htva|tvac|gefactureerd|aangerekend|facturé"
)
_ID_LABEL_RE = re.compile(r"(?i)\b(?:id|nr|no|n°|nummer|number|ref|reference|référence|code|klant|customer|client|account|contract|order)\b")


def _is_money(text: str, start: int, end: int) -> bool:
    label = re.split(r"\d|\[X\]", text[max(0, start - 50):start])[-1]
    tail = text[end:end + 4]
    if _ID_LABEL_RE.search(label):
        return False
    return bool(_MONEY_LABEL_RE.search(label) or re.match(r"(?i)\s*(?:€|eur\b)", tail))


def _known_word(token: str) -> bool:
    if token == MASK:
        return True
    return all(run.lower() in _WORDS or (len(run) == 1 and run.lower() in "aàeéiou") for run in re.findall(r"[^\W\d_]+", token))


def _keep_invoice_words(text: str) -> str:
    """Fail closed: every word that is not an invoice word and every other number becomes [X]."""
    keep: list[tuple[int, int]] = [m.span() for m in _SUPPLIER_PHRASES.finditer(text)]
    keep += [m.span() for m in _MONTH_BEFORE_YEAR.finditer(text)]
    keep += [
        m.span() for m in _ALLOWED_NUMBER_RE.finditer(text)
        if not _MONEY_SHAPE_RE.fullmatch(m.group(0)) or _is_money(text, m.start(), m.end())
    ]

    def replace(match: re.Match[str]) -> str:
        if any(start <= match.start() and match.end() <= end for start, end in keep):
            return match.group(0)
        if not re.search(r"\d", match.group(0)) and _known_word(match.group(0)):
            return match.group(0)
        return MASK

    masked = _TOKEN_RE.sub(replace, text)
    return re.sub(r"\[X\](?:[\s,./:+()-]*\[X\])+", MASK, masked)


def model_passages(text: str) -> str:
    """Only the passages around amounts, dates and quantities (and the heading), not the whole invoice."""
    spans = [(0, min(len(text), 240))]
    spans += [(max(0, m.start() - 90), min(len(text), m.end() + 40)) for m in _ALLOWED_NUMBER_RE.finditer(text)]
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return " … ".join(text[start:end] for start, end in merged)[:MAX_MODEL_CHARS]


def model_enabled() -> bool:
    """Off by default: only KAREN_INVOICE_MODEL=1 with an OpenAI key lets masked invoice passages leave the server."""
    if os.getenv("KAREN_INVOICE_MODEL", "0").strip() != "1":
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


async def _ask_model(masked: str) -> str:
    """One request, no retries, not stored at OpenAI; cancelling it closes the connection."""
    from openai import AsyncOpenAI

    model = os.getenv("KAREN_BACKUP_MODEL", "gpt-5-mini").strip()
    options: dict[str, Any] = {"reasoning": {"effort": "minimal"}} if model.startswith("gpt-5") else {}
    async with AsyncOpenAI(timeout=MODEL_DEADLINE_SECONDS, max_retries=0) as client:
        result = await client.responses.create(
            model=model, instructions=_INSTRUCTION, input=f"Factuurtekst:\n{masked[:MAX_MODEL_CHARS]}", store=False, **options
        )
    return result.output_text or ""


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
        # One text per page, with the chunker's overlap removed, so every printed value has one place.
        from .invoice_analysis import _overlap_length

        self.chunks: list[KnowledgeChunk] = []
        self.texts: list[str] = []
        previous: KnowledgeChunk | None = None
        for chunk in sorted(chunks, key=lambda chunk: (chunk.source, chunk.page)):
            if previous is not None and (previous.source, previous.page) == (chunk.source, chunk.page):
                rest = chunk.text[_overlap_length(previous, chunk):]
                self.texts[-1] = _squash(self.texts[-1] + " " + rest).strip()
            else:
                self.chunks.append(chunk)
                self.texts.append(_squash(chunk.text))
            previous = chunk

    def find(
        self, printed: Any, before: re.Pattern[str] | None = None, after: re.Pattern[str] | None = None,
        accept: Any = None, used: set[str] | None = None,
    ) -> dict[str, Any] | None:
        """The first place `printed` stands whole with `before` in the 120 characters before it, `after` right behind
        it and `accept(fact)` true; a place (`key`: page and offset) in `used` was already taken by another value."""
        if not isinstance(printed, str) or not printed.strip() or MASK in printed:
            return None
        needle = _squash(printed.strip())
        # Whole numbers only: "63,21" must not match inside "163,21" or "63,215".
        pattern = re.compile(r"(?<![\w.,])" + re.escape(needle) + r"(?![\w]|[.,]\d)", re.IGNORECASE)
        for chunk, text in zip(self.chunks, self.texts):
            for match in pattern.finditer(text):
                lead, tail = text[max(0, match.start() - 120):match.start()], text[match.end():match.end() + 24]
                if before is not None and not before.search(lead):
                    continue
                trailing = after.match(tail) if after is not None else None
                if after is not None and trailing is None:
                    continue
                excerpt = text[max(0, match.start() - 80):min(len(text), match.end() + 80)].strip()[:360]
                fact = {"value": needle, "page": chunk.page, "source": chunk.source, "excerpt": excerpt, "read_by": "model",
                        "lead": lead, "tail": tail, "key": f"{chunk.source}:{chunk.page}:{match.start()}",
                        "unit": trailing.group(1).lower() if trailing and trailing.groups() else None}
                if used is not None and fact["key"] in used:
                    continue
                if accept is not None and not accept(fact):
                    continue
                return fact
        return None


_UNIT_RE = re.compile(r"(?i)\s*(kwh|mwh|m³|m3)\b")
_DUE_WORDS = re.compile(r"(?i)betal|saldo|terug|tegoed|voordeel|ontvang|verschuldigd|totaal|bedrag|payer|solde|montant|rembours|faveur|total")
_REFUND_WORDS = re.compile(r"(?i)terug|tegoed|in uw voordeel|te ontvangen|creditnota|rembours|en votre faveur|à recevoir|crédit")
_PAY_WORDS = re.compile(r"(?i)te betalen|nog te betalen|verschuldigd|à payer|montant dû|reste à payer")
_TOTAL_WORDS = re.compile(r"(?i)totaal|totale|total|incl|tvac")
_EXCL_WORDS = re.compile(r"(?i)excl|hors|htva|zonder btw|sans tva")
_ADVANCE_WORDS = re.compile(r"(?i)voorschot|acompte|décompte|intermédiaire|gefactureerd|aangerekend|déjà|reeds")
_NEW_ADVANCE_WORDS = re.compile(r"(?i)voorschot|acompte|intermédiaire")
_DUE_DATE_WORDS = re.compile(r"(?i)verval|vóór|uiterlijk|échéance|avant le|domicili|uw bank zal|overschrijv|prélev|betalen voor")
_DEBIT_WORDS = re.compile(r"(?i)domicili|uw bank zal|automatisch|prélèvement|afgehouden")
# Words that say which energy a number belongs to; the nearest one before the number decides.
_ENERGY_WORDS = re.compile(
    r"(?i)(?P<gas>aardgas|gas|gaz)|(?P<elektriciteit>elektr|stroom|électr|electr|\bdag\b|\bnacht\b|\bpiek|\bdal\b|pleines|creuses|\bjour\b|\bnuit\b)"
)
_FREQUENCIES = (
    (re.compile(r"(?i)tweemaandelijks|bimestriel|om de twee maanden"), "tweemaandelijks"),
    (re.compile(r"(?i)maandelijks|mensuel|per maand|par mois"), "maandelijks"),
    (re.compile(r"(?i)kwartaal|trimestriel"), "per kwartaal"),
    (re.compile(r"(?i)jaarlijks|annuel|per jaar"), "jaarlijks"),
)
_INVOICE_TYPES = {
    "jaarafrekening": re.compile(r"(?i)jaarafrekening|afrekening|facture annuelle|décompte annuel|régularisation"),
    "voorschotfactuur": re.compile(r"(?i)voorschotfactuur|maandelijks voorschot|facture d.acompte|facture intermédiaire|uw (?:maandelijks |tweemaandelijks )?voorschot"),
    "slotfactuur": re.compile(r"(?i)slotfactuur|eindafrekening|facture de clôture|facture finale"),
    "tussentijdse afrekening": re.compile(r"(?i)tussentijdse afrekening|facture de régularisation intermédiaire"),
}
# Money is never followed by a unit: "600 kWh" is not an amount of € 600.
_MONEY_AFTER = re.compile(r"(?i)(?!\s*(?:kwh|mwh|kw|m³|m3|%|kva)\b)")


def _energy_before(lead: str) -> str | None:
    """The energy named closest before a number, within the 80 characters before it."""
    found = None
    for match in _ENERGY_WORDS.finditer(lead[-80:]):
        found = match.lastgroup
    return found


def _negative(fact: dict[str, Any]) -> bool:
    """A minus printed with the number: "-111,10", "- € 111,10", "€ -111,10", "111,10-"."""
    return fact["value"].lstrip().startswith("-") or bool(re.search(r"-\s*(?:€|EUR)?\s*$", fact["lead"])) or fact["tail"].startswith("-")


def _amount(invoice: _Invoice, printed: Any, before: re.Pattern[str] | None = None, after: re.Pattern[str] | None = None, **checks: Any) -> dict[str, Any] | None:
    from .invoice_analysis import _decimal_value

    fact = invoice.find(printed, before, after or _MONEY_AFTER, **checks)
    if fact is None:
        return None
    fact["negative"] = _negative(fact)
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
    for key in ("lead", "tail", "key", "negative"):
        fact.pop(key, None)
    if fact.get("unit") is None:
        fact.pop("unit", None)
    return fact


def _unit(value: Any) -> str:
    return str(value or "").strip().lower().replace("3", "³")


def _total_incl_vat(fact: dict[str, Any]) -> bool:
    """A total, not a line or an amount excl. btw: "Totale energiekosten € 1.234,56", "TVA incl.€ 6.179,17"."""
    label = re.split(r"\d", fact["lead"])[-1][-60:]  # the words since the previous number
    return bool(_TOTAL_WORDS.search(label)) and not _EXCL_WORDS.search(label + fact["tail"][:16])


# Electricity in kWh or MWh; gas in kWh, MWh or m³. Anything else (stroom in m³) is not a consumption.
_ENERGY_UNITS = {"elektriciteit": ("kwh", "mwh"), "gas": ("kwh", "mwh", "m³")}


def _consumption(invoice: _Invoice, data: Any, energy: str) -> dict[str, Any] | None:
    """A consumption printed with its unit, after a word for this energy; a sum of parts only from distinct rows."""
    if not isinstance(data, dict):
        return None
    unit = _unit(data.get("unit") or ("kwh" if energy == "elektriciteit" else ""))
    if unit not in _ENERGY_UNITS[energy]:
        return None

    def fits(fact: dict[str, Any]) -> bool:
        printed_unit = _unit(fact["unit"])
        named = _energy_before(fact["lead"]) or ("gas" if printed_unit == "m³" else None)
        return named == energy and printed_unit == unit

    fact = _amount(invoice, data.get("amount"), after=_UNIT_RE, accept=fits)
    parts = data.get("parts") if energy == "elektriciteit" else None
    if fact is None and isinstance(parts, list) and len(parts) >= 2:
        used: set[str] = set()
        found = []
        for part in parts:
            row = _amount(invoice, part, after=_UNIT_RE, accept=fits, used=used)
            if row is None:
                return None
            used.add(row["key"])
            found.append(row)
        fact = dict(found[0], value=sum(row["value"] for row in found))
    return fact


def verified_model_facts(chunks: list[KnowledgeChunk], data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Keep only the model's values that are printed on the invoice next to words that fit, linked to their page."""
    from .invoice_analysis import _whole

    invoice = _Invoice(chunks)
    facts: dict[str, dict[str, Any]] = {}

    supplier = invoice.find(data.get("supplier"))
    if supplier and re.search(r"[A-Za-zÀ-ÿ]{3}", supplier["value"]):
        facts["supplier"] = supplier

    kind = str(data.get("invoice_type") or "").lower()
    if kind in _INVOICE_TYPES and invoice.chunks:
        # The invoice must say so itself.
        for chunk, text in zip(invoice.chunks, invoice.texts):
            heading = _INVOICE_TYPES[kind].search(text)
            if heading:
                facts["invoice_type"] = {"value": kind, "page": chunk.page, "source": chunk.source,
                                         "excerpt": heading.group(0), "read_by": "model"}
                break
        else:
            kind = ""

    period = data.get("billing_period")
    if isinstance(period, dict) and isinstance(period.get("to"), str):
        # Both dates of one period, printed together: "01/01/2025 tot 31/12/2025".
        start = invoice.find(period.get("from"), after=re.compile(r"\s*(?:tot(?: en met)?|t/m|au|à|-|–)\s*" + re.escape(_squash(period["to"].strip()))))
        if start:
            start["value"] = f"{start['value']} tot {_squash(period['to'].strip())}"
            facts["billing_period"] = start

    due = data.get("amount_due")
    if isinstance(due, dict) and isinstance(due.get("refund", False), bool):
        refund = due.get("refund") is True

        def direction(fact: dict[str, Any]) -> bool:
            # The direction comes from the invoice: its sign, or a refund or payment word just before the amount.
            printed_refund = fact["negative"] or bool(_REFUND_WORDS.search(fact["lead"][-70:]))
            printed_pay = bool(_PAY_WORDS.search(fact["lead"][-70:]))
            return refund == printed_refund and (refund or printed_pay or not _REFUND_WORDS.search(fact["lead"]))

        fact = _amount(invoice, due.get("amount"), _DUE_WORDS, accept=lambda fact: direction(dict(fact, negative=_negative(fact))))
        if fact:
            fact["value"] = -fact["value"] if refund else fact["value"]
            facts["amount_due"] = fact

    total = _amount(invoice, data.get("total_energy_cost"), accept=lambda fact: _total_incl_vat(fact) and not _negative(fact))
    if total:
        facts["total_energy_cost"] = total
    advances = _amount(invoice, data.get("advances_paid"), accept=lambda fact: bool(_ADVANCE_WORDS.search(fact["lead"][-70:])))
    if advances:
        facts["advances_paid"] = advances

    for key, energy, data_key in (("electricity_kwh", "elektriciteit", "electricity"), ("gas_kwh", "gas", "gas")):
        fact = _consumption(invoice, data.get(data_key), energy)
        if fact:
            # Everything Karen says is in kWh, or m³ for gas measured in m³: "25 MWh" is 25.000 kWh.
            printed = _unit(fact["unit"])
            fact["value"] = _whole(fact["value"] * 1000) if printed == "mwh" else fact["value"]
            fact["unit"] = "m³" if printed == "m³" else "kwh"
            facts[key] = fact

    rows, first, used = [], None, set()
    for row in _rows(data.get("new_advance")):
        fact = _amount(invoice, row.get("amount"), _NEW_ADVANCE_WORDS, used=used)
        if fact:
            used.add(fact["key"])
            # Energy and frequency as the invoice prints them near this amount, never as the model says.
            near = fact["lead"][-80:] + fact["tail"]
            frequency = next((name for pattern, name in _FREQUENCIES if pattern.search(near)), None)
            rows.append({"energy": _energy_before(fact["lead"]), "frequency": frequency, "old": None, "new": fact["value"]})
            first = first or fact
    if rows:
        facts["new_advance"] = dict(first, value=rows)

    due_date = invoice.find(data.get("due_date"), _DUE_DATE_WORDS)
    if due_date and re.search(r"\d", due_date["value"]):
        if _DEBIT_WORDS.search(due_date["lead"] + due_date["tail"]):
            due_date["direct_debit"] = True
        facts["due_date"] = due_date

    if kind == "voorschotfactuur":
        lines, first, used = [], None, set()
        for row in _rows(data.get("advance_lines")):
            energy = _energy(row.get("energy"))
            fact = _amount(invoice, row.get("amount"), accept=lambda fact, energy=energy: energy is not None and _energy_before(fact["lead"]) == energy, used=used)
            if fact:
                used.add(fact["key"])
                period_text = row.get("period") if isinstance(row.get("period"), str) else ""
                period_text = period_text if period_text and period_text in fact["lead"][-80:] else ""
                lines.append({"energy": energy, "period": period_text, "amount": fact["value"]})
                first = first or fact
        if lines:
            facts["advance_lines"] = dict(first, value=lines, vat=None)
    return {key: _clean(fact) for key, fact in facts.items()}


def needs_model(facts: dict[str, Any]) -> bool:
    """Only when the rules missed something the first answer needs: the passages then leave the server, masked."""
    advance_bill = (facts.get("invoice_type") or {}).get("value") == "voorschotfactuur"
    wanted = ["supplier", "amount_due", "advance_lines"] if advance_bill else [
        "supplier", "billing_period", "amount_due", "total_energy_cost", "advances_paid"
    ]
    # A gas-only bill has no electricity: one consumption is enough.
    missing_usage = not advance_bill and facts.get("electricity_kwh") is None and facts.get("gas_kwh") is None
    return missing_usage or any(facts.get(key) is None for key in wanted)


_slots: asyncio.Semaphore | None = None


async def read_invoice_with_model(chunks: list[KnowledgeChunk], names: tuple[str, ...] = ()) -> tuple[dict[str, dict[str, Any]], bool]:
    """Masked passages to the model, verified facts back, and whether anything was sent.

    ({}, False) when the model is off or busy; {} when it is slow, fails or answers nonsense."""
    global _slots
    if not chunks or not model_enabled():
        return {}, False
    _slots = _slots or asyncio.Semaphore(_MODEL_SLOTS)
    if _slots.locked():
        return {}, False
    text = " ".join(chunk.text for chunk in sorted(chunks, key=lambda chunk: chunk.page))
    async with _slots:
        try:
            # The deadline cancels the request itself, so nothing keeps running after the rules have answered.
            raw = await asyncio.wait_for(_ask_model(model_passages(mask_invoice_text(text, names))), MODEL_DEADLINE_SECONDS)
            return verified_model_facts(chunks, _parse_json(raw)), True
        except Exception:
            # A model that is down, slow or answers in an unexpected shape leaves the invoice to the rules.
            return {}, True


def adds_up(total: Decimal, advances: Decimal, due: Decimal) -> bool:
    """Costs minus advances is the amount to pay (positive) or to get back (negative)."""
    return abs((total - advances) - due) <= Decimal("0.02")
