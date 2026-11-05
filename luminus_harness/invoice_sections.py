"""Structure-keeping sections of an uploaded invoice, for retrieval and for reading typed addresses.

The fact reader (invoice_analysis) keeps its flattened 1000-character chunks; this module works on the
same pages with their line breaks, so a table keeps its header, every row keeps its label, unit and
btw column, and an address stays with its label. Sections never overlap, so no amount is counted twice.
Document text is data: nothing here follows what it says.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from io import BytesIO
from typing import Literal

MAX_SECTION_CHARS = 800

SectionKind = Literal["fields", "address", "table", "text"]
AddressKind = Literal["billing", "delivery", "unknown"]
Party = Literal["customer", "supplier", "unknown"]


@dataclass(frozen=True)
class Section:
    source: str
    page: int
    kind: SectionKind
    label: str  # the table caption, the address label, or "" for plain text and field groups
    lines: tuple[str, ...]  # the smallest units an answer may quote: one field, one table row, one sentence

    @property
    def text(self) -> str:
        if self.kind == "table" and self.label:
            return "\n".join((self.label, *self.lines))
        return "\n".join(self.lines)


@dataclass(frozen=True)
class AddressBlock:
    kind: AddressKind
    party: Party
    label: str
    value: str
    page: int
    source: str
    excerpt: str


# "Label: value", the label a few words long. "Opzegtermijn: 3 weken" and "Leveringsadres:" are fields;
# "Verhuist u? Geef ..." is not.
_FIELD = re.compile(r"^(?P<label>[^\W\d_][\w\s.()/'’&%-]{0,45}?)\s*:\s*(?P<value>.*)$")
_MONTH = (
    r"jan|feb|mrt|maa|apr|mei|jun|jul|aug|sep|okt|nov|dec|janv|févr|fevr|mars|avr|mai|juin|juil|août|aout|sept|oct|déc"
    r"|januari|februari|maart|april|juni|juli|augustus|september|oktober|november|december"
)
# One table cell: an amount, a quantity with its unit, a percentage, a date or a month.
_VALUE_CELL = re.compile(
    r"^(?:[−–-]\s*)?(?:€\s*)?\d[\d .,'\u00a0]*\s*(?:€|eur|euro|kwh|mwh|kw|kva|m³|m3|%|c€/kwh|€/kwh|cent/kwh)$"
    r"|^(?:[−–-]\s*)?€\s*\d[\d .,'\u00a0]*$|^(?:[−–-]\s*)?\d[\d.\u00a0 ]*,\d{1,3}$|^\d{3,}$"
    rf"|^(?:\d{{1,2}}\s+)?(?:{_MONTH})\w*\.?\s+\d{{4}}$"
    r"|^\d{1,2}[./-]\d{1,2}[./-]\d{2,4}$",
    re.IGNORECASE,
)
_DELIVERY = re.compile(
    r"(?i)\b(?:leverings?adres|afname\w*adres|adres van (?:levering|afname)|verbruiksadres|plaats van levering|"
    r"adresse de (?:fourniture|consommation|livraison)|point de fourniture|lieu de fourniture|delivery address|supply address)\b"
)
_BILLING = re.compile(
    r"(?i)\b(?:facturatie\w*adres|factuuradres|adres facturatie|correspondentieadres|verzendadres|postadres|"
    r"adresse de (?:facturation|correspondance)|billing address|invoice address|postal address)\b"
)
_ANY_ADDRESS = re.compile(r"(?i)^(?:adres|adresse|address|domicilie|woonplaats)$")
_CUSTOMER = re.compile(r"(?i)^(?:klant|klantnaam|naam(?: klant)?|titularis|klanthouder|client|nom|titulaire|customer)$")
_SUPPLIER = re.compile(r"(?i)^(?:leverancier|fournisseur|supplier|energieleverancier)$")
# The line that ends an address: a Belgian (or Dutch) postcode and a place.
_POSTCODE = re.compile(r"\b(?:\d{4}|\d{4}\s?[A-Z]{2})\s+[^\W\d_][\w'’ -]*$")


def page_texts_from_pdf(pdf_data: bytes) -> list[tuple[int, str]]:
    """Each page's selectable text with its line breaks, read in memory."""
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(pdf_data), strict=False)
    return [(number, page.extract_text() or "") for number, page in enumerate(reader.pages, start=1)]


def _lines(text: str) -> list[str]:
    return [" ".join(line.split()) for line in text.splitlines() if line.strip()]


def _address_kind(label: str) -> AddressKind | None:
    if _DELIVERY.search(label):
        return "delivery"
    if _BILLING.search(label):
        return "billing"
    if _ANY_ADDRESS.match(label.strip()):
        return "unknown"
    return None


def _is_sentence(line: str) -> bool:
    return len(line) > 70 or (line.endswith((".", "?", "!")) and len(line.split()) > 4)


def _bounded(lines: list[str], limit: int) -> list[list[str]]:
    """Consecutive lines packed up to `limit` characters, a too long line cut at word boundaries; no overlap."""
    pieces: list[str] = []
    for line in lines:
        while len(line) > limit:
            cut = line.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            pieces.append(line[:cut].strip())
            line = line[cut:].strip()
        if line:
            pieces.append(line)
    groups: list[list[str]] = [[]]
    for piece in pieces:
        if groups[-1] and len("\n".join(groups[-1] + [piece])) > limit:
            groups.append([])
        groups[-1].append(piece)
    return [group for group in groups if group]


def _table_rows(run: list[str]) -> tuple[str, list[str], list[str]] | None:
    """A run of short lines with value cells: (caption, rows, trailing lines), or None when there are no cells."""
    first = next((index for index, line in enumerate(run) if _VALUE_CELL.match(line)), None)
    if first is None or first == 0:
        return None
    header = run[: first - 1]
    rows: list[str] = []
    index = first - 1
    columns: list[str] | None = None
    caption: list[str] = header
    while index < len(run):
        label = run[index]
        if _is_sentence(label):
            break
        cells: list[str] = []
        index += 1
        while index < len(run) and _VALUE_CELL.match(run[index]):
            cells.append(run[index])
            index += 1
        if columns is None and cells:
            # The last header lines name the value columns; the lines before them are the caption.
            if len(header) >= len(cells):
                columns, caption = header[len(header) - len(cells):], header[: len(header) - len(cells)]
            else:
                columns = []
        if cells and columns and len(columns) == len(cells):
            rows.append(f"{label} — " + " · ".join(f"{name}: {cell}" for name, cell in zip(columns, cells)))
        elif cells:
            rows.append(f"{label}: " + " · ".join(cells))
        else:
            rows.append(label)
    return " · ".join(caption), rows, run[index:]


def _text_sections(run: list[str], page: int, source: str) -> list[Section]:
    return [Section(source, page, "text", "", tuple(group)) for group in _bounded(run, MAX_SECTION_CHARS)]


def _run_sections(run: list[str], page: int, source: str) -> list[Section]:
    table = _table_rows(run)
    if table is None:
        return _text_sections(run, page, source)
    caption, rows, rest = table
    sections: list[Section] = []
    # A long table is split by rows; every part keeps the caption, no row is repeated.
    for group in _bounded(rows, MAX_SECTION_CHARS - len(caption) - 1):
        sections.append(Section(source, page, "table", caption, tuple(group)))
    return sections + (_run_sections(rest, page, source) if rest else [])


def build_sections(pages: list[tuple[int, str]], source: str) -> list[Section]:
    """Field groups, typed address blocks, tables with their headers, and bounded text, page by page."""
    sections: list[Section] = []
    for page, page_text in pages:
        lines = _lines(page_text)
        fields: list[str] = []
        run: list[str] = []

        def flush_fields() -> None:
            if fields:
                sections.extend(Section(source, page, "fields", "", tuple(group)) for group in _bounded(fields, MAX_SECTION_CHARS))
                fields.clear()

        def flush_run() -> None:
            if run:
                sections.extend(_run_sections(run, page, source))
                run.clear()

        index = 0
        while index < len(lines):
            line = lines[index]
            field = _FIELD.match(line)
            if field and not _is_sentence(field.group("label")):
                label, value = field.group("label").strip(), field.group("value").strip()
                kind = _address_kind(label)
                if kind is not None:
                    parts = [value] if value else []
                    # The address may continue on the next lines, up to its postcode and place.
                    while (not parts or not _POSTCODE.search(parts[-1])) and index + 1 < len(lines) and len(parts) < 3:
                        following = lines[index + 1]
                        if _FIELD.match(following) or _is_sentence(following):
                            break
                        parts.append(following)
                        index += 1
                    flush_run()
                    flush_fields()
                    sections.append(Section(source, page, "address", label, (f"{label}: {', '.join(parts)}",)))
                else:
                    flush_run()
                    fields.append(line)
            else:
                flush_fields()
                run.append(line)
            index += 1
        flush_run()
        flush_fields()
    return sections


def addresses_from(pages: list[tuple[int, str]], source: str) -> list[AddressBlock]:
    """Typed addresses with the party they belong to. A typed label without a party is the customer's
    (delivery and billing addresses on an energy bill are the customer's); a bare "Adres:" is the customer's
    when the last party field before it names the customer, else its party is unknown."""
    found: list[AddressBlock] = []
    for page, page_text in pages:
        party: Party = "unknown"
        lines = _lines(page_text)
        for index, line in enumerate(lines):
            field = _FIELD.match(line)
            if not field:
                continue
            label, value = field.group("label").strip(), field.group("value").strip()
            if _CUSTOMER.match(label):
                party = "customer"
                continue
            if _SUPPLIER.match(label):
                party = "supplier"
                continue
            kind = _address_kind(label)
            if kind is None:
                continue
            parts = [value] if value else []
            step = index
            while (not parts or not _POSTCODE.search(parts[-1])) and step + 1 < len(lines) and len(parts) < 3:
                following = lines[step + 1]
                if _FIELD.match(following) or _is_sentence(following):
                    break
                parts.append(following)
                step += 1
            if not parts or not _POSTCODE.search(parts[-1]):
                continue  # no recognisable address after the label: unknown stays null
            owner: Party = party if kind == "unknown" else (party if party != "unknown" else "customer")
            block = AddressBlock(kind, owner, label, ", ".join(parts), page, source, f"{label}: {', '.join(parts)}")
            if all((other.kind, other.value) != (block.kind, block.value) for other in found):
                found.append(block)
    return found
