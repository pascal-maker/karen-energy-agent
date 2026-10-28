"""Local-only extraction and evidence retrieval for uploaded energy bills."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from .knowledge import KnowledgeChunk, retrieve


AMOUNT_VALUE_PATTERN = r"(-?\d{1,3}(?:[ .]\d{3})*(?:,\d{1,2})?|-?\d+(?:[.,]\d{1,2})?)"
PERIOD_RE = re.compile(
    r"(?i)\b(?:factuurperiode|periode(?: van)?|afrekening(?: over| voor)?)\s*[:\-]?\s*"
    r"(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})"
    r"(?:\s*(?:tot en met|tot|t/m|[-–])\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}))?"
)
CONSUMPTION_RE = re.compile(
    r"(?i)\b(?:elektriciteitsverbruik|stroomverbruik|verbruik|afname)\b[^\d\n]{0,45}"
    r"(\d[\d .]*(?:,\d{1,2})?)\s*(kwh|m³|m3)\b"
)

AMOUNT_LABELS: dict[str, tuple[str, ...]] = {
    "amount_due": (
        "totaal te betalen", "saldo te betalen", "te betalen saldo", "te betalen",
    ),
    "total_energy_cost": (
        "totale energiekosten", "totale energiekost", "totaal energiekosten",
        "totaal kosten", "totaalkost",
    ),
    "advances_paid": (
        "totaal voorschotten reeds aangerekend", "voorschotten reeds aangerekend",
        "voorschotten betaald", "reeds betaalde voorschotten",
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
    pattern = re.compile(
        rf"(?i)\b({label_pattern})\b\s*[:\-]?\s*(?:€\s*)?{AMOUNT_VALUE_PATTERN}(?:\s*€)?"
    )
    for chunk in chunks:
        match = pattern.search(chunk.text)
        if match:
            raw_amount = match.group(2)
            if "€" not in match.group(0) and not re.search(r"[,.]\d{2}\b", raw_amount):
                continue
            amount = _decimal_value(raw_amount)
            if amount is not None:
                start = max(0, match.start() - 45)
                end = min(len(chunk.text), match.end() + 45)
                return _fact(amount, chunk, chunk.text[start:end])
    return None


def _format_decimal(value: Decimal) -> str:
    return f"€ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


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
    }

    for chunk in ordered:
        supplier = re.search(r"(?i)\b(luminus|eneco|elegant)\b", chunk.text)
        if supplier:
            facts["supplier"] = _fact(supplier.group(1).title(), chunk, supplier.group(0))
            break
    facts["electricity_kwh"] = _find_pattern(ordered, CONSUMPTION_RE)
    if facts["electricity_kwh"]:
        fact = facts["electricity_kwh"]
        match = CONSUMPTION_RE.search(fact["excerpt"])
        if match:
            amount = _decimal_value(match.group(1))
            if amount is not None:
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
        "amount_due": "Vermeld bedrag te betalen",
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
        if key == "calculated_difference":
            if value < 0:
                value = -value
            value = _format_decimal(value)
        elif isinstance(value, Decimal):
            value = f"{value} kWh" if key == "electricity_kwh" else _format_decimal(value)
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
    ("advances_paid", re.compile(r"(?i)\bvoorschot")),
    ("amount_due", re.compile(r"(?i)\b(?:nog\s+(?:te\s+)?betalen|moet\s+ik\s+(?:nog\s+)?(?:\w+\s+)?betalen|te\s+betalen|saldo|openstaand)")),
    ("total_energy_cost", re.compile(r"(?i)\b(?:totale?\s+(?:energie)?kost|totaal)")),
    ("electricity_kwh", re.compile(r"(?i)\b(?:verbruik|kwh|hoeveel\s+stroom)")),
    ("billing_period", re.compile(r"(?i)\b(?:periode|welke\s+maanden|van\s+wanneer|tot\s+wanneer)")),
    ("supplier", re.compile(r"(?i)\b(?:leverancier|welk\s+bedrijf|van\s+wie\s+is)")),
)
FACT_NAMES = {
    "advances_paid": "de aangerekende voorschotten",
    "amount_due": "een vermeld bedrag te betalen",
    "total_energy_cost": "de totale energiekosten",
    "electricity_kwh": "je verbruik",
    "billing_period": "de factuurperiode",
    "supplier": "de leverancier",
}


def _fact_sentence(key: str, fact: dict[str, Any]) -> str:
    value = fact["value"]
    page = fact["page"]
    if key == "advances_paid":
        return f"Volgens deze factuur zijn er {_format_decimal(value)} aan voorschotten aangerekend (PDF-pagina {page})."
    if key == "amount_due":
        return f"Volgens deze factuur moet je nog {_format_decimal(value)} betalen (PDF-pagina {page})."
    if key == "total_energy_cost":
        return f"De totale energiekosten op deze factuur zijn {_format_decimal(value)} (PDF-pagina {page})."
    if key == "electricity_kwh":
        unit = "m³" if fact.get("unit") in ("m3", "m³") else "kWh"
        return f"Je verbruik op deze factuur is {value} {unit} (PDF-pagina {page})."
    if key == "billing_period":
        return f"Deze factuur gaat over de periode {value} (PDF-pagina {page})."
    return f"Deze factuur is van {value} (PDF-pagina {page})."


def _fact_source(fact: dict[str, Any]) -> dict[str, str]:
    return {"title": fact["source"], "detail": f"PDF · pagina {fact['page']}", "excerpt": fact["excerpt"]}


def _fact_answer(question: str, facts: dict[str, Any]) -> dict[str, Any] | None:
    """A direct answer from the facts read at upload, with the passage it came from."""
    key = next((name for name, pattern in FACT_QUESTIONS if pattern.search(question)), None)
    if key is None:
        return None
    fact = facts.get(key)
    if fact is not None:
        return {"answer": _fact_sentence(key, fact), "sources": [_fact_source(fact)]}
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
    quoted_evidence = selected[0].text[:260]
    return {
        "answer": f"Ik vond een relevante passage op PDF-pagina {selected[0].page}: “{quoted_evidence}”",
        "sources": sources,
    }
