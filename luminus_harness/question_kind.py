"""What a question asks, decided in code before a price route may take it.

The swarm audit of 5175ecf found the fixed price routes (tariff cards, market prices, contract
check, offer comparison) answering questions that only *mention* a supplier or a price word:
"where do I find the takeover document, and how long before must I tell Engie?" got Engie's
tariff card, "how does the V-test work?" got today's cheapest contracts, and "why is the shop
price lower than the wholesale price?" got the price list again. A price route answers a price
question; a why, how, where, when or must-I question goes to the sourced answer instead.
"""

from __future__ import annotations

import re

# An explicit ask for a price, an amount or a cheapest offer.
_PRICE_ASK = re.compile(
    r"\b(?:wat|hoeveel)\s+(?:kost|kosten|betaal|betaalt|bedraagt|bedragen|is\s+(?:de|het)\s+(?:\w+-?)?(?:prijs|tarief|bedrag))"
    r"|\bwat\s+zijn\s+de\s+(?:\w+-?)?(?:prijzen|tarieven)"
    r"|\b(?:kwh-?prijs|prijs\s+per\s+kwh|per\s+kwh|c€|cent\s+per|vaste\s+vergoeding|goedkoopst\w*|duurst\w*|jaartotaal|jaarbedrag)"
    r"|\bcombien\s+(?:co[uû]te|ça\s+co[uû]te|coûtent|je\s+paie|vais-je\s+payer|payer|d'euros)"
    r"|\b(?:quel|quels)\s+(?:est|sont)\s+(?:le|les)\s+(?:prix|tarifs?)"
    r"|\bprix\s+(?:du|de\s+la|par)\s+kwh|\bmoins\s+cher|\ble\s+plus\s+cher"
    r"|\bhow\s+much\b|\bwhat\s+(?:does|do|is)\b[^?]{0,40}\b(?:cost|price)|\bprice\s+per\b|\bcheapest\b",
    re.IGNORECASE,
)
# Why, how, where, when, must-I and what-does-it-mean questions: the answer is a rule, a reason or a step.
_OTHER_ASK = re.compile(
    r"\b(?:waarom|hoe\s+(?:kan|komt|werkt|lang|vaak|verander|doe|vraag|meld|regel|zeg|stap|vind)|"
    r"waar\s+(?:vind|staat|kan|moet|vraag)|moet\s+ik|mag\s+(?:ik|de|mijn)|wanneer|automatisch|vanzelf|op\s+voorhand|"
    r"recht\s+op|heb\s+ik\s+recht|gaat\s+(?:mijn|de|het)\s+\w+\s+(?:nu\s+)?(?:omhoog|stijgen|dalen)|"
    r"droit\s+[àa]|ai-je\s+droit|entitled|"
    r"combien\s+de\s+temps|client\s+prot[ée]g[ée]|beschermde\s+afnemer|protected\s+customer|fournisseur\s+social|"
    r"sociale\s+leverancier|"
    r"wat\s+(?:betekent|houdt|vergelijkt)|leg\s+(?:uit|eens\s+uit)|document|opzeg\w*|"
    r"pourquoi|comment|o[uù]\s|dois-je|faut-il|est-ce\s+que|explique\w*|c'est\s+quoi|qu'est-ce|document|"
    r"why|how\s+(?:does|do|can|long)|where|when|explain|what\s+does\s+\w+\s+mean)\b",
    re.IGNORECASE,
)
# "I didn't ask for the prices": a price route is exactly what the visitor said they don't want.
_NO_PRICES = re.compile(
    r"\b(?:pas|niet|geen|not|never|nooit)\b[^.?!]{0,25}\b(?:prix|prijs|prijzen|price|prices|tarieven)\b",
    re.IGNORECASE,
)
# Named rules and schemes whose name holds "tarief"/"tarif", but that are no price question.
_SCHEME = re.compile(
    r"\b(?:sociaal\s+tarief|tarif\s+social|social\s+tariff|capaciteitstarief|tarif\s+capacitaire|"
    r"prosumententarief|tarif\s+prosumer|injectietarief)\b",
    re.IGNORECASE,
)
# Opinions no energy assistant should give, even when energy is the topic.
_OPINION = re.compile(
    r"\b(?:politieke?\s+partij\w*|parti\s+politique|partis\s+politiques|political\s+part(?:y|ies)|"
    r"stem(?:men)?\s+op|voter\s+pour|vote\s+for|n-va|vooruit|groen|vlaams\s+belang|open\s+vld|cd&v|pvda|"
    r"ecolo|mr|ps|les\s+engagés)\b.*\b(?:beste|best|meilleur|kies|choisis|pick|choose)\b"
    r"|\b(?:beste|best|meilleur)\b.*\b(?:politieke?\s+partij|parti\s+politique|political\s+party)\b",
    re.IGNORECASE,
)


def asks_for_price(question: str) -> bool:
    return bool(_PRICE_ASK.search(_SCHEME.sub(" ", question)))


# A why or how-does-it-work question: price words in it ("per kWh", "vaste vergoeding") name the
# subject, they don't ask for an amount. Only an outright amount ask still wants the price list.
_EXPLAIN_ASK = re.compile(
    r"\b(?:waarom|hoe\s+(?:kan|komt|werkt|verandert|evolueert|wordt|zit)|wat\s+(?:betekent|houdt)|leg\s+(?:uit|eens\s+uit)|"
    r"pourquoi|comment\s+(?:fonctionne|ça\s+marche|se\s+fait|est\s+calcul|évolue|change)|explique\w*|"
    r"why|how\s+(?:does|do|is|are|can)|explain)\b",
    re.IGNORECASE,
)
_AMOUNT_ASK = re.compile(
    r"\b(?:wat|hoeveel)\s+(?:kost|kosten|betaal|betaalt|bedraagt)\b|\bwat\s+is\s+(?:de|het)\s+(?:\w+-?)?(?:prijs|bedrag)\b|"
    r"\bcombien\s+(?:co[uû]te|ça\s+co[uû]te|coûtent|je\s+paie)|\bquel\s+est\s+le\s+prix\b|\bhow\s+much\b|"
    r"\bwhat\s+(?:does|do)\b[^?]{0,40}\bcost\b|\bwhat\s+is\s+the\s+price\b|"
    # Comparing on price is asking for the prices: "How do Eneco and Luminus compare on price per kWh?"
    r"\b(?:compar\w*|vergelijk\w*)\b[^?]{0,40}\b(?:price|prices|prijs\w*|prix|tarie\w*|tarif\w*)\b|"
    r"\b(?:price|prices|prijs\w*|prix)\b[^?]{0,40}\b(?:compar\w*|vergelijk\w*)\b",
    re.IGNORECASE,
)


def price_route_allowed(question: str) -> bool:
    """False when a fixed price route would answer something other than what was asked."""
    if _NO_PRICES.search(question):
        return False
    if _EXPLAIN_ASK.search(question):
        return bool(_AMOUNT_ASK.search(question))
    if asks_for_price(question):
        return True
    return not _OTHER_ASK.search(question)


# A supplier's tariff (kWh price, fixed fee, a year's total): the contract-check steps don't answer that.
_TARIFF_ASK = re.compile(
    r"\b(?:kwh-?prijs|prijs\s+(?:per\s+kwh|van)|per\s+kwh|vaste\s+vergoeding|jaartotaal|jaarbedrag|c€|"
    r"prix\s+(?:du|par)\s+kwh|price\s+per\s+kwh)\b",
    re.IGNORECASE,
)


def asks_for_tariff(question: str) -> bool:
    return bool(_TARIFF_ASK.search(question))


def asks_for_opinion(question: str) -> bool:
    return bool(_OPINION.search(question))


# --- Tariff cards hold residential prices for Flanders only ------------------------------------------

def regional_tariff_answer(region: str, language: str) -> dict:
    """For Brussels or Wallonia: say the cards are Flemish and point to the region's own comparator."""
    from .market_prices import _COMPARATORS

    name, url = _COMPARATORS[region]
    place = {"brussels": {"nl": "Brussel", "fr": "Bruxelles", "en": "Brussels"},
             "wallonia": {"nl": "Wallonië", "fr": "Wallonie", "en": "Wallonia"}}[region][language]
    answer = {
        "nl": f"De tariefkaarten die ik heb, gelden voor gezinnen in Vlaanderen, niet voor {place}. "
              f"Leveranciers rekenen in {place} andere prijzen en nettarieven. Vergelijk de prijzen voor jouw "
              f"verbruik met de officiële vergelijker {name}: {url}",
        "fr": f"Les cartes tarifaires que j'ai valent pour les ménages en Flandre, pas pour la {place}. "
              f"Les fournisseurs y appliquent d'autres prix et tarifs de réseau. Compare les prix pour ta "
              f"consommation avec le comparateur officiel {name} : {url}",
        "en": f"The tariff cards I have are for households in Flanders, not {place}. Suppliers charge other "
              f"prices and grid tariffs there. Compare prices for your usage with the official comparator "
              f"{name}: {url}",
    }[language]
    if region == "brussels" and language == "fr":
        answer = answer.replace("pas pour la Bruxelles", "pas pour Bruxelles")
    return {"answer": answer, "sources": [{"title": name, "url": url}], "live_source_status": "not-needed",
            "processing": "regional-tariff"}


# --- A price at a supplier Karen has no card for ----------------------------------------------------

# Real Belgian suppliers without a checked tariff card here (the cards: market/tariffs-phase2.json).
_OTHER_SUPPLIERS = ("Ecopower", "Trevion", "DATS 24", "Wase Wind", "Energie.be", "Elindus", "Vivi", "Watz",
                    "Cociter", "Comfort Energy", "Belgian Eco Energy", "Bee")
_NAME_AFTER = re.compile(
    r"\b(?:bij|van|over|chez|de|at|from|with)\s+((?:[A-Z][\w+&.-]*)(?:\s+[A-Z0-9][\w+&.-]*){0,3})"
)
_NOT_A_SUPPLIER = re.compile(
    r"^(?:Vlaanderen|Brussel|Wallonië|Wallonie|Bruxelles|Belgi[eë]|Belgique|Flandre|VREG|CREG|Brugel|CWaPE|Fluvius|"
    r"Sibelga|ORES|RESA|OCMW|CPAS|FOD|V-test|BruSim|CompaCWaPE|Mijn|My|Januari|Februari|Maart|April|Mei|Juni|"
    r"Juli|Augustus|September|Oktober|November|December|Karen|Ik|Je|Jij|Een|Vlaamse|Waalse|Brusselse|Belgische|"
    r"Europese|Federale|Mijn\s+\w+Premie)\b"
)


def _known_card_suppliers() -> set[str]:
    import json
    from pathlib import Path

    data = json.loads((Path(__file__).resolve().parent.parent / "market" / "tariffs-phase2.json").read_text())
    return {entry["name"].casefold() for key in ("suppliers", "gas_suppliers") for entry in data.get(key, [])}


# The price of energy supply (not of a heat pump, battery or panels sold under a brand name).
_SUPPLY = re.compile(
    r"\b(?:stroom|elektriciteit|gas|energie|contract\w*|kwh|leverancier\w*|tarie\w*|"
    r"électricité|electricite|gaz|énergie|fournisseur\w*|tarif\w*|electricity|energy|supplier\w*|tariff\w*|"
    r"vast|variabel|fixe|variable|fixed)\b",
    re.IGNORECASE,
)
_PRODUCT = re.compile(
    r"\b(?:warmtepomp\w*|batterij\w*|thuisbatterij\w*|zonnepane\w*|panelen|laadpa\w*|laadstation\w*|boiler\w*|"
    r"omvormer\w*|airco\w*|auto\w*|wagen\w*|ketel\w*|pompe\s+à\s+chaleur|batterie\w*|panneaux|borne\w*|"
    r"voiture\w*|chaudière\w*|heat\s+pump\w*|batter(?:y|ies)|panels?|charger\w*|car|boiler\w*|inverter\w*)\b",
    re.IGNORECASE,
)
_OTHER_SUPPLIER_PATTERN = re.compile(
    r"(?<!\w)(" + "|".join(re.escape(name) for name in sorted(_OTHER_SUPPLIERS, key=len, reverse=True) if name != "Bee")
    + r")(?!\w)",
    re.IGNORECASE,
)


def unknown_supplier(question: str) -> str | None:
    """The supplier named in a question about the price of energy supply when Karen has no tariff card for it."""
    # Only a price question: "why is the price at Ecopower higher?" goes on to the sources.
    if not asks_for_price(question) or not price_route_allowed(question) or not _SUPPLY.search(question):
        return None
    listed = _OTHER_SUPPLIER_PATTERN.search(question)
    if listed:
        return next(name for name in _OTHER_SUPPLIERS if name.casefold() == listed.group(1).casefold())
    known = _known_card_suppliers()
    for match in _NAME_AFTER.finditer(question):
        # "een warmtepomp van Daikin": the brand of a product, not an energy supplier.
        if _PRODUCT.search(question[max(0, match.start() - 40):match.start()]):
            continue
        name = match.group(1).strip(" .,?!")
        if _NOT_A_SUPPLIER.match(name) or any(name.casefold().startswith(k) or k.startswith(name.casefold()) for k in known):
            continue
        return name
    return None


def unknown_supplier_answer(name: str, language: str) -> dict:
    listed = any(name.casefold() == other.casefold() for other in _OTHER_SUPPLIERS)
    if language == "fr":
        answer = (f"Je n'ai pas de carte tarifaire vérifiée pour {name}"
                  + (", et ce nom n'est pas dans ma liste de fournisseurs belges. Vérifie le nom ou demande-leur leur carte tarifaire. "
                     if not listed else ". Regarde leur carte tarifaire actuelle sur leur site. ")
                  + "Pour comparer les prix, utilise le comparateur officiel de ta région.")
    elif language == "en":
        answer = (f"I don't have a checked tariff card for {name}"
                  + (", and that name isn't on my list of Belgian energy suppliers. Check the name or ask them for their tariff card. "
                     if not listed else ". Look up their current tariff card on their website. ")
                  + "To compare prices, use your region's official comparator.")
    else:
        answer = (f"Van {name} heb ik geen gecontroleerde tariefkaart"
                  + (", en die naam staat niet in mijn lijst van Belgische energieleveranciers. Controleer de naam of vraag hun tariefkaart op. "
                     if not listed else ". Bekijk hun actuele tariefkaart op hun website. ")
                  + "Om prijzen te vergelijken, gebruik je de officiële vergelijker van je gewest, zoals de V-test in Vlaanderen.")
    return {"answer": answer, "sources": [], "live_source_status": "not-needed", "processing": "unknown-supplier"}
