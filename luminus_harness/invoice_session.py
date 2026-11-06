"""One visitor's uploaded invoice and the conversation about it, held in memory for the upload session's lifetime.

What the session adds to the fact reader (invoice_analysis):
- typed addresses with their party (invoice_sections.addresses_from) in the invoice object;
- structure-keeping sections and a hybrid index over them (invoice_retrieval) for questions no fact answers;
- the last turns, so "en waar woont ze?" and "is dit bedrag inclusief btw?" resolve only when the earlier
  answer makes the reference clear, and otherwise Karen asks.

Nothing here calls a hosted model or writes to disk; a session never sees another session's text. Document
text is data: a line in the PDF that reads like an instruction is quoted or ignored, never followed.
Passages shown to the visitor come from their own upload and keep their words; identifiers (customer, meter
and account numbers) stay masked unless the question asks for that identifier.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from .invoice_analysis import analyze_invoice_chunks, answer_uploaded_pdf_question
from .invoice_model import _WORDS, mask_identifiers
from .invoice_retrieval import Hit, InvoiceIndex, content_terms, embed, expected_value, has_value, terms
from .invoice_schema import FOLLOW_UP_SUGGESTIONS, Invoice, build_invoice
from .invoice_sections import Section, addresses_from, build_sections, page_texts_from_pdf
from .knowledge import KnowledgeChunk, load_pdf_chunks_from_bytes
from .photo_invoice import chunks_from_text

MAX_TURNS = 6
AddressIntent = Literal["residence", "delivery", "billing", "address"]


@dataclass(frozen=True)
class AmountRef:
    """An amount an answer gave as its answer, with the field it was read from."""

    amount: str  # as printed: "€ 42,00", "179,00 €"
    field: str  # the line or table cell it stands in: "Gas — Btw 6%: € 42,00", "Totaal te betalen: € 172,00"
    column: str = ""  # the table column or field label: "Btw 6%", "Totaal te betalen"
    page: int = 0


@dataclass(frozen=True)
class Turn:
    question: str
    answer: str
    amounts: tuple[AmountRef, ...]  # the values the answer was about; "dit bedrag" means one of these
    named_customer: bool  # the answer named the bill's customer


@dataclass
class InvoiceSession:
    chunks: list[KnowledgeChunk]
    analysis: dict[str, Any]
    sections: list[Section]
    filename: str
    turns: list[Turn] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.index = InvoiceIndex.build(self.sections)

    @property
    def invoice(self) -> Invoice:
        return self.analysis["invoice"]

    @property
    def facts(self) -> dict[str, Any]:
        return self.analysis.get("facts") or {}

    # --- answering ---------------------------------------------------------------------------------

    def ask(self, question: str) -> dict[str, Any]:
        question = " ".join(question.split())
        result = self._answer(question)
        refs = result.pop("refs", None)
        for source in result.get("sources", []):
            source.pop("masked", None)
        self._remember(question, result, refs)
        return result

    def _answer(self, question: str) -> dict[str, Any]:
        intent = address_intent(question, self._customer_parts())
        if intent is not None:
            return self._address_answer(question, intent)
        if _THIS_AMOUNT.search(question):
            return self._this_amount_answer(question)
        return answer_uploaded_pdf_question(question, self.chunks, self.facts, self.invoice, passages=self._passages)

    def _remember(self, question: str, result: dict[str, Any], refs: list[AmountRef] | None) -> None:
        answer = str(result.get("answer", ""))
        if refs is None:
            # A fact answer names its value first ("Volgens deze factuur moet je nog € 172,00 betalen"); the
            # amounts after it (a due date's sentence, a calculation) are context. Two different amounts in the
            # leading sentence make "dit bedrag" ambiguous.
            lead = re.split(r"(?<=[.!?])\s", answer, maxsplit=1)[0]
            amounts = list(dict.fromkeys(_EURO.findall(lead)))
            if _CALCULATION.search(lead):
                amounts = amounts[-1:]  # "12 × € 179,00 = € 2.148,00": the result
            excerpts = [source.get("excerpt", "") for source in result.get("sources", [])]
            refs = [ref for amount in amounts for ref in self._refs_for(amount, excerpts)]
        customer = self.invoice.customer.value if self.invoice.customer else ""
        named = bool(customer) and customer.lower() in answer.lower()
        self.turns.append(Turn(question, answer, tuple(refs), named))
        del self.turns[:-MAX_TURNS]

    def _refs_for(self, amount: str, excerpts: list[str]) -> list[AmountRef]:
        """The field this amount was read from. The evidence passage of the answer decides when the same amount
        stands in more than one field (€ 700,00 due and € 700,00 gas excl. btw); without evidence for one,
        every field holding it is returned and "dit bedrag" asks which."""
        fields = self._fields_holding(_decimal(amount))
        evidenced = [field for field in fields if any(_label_before_value(field[1], field[2], excerpt) for excerpt in excerpts)]
        chosen = evidenced[:1] or fields or [(None, "", "")]
        return [AmountRef(amount, _field_text(unit, cell) if unit else "", column, unit.page if unit else 0) for unit, column, cell in chosen]

    def _fields_holding(self, value: Any) -> list[tuple[Any, str, str]]:
        """(unit, column or label, cell) for every field whose value is this amount, or this text."""
        found = []
        for unit in self.index.units:
            for column, cell in _cells(unit.text):
                if isinstance(value, Decimal):
                    holds = value in {_decimal(raw) for raw in _AMOUNT_IN_TEXT.findall(cell)}
                else:
                    holds = bool(str(value).strip()) and _fold(str(value)) in _fold(cell)
                if holds:
                    found.append((unit, column, cell))
        return found

    def _fact_fields(self, fact_answer: dict[str, Any]) -> list[tuple[Any, str, str]]:
        """The fields the fact answer was read from: its facts' values, found where their evidence puts them."""
        fields = []
        for source in fact_answer.get("sources", []):
            fact = next((fact for fact in self.facts.values() if isinstance(fact, dict) and fact.get("excerpt") == source.get("excerpt")), None)
            if fact is None or isinstance(fact.get("value"), (list, dict)):
                continue
            holding = self._fields_holding(fact["value"])
            evidenced = [field for field in holding if _label_before_value(field[1], field[2], fact["excerpt"])]
            fields.extend(evidenced[:1] or holding[:1])
        return fields

    # --- people and addresses ----------------------------------------------------------------------

    def _customer_parts(self) -> set[str]:
        customer = self.invoice.customer.value if self.invoice.customer else ""
        return {_fold(part) for part in re.findall(r"[^\W\d_]{2,}", customer)}

    def _who(self, question: str) -> tuple[str, str | None]:
        """Whom an address question is about: ("customer", name as asked), ("unknown", name) for a name not on the
        bill, ("supplier", None), ("unclear", None) for a pronoun nothing earlier explains."""
        folded = _fold(question)
        words = re.findall(r"[^\W\d_]+", question)
        parts = self._customer_parts()
        supplier = self.invoice.supplier.value if self.invoice.supplier else ""
        if any(_fold(word) in _OTHER_COMPANIES and _fold(word) not in _fold(supplier) for word in words):
            return "other", None  # "het adres van Fluvius": not an address on this bill
        if re.search(r"\b(?:leverancier|fournisseur|supplier)\b", folded) or (supplier and _fold(supplier.split(" — ")[0]) in folded):
            return "supplier", None
        for word in words:
            if _fold(word) in parts:
                return "customer", word
        named = [word for position, word in enumerate(words) if _looks_like_a_name(word, position, supplier)]
        if named:
            return "unknown", named[0]
        if re.search(r"\b(?:ik|mijn|me|mij|my|i|je|mon|ma|de klant|klant|titularis|client)\b", folded):
            return "customer", None
        if re.search(r"\b(?:ze|zij|hij|hem|haar|die|diegene|hen|il|elle|she|he|they|them)\b", folded):
            if any(turn.named_customer for turn in self.turns[-2:]):
                return "customer", None
            return "unclear", None
        return "customer", None

    def _address_answer(self, question: str, intent: AddressIntent) -> dict[str, Any]:
        customer = self.invoice.customer.value if self.invoice.customer else ""
        who, name = self._who(question)
        if who == "other":
            return {"answer": "Deze vraag gaat niet over een adres op je factuur.", "sources": [], "found": False}
        if who == "unknown":
            if customer:
                return {"answer": f"Ik zie {name} niet op deze factuur; ze staat op naam van {customer}. Bedoel je {customer}?", "sources": []}
            return {"answer": f"Ik vond geen naam op deze factuur, dus ik kan niet zeggen of ze over {name} gaat. "
                              "Wil je weten welk adres erop staat?", "sources": []}
        if who == "unclear":
            if customer:
                return {"answer": f"Over wie gaat het? Op deze factuur staat {customer} als klant. Bedoel je die persoon?", "sources": []}
            return {"answer": "Over wie gaat het? Ik vond geen naam op deze factuur.", "sources": []}
        party = "supplier" if who == "supplier" else "customer"
        addresses = [address for address in self.invoice.addresses if address.party in (party, "unknown")]
        person = name or customer or ("de leverancier" if party == "supplier" else "de klant")
        if not addresses:
            whose = "de leverancier" if party == "supplier" else person
            return {"answer": f"Op deze factuur staat geen adres van {whose}.", "sources": []}
        wanted = {"delivery": "delivery", "billing": "billing"}.get(intent)
        if wanted:
            chosen = [address for address in addresses if address.kind == wanted]
            if not chosen:
                others = "; ".join(_address_phrase(address) for address in addresses)
                answer = f"Op deze factuur staat geen {_KIND_NAMES[wanted]}. Wel: {others}."
                return {"answer": answer, "sources": _address_sources(addresses)}
            answer = " ".join(f"Op de factuur staat dit als {_KIND_NAMES[wanted]}: {address.value} (PDF-pagina {address.page})." for address in chosen)
            return {"answer": answer, "sources": _address_sources(chosen)}
        answer = "; ".join(_address_phrase(address) for address in addresses)
        answer = f"Op de factuur staat {answer}."
        if any(address.kind == "unknown" for address in addresses):
            answer += " De factuur noemt dat adres niet als leverings- of facturatieadres."
        if party == "customer" and intent == "residence":
            answer += (f" Ik kan hieruit niet bevestigen dat {person} daar woont: een adres op een energiefactuur zegt "
                       "waar de energie geleverd of de factuur gestuurd wordt, niet waar iemand woont.")
        return {"answer": answer, "sources": _address_sources(addresses)}

    # --- "dit bedrag" ------------------------------------------------------------------------------

    def _this_amount_answer(self, question: str) -> dict[str, Any]:
        refs = next((turn.amounts for turn in reversed(self.turns[-2:]) if turn.amounts), ())
        distinct = list({(_decimal(ref.amount), ref.field): ref for ref in refs}.values())
        if not distinct:
            return {"answer": "Over welk bedrag gaat het? Noem het bedrag of de regel op de factuur, dan zoek ik het op.", "sources": []}
        if len(distinct) > 1:
            options = "; ".join(f"{ref.amount} ({ref.field or ref.column})" if (ref.field or ref.column) else ref.amount for ref in distinct[:4])
            return {"answer": f"Welk bedrag bedoel je? Mijn vorige antwoord noemde er meer: {options}.", "sources": []}
        ref = distinct[0]
        if not ref.field:
            return {"answer": f"Ik vond {ref.amount} niet terug als regel op de factuur.", "sources": []}
        shown = mask_identifiers(ref.field, self._names())
        page = f" (PDF-pagina {ref.page})" if ref.page else ""
        source = [{"title": self.filename, "detail": f"PDF · pagina {ref.page}", "excerpt": shown, "masked": True}]
        refs_out = [ref]
        if not re.search(r"(?i)btw|tva|vat", question):
            return {"answer": f"{ref.amount} staat op de factuur als „{shown}“{page}.", "sources": source, "refs": refs_out}
        if re.match(r"(?i)\s*(?:btw|tva|vat)\b", ref.column):
            return {"answer": f"{ref.amount} is het btw-bedrag zelf: „{shown}“{page}.", "sources": source, "refs": refs_out}
        # Only the cell's own column or line says incl. or excl.; a neighbouring column never does.
        state = _VAT_STATE.search(ref.column or ref.field)
        if state is None:
            return {"answer": f"Bij {ref.amount} staat niet of het inclusief of exclusief btw is: „{shown}“{page}.", "sources": source, "refs": refs_out}
        included = state.group(1).lower().startswith(("incl", "tvac", "ttc"))
        asked_included = not re.search(r"(?i)\bexcl|exclusief|hors|htva", question)
        verdict = "Ja" if included == asked_included else "Nee"
        what = "inclusief" if included else "exclusief"
        return {"answer": f"{verdict}: {ref.amount} staat op de factuur als „{shown}“, dus {what} btw{page}.", "sources": source, "refs": refs_out}

    # --- passages ----------------------------------------------------------------------------------

    def _names(self) -> tuple[str, ...]:
        return (self.invoice.customer.value,) if self.invoice.customer else ()

    def _passages(self, question: str, fact_answer: dict[str, Any] | None) -> dict[str, Any] | None:
        """A passage that answers the question, or a plain 'not on this bill' for a value the bill lacks.
        With a fact answer, a passage replaces it only when the passage covers more of what was asked
        ("hoeveel btw betaal ik op gas" is the gas row's btw, not the amount due)."""
        kind = expected_value(question)
        hits = self.index.answering(question, kind=kind)
        if fact_answer is not None:
            fact_text = fact_answer["answer"] + " " + " ".join(source.get("excerpt", "") for source in fact_answer.get("sources", []))
            if kind in _MUST_HOLD and not has_value(fact_text, kind):
                return self._format(question, hits) if hits else _missing(kind)
            wanted = content_terms(question)
            # Only the fields the facts were read from count, with Karen's own sentence; the evidence passage
            # around them ("Gas: … Btw 6%: … Totaal te betalen: €106") would make any fact look relevant.
            fields = self._fact_fields(fact_answer)
            covered = _covered(wanted, fact_answer["answer"] + " " + " ".join(_field_label(column, cell) for _, column, cell in fields))
            if hits and hits[0].coverage > covered:
                return self._format(question, hits)
            if wanted and covered < _needed(len(wanted)):
                # The fact reader matched a word ("betaal") but its value is about something else: "hoeveel
                # btw betaal ik op gas" is not answered by the amount due. Say so, with what the bill does show.
                return self._not_on_the_bill(question, kind)
            lead = re.split(r"(?<=[.!?])\s", fact_answer["answer"], maxsplit=1)[0]
            amounts = list(dict.fromkeys(_EURO.findall(lead)))
            if len(amounts) == 1:
                # "dit bedrag" afterwards is this value in the field it was read from, not an equal amount elsewhere.
                value = _decimal(amounts[0])
                field = next((field for field in fields if value in {_decimal(raw) for raw in _AMOUNT_IN_TEXT.findall(field[2])}), None)
                if field is not None:
                    fact_answer["refs"] = [AmountRef(amounts[0], _field_text(field[0], field[2]), field[1], field[0].page)]
            return None
        if hits:
            return self._format(question, hits)
        return _missing(kind) if kind in _MUST_HOLD else None

    def _not_on_the_bill(self, question: str, kind: str) -> dict[str, Any]:
        """No line answers the question: say that, and show the closest line of the right kind as what is there."""
        answer = "Ik vond op deze factuur geen regel die precies dat zegt."
        wanted = content_terms(question)
        partial = [hit for hit in self.index.search(question) if has_value(hit.unit.text, kind) and hit.coverage > 0]
        partial.sort(key=lambda hit: (hit.coverage, hit.exact, hit.dense or 0.0), reverse=True)
        if not wanted or not partial:
            return {"answer": answer, "sources": [], "suggestions": FOLLOW_UP_SUGGESTIONS}
        # The closest lines, each covering part of the question ("gas" in one, "btw" in another): at most two.
        units = [hit.unit for hit in partial if hit.coverage == partial[0].coverage][:2]
        shown = [_shown(unit, question, self._names()) for unit in units]
        there = " en ".join(f"„{text}“ (PDF-pagina {unit.page})" for unit, text in zip(units, shown))
        answer += f" Wat er wel staat: {there}."
        return {"answer": answer, "sources": [_source(self.filename, unit, text) for unit, text in zip(units, shown)], "refs": []}

    def _format(self, question: str, hits: list[Hit]) -> dict[str, Any]:
        top = hits[0]
        # Rows of the same table that match the question equally ("hoeveel is de btw": every energy's row).
        same = [hit for hit in hits if hit.unit.section == top.unit.section and hit.coverage == top.coverage][:4]
        units = [hit.unit for hit in same]
        shown = [_shown(unit, question, self._names()) for unit in units]
        page = top.unit.page
        if top.unit.kind == "table":
            caption = top.unit.section.label
            where = f"in de tabel „{caption}“ op PDF-pagina {page}" if caption else f"in de tabel op PDF-pagina {page}"
            answer = f"Dit staat {where}:\n" + "\n".join(f"- {_focus(line, question)}" for line in shown)
            refs = [
                AmountRef(found, _field_text(unit, cell), column, unit.page)
                for unit in units for column, cell in _focused_cells(unit.text, question)
                for found in _EURO.findall(cell)
            ]
            if not refs:  # no column asked for: every amount in the rows shown, so "dit bedrag" asks which
                refs = [AmountRef(found, _field_text(unit, cell), column, unit.page)
                        for unit in units for column, cell in _cells(unit.text) for found in _EURO.findall(cell)]
            return {"answer": answer, "sources": [_source(self.filename, unit, text) for unit, text in zip(units, shown)], "refs": refs}
        elif top.unit.kind in ("fields", "address"):
            quoted = "; ".join(f"„{line.rstrip('.')}“" for line in shown)
            answer = f"Op de factuur staat {quoted} (PDF-pagina {page})."
        else:
            answer = f"Op PDF-pagina {page} staat: „{' '.join(shown)}“"
        return {"answer": answer, "sources": [_source(self.filename, unit, text) for unit, text in zip(units, shown)]}


# --- building a session ------------------------------------------------------------------------------

def session_from_upload(chunks: list[KnowledgeChunk], analysis: dict[str, Any], pages: list[tuple[int, str]], filename: str) -> InvoiceSession:
    """The session for an upload already read by the fact reader; the invoice gains its typed addresses."""
    sections = build_sections(pages, filename)
    addresses = addresses_from(pages, filename)
    invoice = build_invoice(analysis.get("facts") or {}, filename, addresses) if analysis.get("facts") is not None else analysis["invoice"]
    return InvoiceSession(chunks, {**analysis, "invoice": invoice}, sections, filename)


def open_invoice_session(pdf_data: bytes, filename: str) -> InvoiceSession:
    """Read a PDF in memory with the rules only (no hosted model) and open a session for it."""
    chunks = load_pdf_chunks_from_bytes(pdf_data, source=filename)
    return session_from_upload(chunks, analyze_invoice_chunks(chunks), page_texts_from_pdf(pdf_data), filename)


def open_text_session(text: str, filename: str) -> InvoiceSession:
    """A session for text read from a photo (OCR), as one page."""
    chunks = chunks_from_text(text, source=filename)
    return session_from_upload(chunks, analyze_invoice_chunks(chunks), [(1, text)], filename)


# --- question understanding ----------------------------------------------------------------------------

# Example questions per kind of address question. A question matches a card by its words (after names and
# pronouns are set aside) or, with the local model, by meaning; never by a single keyword.
ADDRESS_CARDS: dict[AddressIntent, tuple[str, ...]] = {
    "residence": (
        "waar woont de klant", "waar woon ik", "waar wonen ze", "wat is het woonadres", "woonplaats van de klant",
        "in welke gemeente woont de klant", "waar is de klant gedomicilieerd", "waar verblijft de klant",
        "où habite le client", "où j'habite", "where does the customer live",
    ),
    "delivery": (
        "wat is het leveringsadres", "wat is mijn leveringsadres", "naar welk adres wordt de energie geleverd",
        "op welk adres wordt de stroom geleverd", "wat is het verbruiksadres", "wat is het afnameadres",
        "quelle est l'adresse de fourniture", "what is the delivery address", "what is the supply address",
    ),
    "billing": (
        "wat is het facturatieadres", "wat is mijn facturatieadres", "wat is het factuuradres",
        "naar welk adres wordt de factuur gestuurd", "wat is het correspondentieadres",
        "quelle est l'adresse de facturation", "what is the billing address",
    ),
    "address": (
        "wat is het adres van de klant", "wat is mijn adres", "welk adres staat op de factuur", "op welk adres",
        "wat is het adres", "adres van de leverancier", "quelle est l'adresse", "what is the address",
    ),
}
CARD_WORD_MATCH = 0.6  # share of words in common with an example question
CARD_MEANING_MATCH = 0.82  # cosine with an example question, when the model is there
_PRONOUNS = frozenset("ze zij hij hem haar die diegene hen il elle she he they them klant client customer titularis".split())
_CARD_STOP = frozenset(
    "de het een en van in op ik je u uw mijn me mij wat hoe waar is zijn staat er dit deze die the a of le la l les "
    "est quelle quel what is my".split()
)


def _card_words(text: str, names: set[str]) -> set[str]:
    words = {_fold(word) for word in re.findall(r"[^\W\d_]+", text)}
    return {word for word in words if word not in _CARD_STOP and word not in _PRONOUNS and word not in names}


_card_vectors: dict[str, list[float]] = {}


def address_intent(question: str, customer_parts: set[str] = frozenset()) -> AddressIntent | None:
    """Which kind of address the question asks for, or None. A question names the energy it asks about only
    through its words, so 'wie is mijn leverancier' is not an address question."""
    names = set(customer_parts) | {_fold(word) for position, word in enumerate(re.findall(r"[^\W\d_]+", question))
                                   if _looks_like_a_name(word, position, "")}
    asked = _card_words(question, names)
    if not asked:
        return None
    best: tuple[float, AddressIntent | None] = (0.0, None)
    for intent, examples in ADDRESS_CARDS.items():
        for example in examples:
            words = _card_words(example, set())
            score = len(asked & words) / len(asked | words)
            if score > best[0]:
                best = (score, intent)
    if best[0] >= CARD_WORD_MATCH:
        return best[1]
    # A paraphrase ("op welk adres krijg ik mijn stroom?") by meaning, only when it shares an address word.
    if not any("adres" in word or word.startswith(("woon", "woont", "habit", "adress", "address")) for word in asked):
        return None
    examples = [(intent, example) for intent, cards in ADDRESS_CARDS.items() for example in cards]
    missing = [example for _, example in examples if example not in _card_vectors]
    vectors = embed(missing + [question])
    if vectors is None:
        return None
    for example, vector in zip(missing, vectors):
        _card_vectors[example] = vector
    asked_vector = vectors[-1]
    scored = [(sum(a * b for a, b in zip(asked_vector, _card_vectors[example])), intent) for intent, example in examples]
    score, intent = max(scored)
    return intent if score >= CARD_MEANING_MATCH else None


def _looks_like_a_name(word: str, position: int, supplier: str) -> bool:
    """A capitalised word inside the question that is no invoice word, abbreviation or supplier name: "Kim"."""
    if position == 0 or not word[:1].isupper() or word.isupper() or len(word) < 2:
        return False
    if word.lower() in _WORDS or _fold(word) in _CARD_STOP or (supplier and word.lower() in supplier.lower()):
        return False
    return _fold(word) not in _OTHER_COMPANIES | {"karen", "belgie"}


_OTHER_COMPANIES = frozenset("luminus engie fluvius eneco totalenergies mega bolt ores sibelga resa elia vreg creg ombudsdienst".split())


# --- formatting helpers ----------------------------------------------------------------------------------

_KIND_NAMES = {"billing": "facturatieadres", "delivery": "leveringsadres", "unknown": "adres"}
_MUST_HOLD = ("price_per_kwh", "duration")
_THIS_AMOUNT = re.compile(r"(?i)\b(?:dit|dat|deze|die|het\s+zelfde|hetzelfde|ce|cet|this|that)\s+(?:bedrag|som|totaal|getal|montant|amount)\b")
_CALCULATION = re.compile(r"[×x*]\s*€|=\s*\**\s*€")
_EURO = re.compile(r"€\s?-?\d[\d. ]*,\d{2}|-?\d[\d. ]*,\d{2}\s?€")
_AMOUNT_IN_TEXT = re.compile(r"\d[\d.  ]*,\d{2}")
_VAT_STATE = re.compile(r"(?i)\b(incl|excl|inclusief|exclusief|tvac|htva|ttc|hors)\.?\s*(?:btw|tva|vat|taxes)?")


def _fold(text: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(char))


def _decimal(text: str) -> Decimal:
    digits = re.sub(r"[^\d,-]", "", text).replace(",", ".")
    return Decimal(digits)


def _address_phrase(address: Any) -> str:
    kind = "dit adres zonder leverings- of facturatielabel" if address.kind == "unknown" else f"dit als {_KIND_NAMES[address.kind]}"
    return f"{kind}: {address.value} (PDF-pagina {address.page})"


def _address_sources(addresses: list[Any]) -> list[dict[str, Any]]:
    return [{"title": address.source, "detail": f"PDF · pagina {address.page}", "excerpt": address.excerpt, "masked": True} for address in addresses]


def _shown(unit: Any, question: str, names: tuple[str, ...]) -> str:
    """The unit as the visitor sees it: identifiers masked, unless the question asks for that very field."""
    label = unit.text.split(":", 1)[0] if ":" in unit.text else ""
    asked = set(re.findall(r"[a-z0-9]+", _fold(question)))
    if label and unit.kind == "fields" and asked & set(re.findall(r"[a-z0-9]+", _fold(label))) - {"nummer", "van", "de"}:
        return mask_identifiers(unit.text.split(":", 1)[0], names) + ":" + unit.text.split(":", 1)[1]
    return mask_identifiers(unit.text, names)


def _qualifiers(text: str) -> set[str]:
    folded = _fold(text)
    return {word for word, pattern in (("incl", r"\b(?:incl|inclusief|tvac|ttc)\b"), ("excl", r"\b(?:excl|exclusief|htva|hors)\b"))
            if re.search(pattern, folded)}


def _row_label(row: str) -> str:
    return row.split(" — ", 1)[0] if " — " in row else ""


def _cells(text: str) -> list[tuple[str, str]]:
    """(column, cell) pairs: a table row's cells with their column, a field with its label, or ("", line)."""
    if " — " in text:
        return [(part.split(":", 1)[0].strip(), part.strip()) if ":" in part else ("", part.strip()) for part in text.split(" — ", 1)[1].split(" · ")]
    if ":" in text:
        return [(text.split(":", 1)[0].strip(), text.strip())]
    return [("", text.strip())]


def _focused_cells(row: str, question: str) -> list[tuple[str, str]]:
    """The cells whose column the question asks for: shared subject words, and incl./excl. only when asked."""
    if " — " not in row:
        return []
    wanted = content_terms(question)
    asked = _qualifiers(question)
    scored = []
    for column, cell in _cells(row):
        if not column:
            continue
        # An asked qualifier ("inclusief btw") outweighs a shared word: it names the column.
        shared = len(wanted & set(terms(_VAT_STATE.sub(" ", column)))) + 2 * len(asked & _qualifiers(column))
        scored.append((shared - len(_qualifiers(column) - asked), column, cell))
    best = max((score for score, _, _ in scored), default=0)
    return [(column, cell) for score, column, cell in scored if best > 0 and score == best]


def _focus(row: str, question: str) -> str:
    """'Gas — Bedrag excl. btw: € 700,00 · Btw 6%: € 42,00 · …' with the asked column in bold."""
    focused = {cell for _, cell in _focused_cells(row, question)}
    if not focused:
        return row
    label, cells = row.split(" — ", 1)
    return f"{label} — " + " · ".join(f"**{part}**" if part.strip() in focused else part for part in cells.split(" · "))


def _field_text(unit: Any, cell: str) -> str:
    row = _row_label(unit.text)
    return f"{row} — {cell}" if row else cell


def _field_label(column: str, cell: str) -> str:
    return column or cell


def _label_before_value(column: str, cell: str, excerpt: str) -> bool:
    """Whether the evidence passage prints this field's label right before its value ("Totaal te betalen: € 700,00")."""
    if not column or not excerpt:
        return False
    raw = cell.split(":", 1)[1] if ":" in cell else cell
    digits = re.sub(r"\D", "", raw)
    folded, label = _fold(excerpt), _fold(column)
    for match in re.finditer(re.escape(label), folded):
        following = re.sub(r"\D", "", folded[match.end():match.end() + 25])
        if digits and following.startswith(digits):
            return True
    return False


def _covered(wanted: set[str], text: str) -> float:
    """Share of the question's subject terms found in a text, also inside compounds ("verbruik" in "stroomverbruik")."""
    if not wanted:
        return 0.0
    folded = _fold(text)
    return len({term for term in wanted if term in folded or term in set(terms(text))}) / len(wanted)


def _needed(count: int) -> float:
    return math.ceil(count * 2 / 3) / count


def _source(filename: str, unit: Any, shown: str) -> dict[str, Any]:
    return {"title": filename, "detail": f"PDF · pagina {unit.page}", "excerpt": shown, "masked": True}


def _missing(kind: str) -> dict[str, Any]:
    what = {"price_per_kwh": "geen prijs per kWh", "duration": "geen termijn of duur"}[kind]
    extra = " Ze vermeldt verbruik en bedragen, maar geen eenheidsprijs." if kind == "price_per_kwh" else ""
    return {"answer": f"Op deze factuur vond ik {what} die je vraag beantwoordt.{extra}", "sources": []}
