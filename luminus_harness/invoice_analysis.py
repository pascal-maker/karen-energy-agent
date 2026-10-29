"""Local-only extraction and evidence retrieval for uploaded energy bills."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from .knowledge import STOP_WORDS, TOKEN_RE, KnowledgeChunk, retrieve


# Thousands separated by a dot or space ("1.234,56"), or written together ("1234,56"); never cut short.
AMOUNT_VALUE_PATTERN = r"(-?(?:\d{1,3}(?:[ .]\d{3})+|\d+)(?:[,.]\d{1,2})?)(?![.,/-]?\d)"
PERIOD_RE = re.compile(
    r"(?i)\b(?:factuurperiode|facturatieperiode|verbruiksperiode|période de facturation|période|periode(?: van)?|afrekening(?: over| voor)?)\s*[:\-]?\s*"
    r"(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})"
    r"(?:\s*(?:tot en met|tot|t/m|au|[-–])\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}))?"
)
CONSUMPTION_RE = re.compile(
    r"(?i)\b(?:elektriciteitsverbruik|stroomverbruik|verbruik|électricité|consommation d'électricité)\b[^\d\n]{0,45}"
    r"(\d[\d .]*(?:,\d{1,2})?)\s*(kwh|m³|m3)\b"
)
# A consumption table row such as "Actief+ elektriciteit dag 2.208 kWh"; day and night are added up.
ELECTRICITY_ROW_RE = re.compile(
    r"(?i)\belektriciteit\s+(dag|nacht|normaal|enkelvoudig|piek|dal)\s+(\d{1,3}(?:\.\d{3})+|\d+)\s*kwh\b"
)
# A meter table row "Elektriciteit dag 42.400 kWh 44.608 kWh 2.208 kWh": begin, end, consumption.
# OCR glues words and adds underscores ("Elektriciteitdag _10.000kWh ___500 kWh").
_KWH = r"(\d{1,3}(?:\.\d{3})+|\d+)\s*kwh"
ELECTRICITY_TABLE_ROW_RE = re.compile(
    r"(?i)\belektriciteit\s*(dag|nacht|normaal|enkelvoudig|piek|dal)[\s_|]*" + _KWH + r"[\s_|]+" + _KWH + r"[\s_|]+" + _KWH + r"\b"
)
GAS_ROW_RE = re.compile(r"(?i)\b(?:actief\+?\s+)?gas\s+(\d{1,3}(?:\.\d{3})+|\d+)\s*kwh\b")
GAS_CONSUMPTION_RE = re.compile(r"(?i)\bgasverbruik\b[^\d\n]{0,45}(\d{1,3}(?:\.\d{3})+|\d+)\s*(kwh|m³|m3)\b")
# "Uw nieuw voorschot ... Elektriciteit tweemaandelijks € 100 € 108": the old and the new amount.
NEW_ADVANCE_RE = re.compile(r"(?i)\bnieuwe?\s+voorschot")
# "Nieuw maandelijks voorschot: € 105,00": one amount for gas and electricity together.
NEW_ADVANCE_SINGLE_RE = re.compile(
    r"(?i)\bnieuwe?\s+((?:twee|drie)?maandelijks(?:e)?\s+)?voorschot\s*:?\s*€\s*((?:\d{1,3}(?:[ .]\d{3})+|\d+)(?:,\d{1,2})?)"
)
NEW_ADVANCE_ROW_RE = re.compile(
    r"(?i)\b(elektriciteit|gas)\s+((?:twee|drie)?maandelijks|per\s+maand|jaarlijks)\s+€\s*"
    + r"((?:\d{1,3}(?:[ .]\d{3})+|\d+)(?:,\d{1,2})?)\s+€\s*((?:\d{1,3}(?:[ .]\d{3})+|\d+)(?:,\d{1,2})?)"
)
# "Gelieve vóór 15.04.2021 het bedrag ... te betalen", "Vervaldatum: 15/04/2021".
DUE_DATE_RE = re.compile(
    r"(?i)\b(?:gelieve\s+(?:vóór|voor|uiterlijk\s+op)|te\s+betalen\s+(?:vóór|voor|uiterlijk\s+op)|vervaldatum|vervaldag|uiterste\s+betaaldatum|échéance|date\s+d'échéance)\s*:?\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})"
)
# "Uw bank zal op 31.10.2026 het bedrag van € 179,00 overschrijven": paid by direct debit on that date.
DIRECT_DEBIT_RE = re.compile(r"(?i)\buw\s+bank\s+(?:zal\s+)?op\s+(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})")
# An advance bill ("Uw maandelijks voorschot", "Uw maandelijkse voorschotfactuur") shows no real consumption.
ADVANCE_BILL_RE = re.compile(
    r"(?i)\b(?:uw\s+)?(?:(?:twee|drie)?maandelijkse?\s+voorschot(?:factuur)?|voorschotfactuur|facture\s+d'acompte)\b"
)
SETTLEMENT_RE = re.compile(r"(?i)\b(?:jaarafrekening|slotfactuur|eindafrekening|afrekeningsfactuur|facture\s+de\s+régularisation|facture\s+annuelle)\b")
_MONTH = (r"(?:jan(?:uari)?|feb(?:ruari)?|maa(?:rt)?|apr(?:il)?|mei|jun(?:i)?|jul(?:i)?|aug(?:ustus)?|sep(?:t(?:ember)?)?|"
          r"okt(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?")
# One energy's line on an advance bill: "Elektriciteit (EAN 5414...) okt 2026 63,21 €", "Aardgas februari 2026 € 90,00".
ADVANCE_LINE_RE = re.compile(
    r"(?i)\b(elektriciteit|aardgas|gas)\b(?:\s*\(EAN\s*\d+\))?\s+(" + _MONTH + r"\s+\d{4})\s+(?:€\s*)?"
    r"((?:\d{1,3}(?:[ .]\d{3})+|\d+),\d{2})(?:\s*€)?"
)
# Meter readings ("Beginstand 42.400 kWh") are counter positions, not consumption.
METER_READING_RE = re.compile(r"(?i)\b(?:beginstand|eindstand|meterstand|afname\s+(?:dag|nacht))")

_NOT_A_STREET = r"(?![A-ZÀ-Ý][a-zà-ÿ'-]*(?:straat|laan|weg|plein|lei|dreef|baan|kaai|kade|singel|park|hof|dijk|vest|markt)\b)"
# The addressee: a title followed by capitalised names, so a street in capitals ends the name.
CUSTOMER_RE = re.compile(
    r"\b(?:Dhr\.?|Mevr\.?|Juffr\.?|Mr\.?|Mw\.?|De heer|Mevrouw|Meneer|Madame|Monsieur)\s+"
    r"((?:[A-ZÀ-Ý][a-zà-ÿ'-]+|[A-Z]\.)(?:\s+(?:(?:van|de|der|den|le|la)\s+)*" + _NOT_A_STREET + r"[A-ZÀ-Ý][a-zà-ÿ'-]+){0,3})"
)

# "Klant: John Doe Adres: ..." and "Leverancier: Kanaal Energie": a label, then capitalised words up to
# the next "Label:".
_LABEL_VALUE = r"\s*:\s*([A-ZÀ-Ý][\w+.'&-]*(?:\s+(?!\w+\s*:)" + _NOT_A_STREET + r"[A-ZÀ-Ý0-9][\w+.'&-]*){0,3})"
CUSTOMER_LABEL_RE = re.compile(r"\b(?:Klant|Klantnaam|Naam klant|Client|Nom)" + _LABEL_VALUE)
SUPPLIER_LABEL_RE = re.compile(r"\b(?:Leverancier|Energieleverancier|Fournisseur)" + _LABEL_VALUE)

AMOUNT_LABELS: dict[str, tuple[str, ...]] = {
    "amount_due": (
        "totaal factuurbedrag", "totaal te betalen", "te betalen bedrag", "saldo te betalen",
        "te betalen saldo", "te betalen", "terug te krijgen", "factuurbedrag",
        "tegoed terug te ontvangen", "terug te ontvangen", "uw tegoed", "saldo in uw voordeel", "in uw voordeel",
        "solde à payer", "montant à payer", "à payer", "solde en votre faveur", "à rembourser",
    ),
    "total_energy_cost": (
        "totale energiekosten", "totale energiekost", "totaal energiekosten",
        "totaal kosten", "totaalkost", "totaal (incl. btw)", "totaal incl. btw",
        "coût total", "montant total",
    ),
    "advances_paid": (
        "totaal voorschotten reeds aangerekend", "voorschotten reeds aangerekend",
        "aangerekende voorschotten", "voorschotten betaald", "reeds betaalde voorschotten",
        "acomptes déjà facturés", "acomptes facturés", "acomptes payés", "acomptes déjà payés",
    ),
}


def _decimal_value(raw: str) -> Decimal | None:
    value = raw.replace(" ", "").replace("\u00a0", "")
    if "," in value:
        value = value.replace(".", "").replace(",", ".")
    elif value.count(".") == 1 and len(value.rsplit(".", 1)[1]) == 3:
        value = value.replace(".", "")
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def _fact(value: Any, chunk: KnowledgeChunk, excerpt: str) -> dict[str, Any]:
    return {
        "value": value,
        "page": chunk.page,
        "source": chunk.source,
        "excerpt": excerpt.strip()[:360],
    }


def _find_pattern(chunks: list[KnowledgeChunk], pattern: re.Pattern[str], *, group: int = 1) -> dict[str, Any] | None:
    for chunk in chunks:
        match = pattern.search(chunk.text)
        if match:
            value = match.group(group)
            start = max(0, match.start() - 65)
            end = min(len(chunk.text), match.end() + 65)
            return _fact(value, chunk, chunk.text[start:end])
    return None


def _find_period(chunks: list[KnowledgeChunk]) -> dict[str, Any] | None:
    for chunk in chunks:
        match = PERIOD_RE.search(chunk.text)
        if match:
            value = match.group(1)
            if match.group(2):
                value += f" tot {match.group(2)}"
            start = max(0, match.start() - 65)
            end = min(len(chunk.text), match.end() + 65)
            return _fact(value, chunk, chunk.text[start:end])
    return None


def _find_labeled_amount(chunks: list[KnowledgeChunk], labels: tuple[str, ...]) -> dict[str, Any] | None:
    label_pattern = "|".join(re.escape(label) for label in sorted(labels, key=len, reverse=True))
    # "Totaal factuurbedrag (incl.btw) € 9,65", "Aangerekende voorschotten - € 1.002,00", "Te betalen: 300,00 EUR".
    pattern = re.compile(
        rf"(?i)(?<!\w)({label_pattern})(?!\w)\s*(?:\((?:in|ex)cl\.?\s*btw\)|tvac|htva)?\s*:?\s*([-−]\s*)?(?:€\s*)?{AMOUNT_VALUE_PATTERN}(?:\s*(?:€|eur\b))?"
    )
    for chunk in chunks:
        for match in pattern.finditer(chunk.text):
            raw_amount = match.group(3)
            if not re.search(r"€|eur\b", match.group(0), re.I) and not re.search(r"[,.]\d{2}$", raw_amount):
                continue
            amount = _decimal_value(raw_amount)
            if amount is None:
                continue
            label = match.group(1).lower()
            negative = bool(match.group(2)) or amount < 0
            amount = abs(amount)
            # Advances are printed as a deduction ("- € 1.002,00"); their size is what counts. A minus
            # or "terug te krijgen" on the balance means money comes back.
            if "voorschot" not in label and "acompte" not in label and (
                negative or any(word in label for word in ("terug", "tegoed", "voordeel", "faveur", "rembours"))
            ):
                amount = -amount
            start = max(0, match.start() - 45)
            end = min(len(chunk.text), match.end() + 45)
            return _fact(amount, chunk, chunk.text[start:end])
    return None


def _find_electricity_table(chunks: list[KnowledgeChunk]) -> dict[str, Any] | None:
    """Day + night consumption from a meter table, only when each row's end minus begin equals its consumption."""
    # A table can be split over two chunks, and the chunk overlap repeats rows: collect every row once.
    seen: dict[tuple[Any, ...], Decimal] = {}
    first: tuple[KnowledgeChunk, re.Match[str]] | None = None
    for chunk in chunks:
        for row in ELECTRICITY_TABLE_ROW_RE.finditer(chunk.text):
            begin, end, used = (_decimal_value(row.group(n)) for n in (2, 3, 4))
            if None in (begin, end, used) or end - begin != used:
                return None
            seen.setdefault((row.group(1).lower(), begin, end, used), used)
            first = first or (chunk, row)
    if first is None:
        return None
    chunk, row = first
    fact = _fact(sum(seen.values()), chunk, chunk.text[max(0, row.start() - 30):row.end() + 120])
    fact["unit"] = "kwh"
    return fact


def _find_electricity_rows(chunks: list[KnowledgeChunk]) -> dict[str, Any] | None:
    for chunk in chunks:
        rows = [row for row in ELECTRICITY_ROW_RE.finditer(chunk.text) if not METER_READING_RE.search(chunk.text[max(0, row.start() - 80):row.start()])]
        if rows:
            total = sum((_decimal_value(row.group(2)) or Decimal(0)) for row in rows)
            fact = _fact(total, chunk, chunk.text[max(0, rows[0].start() - 30):rows[-1].end() + 10])
            fact["unit"] = "kwh"
            return fact
    return None


def _find_gas(chunks: list[KnowledgeChunk]) -> dict[str, Any] | None:
    for chunk in chunks:
        for pattern in (GAS_ROW_RE, GAS_CONSUMPTION_RE):
            for match in pattern.finditer(chunk.text):
                if METER_READING_RE.search(chunk.text[max(0, match.start() - 80):match.start()]) or re.search(
                    r"(?i)consumptie\s+$", chunk.text[max(0, match.start() - 12):match.start()]
                ):
                    continue
                fact = _fact(_decimal_value(match.group(1)), chunk, chunk.text[max(0, match.start() - 30):match.end() + 10])
                fact["unit"] = (match.group(2) if pattern is GAS_CONSUMPTION_RE else "kwh").lower()
                return fact
    return None


def _find_new_advance(chunks: list[KnowledgeChunk]) -> dict[str, Any] | None:
    for chunk in chunks:
        heading = NEW_ADVANCE_RE.search(chunk.text)
        rows = list(NEW_ADVANCE_ROW_RE.finditer(chunk.text, heading.end(), heading.end() + 400)) if heading else []
        if rows:
            fact = _fact(
                [{"energy": row.group(1).lower(), "frequency": row.group(2).lower(),
                  "old": _decimal_value(row.group(3)), "new": _decimal_value(row.group(4))} for row in rows],
                chunk, chunk.text[heading.start():rows[-1].end()],
            )
            return fact
        single = NEW_ADVANCE_SINGLE_RE.search(chunk.text)
        if single:
            frequency = (single.group(1) or "").strip().lower().removesuffix("e") or None
            return _fact([{"energy": None, "frequency": frequency, "old": None, "new": _decimal_value(single.group(2))}],
                         chunk, single.group(0))
    return None


def _find_consumption(chunks: list[KnowledgeChunk]) -> dict[str, Any] | None:
    for chunk in chunks:
        for match in CONSUMPTION_RE.finditer(chunk.text):
            if METER_READING_RE.search(chunk.text[max(0, match.start() - 80):match.end()]):
                continue
            start = max(0, match.start() - 65)
            return _fact(match.group(1), chunk, chunk.text[start:match.end() + 65])
    return None


def _format_decimal(value: Decimal) -> str:
    return f"€ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


_VAT_TOTAL_RE = re.compile(
    r"(?i)\b(?:totaal|total)\b[^\d€]{0,8}\(?\s*(excl\.?|exclusief|incl\.?|inclusief|htva|tvac)\b[^\d€]{0,25}(?:€\s*)?"
    r"((?:\d{1,3}(?:[ .]\d{3})+|\d+),\d{2})"
)


def _advance_lines_vat(text: str, amounts: list[Decimal | None], amount_due: dict[str, Any] | None) -> str | None:
    """'excl. btw' or 'incl. btw' only when the lines add up to a total the bill labels that way; else None."""
    if None in amounts:
        return None
    lines_total = sum(amounts)
    for match in _VAT_TOTAL_RE.finditer(text):
        if _decimal_value(match.group(2)) == lines_total:
            return "excl. btw" if match.group(1).lower().startswith(("excl", "htva")) else "incl. btw"
    if amount_due and amount_due["value"] == lines_total:
        return "incl. btw"
    return None


def analyze_invoice_chunks(chunks: list[KnowledgeChunk]) -> dict[str, Any]:
    """Extract conservative, page-linked facts and calculate only labeled totals."""
    ordered = sorted(chunks, key=lambda chunk: chunk.page)
    facts: dict[str, dict[str, Any] | None] = {
        "supplier": None,
        "billing_period": _find_period(ordered),
        "electricity_kwh": None,
        "amount_due": _find_labeled_amount(ordered, AMOUNT_LABELS["amount_due"]),
        "total_energy_cost": _find_labeled_amount(ordered, AMOUNT_LABELS["total_energy_cost"]),
        "advances_paid": _find_labeled_amount(ordered, AMOUNT_LABELS["advances_paid"]),
        "calculated_difference": None,
        "customer": None,
        "gas_kwh": _find_gas(ordered),
        "new_advance": _find_new_advance(ordered),
        "due_date": _find_pattern(ordered, DUE_DATE_RE),
        "invoice_type": None,
        "advance_lines": None,
    }
    # Paid by direct debit, whatever due date is also printed: the visitor does not have to transfer anything.
    debit = _find_pattern(ordered, DIRECT_DEBIT_RE)
    if debit:
        debit["direct_debit"] = True
        facts["due_date"] = debit
    text = " ".join(chunk.text for chunk in ordered)
    # The heading comes first: "Uw maandelijkse voorschotfactuur ... geen saldo van een jaarafrekening".
    advance_bill = ADVANCE_BILL_RE.search(text)
    settlement = SETTLEMENT_RE.search(text)
    if advance_bill and (not settlement or advance_bill.start() < settlement.start()) and not facts["total_energy_cost"] and not facts["advances_paid"]:
        chunk = next(chunk for chunk in ordered if ADVANCE_BILL_RE.search(chunk.text))
        facts["invoice_type"] = _fact("voorschotfactuur", chunk, advance_bill.group(0))
        rows: dict[tuple[str, str, Decimal | None], dict[str, Any]] = {}
        first_line: tuple[KnowledgeChunk, re.Match[str]] | None = None
        for chunk in ordered:
            for line in ADVANCE_LINE_RE.finditer(chunk.text):
                energy = "gas" if line.group(1).lower() == "aardgas" else line.group(1).lower()
                amount = _decimal_value(line.group(3))
                rows.setdefault((energy, line.group(2).lower(), amount), {"energy": energy, "period": line.group(2), "amount": amount})
                first_line = first_line or (chunk, line)
        if first_line:
            chunk, line = first_line
            fact = _fact(list(rows.values()), chunk, chunk.text[max(0, line.start() - 20):line.end() + 120])
            fact["vat"] = _advance_lines_vat(text, [row["amount"] for row in rows.values()], facts["amount_due"])
            facts["advance_lines"] = fact
    elif settlement:
        chunk = next(chunk for chunk in ordered if SETTLEMENT_RE.search(chunk.text))
        facts["invoice_type"] = _fact(SETTLEMENT_RE.search(chunk.text).group(0).lower(), chunk, SETTLEMENT_RE.search(chunk.text).group(0))

    for pattern in (CUSTOMER_RE, CUSTOMER_LABEL_RE):
        for chunk in ordered:
            customer = pattern.search(chunk.text)
            if customer:
                facts["customer"] = _fact(customer.group(1).rstrip(".,;"), chunk, customer.group(0))
                break
        if facts["customer"]:
            break
    for chunk in ordered:
        supplier = SUPPLIER_LABEL_RE.search(chunk.text) or re.search(r"(?i)\b(luminus|eneco|elegant)\b", chunk.text)
        if supplier:
            name = supplier.group(1).rstrip(".,;")
            facts["supplier"] = _fact(name if supplier.re is SUPPLIER_LABEL_RE else name.title(), chunk, supplier.group(0))
            break
    facts["electricity_kwh"] = (
        _find_electricity_table(ordered) or _find_electricity_rows(ordered) or _find_consumption(ordered)
    )
    fact = facts["electricity_kwh"]
    if fact and not isinstance(fact["value"], Decimal):
        match = CONSUMPTION_RE.search(fact["excerpt"])
        amount = _decimal_value(match.group(1)) if match else None
        if amount is None:
            facts["electricity_kwh"] = None
        else:
            fact["value"] = amount
            fact["unit"] = match.group(2).lower()

    total = facts["total_energy_cost"]
    advances = facts["advances_paid"]
    if total and advances:
        difference = total["value"] - advances["value"]
        facts["calculated_difference"] = {
            "value": difference,
            "page": total["page"],
            "source": total["source"],
            "excerpt": f"Berekening: {total['value']} totale energiekosten − {advances['value']} voorschotten.",
            "input_pages": [total["page"], advances["page"]],
        }

    labels = {
        "supplier": "Leverancier",
        "billing_period": "Factuurperiode",
        "electricity_kwh": "Elektriciteitsverbruik",
        **({"gas_kwh": "Gasverbruik"} if facts["gas_kwh"] else {}),
        "amount_due": (
            "Vermeld bedrag terug te krijgen"
            if facts["amount_due"] and facts["amount_due"]["value"] < 0
            else "Vermeld bedrag te betalen"
        ),
        "total_energy_cost": "Totale energiekosten",
        "advances_paid": "Voorschotten aangerekend",
        "calculated_difference": (
            "Rekenverschil"
            if facts["calculated_difference"] is None
            else
            "Rekenverschil (kosten hoger dan voorschotten)"
            if facts["calculated_difference"]["value"] > 0
            else "Rekenverschil (voorschotten hoger dan kosten)"
            if facts["calculated_difference"]["value"] < 0
            else "Rekenverschil (kosten en voorschotten gelijk)"
        ),
    }
    lines = ["Dit kon ik op Karen’s backend uit de PDF halen:"]
    sources: list[dict[str, str]] = []
    seen: set[tuple[str, int, str]] = set()
    for key, label in labels.items():
        item = facts[key]
        if item is None:
            lines.append(f"- {label}: niet betrouwbaar gevonden.")
            continue
        value = item["value"]
        if key in ("calculated_difference", "amount_due"):
            if value < 0:
                value = -value
            value = _format_decimal(value)
        elif isinstance(value, Decimal):
            value = (
                f"{value} {'m³' if item.get('unit') in ('m3', 'm³') else 'kWh'}"
                if key in ("electricity_kwh", "gas_kwh") else _format_decimal(value)
            )
        lines.append(f"- {label}: {value} (PDF-pagina {item['page']}).")
        source_key = (item["source"], item["page"], item["excerpt"])
        if source_key not in seen:
            seen.add(source_key)
            sources.append({
                "title": item["source"],
                "detail": f"PDF · pagina {item['page']}",
                "excerpt": item["excerpt"],
            })
    if not total or not advances:
        lines.append("Ik bereken geen verschil zonder zowel herkenbare totale kosten als voorschotten.")
    else:
        lines.append("Dit rekenverschil tussen deze twee bedragen bevestigt niet op zichzelf het uiteindelijke afrekensaldo.")
    return {"answer": "\n".join(lines), "facts": facts, "sources": sources}


# Follow-up questions about a fact read at upload; checked in this order, so "welke voorschotten
# heb ik betaald" is about advances and not about the amount still to pay.
FACT_QUESTIONS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("new_advance", re.compile(r"(?i)\b(?:nieuwe?|volgende|toekomstige?)\s+voorschot|voorschot\w*\s+(?:wordt|worden|zal|gaat)|voorschot\w*\s+(?:nu|straks|vanaf)")),
    ("advances_paid", re.compile(r"(?i)\bvoorschot")),
    ("due_date", re.compile(r"(?i)\b(?:wanneer|tegen\s+wanneer|vervaldatum|vervaldag|uiterlijk|tot\s+welke\s+datum)\b.*\bbetal|\bvervaldatum|\bvervaldag")),
    ("amount_due", re.compile(r"(?i)\b(?:betalen|betaal\b|factuurbedrag|saldo|openstaand|tegoed|terug\s*(?:krijg|betaal)|krijg\s+ik\s+(?:\w+\s+)?(?:geld\s+)?terug)")),
    ("total_energy_cost", re.compile(r"(?i)\b(?:totale?\s+(?:energie)?kost|totaal)")),
    ("gas_kwh", re.compile(r"(?i)\bgas\w*\b.*\b(?:verbruik|kwh|m3|m³)|\bgasverbruik|\b(?:verbruik|kwh)\w*\b.*\bgas")),
    ("electricity_kwh", re.compile(r"(?i)\b(?:verbruik|kwh|hoeveel\s+stroom)")),
    ("billing_period", re.compile(r"(?i)\b(?:periode|welke\s+maanden|van\s+wanneer|tot\s+wanneer)")),
    ("supplier", re.compile(r"(?i)\b(?:leverancier|welk\s+bedrijf|van\s+wie\s+is)")),
    ("customer", re.compile(r"(?i)\b(?:welke\s+persoon|wie\s+(?:is|staat)\s+(?:de\s+)?(?:klant|op)|op\s+(?:wiens|welke)\s+naam|klantnaam|naam\s+van\s+de\s+klant|voor\s+wie)")),
)
# "Leg mijn factuur uit": the overview of everything read at upload.
OVERVIEW_QUESTION = re.compile(
    r"(?i)\b(?:uitleg|leg\b.*\buit|uitleggen|samenvat|overzicht|wat\s+staat\s+(?:er\s+)?(?:op|in)|wat\s+betekent|"
    r"explain|summar|résum|expliqu)"
)
OVERVIEW_LABELS = {
    "invoice_type": "Soort factuur",
    "supplier": "Leverancier",
    "billing_period": "Periode",
    "electricity_kwh": "Stroomverbruik",
    "gas_kwh": "Gasverbruik",
    "total_energy_cost": "Totale energiekosten",
    "advances_paid": "Voorschotten aangerekend",
    "advance_lines": "Voorschot per energie",
    "amount_due": "Nog te betalen",
    "new_advance": "Nieuw voorschot",
    "due_date": "Betalen vóór",
}
FACT_NAMES = {
    "advances_paid": "de aangerekende voorschotten",
    "amount_due": "een vermeld bedrag te betalen",
    "total_energy_cost": "de totale energiekosten",
    "electricity_kwh": "je verbruik",
    "billing_period": "de factuurperiode",
    "supplier": "de leverancier",
    "customer": "de naam van de klant",
    "gas_kwh": "je gasverbruik",
    "new_advance": "je nieuwe voorschot",
    "due_date": "de datum waarop je moet betalen",
}


def _fact_sentence(key: str, fact: dict[str, Any]) -> str:
    value = fact["value"]
    page = fact["page"]
    if key == "advances_paid":
        return f"Volgens deze factuur zijn er {_format_decimal(value)} aan voorschotten aangerekend (PDF-pagina {page})."
    if key == "amount_due":
        if value < 0:
            return f"Volgens deze factuur krijg je {_format_decimal(-value)} terug (PDF-pagina {page})."
        if value == 0:
            return f"Volgens deze factuur moet je niets meer betalen (PDF-pagina {page})."
        return f"Volgens deze factuur moet je nog {_format_decimal(value)} betalen (PDF-pagina {page})."
    if key == "gas_kwh":
        unit = "m³" if fact.get("unit") in ("m3", "m³") else "kWh"
        return f"Je gasverbruik op deze factuur is {value} {unit} (PDF-pagina {page})."
    if key == "new_advance":
        rows = "; ".join(
            " ".join(part for part in (row["energy"], _format_decimal(row["new"]), row["frequency"]) if part)
            + (f" (was {_format_decimal(row['old'])})" if row["old"] is not None else "")
            for row in value
        )
        return f"Je nieuwe voorschot volgens deze factuur: {rows} (PDF-pagina {page})."
    if key == "total_energy_cost":
        return f"De totale energiekosten op deze factuur zijn {_format_decimal(value)} (PDF-pagina {page})."
    if key == "electricity_kwh":
        unit = "m³" if fact.get("unit") in ("m3", "m³") else "kWh"
        return f"Je stroomverbruik op deze factuur is {value} {unit} (PDF-pagina {page})."
    if key == "due_date":
        if fact.get("direct_debit"):
            return f"Je hoeft niets te doen: je bank betaalt dit bedrag op {value} via domiciliëring (PDF-pagina {page})."
        return f"Volgens deze factuur betaal je vóór {value} (PDF-pagina {page})."
    if key == "billing_period":
        return f"Deze factuur gaat over de periode {value} (PDF-pagina {page})."
    if key == "customer":
        return f"Deze factuur staat op naam van {value} (PDF-pagina {page})."
    return f"Deze factuur is van {value} (PDF-pagina {page})."


def _overview_value(key: str, fact: dict[str, Any]) -> str:
    value = fact["value"]
    if key in ("electricity_kwh", "gas_kwh"):
        return f"{value} {'m³' if fact.get('unit') in ('m3', 'm³') else 'kWh'}"
    if key == "advance_lines":
        lines = "; ".join(f"{row['energy']} {row['period']} {_format_decimal(row['amount'])}" for row in value)
        return f"{lines} ({fact['vat']})" if fact.get("vat") else lines
    if key == "due_date" and fact.get("direct_debit"):
        return f"{value} (je bank betaalt via domiciliëring)"
    if key == "new_advance":
        return "; ".join(
            " ".join(part for part in (row["energy"], _format_decimal(row["new"]), row["frequency"]) if part) for row in value
        )
    return _format_decimal(value) if isinstance(value, Decimal) else str(value)


def _overview_answer(facts: dict[str, Any]) -> dict[str, Any]:
    found = [(key, facts[key]) for key in OVERVIEW_LABELS if facts.get(key)]
    if not found:
        return {
            "answer": (
                "Ik kon op deze factuur geen leverancier, periode, verbruik of bedragen herkennen. "
                "Vraag me gerust naar een specifiek onderdeel, dan zoek ik de passage op."
            ),
            "sources": [],
        }
    lines = ["Dit staat op je factuur:"]
    for key, fact in found:
        label, value = OVERVIEW_LABELS[key], _overview_value(key, fact)
        if key == "amount_due" and fact["value"] < 0:
            label, value = "Terug te krijgen", _format_decimal(-fact["value"])
        lines.append(f"- **{label}:** {value} (PDF-pagina {fact['page']})")
    difference = facts.get("calculated_difference")
    if not facts.get("amount_due") and difference is not None:
        value = difference["value"]
        lines.append(
            f"Er staat geen bedrag te betalen vermeld; je kosten zijn {_format_decimal(abs(value))} "
            f"{'meer' if value > 0 else 'minder'} dan je voorschotten, maar dat bevestigt niet op zichzelf het eindsaldo."
        )
    sources: list[dict[str, str]] = []
    for _, fact in found:
        source = _fact_source(fact)
        if source not in sources:
            sources.append(source)
    return {"answer": "\n".join(lines), "sources": sources}


def _passage_excerpt(text: str, question: str, width: int = 260) -> str:
    """The part of a passage where the question's words cluster, not just its first characters."""
    if len(text) <= width:
        return text
    lowered = text.lower()
    terms = {token.lower() for token in TOKEN_RE.findall(question) if token.lower() not in STOP_WORDS and len(token) > 2}
    hits = sorted(match.start() for term in terms for match in re.finditer(re.escape(term), lowered))
    if not hits:
        return text[:width].rsplit(" ", 1)[0] + "…"
    best = max(hits, key=lambda start: sum(start <= hit < start + width for hit in hits))
    start = max(0, best - width // 4)
    end = min(len(text), start + width)
    if start > 0:
        start = text.find(" ", start) + 1 or start
    if end < len(text):
        end = text.rfind(" ", start, end) if text.rfind(" ", start, end) > start else end
    return ("…" if start > 0 else "") + text[start:end].strip() + ("…" if end < len(text) else "")


def _fact_source(fact: dict[str, Any]) -> dict[str, str]:
    return {"title": fact["source"], "detail": f"PDF · pagina {fact['page']}", "excerpt": fact["excerpt"]}


def _fact_answer(question: str, facts: dict[str, Any]) -> dict[str, Any] | None:
    """A direct answer from the facts read at upload, with the passage it came from."""
    key = next((name for name, pattern in FACT_QUESTIONS if pattern.search(question)), None)
    if key is None:
        return _overview_answer(facts) if OVERVIEW_QUESTION.search(question) else None
    fact = facts.get(key)
    invoice_type = facts.get("invoice_type")
    if fact is None and invoice_type and invoice_type["value"] == "voorschotfactuur" and key in (
        "electricity_kwh", "gas_kwh", "advances_paid", "total_energy_cost",
    ):
        # An advance bill has no consumption or settlement; say what it is instead of "not found".
        lines = facts.get("advance_lines")
        due = facts.get("amount_due")
        parts = ["Dit is een voorschotfactuur: een vast bedrag op basis van je geschatte verbruik."]
        if due:
            vat = f" {lines['vat']}" if lines and lines.get("vat") else ""
            detail = (" (" + ", ".join(f"{row['energy']} {_format_decimal(row['amount'])}" for row in lines["value"]) + vat + ")") if lines else ""
            parts.append(f"Het voorschot is {_format_decimal(due['value'])}{detail} (PDF-pagina {due['page']}).")
        if key in ("electricity_kwh", "gas_kwh"):
            parts.append("Je werkelijke verbruik staat niet op deze factuur, maar op je jaarafrekening.")
        else:
            parts.append("Wat je echt verbruikt hebt en wat je in totaal aan voorschotten betaalde, zie je op je jaarafrekening.")
        sources = [_fact_source(item) for item in (invoice_type, due, lines) if item]
        return {"answer": " ".join(parts), "sources": sources}
    if fact is not None:
        answer = {"answer": _fact_sentence(key, fact), "sources": [_fact_source(fact)]}
        due = facts.get("due_date")
        if key == "amount_due" and fact["value"] > 0 and due:
            if due.get("direct_debit"):
                answer["answer"] = (
                    f"Deze factuur bedraagt {_format_decimal(fact['value'])} (PDF-pagina {fact['page']}). " + _fact_sentence("due_date", due)
                )
            else:
                answer["answer"] += " " + _fact_sentence("due_date", due)
            answer["sources"].append(_fact_source(due))
        gas = facts.get("gas_kwh")
        # "Wat is mijn verbruik?" on a bill with both energies names both.
        if key == "electricity_kwh" and gas and not re.search(r"(?i)stroom|elektric", question):
            answer["answer"] += " " + _fact_sentence("gas_kwh", gas)
            answer["sources"].append(_fact_source(gas))
        return answer
    difference = facts.get("calculated_difference")
    if key == "amount_due" and difference is not None:
        # A calculation is never presented as the balance the invoice states.
        value = difference["value"]
        direction = "meer" if value > 0 else "minder"
        return {
            "answer": (
                "Ik vond op deze factuur geen duidelijk vermeld bedrag te betalen. "
                f"Je totale energiekosten zijn {_format_decimal(abs(value))} {direction} dan je voorschotten "
                f"(PDF-pagina {difference['page']}), maar dat rekenverschil bevestigt niet op zichzelf het uiteindelijke afrekensaldo."
            ),
            "sources": [_fact_source(facts[name]) for name in ("total_energy_cost", "advances_paid") if facts.get(name)],
        }
    return {"answer": f"Ik vond {FACT_NAMES[key]} niet duidelijk op deze factuur.", "sources": []}


def answer_uploaded_pdf_question(
    question: str, chunks: list[KnowledgeChunk], facts: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Answer locally from the facts read at upload, else by returning matched PDF passages, without hosted inference."""
    if facts:
        direct = _fact_answer(question, facts)
        if direct is not None:
            return direct
    selected = retrieve(chunks, question, limit=3)
    if not selected:
        return {
            "answer": "Ik vond geen duidelijke passage in je PDF om deze vraag mee te beantwoorden.",
            "sources": [],
        }
    sources = [
        {
            "title": chunk.source,
            "detail": f"PDF · pagina {chunk.page}",
            "excerpt": chunk.text[:600],
        }
        for chunk in selected
    ]
    quoted_evidence = _passage_excerpt(selected[0].text, question)
    return {
        "answer": f"Ik vond een relevante passage op PDF-pagina {selected[0].page}: “{quoted_evidence}”",
        "sources": sources,
    }
