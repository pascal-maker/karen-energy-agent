"""Today's official maximum prices for heating oil, diesel, petrol and propane in Belgium.

The FOD Economie page sits behind a captcha, so Karen reads the same official maximum prices
from Energia (the federation of the oil sector), which lists every product with its price per
litre (VAT included) and the date it applies from. Searching the web for "prijs olie vandaag"
only found electricity and gas pages, so these questions get this page instead.
"""

from __future__ import annotations

import asyncio
import html
import re
import time
from datetime import date, datetime
from typing import Any
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .market_prices import question_language

SOURCE_URL = "https://www.petrolfed.be/nl/maximumprijzen"
SOURCE_TITLE = "Energia · officiële maximumprijzen brandstoffen"
CACHE_SECONDS = 60 * 60
_BRUSSELS = ZoneInfo("Europe/Brussels")

_ROW = re.compile(
    r'samenstelling-chart__title">\s*(?P<name>[^<]*(?:<[^/][^<]*)?)</h3>\s*'
    r'<div class="samenstelling-chart__price(?P<change>[^"]*)">\s*€/l\s*(?P<price>\d+[.,]\d+)\s*</div>\s*'
    r'<div class="samenstelling-chart__date">\s*vanaf\s*(?P<date>\d{4}-\d{2}-\d{2})',
)
_cache: dict[str, Any] = {"at": 0.0, "rows": None}

# Product asked about -> the row Karen leads with, and the rows shown under it.
_PRODUCTS = {
    "heating_oil": ("Gasolie verwarming (H0/H7) (<2000 L)", ("Gasolie verwarming (H0/H7) (≥ 2000 L)",)),
    "diesel": ("Diesel - B7", ("Diesel - B10",)),
    "petrol": ("Benzine 95 RON - E10", ("Benzine 95 RON - E5", "Benzine 98 RON - E5")),
    "propane": ("Propaan bulk (≥ 2000 L)", ()),
    "lpg": ("LPG", ()),
}
_LABELS = {
    "Gasolie verwarming (H0/H7) (<2000 L)": "Stookolie, minder dan 2000 liter",
    "Gasolie verwarming (H0/H7) (≥ 2000 L)": "Stookolie, vanaf 2000 liter",
    "Diesel - B7": "Diesel (B7)",
    "Diesel - B10": "Diesel (B10)",
    "Benzine 95 RON - E10": "Benzine 95 (E10)",
    "Benzine 95 RON - E5": "Benzine 95 (E5)",
    "Benzine 98 RON - E5": "Benzine 98 (E5)",
    "Propaan bulk (≥ 2000 L)": "Propaan in bulk, vanaf 2000 liter",
    "LPG": "LPG",
}
_LEAD = {
    "heating_oil": "stookolie (mazout)",
    "diesel": "diesel",
    "petrol": "benzine 95",
    "propane": "propaan in bulk",
    "lpg": "LPG aan de pomp",
}

_PRICE_WORDS = re.compile(
    r"\b(?:prijs\w*|olieprijs|kost\w*|vandaag|per liter|literprijs|maximumprijs\w*|price|cost\w*|today)\b",
    re.IGNORECASE,
)
# Why prices move, or where they go next, is a news question: the search path answers those.
_NOT_TODAY = re.compile(r"\b(?:waarom|why|hoe lang|stijg\w*|daal\w*|zal|zullen|gaan|verwacht\w*|will)\b", re.IGNORECASE)
# Specific fuels first: "olie" on its own means heating oil, the oil households buy.
_PRODUCT_WORDS = (
    ("diesel", re.compile(r"\bdiesel\b", re.IGNORECASE)),
    ("petrol", re.compile(r"\b(?:benzine|petrol|gasoline)\b", re.IGNORECASE)),
    ("propane", re.compile(r"\b(?:propaan|propane)\b", re.IGNORECASE)),
    ("lpg", re.compile(r"\blpg\b", re.IGNORECASE)),
    ("heating_oil", re.compile(r"\b(?:stookolie\w*|mazout\w*|huisbrandolie|gasolie|olie|olieprijs|heating oil|oil)\b", re.IGNORECASE)),
)
_NAMES_HEATING_OIL = re.compile(r"\b(?:stookolie\w*|mazout\w*|huisbrandolie|gasolie|heating oil)\b", re.IGNORECASE)


def fuel_product(question: str) -> str | None:
    """Which fuel a question asks today's price of, or None for any other question."""
    if not _PRICE_WORDS.search(question) or _NOT_TODAY.search(question):
        return None
    return next((product for product, words in _PRODUCT_WORDS if words.search(question)), None)


def parse_prices(page: str) -> dict[str, dict[str, Any]]:
    """Product name -> price per litre, the date it applies from, and whether it went up or down."""
    rows = {}
    for match in _ROW.finditer(page):
        name = html.unescape(" ".join(match["name"].split()))
        change = "up" if "--up" in match["change"] else "down" if "--down" in match["change"] else ""
        rows[name] = {"price": float(match["price"].replace(",", ".")), "from": match["date"], "change": change}
    return rows


def _download() -> str:
    request = Request(SOURCE_URL, headers={"User-Agent": "Mozilla/5.0 (karen-energy-agent)"})
    with urlopen(request, timeout=10) as response:
        return response.read().decode("utf-8", errors="ignore")


async def load_prices() -> dict[str, dict[str, Any]] | None:
    """Today's rows, cached for an hour; None when the page can't be read."""
    now = time.monotonic()
    if _cache["rows"] and now - _cache["at"] < CACHE_SECONDS:
        return _cache["rows"]
    try:
        rows = parse_prices(await asyncio.to_thread(_download))
    except Exception:
        return None
    if not rows:
        return None
    _cache.update(at=now, rows=rows)
    return rows


def _euro(value: float) -> str:
    return f"€ {value:.3f}".replace(".", ",")


def _day(iso: str) -> str:
    months = ("jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov", "dec")
    day = date.fromisoformat(iso)
    return f"{day.day} {months[day.month - 1]} {day.year}"


def _when(iso: str, today: date) -> str:
    """The page often already lists tomorrow's price, published the day before."""
    day = date.fromisoformat(iso)
    if day > today:
        return "Vanaf morgen" if (day - today).days == 1 else f"Vanaf {_day(iso)}"
    return "Vandaag"


def fuel_reply(product: str, rows: dict[str, dict[str, Any]], question: str = "", today: date | None = None) -> dict[str, Any] | None:
    lead_name, others = _PRODUCTS[product]
    lead = rows.get(lead_name)
    if lead is None:
        return None
    today = today or datetime.now(_BRUSSELS).date()
    change = {"up": " Dat is meer dan de vorige maximumprijs.", "down": " Dat is minder dan de vorige maximumprijs."}
    lines = [
        f"{_when(lead['from'], today)} is de officiële maximumprijs voor {_LEAD[product]} "
        f"**{_euro(lead['price'])} per liter**, btw inbegrepen.{change.get(lead['change'], '')}",
        "",
    ]
    for name in (lead_name, *others):
        row = rows.get(name)
        if row:
            lines.append(f"- **{_LABELS.get(name, name)}:** {_euro(row['price'])} per liter, vanaf {_day(row['from'])}.")
    tip = " Vraag gerust een offerte bij een paar handelaars." if product in ("heating_oil", "propane") else ""
    lines.append(
        "- **Wat dit is:** de hoogste prijs die een handelaar of tankstation mag vragen, vastgelegd door de overheid. "
        f"Vaak betaal je iets minder.{tip}"
    )
    if product == "heating_oil" and not _NAMES_HEATING_OIL.search(question):
        lines.append("- **Ruwe olie (Brent):** de koers per vat op de wereldmarkt volg ik niet; dit is de prijs die je thuis betaalt.")
    excerpt = "; ".join(
        f"{name}: €/l {rows[name]['price']:.4f} vanaf {rows[name]['from']}" for name in (lead_name, *others) if name in rows
    )
    return {
        "answer": "\n".join(lines),
        "sources": [{"title": SOURCE_TITLE, "url": SOURCE_URL, "detail": "Officiële maximumprijzen, dagelijks bijgewerkt", "excerpt": excerpt}],
        "live_source_status": "checked",
        "processing": "fuel-prices",
    }


async def answer_fuel_price_question(question: str) -> dict[str, Any] | None:
    """Today's official maximum price for the fuel asked about, in Dutch; None for other questions."""
    product = fuel_product(question)
    if product is None or question_language(question) != "nl":
        return None
    rows = await load_prices()
    if rows is None:
        return None
    return fuel_reply(product, rows, question)
