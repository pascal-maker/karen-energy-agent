"""Checking your energy contract: the four steps, the CREG-scan, sjoemelkortingen, vaste vergoeding, switching.

Live searches for these questions mostly came back empty ("geen betrouwbare bron"), so these
answers are written out by hand from official pages and the VRT NWS guide of 21 October 2026.
Every fact below is quoted in a source excerpt; re-check the pages when a rule changes.
Dutch only: these terms are Belgian-Dutch, and other languages keep the search path.
"""

from __future__ import annotations

import re
from typing import Any

from .browserbase_sources import named_supplier
from .market_prices import question_language

CHECKED_ON = "gecontroleerd op 23 okt 2026"

_VRT_GUIDE = {
    "title": "VRT NWS · zo kijk je je energiecontract na in 4 stappen (21 okt 2026)",
    "url": "https://www.vrt.be/vrtnws/nl/2026/10/21/energiecontract-elektriciteit-gas-prijs-v-test-creg-scan/",
}
_CREG_SCAN = {"title": "CREG-scan", "url": "https://www.cregscan.be/nl"}
_CREG_COMPARISON = {
    "title": "CREG · prijzen vergelijken",
    "url": "https://www.creg.be/nl/consumenten/prijzen-en-tarieven/vergelijking-van-prijzen-voor-elektriciteit-en-aardgas",
}
_VTEST = {"title": "V-test (Vlaamse Nutsregulator)", "url": "https://www.vtest.be/"}
_BRUSIM = {"title": "BruSim (BRUGEL)", "url": "https://brugel.brussels/nl_BE/outils/brusim-2"}
_COMPACWAPE = {"title": "CompaCWaPE (CWaPE)", "url": "https://www.compacwape.be/"}
_VRT_DISCOUNTS = {
    "title": "VRT NWS · 'Deceptive discounts' on energy contracts banned (30 sep 2026)",
    "url": "https://www.vrt.be/vrtnws/en/2026/09/30/_deceptive-discounts-on-energy-contracts-banned.consumer/",
}
_SWITCHING = {
    "title": "Vlaanderen.be · energieleveranciers en energiecontracten",
    "url": "https://www.vlaanderen.be/energieleveranciers-en-energiecontracten",
}


def _source(page: dict[str, str], excerpt: str) -> dict[str, str]:
    return {**page, "detail": f"Officiële of journalistieke bron · {CHECKED_ON}", "excerpt": excerpt}


def _comparators() -> list[dict[str, str]]:
    return [{**page, "detail": "Officiële vergelijker · zelf je verbruik invullen"} for page in (_VTEST, _BRUSIM, _COMPACWAPE)]


_TOPICS = (
    ("sjoemelkorting", re.compile(r"sjoemel\w*|welkomstkorting\w*|lokkorting\w*", re.IGNORECASE)),
    ("vaste_vergoeding", re.compile(r"vaste\s+vergoeding|abonnementsgeld|abonnementskost", re.IGNORECASE)),
    ("creg_scan", re.compile(r"creg[\s-]?scan", re.IGNORECASE)),
    ("switching", re.compile(
        r"(?:zelf|eerst)\s+(?:op\s*(?:te\s+)?zeggen|opzeggen)"
        r"|(?:oude|huidige|vorige)\s+(?:energie)?contract\s+(?:\w+\s+){0,2}(?:op\s*(?:te\s+)?zeggen|opzeggen)"
        r"|hoe\s+(?:stap|wissel|verander)\s+ik"
        r"|(?:overstappen|veranderen|wisselen)\s+van\s+(?:energie)?leverancier\s+(?:\w+\s+){0,2}(?:gratis|kosten|kost|lang)",
        re.IGNORECASE,
    )),
    ("check", re.compile(
        r"(?:check\w*|controle\w*|nakijk\w*|na\s+te\s+kijken|nagaan|onder\s+de\s+loep).{0,40}(?:energie)?contract"
        r"|kijk\s+ik\s+\w+\s+(?:energie)?contract\s+na"
        r"|(?:energie)?contract\w*.{0,30}(?:check\w*|controle\w*|nakijk\w*|nagaan)"
        r"|(?:betaal\s+ik|betalen|betaal\s+je|betaalt)\s+(?:\w+\s+){0,2}te\s+veel|te\s+veel\s+(?:betalen|betaal)"
        r"|moet\s+ik\s+(?:\w+\s+){0,2}(?:over\s*stappen|overstappen|wisselen|veranderen)(?:\s+van\s+(?:energie)?leverancier)?\s*\??$",
        re.IGNORECASE,
    )),
)


def contract_check_topic(question: str) -> str | None:
    """Which contract-check answer fits, or None. A named supplier keeps its own opzeg-route."""
    if question_language(question) != "nl":
        return None
    for topic, pattern in _TOPICS:
        if pattern.search(question):
            if topic == "switching" and named_supplier(question):
                return None
            return topic
    return None


def _check() -> tuple[str, list[dict[str, str]]]:
    answer = (
        "Begin bij je eigen contract en beslis niet overhaast. Zo kijk je het na:\n"
        "1. **Neem je facturen erbij:** kijk welk contract je hebt, in welke maand je het afsloot en wat je jaarverbruik is (dat staat op je jaarafrekening).\n"
        "2. **Doe de CREG-scan:** in nog geen 2 minuten zie je of je contract duur (rood), gemiddeld (oranje) of goedkoop (groen) is. Groen? Dan hoef je niets te veranderen.\n"
        "3. **Vergelijk met de V-test:** die zet alle aanbiedingen op een rij, de goedkoopste bovenaan (in Brussel BruSim, in Wallonië CompaCWaPE). Kijk ook naar looptijd, vaste vergoeding en voorwaarden, niet alleen naar de prijs.\n"
        "4. **Pas je contract aan (of niet):** dat kan elke maand, ook bij je huidige leverancier. Stap je over, dan regelt je nieuwe leverancier alles en hoef je niet zelf op te zeggen.\n"
        "Volgens de CREG betalen 700.000 gezinnen gemiddeld 700 euro per jaar te veel voor stroom en gas."
    )
    sources = [
        _source(_VRT_GUIDE, "Volgens de laatste cijfers van de federale energiewaakhond CREG betalen 700.000 gezinnen "
                            "gemiddeld 700 euro per jaar te veel voor stroom en gas."),
        _source(_VRT_GUIDE, "Het belangrijkste advies dat ik kan geven, is dus: neem eerst je eigen contract erbij "
                            "en maak geen overhaaste beslissingen."),
        _source(_SWITCHING, "U moet uw huidige contract dus niet opzeggen. Uw nieuwe leverancier regelt alles."),
    ]
    return answer, sources


def _creg_scan() -> tuple[str, list[dict[str, str]]]:
    answer = (
        "De CREG-scan is een gratis onlinetool van de federale energiewaakhond CREG: hij vergelijkt je huidige contract met wat er vandaag op de markt is.\n"
        "- **Wat je invult:** welk contract je hebt en in welke maand je het afsloot. Ook een contract van jaren geleden kan.\n"
        "- **Resultaat:** in nog geen 2 minuten toont een balk of je contract duur (rood), gemiddeld (oranje) of goedkoop (groen) is.\n"
        "- **Daarna:** groen betekent dat je goed zit. Anders zoek je een voordeliger contract via de V-test (in Brussel BruSim, in Wallonië CompaCWaPE)."
    )
    sources = [
        _source(_CREG_COMPARISON, "De CREG Scan maakt het mogelijk om uw contract te vergelijken, zelfs wanneer u dit "
                                  "contract jaren geleden hebt ondertekend en het dus niet langer deel uitmaakt van het "
                                  "actief aanbod van de leveranciers."),
        _source(_VRT_GUIDE, "Op een balk toont de scan je in nog geen 2 minuten of je huidige contract duur (rood), "
                            "gemiddeld (oranje) of goedkoop (groen) is in vergelijking met wat er vandaag op de markt beschikbaar is."),
    ]
    return answer, sources


def _sjoemelkorting() -> tuple[str, list[dict[str, str]]]:
    answer = (
        "Ja, sinds 1 oktober 2026 zijn sjoemelkortingen verboden in België.\n"
        "- **Wat dat betekent:** een korting moet gelden vanaf de eerste kilowattuur en voor de hele looptijd van het contract, niet pas na een of twee jaar.\n"
        "- **Let op bij een lopend contract:** volgens de CREG kunnen zulke kortingen nog gelden voor je huidige contract. Kijk dus na wanneer en onder welke voorwaarden je korting wordt uitbetaald."
    )
    sources = [
        _source(_VRT_DISCOUNTS, "Discounts must apply from the very first kilowatt-hour and remain in force for the "
                                "entire duration of the contract."),
        _source(_VRT_GUIDE, "Maar die kortingen zouden wel nog van toepassing kunnen zijn op je lopende contract. Let daar goed op."),
    ]
    return answer, sources


def _vaste_vergoeding() -> tuple[str, list[dict[str, str]]]:
    answer = (
        "De vaste vergoeding is een vast bedrag dat sommige leveranciers per jaar aanrekenen, los van je verbruik: een soort abonnementsgeld.\n"
        "- **Bij vertrek:** verbreek je je contract, dan krijg je die niet altijd terug. Eén leverancier rekent bijvoorbeeld meteen 6 maanden vaste vergoeding aan als je binnen een halfjaar vertrekt.\n"
        "- **Vergelijken:** de V-test toont zulke nadelige verbrekingsregelingen duidelijk. Tel de vaste vergoeding mee als je contracten vergelijkt."
    )
    sources = [
        _source(_VRT_GUIDE, "Er zijn leveranciers die bijvoorbeeld jaarlijks een 'vaste vergoeding' aanrekenen. "
                            "Dat is een soort abonnementsgeld, zeg maar. Als je je contract verbreekt, krijg je dat niet altijd terug."),
        _source(_VRT_GUIDE, "Ook zulke nadelige verbrekingsregelingen geeft de V-test duidelijk aan."),
    ]
    return answer, sources


def _switching() -> tuple[str, list[dict[str, str]]]:
    answer = (
        "Nee, je hoeft je huidige contract niet zelf op te zeggen: je sluit een contract af bij je nieuwe leverancier en die regelt alles.\n"
        "- **Kosten:** van leverancier veranderen kan altijd en is gratis.\n"
        "- **Wanneer:** je nieuwe contract gaat in principe een maand later in, aan de prijs van het aanbod op het moment dat je het afsluit.\n"
        "- **Kleine lettertjes:** sommige leveranciers houden een vaste vergoeding in als je vroeg vertrekt. De V-test toont zulke regelingen."
    )
    sources = [
        _source(_SWITCHING, "U moet uw huidige contract dus niet opzeggen. Uw nieuwe leverancier regelt alles."),
        _source(_SWITCHING, "Van leverancier veranderen kan altijd en is gratis."),
        _source(_VRT_GUIDE, "Je nieuwe contract gaat in principe een maand later in. De prijs die je betaalt, is die "
                            "van het aanbod op het moment dat je het nieuwe contract afsluit."),
    ]
    return answer, sources


_ANSWERS = {
    "check": _check, "creg_scan": _creg_scan, "sjoemelkorting": _sjoemelkorting,
    "vaste_vergoeding": _vaste_vergoeding, "switching": _switching,
}


def answer_contract_check(question: str) -> dict[str, Any] | None:
    """The hand-checked contract-check answer with its sources, or None for other questions."""
    topic = contract_check_topic(question)
    if topic is None:
        return None
    answer, sources = _ANSWERS[topic]()
    suggested = [{**_CREG_SCAN, "detail": "Officiële tool van de CREG"}] if topic in ("check", "creg_scan") else []
    return {
        "answer": answer, "sources": sources, "suggested_sources": suggested + _comparators(),
        "live_source_status": "checked", "processing": "contract-check",
    }
