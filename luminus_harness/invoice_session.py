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

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from .invoice_analysis import analyze_invoice_chunks, answer_uploaded_pdf_question
from .invoice_model import _WORDS, mask_identifiers
from .invoice_retrieval import Hit, InvoiceIndex, content_terms, embed, expected_value, has_value, terms
from .invoice_schema import Invoice, build_invoice
from .invoice_sections import Section, addresses_from, build_sections, page_texts_from_pdf
from .knowledge import KnowledgeChunk, load_pdf_chunks_from_bytes
from .photo_invoice import chunks_from_text

MAX_TURNS = 6
AddressIntent = Literal["residence", "delivery", "billing", "address"]


@dataclass(frozen=True)
class Turn:
    question: str
    answer: str
    amount: str | None  # the first euro amount the answer named, as printed ("€ 179,00")
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
        self._remember(question, result)
        return result

    def _answer(self, question: str) -> dict[str, Any]:
        intent = address_intent(question, self._customer_parts())
        if intent is not None:
            return self._address_answer(question, intent)
        if _THIS_AMOUNT.search(question):
            return self._this_amount_answer(question)
        return answer_uploaded_pdf_question(question, self.chunks, self.facts, self.invoice, passages=self._passages)

    def _remember(self, question: str, result: dict[str, Any]) -> None:
        answer = str(result.get("answer", ""))
        amount = _EURO.search(answer)
        customer = self.invoice.customer.value if self.invoice.customer else ""
        named = bool(customer) and customer.lower() in answer.lower()
        self.turns.append(Turn(question, answer, amount.group(0) if amount else None, named))
        del self.turns[:-MAX_TURNS]

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
        earlier = next((turn.amount for turn in reversed(self.turns[-2:]) if turn.amount), None)
        if earlier is None:
            return {"answer": "Over welk bedrag gaat het? Noem het bedrag of de regel op de factuur, dan zoek ik het op.", "sources": []}
        value = _decimal(earlier)
        holding = [unit for unit in self.index.units if value in {_decimal(found) for found in _AMOUNT_IN_TEXT.findall(unit.text)}]
        if not holding:
            return {"answer": f"Ik vond {earlier} niet terug als regel op de factuur.", "sources": []}
        holding.sort(key=lambda unit: bool(_VAT_STATE.search(unit.words)), reverse=True)
        unit = holding[0]
        shown = _shown(unit, question, self._names())
        source = [_source(self.filename, unit, shown)]
        if re.search(r"(?i)btw|tva|vat", question):
            state = _VAT_STATE.search(unit.words)
            if state is None:
                return {"answer": f"Bij {earlier} staat niet of het inclusief of exclusief btw is: „{shown}“ (PDF-pagina {unit.page}).", "sources": source}
            included = state.group(1).lower().startswith(("incl", "tvac", "ttc"))
            asked_included = not re.search(r"(?i)\bexcl|exclusief|hors|htva", question)
            verdict = "Ja" if included == asked_included else "Nee"
            what = "inclusief" if included else "exclusief"
            return {"answer": f"{verdict}: {earlier} staat op de factuur als „{shown}“, dus {what} btw (PDF-pagina {unit.page}).", "sources": source}
        return {"answer": f"{earlier} staat op de factuur als „{shown}“ (PDF-pagina {unit.page}).", "sources": source}

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
            covered = len(wanted & set(terms(fact_text))) / len(wanted) if wanted else 0.0
            if hits and hits[0].coverage > covered:
                return self._format(question, hits)
            return None
        if hits:
            return self._format(question, hits)
        return _missing(kind) if kind in _MUST_HOLD else None

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
        elif top.unit.kind in ("fields", "address"):
            answer = f"Op de factuur staat: {'; '.join(shown)} (PDF-pagina {page})."
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
    return word.lower() not in {"karen", "luminus", "engie", "fluvius", "eneco", "totalenergies", "mega", "bolt", "ores", "sibelga", "belgie", "belgië"}


# --- formatting helpers ----------------------------------------------------------------------------------

_KIND_NAMES = {"billing": "facturatieadres", "delivery": "leveringsadres", "unknown": "adres"}
_MUST_HOLD = ("price_per_kwh", "duration")
_THIS_AMOUNT = re.compile(r"(?i)\b(?:dit|dat|deze|die|het\s+zelfde|hetzelfde|ce|cet|this|that)\s+(?:bedrag|som|totaal|getal|montant|amount)\b")
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


def _focus(row: str, question: str) -> str:
    """'Gas — Bedrag excl. btw: € 700,00 · Btw 6%: € 42,00 · …' with the asked column in bold."""
    if " — " not in row:
        return row
    label, cells = row.split(" — ", 1)
    wanted = content_terms(question)
    parts = cells.split(" · ")
    marked = [f"**{part}**" if wanted & set(terms(part.split(":", 1)[0])) and not re.match(r"(?i)bedrag (?:in|ex)cl", part) else part for part in parts]
    return f"{label} — " + " · ".join(marked)


def _source(filename: str, unit: Any, shown: str) -> dict[str, Any]:
    return {"title": filename, "detail": f"PDF · pagina {unit.page}", "excerpt": shown, "masked": True}


def _missing(kind: str) -> dict[str, Any]:
    what = {"price_per_kwh": "geen prijs per kWh", "duration": "geen termijn of duur"}[kind]
    extra = " Ze vermeldt verbruik en bedragen, maar geen eenheidsprijs." if kind == "price_per_kwh" else ""
    return {"answer": f"Op deze factuur vond ik {what} die je vraag beantwoordt.{extra}", "sources": []}
