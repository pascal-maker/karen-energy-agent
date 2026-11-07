"""What a visitor asks about their uploaded invoice, as a short list of typed tasks.

"heb je advies over deze factuur zoek het adres op" is two tasks: advice and an address. Karen's own code then
carries out each task on the active invoice (invoice_session); nothing here looks up a value, computes an amount
or reads the invoice.

Two parsers fill the same strict schema:
- a language model, only when KAREN_INTENT_MODEL=1 and an OpenAI key are set. It receives the question with
  the customer's name and identifiers masked, plus minimal context (which task came last, whether an amount
  or the customer was just mentioned). Never the PDF, its text or its addresses. Output that does not fit the
  schema, or a task whose text is not in the question, is dropped; on any failure the local parser decides.
- a local parser (always available): the question is split before command words and conjunctions, and each
  part is classified with the existing recognisers, typos included.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Literal

TaskType = Literal["address", "advice", "payer", "yearly", "summary", "explain", "insights", "this_amount", "question"]
AddressKind = Literal["residence", "delivery", "billing", "address"]
TASK_TYPES: tuple[str, ...] = ("address", "advice", "payer", "yearly", "summary", "explain", "insights", "this_amount", "question")
MAX_TASKS = 4
INTENT_DEADLINE_SECONDS = 6


@dataclass(frozen=True)
class Task:
    type: str  # one of TASK_TYPES; "question" is any other question about the bill, answered from its facts or passages
    text: str  # the part of the visitor's question this task answers, in their words
    address_kind: str | None = None


@dataclass
class Plan:
    tasks: list[Task]
    unclear: list[str] = field(default_factory=list)  # parts Karen could not place: she asks about these, specifically
    parsed_by: Literal["rules", "language_model"] = "rules"


# --- the local parser -------------------------------------------------------------------------------

# A new request starts at a command or question word ("… deze factuur zoek het adres op") or after "en", a comma,
# a semicolon or a sentence end. Splitting is only kept when at least two parts are recognised tasks.
_BOUNDARY = re.compile(
    r"(?i)\s*(?:[?!.;]+|,)\s*|\s+(?:en|maar|ook|daarnaast|plus|and|et)\s+(?=\S)"
    r"|\s+(?=(?:zoek|geef|vat|toon|leg|bereken|controleer|check|zeg|vertel|wie|waar|wanneer|hoeveel|heb\s+je|kan\s+je|kun\s+je|is\s+er|klopt)\b)"
)
_ADVICE = re.compile(
    r"\b(?:advies|adviseer|advise|raad|aanraden|tip|tips|wat\s+(?:moet|kan|zou)\s+ik\s+(?:hier(?:mee)?\s+|nu\s+)?doen"
    r"|wat\s+nu|aandachtspunt\w*|waar\s+moet\s+ik\s+op\s+letten|conseil\w*|advice)\b"
)
_VOCABULARY = ("adres", "advies", "aandachtspunten", "leveringsadres", "facturatieadres", "samenvatting", "woont")


def _plain(text: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(char))


def _normalise(text: str) -> str:
    """Typos in the few words that pick a task: 'adress', 'advis', 'leveringsadrs' become the word itself."""
    def fix(match: re.Match[str]) -> str:
        word = match.group(0)
        if len(word) < 4:
            return word
        close = difflib.get_close_matches(_plain(word), _VOCABULARY, n=1, cutoff=0.8)
        return close[0] if close else word
    return re.sub(r"[^\W\d_]+", fix, text)


def classify(text: str, customer_parts: set[str] = frozenset()) -> Task | None:
    """The task one part of a question asks for, or None when it is not a recognisable request."""
    from .invoice_schema import (
        is_explanation_request, is_insight_request, is_other_payer_question, is_payer_request, is_summary_request,
        is_yearly_request,
    )
    from .invoice_analysis import _COST_WORDS
    from .invoice_session import _THIS_AMOUNT, address_intent

    normal = _normalise(text)
    plain = _plain(normal)
    named = re.search(r"\b(?:\w*adres\w*|adresse|address)\b", plain)
    kind = address_intent(normal, customer_parts) or (
        # "zoek het adres", or a short part of a longer message: "…, en het adres graag"
        "address" if named and (re.search(r"\b(?:zoek|geef|toon|wat|welk|waar|vind|lees)\w*\b", plain) or len(plain.split()) <= 4) else None
    )
    if kind is not None:
        # The word the visitor typed decides the kind: "geef het leveringsadres" is the delivery address.
        if re.search(r"\b(?:leverings?adres|adresse de (?:livraison|fourniture))\b", plain):
            kind = "delivery"
        elif re.search(r"\b(?:facturatieadres|factuuradres|adresse de facturation)\b", plain):
            kind = "billing"
        return Task("address", text, kind)
    if _THIS_AMOUNT.search(normal):
        return Task("this_amount", text)
    if is_payer_request(normal) and not is_other_payer_question(normal):
        return Task("payer", text)
    advice = _ADVICE.search(plain)
    if advice and _only_asks_for_advice(plain, advice, customer_parts):
        return Task("advice", text)
    if is_yearly_request(normal) and _COST_WORDS.search(normal):
        return Task("yearly", text)
    if is_insight_request(normal):
        return Task("insights", text)
    if is_summary_request(normal):
        return Task("summary", text)
    if is_explanation_request(normal):
        return Task("explain", text)
    return None


def _task_key(task: Task) -> tuple[str, str | None, str | None]:
    """Two parts are the same task only with the same type and subject: "per jaar aan gas" and "per jaar aan
    stroom" are two yearly tasks; "vat samen, vat samen" is one."""
    from .invoice_schema import _asked_energy

    return task.type, task.address_kind, _asked_energy(task.text)


def _only_asks_for_advice(plain: str, match: re.Match[str], customer_parts: set[str]) -> bool:
    """Advice about this bill, not about a topic: "wat moet ik doen als ik verhuis?" and "tips om te besparen"
    are questions of their own, answered from the bill's passages or without the bill."""
    rest = plain[:match.start()] + " " + plain[match.end():]
    return all(word in _ADVICE_EXTRAS or word in customer_parts for word in _content_words(rest))


_ADVICE_EXTRAS = frozenset("heb hebt kan kun zie jij nog wat iets aan geef geven".split())


def _content_words(text: str) -> list[str]:
    return [word for word in re.findall(r"[a-z]{3,}", _plain(text)) if word not in _FILLER]


_FILLER = frozenset(
    "deze dit dat die het een van over voor met mijn mij jij jou hier daar ook nog even graag aub alsjeblieft please "
    "factuur rekening document pdf bijlage dank bedankt merci thanks wel alvast super top oke okay".split()
)


def parse_locally(question: str, customer_parts: set[str] = frozenset()) -> Plan:
    whole = classify(question, customer_parts)
    parts = [part.strip() for part in _BOUNDARY.split(question) if part and part.strip()]
    if len(parts) > 1:
        tasks: list[Task] = []
        unclear: list[str] = []
        for part in parts:
            task = classify(part, customer_parts)
            if task is not None and all(_task_key(task) != _task_key(other) for other in tasks):
                tasks.append(task)
            elif task is None and _content_words(part):
                unclear.append(part)
        if len(tasks) >= 2:
            return Plan(tasks[:MAX_TASKS], unclear[:2])
        if len(tasks) == 1 and (whole is None or unclear):
            # "heb je iets voor me over deze factuur, zoek het adres op": do the part that is clear, ask about the rest.
            return Plan(tasks, unclear[:2])
    return Plan([whole or Task("question", question)])


# --- the language-model parser ------------------------------------------------------------------------

def intent_model_enabled() -> bool:
    """Off by default: only KAREN_INTENT_MODEL=1 with an OpenAI key sends the (masked) question to a hosted model."""
    return os.getenv("KAREN_INTENT_MODEL", "0").strip() == "1" and bool(os.getenv("OPENAI_API_KEY", "").strip())


_INSTRUCTION = """Je deelt een vraag over een geüploade energiefactuur op in taken. Je ziet de factuur niet.
Persoonsgegevens zijn vervangen door [klant] of [X]. Geef alleen JSON volgens het schema.
Taaktypes:
- address: een adres (address_kind: residence = waar iemand woont, delivery = leveringsadres, billing = facturatieadres, address = een adres)
- advice: advies, tips, aandachtspunten, wat de gebruiker moet doen
- payer: wie betaalt of wie het verschuldigd is
- yearly: kosten of voorschotten per jaar
- summary: samenvatten
- explain: wat de factuur betekent
- insights: wat je uit de factuur kunt afleiden
- this_amount: een vraag over "dit bedrag" of "dat bedrag" uit het vorige antwoord
- question: elke andere vraag over de factuur (bedrag, btw, datum, verbruik, voorwaarden)
"text" is het deel van de vraag dat bij de taak hoort, letterlijk overgenomen. Delen die je niet kunt plaatsen
zet je in "unclear", letterlijk. Hoogstens 4 taken."""

INTENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["tasks", "unclear"],
    "properties": {
        "tasks": {
            "type": "array",
            "maxItems": MAX_TASKS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["type", "text", "address_kind"],
                "properties": {
                    "type": {"type": "string", "enum": list(TASK_TYPES)},
                    "text": {"type": "string", "maxLength": 300},
                    "address_kind": {"type": ["string", "null"], "enum": ["residence", "delivery", "billing", "address", None]},
                },
            },
        },
        "unclear": {"type": "array", "maxItems": 2, "items": {"type": "string", "maxLength": 300}},
    },
}


def masked_question(question: str, names: tuple[str, ...]) -> str:
    """The question as the model sees it: the customer's name as [klant], identifiers as [X]."""
    from .invoice_model import mask_identifiers

    masked = mask_identifiers(question, ())  # e-mail, IBAN and numbers first, so a name inside an e-mail goes with it
    for name in names:
        for part in sorted({part for part in re.findall(r"[^\W\d_]{2,}", name)}, key=len, reverse=True):
            masked = re.sub(rf"(?i)\b{re.escape(part)}\b", "[klant]", masked)
    masked = re.sub(r"\[klant\](?:\s+\[klant\])+", "[klant]", masked)
    # An address typed in the question ("woont ze in de Kerkstraat 12, 9000 Gent?") is not needed to see the task.
    masked = _STREET_FIRST.sub("[adres]", masked)
    masked = _STREET.sub("[adres]", masked)
    masked = _POSTCODE_PLACE.sub("[adres]", masked)
    # Whatever stands before a house number and a postcode is the street, in any language: "Grote Markt 1, [adres]".
    masked = _BEFORE_POSTCODE.sub("[adres]", masked)
    masked = re.sub(r"\[adres\](?:[\s,]+\[adres\])+", "[adres]", masked)
    return mask_identifiers(masked, ())


# French and other street names that start with their kind: "Rue des Tests 12", "Chaussée de Wavre 100 bte 3".
_STREET_FIRST = re.compile(
    r"(?i)\b(?:rue|avenue|av\.|boulevard|bd|chauss[ée]e|place|square|all[ée]e|chemin|quai|impasse|route|dr[èe]ve|clos|cours"
    r"|passage|rond-point|sentier|street|road|lane)\b(?:\s+[^\W\d_][\w'’-]*){0,5}\s+\d+\s*\w?\b(?:\s*(?:bus|bte|boîte|b)\.?\s*\w+)?"
)
_BEFORE_POSTCODE = re.compile(r"(?:\b[^\W\d_][\w'’-]*\s+){1,5}\d+\s*\w?\b(?:\s*(?:bus|bte|boîte)\.?\s*\w+)?,?\s*(?=\[adres\])")
_STREET = re.compile(
    r"(?i)(?:(?-i:\b[A-Z][^\W\d_]*)\s+){0,3}\b[^\W\d_]*(?:straat|laan|weg|plein|lei|dreef|baan|steenweg|kaai|markt|singel|dijk|pad|hof|rue|avenue|chauss[ée]e|boulevard|place)"
    r"\s*\d+\s*\w?\b(?:\s*(?:bus|bte|boîte)\s*\w+)?"
)
_POSTCODE_PLACE = re.compile(r"\b\d{4}\s?(?:[A-Z]{2}\s+)?[A-Z][^\W\d_]+(?:[- ][A-Z][^\W\d_]+)*")


async def _ask_intent_model(masked: str, context: dict[str, Any]) -> str:
    from openai import AsyncOpenAI

    model = os.getenv("KAREN_INTENT_MODEL_NAME", "gpt-5-mini").strip()
    options: dict[str, Any] = {"reasoning": {"effort": "minimal"}} if model.startswith("gpt-5") else {}
    payload = json.dumps({"vraag": masked, "context": context}, ensure_ascii=False)
    async with AsyncOpenAI(timeout=INTENT_DEADLINE_SECONDS, max_retries=0) as client:
        result = await client.responses.create(
            model=model, instructions=_INSTRUCTION, input=payload, store=False,
            text={"format": {"type": "json_schema", "name": "karen_invoice_tasks", "schema": INTENT_SCHEMA, "strict": True}},
            **options,
        )
    return result.output_text or ""


def validate(raw: str, question: str, masked: str, names: tuple[str, ...]) -> Plan | None:
    """The model's plan when it fits the schema and every task quotes the question; otherwise None."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or set(data) != {"tasks", "unclear"} or not isinstance(data["tasks"], list):
        return None
    customer = names[0] if names else ""
    tasks: list[Task] = []
    for item in data["tasks"][:MAX_TASKS]:
        if not isinstance(item, dict) or item.get("type") not in TASK_TYPES or not isinstance(item.get("text"), str):
            return None
        kind = item.get("address_kind")
        if kind not in (None, "residence", "delivery", "billing", "address") or (item["type"] == "address") != (kind is not None):
            return None
        if not _quotes(item["text"], masked):
            return None  # a task the question does not contain: invented
        text = item["text"].replace("[klant]", customer) if customer else item["text"]
        tasks.append(Task(item["type"], text if _quotes(text, question) else question, kind))
    unclear = [text for text in data.get("unclear") or [] if isinstance(text, str) and _quotes(text, masked)][:2]
    if not tasks:
        return None
    return Plan(tasks, unclear, "language_model")


def _quotes(text: str, question: str) -> bool:
    words = re.findall(r"[a-z0-9\[\]]+", _plain(text))
    asked = set(re.findall(r"[a-z0-9\[\]]+", _plain(question)))
    return bool(words) and sum(word in asked for word in words) >= max(1, round(len(words) * 0.8))


async def parse(question: str, names: tuple[str, ...], customer_parts: set[str], context: dict[str, Any]) -> Plan:
    """The model's plan when enabled and valid, else the local parser's."""
    if intent_model_enabled() and 2 <= len(question) <= 500:
        masked = masked_question(question, names)
        try:
            raw = await asyncio.wait_for(_ask_intent_model(masked, context), INTENT_DEADLINE_SECONDS + 1)
        except Exception:
            raw = ""
        plan = validate(raw, question, masked, names)
        if plan is not None:
            return plan
    return parse_locally(question, customer_parts)
