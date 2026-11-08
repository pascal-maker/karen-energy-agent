"""Hybrid retrieval within one uploaded invoice: lexical (BM25) and semantic (a small local embedding model)
over the invoice's own sections, fused by reciprocal rank, then a check that the passage can answer the question.

Everything runs on Karen's server. The embedding model is a quantized multilingual MiniLM (about 220 MB, Apache-2.0,
via fastembed and onnxruntime on CPU); no invoice text leaves the server. Without the model (not installed, or
KAREN_INVOICE_EMBEDDINGS=0) retrieval is lexical only. One index belongs to one upload session and lives as long
as that session; nothing is written to disk except the model files themselves.
"""

from __future__ import annotations

import math
import os
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from typing import Literal, Sequence

from .invoice_sections import MAX_SECTION_CHARS, Section

__all__ = ["MAX_SECTION_CHARS", "Unit", "InvoiceIndex", "embedder_available", "expected_value", "terms", "content_terms"]

EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
CANDIDATES = 8  # units reranked after fusion
RRF_K = 60

ValueKind = Literal["price_per_kwh", "amount", "quantity", "date", "duration", "any"]

# Words that carry no subject: question words, pronouns, and words every bill question shares.
_STOP = frozenset(
    """de het een en van in op ik je jij u uw mijn me mij wat hoe hoeveel waar wanneer wie welk welke waarom is zijn ben
    kan kun kunt moet moeten mag wil zou zal voor met naar bij of dat dit die deze er aan als ook nog niet geen wel
    te om over uit tot al dan maar zo hier daar staat staan stond the a an of to is are what how where when who which
    my i le la les un une des du et est sont ce cette mon ma mes quel quelle quels combien où quand pour par sur dans
    eens even graag aub alsjeblieft please zeg vertel leg toon geef laat weten gaat doen gebeurt gebeuren nodig lang"""
    .split()
)


def _fold(text: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(char))


def _stem(token: str) -> str:
    """A five-letter prefix after folding doubled vowels: betaal/betalen, verhuist/verhuizen, opzeggen/opzegtermijn."""
    token = re.sub(r"([aeiou])\1", r"\1", token)
    return token[:5]


# Subject words that are on every bill: they rank a passage but never prove it answers the question.
_WEAK = frozenset(_stem(word) for word in "factuur facture rekening bedrag betaal betalen kost kosten prijs euro totaal".split())


def terms(text: str) -> list[str]:
    return [_stem(token) for token in re.findall(r"[a-z0-9€%]+", _fold(text)) if token not in _STOP and len(token) > 1]


def content_terms(text: str) -> set[str]:
    """The terms that say what the question is about (no question words, no words on every bill)."""
    return {term for term in terms(text) if term not in _WEAK and not term.isdigit()}


# --- What kind of value would answer the question --------------------------------------------------

_PER_KWH_Q = re.compile(r"(?i)(?:per|/|een|1)\s*kwh|kwh[- ]?prijs|prijs per|eenheidsprijs|tarief per|kost(?:t|en)? (?:een|1) kwh|prix (?:du|par) kwh")
_DURATION_Q = re.compile(r"(?i)\bhoe\s+lang\b|contractduur|looptijd|\bduur\s+van\s+(?:het|je|uw|mijn)\s+contract|dur[ée]e\s+du\s+contrat|\btermijn|\bop\s+voorhand\b|\bvooraf\b|\bhoeveel\s+(?:dagen|weken|maanden|jaar)\b|\bcombien de (?:jours|semaines|mois)\b|\bdélai\b")
_DATE_Q = re.compile(r"(?i)\bwanneer\b|datum\b|\btegen\s+wanneer\b|\bvervaldag|\bquand\b|\bdate\b")
_QUANTITY_Q = re.compile(r"(?i)verbruik|\bkwh\b|\bm³|\bm3\b|\bconsommation\b")
_AMOUNT_Q = re.compile(r"(?i)\bhoeveel\b|\bbedrag|\bkost|\bbtw\b|\bbetaal|\bbetalen|\bprijs|\bvoorschot|\bcombien\b|\bmontant|\btva\b|€")

_PER_KWH = re.compile(r"(?i)(?:€|c€|eur|euro|cent|ct)\s*/\s*kwh|(?:euro|eur|cent)\s+per\s+kwh|/kwh")
_AMOUNT = re.compile(r"€\s*-?\d|\d[\d.\u00a0 ]*,\d{2}\s*(?:€|eur)|(?i:\beur\b)")
_QUANTITY = re.compile(r"(?i)\d[\d.\u00a0 ]*(?:,\d+)?\s*(?:kwh|mwh|m³|m3)\b")
_DATE = re.compile(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b|(?i:\b\d{1,2}\s+(?:januari|februari|maart|april|mei|juni|juli|augustus|september|oktober|november|december)\s+\d{4})")
_DURATION = re.compile(r"(?i)\b(?:\d+|een|één|twee|drie|vier|zes|twaalf)\s+(?:dag|dagen|week|weken|maand|maanden|jaar|jaren|jours?|semaines?|mois|ans?)\b"
    # A term without a number is still a term: "onbepaalde duur", "contrat à durée indéterminée".
    r"|\b(?:on)?(?:bepaalde|beperkte)\s+(?:duur|tijd)\b|\bdur[ée]e\s+(?:du\s+contrat\s*:\s*)?(?:in)?d[ée]termin[ée]e|\b(?:in)?d[ée]termin[ée]e\b")
_VALUE_SHAPES: dict[str, re.Pattern[str]] = {
    "price_per_kwh": _PER_KWH, "amount": _AMOUNT, "quantity": _QUANTITY, "date": _DATE, "duration": _DURATION,
}


def expected_value(question: str) -> ValueKind:
    if _PER_KWH_Q.search(question) and re.search(r"(?i)kost|prijs|tarief|betaal|prix|co[uû]t", question):
        return "price_per_kwh"
    if _DURATION_Q.search(question):
        return "duration"
    if _DATE_Q.search(question):
        return "date"
    if _QUANTITY_Q.search(question) and not re.search(r"(?i)kost|prijs|bedrag|betaal|btw|€", question):
        return "quantity"
    if _AMOUNT_Q.search(question):
        return "amount"
    return "any"


# What a date or duration is the date or duration OF. A notice period is no contract term, a billing period no
# due date: a question naming one relation is never answered by a field naming another (audit 2026-11-07).
_RELATIONS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("notice", re.compile(r"(?i)opzeg|pr[ée]avis|r[ée]sili")),
    ("contract_term", re.compile(
        r"(?i)looptijd|contractduur|duur\s+van\s+(?:het|je|uw|mijn)\s+contract|einde?\s+(?:van\s+)?(?:het\s+|je\s+|uw\s+|mijn\s+)?contract"
        r"|contract\w*\s+(?:loopt|eindigt|afloopt|geldig)|(?:hoe\s+lang|tot\s+wanneer)\b.*\bcontract|contract\b.*\b(?:hoe\s+lang|tot\s+wanneer)"
        r"|(?:on)?bepaalde\s+duur|dur[ée]e\s+(?:du\s+contrat|ind[ée]termin[ée]e|d[ée]termin[ée]e)")),
    ("due_date", re.compile(
        r"(?i)vervaldat|vervaldag|uiterlijk\s+(?:te\s+)?betal|te\s+betalen\s+v[oó][oó]r|tegen\s+wanneer|wanneer\b.*\b(?:moet|moeten)\b.*\bbetal"
        r"|op\s+(?:hun|de|jullie|uw|hun)\s+rekening|[ée]ch[ée]ance")),
    ("invoice_date", re.compile(
        r"(?i)factuurdatum|datum\s+van\s+(?:de|deze)\s+factuur|date\s+de\s+(?:la\s+)?facture"
        r"|wanneer\b.*\bfactuur\b.*\b(?:opgemaakt|gemaakt|verstuurd|uitgereikt|gedateerd)")),
    ("supply_start", re.compile(
        r"(?i)(?:start|begin)\w*\s+(?:van\s+de\s+)?levering|leveringsdatum|levering\w*\s+(?:start|begint|gestart)|sinds\s+wanneer\b.*\bgeleverd"
        r"|d[ée]but\s+de\s+(?:la\s+)?fourniture")),
    ("billing_period", re.compile(r"(?i)factuurperiode|\bperiode|p[ée]riode")),
)


def relation(text: str) -> str | None:
    """The one relation a question or a field names (notice, contract_term, due_date, …), or None."""
    return next((name for name, pattern in _RELATIONS if pattern.search(text)), None)


def _relations(text: str) -> set[str]:
    return {name for name, pattern in _RELATIONS if pattern.search(text)}


def field_relations(text: str) -> set[str]:
    """What a field is about: its label ("Opzegtermijn: 3 weken"), or, under a label that names none
    ("Contract: … onbepaalde duur, opzegtermijn 3 weken"), the relations its value names."""
    head = text.split("\n", 1)[0]
    label, _, value = head.partition(":") if ":" in head[:80] else (head, "", "")
    return _relations(label) or _relations(value)


def _field_relation(text: str) -> str | None:
    return next(iter(sorted(field_relations(text))), None)


def same_relation(question: str, text: str) -> bool:
    """A question that names a relation ("hoe lang loopt mijn contract") is answered only by a field that names the
    same one: a sentence that merely holds a duration ("een maand vooraf") is no contract term either."""
    asked = relation(question)
    return asked is None or asked in field_relations(text)


def has_value(text: str, kind: ValueKind) -> bool:
    return kind == "any" or bool(_VALUE_SHAPES[kind].search(text))


# --- The local embedding model -----------------------------------------------------------------------

_model = None
_model_lock = threading.Lock()
_model_failed = False


def embedder_available() -> bool:
    if os.getenv("KAREN_INVOICE_EMBEDDINGS", "1") == "0" or _model_failed:
        return False
    try:
        import fastembed  # noqa: F401
    except ImportError:
        return False
    return True


def _embedder():
    global _model, _model_failed
    if not embedder_available():
        return None
    with _model_lock:
        if _model is None and not _model_failed:
            try:
                from fastembed import TextEmbedding

                _model = TextEmbedding(EMBEDDING_MODEL, cache_dir=os.getenv("KAREN_EMBED_CACHE") or None)
            except Exception:
                _model_failed = True  # no model (no network on first use, disk full): lexical only
        return _model


def warm_up() -> bool:
    """Load the model ahead of the first question; True when semantic retrieval is on."""
    return _embedder() is not None


def embed(texts: Sequence[str]) -> list[list[float]] | None:
    model = _embedder()
    if model is None or not texts:
        return None
    with _model_lock:  # onnxruntime sessions are not shared safely across threads by fastembed
        vectors = list(model.embed(list(texts)))
    result = []
    for vector in vectors:
        norm = math.sqrt(float((vector * vector).sum())) or 1.0
        result.append([float(value) / norm for value in vector])
    return result


def _cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


# --- The index -----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Unit:
    """The smallest passage an answer quotes: one field, one table row, one address or one sentence."""

    section: Section
    text: str  # what is quoted
    context: str  # what the model compares: the table caption with the row
    heading: str = ""  # the short heading above the section, which only the word match uses

    @property
    def words(self) -> str:
        return f"{self.heading}: {self.context}" if self.heading else self.context

    @property
    def page(self) -> int:
        return self.section.page

    @property
    def kind(self) -> str:
        return self.section.kind


@dataclass
class Hit:
    unit: Unit
    lexical: float
    dense: float | None
    coverage: float  # share of the question's content terms found in the unit
    score: float
    exact: int = 0  # question words found unstemmed: "klantnummer" over "klantendienst"


def _sentences(lines: Sequence[str]) -> list[str]:
    text = " ".join(lines)
    # Not at "?": "Verhuist u? Geef uw nieuwe adres door" is one instruction.
    parts = re.split(r"(?<=[.!])\s+(?=[A-ZÀ-Ý0-9“\"(])", text)
    return [part.strip() for part in parts if part.strip()]


_QUALIFIER = re.compile(r"(?i)\b(?:incl|excl|inclusief|exclusief|hors|htva|tvac)\.?\s+\w+")


def _is_heading(section: Section) -> bool:
    return section.kind == "text" and len(section.lines) == 1 and len(section.lines[0].split()) <= 4 and not any(
        has_value(section.lines[0], kind) for kind in ("amount", "date", "quantity")
    )


def units_from(sections: Sequence[Section]) -> list[Unit]:
    """One unit per field, table row, address or sentence. A short heading ("Contractvoorwaarden") is scored
    with the section under it and is never quoted on its own."""
    units: list[Unit] = []
    heading = ""
    for position, section in enumerate(sections):
        if _is_heading(section):
            following = sections[position + 1] if position + 1 < len(sections) else None
            heading = section.lines[0] if following is not None and following.page == section.page else ""
            continue
        lines = _sentences(section.lines) if section.kind == "text" else list(section.lines)
        for line in lines:
            context = f"{section.label}: {line}" if section.kind == "table" and section.label else line
            units.append(Unit(section, line, context, heading))
        heading = ""
    return units


@dataclass
class InvoiceIndex:
    """BM25 and (when available) embeddings over one invoice's units; built once per upload session."""

    units: list[Unit]
    _terms: list[list[str]] = field(default_factory=list, repr=False)
    _vectors: list[list[float]] | None = field(default=None, repr=False)
    _embedded: bool = field(default=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        self._terms = [terms(unit.words) for unit in self.units]
        # "Bedrag incl. btw" qualifies an amount; it is not the btw. Those words rank but do not cover.
        self._subject = [set(terms(_QUALIFIER.sub(" ", unit.words))) for unit in self.units]
        self._exact = [set(re.findall(r"[a-z0-9]+", _fold(unit.words))) for unit in self.units]
        count = len(self.units) or 1
        frequency: dict[str, int] = {}
        for unit_terms in self._terms:
            for term in set(unit_terms):
                frequency[term] = frequency.get(term, 0) + 1
        self._idf = {term: math.log(1 + (count - n + 0.5) / (n + 0.5)) for term, n in frequency.items()}
        self._average = sum(len(t) for t in self._terms) / count or 1.0

    @classmethod
    def build(cls, sections: Sequence[Section]) -> "InvoiceIndex":
        return cls(units_from(sections))

    @property
    def semantic(self) -> bool:
        self._ensure_vectors()
        return self._vectors is not None

    def prepare(self) -> None:
        """Embed every unit now (about 50 ms per unit on a small CPU); the app calls this in the background
        right after an upload, so the first question rarely waits for it."""
        self._ensure_vectors()

    def _ensure_vectors(self) -> None:
        with self._lock:
            if not self._embedded:
                self._vectors = embed([unit.context for unit in self.units])
                self._embedded = True

    def _bm25(self, query: list[str], index: int) -> float:
        unit_terms = self._terms[index]
        if not unit_terms:
            return 0.0
        score = 0.0
        length = len(unit_terms)
        for term in set(query):
            tf = unit_terms.count(term)
            if tf:
                score += self._idf.get(term, 0.0) * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * length / self._average))
        return score

    def search(self, question: str, *, limit: int = CANDIDATES) -> list[Hit]:
        """Units ranked by reciprocal-rank fusion of BM25 and cosine similarity, top `limit`."""
        if not self.units:
            return []
        query = terms(question)
        wanted = content_terms(question)
        asked_words = set(re.findall(r"[a-z0-9]+", _fold(question))) - _STOP
        lexical = [self._bm25(query, index) for index in range(len(self.units))]
        self._ensure_vectors()
        dense: list[float] | None = None
        if self._vectors is not None:
            asked = embed([question])
            if asked:
                dense = [_cosine(asked[0], vector) for vector in self._vectors]
        fused = [0.0] * len(self.units)
        for scores in (lexical, dense):
            if scores is None:
                continue
            order = sorted(range(len(self.units)), key=lambda index: scores[index], reverse=True)
            for rank, index in enumerate(order):
                if scores is lexical and scores[index] <= 0:
                    continue
                fused[index] += 1 / (RRF_K + rank + 1)
        ranked = sorted(range(len(self.units)), key=lambda index: fused[index], reverse=True)[:limit]
        hits = []
        for index in ranked:
            if fused[index] <= 0:
                continue
            coverage = len(wanted & self._subject[index]) / len(wanted) if wanted else 0.0
            exact = len(asked_words & self._exact[index])
            hits.append(Hit(self.units[index], lexical[index], dense[index] if dense else None, coverage, fused[index], exact))
        return hits

    def answering(self, question: str, *, kind: ValueKind | None = None) -> list[Hit]:
        """The candidates that can answer: the right kind of value, and the question's subject in the passage.
        The subject counts as present by its words (all of one or two content terms, two thirds of more), or,
        for a paraphrase, by meaning, but only when the question asks for a specific kind of value (a duration,
        a date, a quantity, a price per kWh) and the passage holds one. Meaning alone never supports an amount
        or an open question: money passages all look alike to the model."""
        kind = expected_value(question) if kind is None else kind
        wanted = len(content_terms(question))
        needed = math.ceil(wanted * 2 / 3) / wanted if wanted else 1.0
        if kind not in ("any", "amount"):
            needed = min(needed, 0.5)  # the kind of value asked for is evidence too: half the subject suffices
        supported = []
        asked = relation(question)
        for hit in self.search(question):
            if not has_value(hit.unit.text, kind) or not same_relation(question, hit.unit.text):
                continue
            # A field labelled with the relation asked ("Factuurdatum:" for "wat is de factuurdatum") is on topic.
            by_relation = asked is not None and kind not in ("any", "amount")
            by_words = by_relation or (wanted > 0 and hit.coverage >= needed - 1e-9)
            by_meaning = kind not in ("any", "amount") and hit.dense is not None and hit.dense >= SEMANTIC_MATCH
            if by_words or by_meaning:
                supported.append(hit)
        # Rerank: the subject's words first, then meaning, then the fused rank.
        supported.sort(key=lambda hit: (round(hit.coverage, 2), hit.exact, hit.dense or 0.0, hit.score), reverse=True)
        return supported


SEMANTIC_MATCH = 0.45  # cosine above which a passage without shared words counts as on-topic; see docs
