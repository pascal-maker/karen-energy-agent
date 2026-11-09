"""Safety answers decided in code: outages and gas smells, the network operator, the social tariff,
winter disconnection and a visitor who is scared.

The swarm audit of 5175ecf (experiments/karen_swarm/REPORT-full-5175ecf.md) found the model giving
Brussels emergency numbers to someone in Namur, Walloon budget-meter rules to someone in Antwerp,
"the social tariff depends on your region" and "112" to a parent afraid of a disconnection. These
questions are answered here, before any other route, from facts checked on official pages
(experiments/karen_swarm/pr1-safety-facts.md). The region comes from what the visitor wrote, never
from Karen's earlier answers. A fact that could not be checked for a region is not given for it:
Karen says where to find it instead, and never gives the number of another network operator.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from .market_prices import question_language

FLANDERS, BRUSSELS, WALLONIA = "flanders", "brussels", "wallonia"


def _fold(text: str) -> str:
    """Lower case without accents, so 'Liège', 'liege' and 'LIÈGE' match alike ('sœur' becomes 'soeur')."""
    text = text.casefold().replace("\u0153", "oe").replace("\u00e6", "ae")
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


# Municipalities by region. Names that are also common words are left out ("ans", "spa", "boom",
# "geel", "bergen", "vorst" = frost, "forest", "laken", "lier", "namen", "luik", "menen", "tienen",
# "jette", "waver"), so "hoeveel namen staan op mijn factuur" never reads as a town. A town only
# gives a region: which Walloon operator (ORES or RESA) serves it is not on an official page we
# could read, so Karen never picks one from the town.
_BRUSSELS_TOWNS = (
    "brussel", "bruxelles", "brussels", "anderlecht", "oudergem", "auderghem", "sint-agatha-berchem",
    "berchem-sainte-agathe", "etterbeek", "evere", "ganshoren", "elsene", "ixelles",
    "koekelberg", "sint-jans-molenbeek", "molenbeek", "sint-gillis", "saint-gilles", "sint-joost-ten-node",
    "saint-josse", "schaarbeek", "schaerbeek", "ukkel", "uccle", "watermaal-bosvoorde", "watermael-boitsfort",
    "sint-lambrechts-woluwe", "woluwe-saint-lambert", "sint-pieters-woluwe", "woluwe-saint-pierre", "laeken",
)
_FLEMISH_TOWNS = (
    "antwerpen", "anvers", "antwerp", "gent", "gand", "ghent", "brugge", "bruges", "leuven", "louvain", "mechelen", "malines",
    "aalst", "alost", "hasselt", "kortrijk", "courtrai", "oostende", "ostende", "genk", "sint-niklaas", "roeselare",
    "turnhout", "dendermonde", "beveren", "vilvoorde", "herentals", "lokeren", "ninove",
    "zaventem", "tongeren", "ieper", "waregem", "lommel", "deinze", "eeklo", "oudenaarde",
    "knokke", "heist-op-den-berg", "brasschaat", "schoten", "mortsel", "wevelgem", "torhout", "diest",
    "aarschot", "zottegem", "geraardsbergen", "ronse", "maaseik", "bilzen", "sint-truiden", "beringen",
    "oud-heverlee", "kessel-lo", "heverlee", "merelbeke", "wetteren", "temse", "kapellen", "edegem",
)
_WALLOON_TOWNS = (
    "namur", "charleroi", "mons", "tournai", "doornik", "arlon", "aarlen", "wavre", "louvain-la-neuve",
    "ottignies", "verviers", "mouscron", "moeskroen", "eupen", "frameries", "liege", "seraing", "herstal",
    "la louviere", "nivelles", "nijvel", "huy", "hoei", "dinant", "marche-en-famenne", "bastogne", "malmedy",
    "gembloux", "waterloo", "braine-l'alleud", "jodoigne", "binche",
)
_REGION_WORDS = (
    (FLANDERS, r"vlaanderen|flandre|flanders|vlaams|vlaamse"),
    (BRUSSELS, r"brussel|bruxelles|brussels|brusselse"),
    (WALLONIA, r"wallonie|wallonië|wallonia|waals|waalse|wallon|wallonne"),
)
_NEGATION = re.compile(r"\b(?:niet|geen|pas|not|plus)\s+(?:in\s+|en\s+|a\s+|à\s+)?$")
# Whose home a place is: the visitor's ("ik woon in", "j'habite à", "verhuis ... naar") or someone
# else's ("mijn zus woont in", "ma soeur habite à"), read from the words just before the place.
_SELF_HOME = re.compile(
    r"(?:\bik woon\w*|\bwij wonen|\bwe wonen|\bj.habite|\bnous habitons|\bje vis\b|\bi live|\bwe live|\bmijn adres|\bchez moi|"
    r"\bin (?:mijn|ons|onze) (?:huis|woning|appartement|flat|studio|kot)\b|\bdans mon (?:appartement|logement)\b|\bin my (?:house|home|flat)\b|"
    r"\b(?:ik ben|we zijn|wij zijn|ik)\s+(?:\w+\s+){0,2}verhuisd\b.{0,25}\bnaar|\b(?:j.ai|nous avons|je suis|nous sommes)\s+demenage\w*.{0,25}\b(?:a|vers)|"
    r"\b(?:i|we) (?:have |just )?moved to)\W+(?:\w+\W+){0,6}$"
)
# A move still to come ("ik verhuis volgende week naar Brussel"): the destination, not yet the home.
_MOVING_TO = re.compile(
    r"(?:\bik verhuis\b|\bwe verhuizen\b|\bwij verhuizen\b|\bik ga verhuizen|\bwe gaan verhuizen|\bje demenage\b|\bnous demenageons|"
    r"\bje vais demenager|\bi.?m moving|\bwe.?re moving|\b(?:i|we) will move)"
    r".{0,30}\b(?:naar|a|vers|to)\W+(?:\w+\W+){0,2}$"
)
# Someone else's home: "mijn tante woont in", "ma soeur habite à", "my aunt lives in", "bij mijn zus in".
_OTHER_HOME = re.compile(
    r"\b(?:mijn|onze|ma|mon|mes|my|our)\s+\w+(?:\s+\w+)?\s+(?:is |a |has )?(?:woont|wonen|habite\w*|vit|vivent|lives?|"
    r"verhuisd|demenage\w*|moved)\b.{0,15}\W+(?:\w+\W+){0,3}$"
)
# Where something happens, at someone else's: "bij mijn zus in Brussel", "chez ma soeur à Namur".
_OTHER_PLACE = re.compile(r"\b(?:bij|chez|at)\s+(?:mijn|onze|ma|mon|mes|my|our)\s+\w+\W+(?:\w+\W+){0,3}$")
_SOMEONE_ELSE = re.compile(r"\b(?:mijn|onze|ma|mon|mes|my|our)\s+(?!adres|factuur|facture|huis|maison|meter|compteur|contract|contrat)\w+")
# "Ik ben verhuisd", "ik woon daar niet meer": an earlier place no longer counts.
_MOVED = re.compile(
    r"\b(?:ik ben|we zijn|wij zijn)\s+(?:\w+\s+){0,2}verhuisd|\bik woon (?:er |daar |hier )?niet meer|\b(?:j.ai|nous avons) demenage|"
    r"\bje n.habite plus|\b(?:i|we) (?:have |just )?moved\b|\bi no longer live"
)
# The visitor stating their own operator ("chez ORES", "bij RESA", "mon gestionnaire est ORES").
_STATED_OPERATOR = re.compile(r"(?:chez|bij|klant (?:bij|van)|client\w* (?:de|chez)|gestionnaire (?:est|c.est)|netbeheerder is|est|is)\s+(ores|resa)\b(?!\s*(?:ou|of|or)\b)")
_DENIED = re.compile(r"\b(?:niet|geen|pas|not|plus|jamais|nooit|never)\b")


# Town names that are also words ("luik" = hatch, "geel" = yellow, "boom" = tree): a town only after a
# preposition of place ("in Luik", "naar Geel", "à Spa").
_PLACE_ONLY_TOWNS = {
    "luik": WALLONIA, "namen": WALLONIA, "waver": WALLONIA, "bergen": WALLONIA, "spa": WALLONIA, "ans": WALLONIA,
    "ath": WALLONIA, "menen": FLANDERS, "tienen": FLANDERS, "geel": FLANDERS, "lier": FLANDERS, "halle": FLANDERS,
    "mol": FLANDERS, "boom": FLANDERS, "vorst": BRUSSELS, "forest": BRUSSELS, "jette": BRUSSELS, "laken": BRUSSELS,
}
_PLACE_PREPOSITION = r"\b(?:in|te|naar|uit|van|a|vers|to|from)\s+"
_PLACE_ONLY_PATTERNS = [
    (re.compile(_PLACE_PREPOSITION + r"(" + town + r")(?![\w-])"), region) for town, region in _PLACE_ONLY_TOWNS.items()
]
# A capitalised place Karen doesn't know ("ik woon in Geel" is known, "in Kontich" is not): it still
# counts as a place, of unknown region, so Karen never falls back to another region's number.
_UNKNOWN_PLACE = re.compile(r"\b(?:in|te|naar|uit|à|vers|to|from)\s+([A-ZÀ-Ý][\w'’-]{2,})")
_NOT_A_PLACE = {
    "belgie", "belgique", "belgium", "europa", "europe", "luminus", "engie", "eneco", "totalenergies", "mega",
    "octa", "bolt", "ecopower", "fluvius", "sibelga", "ores", "resa", "karen", "english", "nederlands", "frans",
    "francais", "januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus", "september",
    "oktober", "november", "december", "janvier", "fevrier", "mars", "avril", "juin", "juillet", "aout",
    "septembre", "octobre", "novembre", "decembre", "brugel", "cwape", "vreg", "creg", "ocmw", "cpas",
}
UNKNOWN = "unknown"
_KNOWN_PLACE_WORDS = {
    _fold(word) for word in (*_BRUSSELS_TOWNS, *_FLEMISH_TOWNS, *_WALLOON_TOWNS, *_PLACE_ONLY_TOWNS,
                             "vlaanderen", "flandre", "flanders", "brussel", "bruxelles", "brussels",
                             "wallonie", "wallonië", "wallonia")
}


def _town_pattern(town: str) -> re.Pattern[str]:
    return re.compile(r"(?<![\w-])" + re.escape(_fold(town)) + r"(?![\w-])")


_TOWN_PATTERNS = (
    [(_town_pattern(t), BRUSSELS) for t in _BRUSSELS_TOWNS]
    + [(_town_pattern(t), FLANDERS) for t in _FLEMISH_TOWNS]
    + [(_town_pattern(t), WALLONIA) for t in _WALLOON_TOWNS]
)
_REGION_PATTERNS = [(region, re.compile(r"(?<!\w)(?:" + _fold(words) + r")(?!\w)")) for region, words in _REGION_WORDS]


_TO_WORD = re.compile(r"\W*(?:naar|vers|a|to)\W*")
_FROM_TO_PAST = re.compile(r"\b(?:ik ben|we zijn|wij zijn)\s+(?:\w+\s+){0,2}verhuisd van\b|\b(?:j.ai|nous avons) demenage de\b|\b(?:i|we) moved from\b")
_FROM_TO_FUTURE = re.compile(r"\b(?:ik verhuis|we verhuizen|wij verhuizen|ik ga verhuizen)(?:\s+\w+){0,3}\s+van\b|\b(?:je demenage|nous demenageons)\s+de\b|\b(?:i.?m|we.?re) moving from\b")


def _mentions(text: str) -> list[tuple[str, str]]:
    """(region, whose) for every place named in one message, skipping 'niet in Wallonië'.

    whose is 'self' (the visitor's home), 'moving' (a move still to come), 'other' (someone else's
    home), 'there' (where something happens at someone else's) or 'neutral'. A marker such as
    "ik woon" only belongs to the first place after it, within the same sentence: in "ik woon in
    Gent. Mijn zus woont in Brussel" Brussel is the sister's, not a second home.
    """
    folded = _fold(text)
    matches = [
        (match.start(), match.end(), region)
        for pattern, region in [*_TOWN_PATTERNS, *((p, r) for r, p in _REGION_PATTERNS)]
        for match in pattern.finditer(folded)
    ]
    matches += [(m.start(1), m.end(1), region) for pattern, region in _PLACE_ONLY_PATTERNS for m in pattern.finditer(folded)]
    for m in _UNKNOWN_PLACE.finditer(text):
        word = _fold(m.group(1))
        if word in _NOT_A_PLACE or word in _KNOWN_PLACE_WORDS or any(word.startswith(k + "-") for k in _KNOWN_PLACE_WORDS):
            continue
        start = folded.find(word)
        matches.append((max(start, 0), max(start, 0) + len(word), UNKNOWN))
    matches.sort()
    found = []
    previous_end = 0
    for start, end, region in matches:
        if start < previous_end:
            continue  # "brussel" inside "brusselse", or a region word inside a town name
        sentence_start = max(folded.rfind(mark, 0, start) for mark in ".!?;\n") + 1
        before = folded[max(previous_end, sentence_start, start - 60):start]
        previous_end = end
        if _NEGATION.search(before[-12:]):
            continue
        whose = ("self" if _SELF_HOME.search(before) else "moving" if _MOVING_TO.search(before)
                 else "other" if _OTHER_HOME.search(before) else "there" if _OTHER_PLACE.search(before) else "neutral")
        # "Ik verhuis (volgende maand) van Gent naar Brussel": Gent is still today's home.
        if whose == "neutral" and re.search(_FROM_TO_FUTURE.pattern + r"\W*$", before):
            whose = "self"
        # "Ik verhuis van Brussel naar Antwerpen": the "naar" place is where the visitor moves to.
        if whose == "neutral" and _TO_WORD.fullmatch(before):
            sentence_before = folded[sentence_start:start]
            if _FROM_TO_PAST.search(sentence_before):
                whose = "self"
            elif _FROM_TO_FUTURE.search(sentence_before):
                whose = "moving"
        found.append((region, whose))
    return found


def _one_region(mentions: list[tuple[str, str]]) -> str | None:
    regions = {region for region, _ in mentions}
    if UNKNOWN in regions:
        return None  # a place Karen doesn't know: ask, never guess
    return regions.pop() if len(regions) == 1 else None


def visitor_region(question: str, earlier_questions: list[str], emergency: bool = False) -> tuple[str | None, str | None]:
    """The visitor's region and, in Wallonia, the operator they named themselves.

    Only the visitor's own words count, never Karen's answers. Within a message the visitor's own
    home wins ('mijn zus woont in Namur, ik woon in Luik'); a place that is someone else's home only
    counts in the current question when nothing else is named (the question is then about them),
    never from history. Several regions without a home, or a move to a town Karen doesn't know,
    give None: Karen asks rather than use another region's numbers.
    """
    region = None
    for index, text in enumerate([question, *reversed(earlier_questions)]):
        mentions = _mentions(text)
        own = [m for m in mentions if m[1] == "self"]
        moving = [m for m in mentions if m[1] == "moving"]
        neutral = [m for m in mentions if m[1] in ("neutral", "there")]
        others = [m for m in mentions if m[1] == "other"]
        # In an emergency a move still to come doesn't count: the gas smell is at today's home.
        # Someone else's home only counts for a non-emergency question about them; for a gas smell or
        # outage without "bij mijn zus in …", the visitor is at home, or Karen asks.
        # From earlier messages an emergency only takes a stated home ("ik woon in Gent"), never a place
        # merely named there ("ik werk in Brussel", "mijn zus heeft een huis in Brussel").
        if emergency and index:
            chosen = own
        else:
            # Not an emergency ("wie is mijn netbeheerder" with a move planned): the new home first.
            chosen = (own if emergency else moving or own) or neutral or (others if index == 0 and not emergency else [])
        if chosen:
            region = _one_region(chosen)
            break
        if _MOVED.search(_fold(text)):
            break
    operator = _stated_operator(question, earlier_questions) if region == WALLONIA else None
    return region, operator


def _stated_operator(question: str, earlier_questions: list[str]) -> str | None:
    """ORES or RESA as the visitor named it for their own home.

    Not when denied ("je ne suis pas chez ORES"), not someone else's ("mijn zus is bij ORES"), and
    not from before a move ("ik ben verhuisd naar Luik" ends what an older message said).
    """
    for text in [question, *reversed(earlier_questions)]:
        folded = _fold(text)
        found = set()
        for sentence in re.split(r"[.!?;\n]", folded):
            for match in _STATED_OPERATOR.finditer(sentence):
                before = sentence[:match.start()]
                if _DENIED.search(before[-25:]):
                    # "Ik ben niet (meer) bij ORES": whatever an older message said no longer holds.
                    return None
                if _SOMEONE_ELSE.search(before):
                    continue
                found.add(match.group(1).upper())
        if len(found) == 1:
            return found.pop()
        if _MOVED.search(folded):
            return None
    return None


def _operator(region: str | None, walloon_operator: str | None) -> str | None:
    """Fluvius serves Flanders, Sibelga Brussels; in Wallonia only the operator the visitor named."""
    if region == FLANDERS:
        return "Fluvius"
    if region == BRUSSELS:
        return "Sibelga"
    return walloon_operator


# Numbers read on the operator's own site (see pr1-safety-facts.md). RESA's site could not be read,
# so RESA has no numbers here: Karen points to the bill and resa.be rather than guess.
_EMERGENCY = {
    "Fluvius": {"power": "078 35 35 00", "gas": "0800 65 065", "site": "fluvius.be"},
    "Sibelga": {"power": "02 274 40 66", "gas": "0800 19 400", "site": "sibelga.be"},
    "ORES": {"power": "078 78 78 00", "gas": "0800 87 087", "site": "ores.be"},
    "RESA": {"site": "resa.be"},
}

# Solar panels, an inverter or a device that "gives no power" is a technical question, not an outage.
_NOT_OUTAGE = re.compile(
    r"zonnepane|omvormer|terug ?lever|injecte|opbrengst|panneaux? solaires?|onduleur|\bsolar|inverter|"
    r"chaudiere|\bketel|\bvoiture|\bauto\b|laadpaal|\bfrigo|koelkast|wasmachine|lave-linge|lave-vaisselle"
)
# An outage named as such (or a whole street without power) is always one; "geen stroom" or a bare
# "panne" next to solar panels, a boiler or a car is a technical question instead.
_GAS_SMELL = (
    r"(?:aard)?gas ?(?:geur|lucht|reuk|lek)|(?:ruik|ruikt|ruiken|rook)\W+(?:\w+\W+){0,3}(?:naar )?(?:aard)?gas\b|"
    r"odeur de gaz|fuite de gaz|sen[st]\W+(?:\w+\W+){0,2}(?:le |du )?gaz\b|(?:gas|gaz) (?:smell|leak)|smell\w*\W+(?:\w+\W+){0,2}(?:natural )?gas\b|"
    r"(?:komt|ontsnapt|lekt|stroomt|sist)\W+(?:\w+\W+){0,2}(?:aard)?gas\b|\b(?:aard)?gas\W+(?:\w+\W+){0,2}(?:komt|ontsnapt|lekt|sist)\b|"
    r"hiss\w*\W+(?:\w+\W+){0,3}gas\b|\bgas\W+(?:\w+\W+){0,2}hiss|gaz qui siffle|sifflement de gaz|"
    r"gaz (?:s.echappe|qui fuit|fuit)|gas (?:is )?(?:escaping|leaking)"
)
_STRONG_OUTAGE = re.compile(
    r"stroom ?(?:storing|panne|onderbreking|uitval)|stroompanne|panne (?:de courant|d.electricite|electrique)|"
    r"coupure de courant|power (?:cut|outage)|black-?out|noodnummer|storingsnummer|numero d.urgence|"
    r"(?:numero|ligne) de depannage|depannage (?:d.|de |chez )?(?:ores|resa|sibelga|fluvius)|"
    r"emergency number|" + _GAS_SMELL
)
# "De hele straat" is only an outage together with "geen stroom", "panne", "zonder stroom"...
_STREET_OUTAGE = re.compile(
    r"(?:straat|buurt|wijk|buren|buurman|buurvrouw|quartier|rue|voisins?|street|neighbou?r\w*)\b.{0,40}(?:geen (?:stroom|elektriciteit)|"
    r"zonder (?:stroom|elektriciteit)|panne|storing|uitval|donker|sans (?:courant|electricite)|coupure|no (?:power|electricity)|outage|power cut)|"
    r"(?:geen (?:stroom|elektriciteit)|zonder (?:stroom|elektriciteit)|panne|storing|sans courant|no power|outage).{0,40}"
    r"(?:straat|buurt|wijk|buren|quartier|rue|voisins?|street|neighbou?r\w*)"
)
_WEAK_OUTAGE = re.compile(r"geen stroom|stroom (?:is )?weg|\bpanne\b|no (?:power|electricity)")
# Power gone, said in full: always an outage, solar panels or inverter mentioned or not.
_POWER_GONE = re.compile(
    r"(?:stroom|elektriciteit|electriciteit)\W+(?:\w+\W+){0,2}(?:uitgevallen|weggevallen|ligt eruit|ligt plat|valt uit|viel uit|is uit\b)|"
    r"geen (?:stroom|elektriciteit) meer|zonder (?:stroom|elektriciteit)\b|stroomuitval|"
    r"(?:courant|electricite) (?:est |a ete |vient d.etre )?(?:coupee?|tombee?)|panne de secteur|"
    r"power (?:failure|outage|is (?:off|out|down))|(?:lights|electricity) (?:went|are|is) out|no (?:mains )?(?:power|electricity)\b|"
    r"(?:plus|pas) (?:d.electricite|de courant)|(?:electricite|courant) (?:a )?(?:saute|coupe)|"
    r"(?:electricity|power) (?:went|is|has gone) (?:out|off|down)|lost (?:power|electricity)|no electricity"
)
_GAS = re.compile(_GAS_SMELL)
_OPERATOR_QUESTION = re.compile(
    r"(?:wie|welke?|wat) (?:is|zijn) (?:mijn|de|onze) netbeheerder|netbeheerder (?:is|van)|is (?:dat|het) (?:niet )?fluvius|"
    r"(?:quel|qui) est (?:mon|le|notre) gestionnaire|gestionnaire de reseau|c.est (?:ores|resa|sibelga)|"
    r"(?:who|which) is my (?:grid|network|distribution) operator"
)
_SOCIAL_TARIFF = re.compile(
    r"sociaal tarief|sociale tarief|tarif social|social tariff|sociale maximumprijs|"
    r"\bigo\b|inkomensgarantie|garantie de revenus|\bgrapa\b"
)
_WINTER = re.compile(
    r"winter ?(?:stop|periode|verbod)|afsluitverbod|treve hivernale|periode hivernale|"
    r"(?:afsluit|afgesloten|afsnijden|couper|coupe|coupure|cut off|disconnect).{0,40}(?:winter|hiver)|"
    r"(?:winter|hiver).{0,40}(?:afsluit|afgesloten|couper|coupe|coupure|cut off|disconnect)"
)
# Cutting off supply, never "een contract afsluiten" (taking out a contract).
_DISCONNECT = re.compile(
    r"(?:mag|mogen|kan|kunnen|gaan|zullen)\b.{0,40}\b(?:mij|me|ons|stroom|gas|verwarming|elektriciteit|levering)\b.{0,15}\b(?:afsluiten|afsnijden)|"
    r"(?:stroom|gas|elektriciteit|verwarming|levering) (?:wordt |worden )?afgesloten|afgesloten worden|"
    r"(?:peu(?:t|vent)|vont)[- ](?:ils|on)? ?(?:me |nous )?couper|can they cut|cut (?:me|us) off"
)
# Taking out a contract ("een vast contract afsluiten", "souscrire un contrat") is not a cut-off.
_CONTRACT_SIGN = re.compile(
    r"(?:contract|contrat|abonnement|lening|verzekering)\w*\W+(?:(?!mij\b|me\b|ons\b)\w+\W+){0,2}(?:af ?te ?sluiten|afsluiten|afgesloten|souscrire|signer)|"
    r"(?:afsluiten|souscrire|signer|take out)\W+(?:\w+\W+){0,3}(?:contract|contrat|abonnement)"
)
# The social-tariff answer covers who has a right and how it is granted; a comparison, a saving or
# "ik wil geen sociaal tarief" goes to the other routes.
_SOCIAL_ASK = re.compile(
    r"recht|krijg|kom ik|automatisch|vanzelf|aanvra|wie (?:heeft|krijgt|kan)|hoe (?:krijg|vraag|werkt)|bestaat|afgeschaft|"
    r"bellen|nummer|check|controleer|wat is|droit|automati|demander|qui (?:a|peut)|comment (?:obtenir|avoir|ca marche)|"
    r"existe|supprime|appeler|c.est quoi|entitled|eligib|apply|who (?:gets|can)|how do i get|still exist|what is|call|\bigo\b|inkomensgarantie"
)
_SOCIAL_NOT = re.compile(
    r"wil geen sociaal tarief|ne veux pas (?:de|le) tarif social|don.?t want|vergelijk|compar|bespaar|econom|\bsave\b|bereken|calcul|"
    r"hoeveel (?:kost|goedkoper|bespaar)|combien|how much|prijs|\bprix\b|\bprice|per kwh|tarief per|\bkost\b|\bcost"
)
# "Client protégé" / "beschermde afnemer" questions are about the social supplier, not the tariff itself.
_PROTECTED_CUSTOMER = re.compile(r"client protege|beschermde (?:klant|afnemer)|fournisseur social|sociale leverancier|protected customer")
_WINTER_FOLLOW_UP = re.compile(r"geldt dat|voor wie|iedereen|beschermd|pour qui|tout le monde|protege|for whom|everyone")
_PAYMENT_PLAN = re.compile(
    r"afbetal|in (?:stukjes|delen|schijven|termijnen) (?:te )?betalen|gespreid (?:te )?betalen|plan de paiement|"
    r"payer en plusieurs fois|etaler|payment plan|pay in (?:parts|instalments|installments)|"
    r"(?:kan|kunnen) (?:mijn |de |onze )?(?:energie)?(?:factuur|rekening|afrekening)? ?niet (?:meer )?betalen|"
    r"ne (?:peux|pouvons) (?:pas|plus) payer|(?:can.?t|cannot|can not|unable to) pay|no money left|"
    r"can.?t afford (?:my|the|our) (?:energy |electricity |gas )?(?:bill|invoice)"
)
# "Who helps me?" after money trouble: the same help as a payment plan.
_HELP_REQUEST = re.compile(r"wie (?:kan |helpt |zal )?(?:mij|me) (?:dan )?(?:helpen|help)|wie helpt (?:mij|me)|qui peut m.aider|who can help")
_ABOLISHED = re.compile(r"afgeschaft|bestaat (?:niet meer|nog)|supprime|aboli|abolished|no longer exists")
_INCREASED_ALLOWANCE = re.compile(r"verhoogde tegemoetkoming|\bvt\b|\bbim\b|intervention majoree|increased (?:allowance|reimbursement)")
_SELF_HARM = re.compile(
    r"zelfmoord|suicida|wil niet meer leven|mezelf iets aandoen|suicid|me tuer|en finir avec (?:la|ma) vie|"
    r"kill myself|end my life|want to die"
)
_CHILDREN = re.compile(r"kinderen (?:afpakken|afnemen|wegnemen|weghalen)|enfants? (?:retire|place|enleve|pris)|take (?:my|the) (?:kids|children)")
_FEAR = re.compile(r"\bbang\b|\bangst|j.ai (?:tres |vraiment |si )?peur|\bpeur\b|\bscared\b|\bafraid\b")
_SHAME = re.compile(r"\bschaam|\bhonte\b|\bashamed\b")
# Fear only counts as distress about money: "bang dat de prijzen stijgen" is a price question.
_MONEY_TROUBLE = re.compile(
    r"schuld|niet (?:meer )?betalen|kan .{0,20}niet betalen|betaalachterstand|achterstand|herinnering|"
    r"aanmaning|deurwaarder|ocmw|niet rond|geen geld|dette|pas payer|plus payer|retard de paiement|impaye|huissier|cpas|"
    r"debt|arrears|can.?t pay|cannot pay|no money|geld is op|geld op is|budgetmeter|prepaid|afsluitverbod|winterstop"
)


def _lang(question: str) -> str:
    return question_language(question)


def _vous(question: str) -> bool:
    return bool(re.search(r"\b(?:vous|votre|vos)\b", _fold(question)))


def _reply(answer: str, kind: str, sources: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {"answer": answer, "sources": sources or [], "live_source_status": "not-needed",
            "processing": f"safety-{kind}"}


_SOCIAL_SOURCES = [
    {"title": "Sociaal tarief voor energie — Vlaamse overheid", "detail": "Officiële pagina",
     "url": "https://www.vlaanderen.be/sociaal-tarief-voor-energie-elektriciteit-aardgas-warmte"},
    {"title": "Hulp bij je energiekosten — Sociale zekerheid", "detail": "Officiële pagina",
     "url": "https://www.socialsecurity.be/citizen/nl/sociale-bijstand-en-ocmw/hulp-bij-je-energiekosten"},
]
_WINTER_SOURCES_FLANDERS = [
    {"title": "Kan ik altijd worden afgesloten van elektriciteit en/of aardgas? — VREG", "detail": "Officiële pagina",
     "url": "https://www.vreg.be/nl/veelgestelde-vragen/kan-ik-altijd-worden-afgesloten-van-elektriciteit-enof-aardgas"},
]


def _ask_region(lang: str, vous: bool, about: str) -> str:
    """Ask for the region in the visitor's language, naming what depends on it."""
    if lang == "fr":
        return (f"Pour {about}, cela dépend de votre région. Vous habitez en Flandre, à Bruxelles ou en Wallonie ? "
                "Le nom de votre commune suffit." if vous else
                f"Pour {about}, ça dépend de ta région. Tu habites en Flandre, à Bruxelles ou en Wallonie ? "
                "Le nom de ta commune suffit.")
    if lang == "en":
        return (f"For {about} it depends on your region. Do you live in Flanders, Brussels or Wallonia? "
                "The name of your town is enough.")
    return (f"Voor {about} hangt het af van je gewest. Woon je in Vlaanderen, Brussel of Wallonië? "
            "De naam van je gemeente is genoeg.")


def _gas_steps(lang: str, vous: bool) -> str:
    """Sibelga's own advice (energuide.be): open doors and windows, no light, leave, call from outside."""
    return {
        "nl": "Ruik je gas? Open ramen en deuren, doe geen licht aan, verlaat de woning en bel pas als je buiten bent.",
        "fr": ("Vous sentez le gaz ? Ouvrez portes et fenêtres, n'allumez pas la lumière, quittez le logement et appelez une fois dehors."
               if vous else
               "Tu sens le gaz ? Ouvre portes et fenêtres, n'allume pas la lumière, quitte le logement et appelle une fois dehors."),
        "en": "Smell gas? Open doors and windows, don't switch on a light, leave the house and call once you're outside.",
    }[lang]


def _regions_named(texts: list[str]) -> list[str]:
    """Every region the visitor's messages name (towns or region words, any person), in a fixed order.

    A place Karen doesn't know counts as all three: she can't rule any region out.
    """
    named = {region for text in texts for region, _ in _mentions(text)}
    if UNKNOWN in named:
        return [FLANDERS, BRUSSELS, WALLONIA]
    return [region for region in (FLANDERS, BRUSSELS, WALLONIA) if region in named]


def _emergency_reply(question: str, earlier: list[str], folded: str, money_trouble: bool) -> dict[str, Any]:
    """Safety net: when the conversation names more than one region, or a place Karen doesn't know, she
    never picks one operator's number. She gives the safety steps, asks where it is, and lists each
    region's number labelled, so the visitor picks the place and nobody gets a wrong one."""
    named = _regions_named([question, *earlier])
    if len(named) > 1:
        return _outage_answer_for_each(question, named, money_trouble)
    scene = _emergency_scene(question)
    if scene is not None:
        region, walloon_operator = scene
    else:
        region, walloon_operator = visitor_region(question, earlier, emergency=True)
    return _outage_answer(question, region, walloon_operator, money_trouble)


_REGION_NAMES = {
    "nl": {FLANDERS: "Vlaanderen", BRUSSELS: "Brussel", WALLONIA: "Wallonië"},
    "fr": {FLANDERS: "en Flandre", BRUSSELS: "à Bruxelles", WALLONIA: "en Wallonie"},
    "en": {FLANDERS: "Flanders", BRUSSELS: "Brussels", WALLONIA: "Wallonia"},
}


def _outage_answer_for_each(question: str, regions: list[str], money_trouble: bool) -> dict[str, Any]:
    """Several regions in the conversation: the steps, the question where, and each region's number labelled."""
    lang, vous = _lang(question), _vous(question)
    gas = bool(_GAS.search(_fold(question)))
    kind = "gas" if gas else "power"
    what = {"nl": "gasgeur" if gas else "stroomstoring", "fr": "odeur de gaz" if gas else "panne",
            "en": "gas smell" if gas else "power cut"}[lang]
    danger = _gas_steps(lang, vous) if gas else ""
    ask = {
        "nl": "Waar is het precies? Bel de netbeheerder van die plek:",
        "fr": f"Où est-ce exactement ? {'Appelez' if vous else 'Appelle'} le gestionnaire de réseau de cet endroit :",
        "en": "Where exactly is it? Call the network operator for that place:",
    }[lang]
    lines = []
    for region in regions:
        label = _REGION_NAMES[lang][region]
        if region == WALLONIA:
            ores = _EMERGENCY["ORES"][kind]
            text = {
                "nl": f"netbeheerder op de factuur; staat er ORES: {what} {ores}",
                "fr": f"gestionnaire indiqué sur la facture ; si c'est ORES : {what} {ores}",
                "en": f"operator on the bill; if it says ORES: {what} {ores}",
            }[lang]
        else:
            operator = _operator(region, None)
            text = f"{operator}, {what} {_EMERGENCY[operator][kind]}"
        lines.append(f"- **{label[0].upper() + label[1:]}:** {text}")
    tail = {"nl": "Bij direct gevaar: 112.", "fr": "En cas de danger immédiat : 112.", "en": "In immediate danger: 112."}[lang]
    head = " ".join(part for part in (danger, ask) if part)
    return _reply("\n".join([head, *lines, tail]), "emergency")


def _outage_answer(question: str, region: str | None, walloon_operator: str | None,
                   money_trouble: bool = False) -> dict[str, Any]:
    lang, vous = _lang(question), _vous(question)
    gas = bool(_GAS.search(_fold(question)))
    operator = _operator(region, walloon_operator)
    numbers = _EMERGENCY.get(operator or "", {})
    kind = "gas" if gas else "power"
    danger = _gas_steps(lang, vous) if gas else ""

    what = {"nl": "gasgeur" if gas else "stroomstoring", "fr": "odeur de gaz" if gas else "panne",
            "en": "gas smell" if gas else "power cut"}[lang]
    if operator and kind in numbers:
        line = {
            "nl": f"Bel {operator}, je netbeheerder: {what} {numbers[kind]}.",
            "fr": f"{'Appelez' if vous else 'Appelle'} {operator}, {'votre' if vous else 'ton'} gestionnaire de réseau : {what} {numbers[kind]}.",
            "en": f"Call {operator}, your network operator: {what} {numbers[kind]}.",
        }[lang]
    elif operator:
        line = {
            "nl": f"Je netbeheerder is {operator}. Het noodnummer staat op je factuur en op {numbers['site']}; ik heb het niet kunnen nakijken, dus ik geef het niet uit het hoofd.",
            "fr": f"{'Votre' if vous else 'Ton'} gestionnaire de réseau est {operator}. Le numéro d'urgence figure sur {'votre' if vous else 'ta'} facture et sur {numbers['site']} ; je n'ai pas pu le vérifier, donc je ne le donne pas de mémoire.",
            "en": f"Your network operator is {operator}. The emergency number is on your bill and on {numbers['site']}; I couldn't verify it, so I won't guess it.",
        }[lang]
    elif region == WALLONIA:
        ores = _EMERGENCY["ORES"][kind]
        line = {
            "nl": f"Je netbeheerder staat op je factuur. Staat er ORES: {what} {ores}. Staat er een andere netbeheerder (zoals RESA): het nummer staat op je factuur en op diens website.",
            "fr": f"{'Votre' if vous else 'Ton'} gestionnaire de réseau est indiqué sur {'votre' if vous else 'ta'} facture. Si c'est ORES : {what} {ores}. Si c'est un autre (comme RESA) : le numéro figure sur la facture et sur son site.",
            "en": f"Your network operator is on your bill. If it says ORES: {what} {ores}. If it's another one (such as RESA): the number is on your bill and on its website.",
        }[lang]
    else:
        line = _ask_region(lang, vous, {"nl": "het juiste noodnummer", "fr": "le bon numéro d'urgence",
                                        "en": "the right emergency number"}[lang])
    tail = {"nl": "Bij direct gevaar: 112.", "fr": "En cas de danger immédiat : 112.", "en": "In immediate danger: 112."}[lang]
    later = {
        "nl": "Over je schulden praten we daarna verder: eerst je veiligheid.",
        "fr": "Pour les dettes, on en reparle ensuite : d'abord la sécurité.",
        "en": "We can talk about the debts afterwards: safety first.",
    }[lang] if money_trouble else ""
    return _reply(" ".join(part for part in (danger, line, tail, later) if part), "emergency")


def _operator_answer(question: str, region: str | None, walloon_operator: str | None) -> dict[str, Any]:
    lang, vous = _lang(question), _vous(question)
    operator = _operator(region, walloon_operator)
    if operator:
        answer = {
            "nl": f"Je netbeheerder is {operator}.",
            "fr": f"{'Votre' if vous else 'Ton'} gestionnaire de réseau est {operator}.",
            "en": f"Your network operator is {operator}.",
        }[lang]
    elif region == WALLONIA:
        answer = {
            "nl": "In Wallonië staat je netbeheerder op je factuur (bijvoorbeeld ORES of RESA).",
            "fr": f"En Wallonie, ton gestionnaire de réseau est indiqué sur ta facture (par exemple ORES ou RESA)." if not vous else "En Wallonie, votre gestionnaire de réseau est indiqué sur votre facture (par exemple ORES ou RESA).",
            "en": "In Wallonia your network operator is on your bill (for example ORES or RESA).",
        }[lang]
    else:
        answer = _ask_region(lang, vous, {"nl": "je netbeheerder", "fr": "le gestionnaire de réseau",
                                          "en": "your network operator"}[lang])
    return _reply(answer, "operator")


def _social_tariff_answer(question: str) -> dict[str, Any]:
    lang, vous = _lang(question), _vous(question)
    folded = _fold(question)
    reply = _social_tariff_lines(question, lang, vous, folded)
    if _ABOLISHED.search(folded):
        reply[0] = {"nl": "Nee, het sociaal tarief bestaat nog. ", "fr": "Non, le tarif social existe toujours. ",
                    "en": "No, the social tariff still exists. "}[lang] + reply[0]
    if _INCREASED_ALLOWANCE.search(folded):
        reply.append({
            "nl": "- **Alleen een verhoogde tegemoetkoming?** Of die op zich nog recht geeft, heb ik niet officieel kunnen nakijken: vraag het na bij de FOD Economie (0800 120 33).",
            "fr": "- **Seulement l'intervention majorée ?** Je n'ai pas pu vérifier officiellement si elle suffit encore : renseignez-vous au SPF Économie (0800 120 33).",
            "en": "- **Only the increased reimbursement?** I couldn't verify officially whether that alone still qualifies: check with FPS Economy (0800 120 33).",
        }[lang])
    return _reply("\n".join(reply), "social-tariff", _SOCIAL_SOURCES)


def _social_tariff_lines(question: str, lang: str, vous: bool, folded: str) -> list[str]:
    if lang == "fr":
        tu = not vous
        lines = [
            "Le tarif social est une règle **fédérale**, la même dans toute la Belgique, et dans la plupart des cas il est appliqué **automatiquement** : "
            + ("tu n'as rien à demander." if tu else "vous n'avez rien à demander."),
            "- **Qui :** entre autres les ménages qui reçoivent le revenu d'intégration ou une aide du CPAS, ou la GRAPA (garantie de revenus aux personnes âgées).",
            "- **Quoi :** électricité, gaz naturel et chaleur.",
            "- **Pas sur la facture ?** Le SPF Économie transmet la liste aux fournisseurs chaque trimestre. S'il manque après quelques mois, "
            + ("appelle le SPF Économie au 0800 120 33 (gratuit) et préviens ton fournisseur." if tu else
               "appelez le SPF Économie au 0800 120 33 (gratuit) et prévenez votre fournisseur."),
        ]
    elif lang == "en":
        lines = [
            "The social tariff is a **federal** rule, the same across Belgium, and in most cases it's applied **automatically**: you don't need to ask.",
            "- **Who:** among others households on the leefloon / CPAS support, or the IGO (income guarantee for older people).",
            "- **What:** electricity, natural gas and heat.",
            "- **Not on your bill?** FPS Economy sends the list to suppliers every quarter. If it's still missing after a few months, call FPS Economy on 0800 120 33 (free) and tell your supplier.",
        ]
    else:
        first = "Het sociaal tarief is een **federale** regel, overal in België dezelfde, en meestal krijg je het **automatisch**: je hoeft niets aan te vragen."
        if re.search(r"\bigo\b|inkomensgarantie", folded):
            first = "Ja: met de IGO (inkomensgarantie voor ouderen) heb je recht op het sociaal tarief, en dat krijg je meestal **automatisch**: je hoeft niets aan te vragen."
        lines = [
            first,
            "- **Wie:** onder meer gezinnen met een leefloon of steun van het OCMW, of met de IGO (inkomensgarantie voor ouderen).",
            "- **Waarvoor:** elektriciteit, aardgas en warmte.",
            "- **Staat het niet op je factuur?** De FOD Economie geeft de lijst elk kwartaal door aan de leveranciers. Ontbreekt het na een paar maanden nog, bel dan gratis de FOD Economie op 0800 120 33 en meld het aan je leverancier.",
        ]
    return lines


def _winter_answer(question: str, region: str | None, disconnect: bool = False) -> dict[str, Any]:
    lang, vous = _lang(question), _vous(question)
    if region is None:
        about = ({"nl": "de regels over afsluiten", "fr": "les règles de coupure", "en": "the disconnection rules"}
                 if disconnect else
                 {"nl": "de regels over afsluiten in de winter", "fr": "les règles de coupure en hiver",
                  "en": "the winter disconnection rules"})[lang]
        return _reply(_ask_region(lang, vous, about), "winter")
    if region == FLANDERS and disconnect and lang == "nl":
        return _reply(
            "Niet zomaar: in Vlaanderen gaat aan een afsluiting een procedure vooraf, en tussen **1 december en 1 april** mag het, voor iedereen, alleen bij onveiligheid, leegstand, fraude of een verhuizing die niet in orde is.\n"
            "- **Nu doen:** vraag je leverancier om een afbetalingsplan, vóór de volgende herinnering.\n"
            "- **Hulp:** het OCMW van je gemeente kan je helpen met je factuur en schulden.\n"
            "- **Sociaal tarief:** kijk of je er recht op hebt via de FOD Economie, 0800 120 33 (gratis).",
            "winter", _WINTER_SOURCES_FLANDERS)
    if region == FLANDERS:
        text = {
            "nl": ("In Vlaanderen mag je stroom en aardgas tussen **1 december en 1 april** niet zomaar worden afgesloten, en dat geldt voor iedereen, niet alleen voor beschermde afnemers.\n"
                   "- **Uitzonderingen:** alleen bij onveiligheid, leegstand, fraude of een verhuizing die niet in orde is.\n"
                   "- **Al afgesloten vóór 1 december?** Dan word je in de winter niet automatisch weer aangesloten.\n"
                   "- **Hulp:** bij betaalproblemen helpt het OCMW van je gemeente, ook met een afbetalingsplan."),
            "fr": ("En Flandre, l'électricité et le gaz ne peuvent pas être coupés entre le **1er décembre et le 1er avril**, sauf en cas de danger, de logement inoccupé, de fraude ou de déménagement non réglé. "
                   + ("Le CPAS de votre commune peut vous aider." if vous else "Le CPAS de ta commune peut t'aider.")),
            "en": ("In Flanders, electricity and gas can't simply be cut off between **1 December and 1 April**, for everyone, except for danger, an empty home, fraud or a move that wasn't settled. "
                   "The OCMW of your town can help with payment problems."),
        }[lang]
        return _reply(text, "winter", _WINTER_SOURCES_FLANDERS)
    body = "Brugel" if region == BRUSSELS else "la CWaPE"
    text = {
        "nl": (f"De regels over afsluiten in de winter in {'Brussel' if region == BRUSSELS else 'Wallonië'} heb ik niet officieel kunnen nakijken, dus ik zeg er niets over dat ik niet zeker weet.\n"
               f"- **Navragen:** bij {'Brugel of Sibelga' if region == BRUSSELS else 'de CWaPE of je netbeheerder (ORES of RESA)'}.\n"
               "- **Hulp:** bij betaalproblemen helpt het OCMW van je gemeente."),
        "fr": ((f"{'À Bruxelles' if region == BRUSSELS else 'En Wallonie'}, je n'ai pas pu vérifier officiellement les règles de coupure en hiver, donc je préfère ne rien affirmer. "
                f"Renseignez-vous auprès de {body} et du CPAS de votre commune.") if vous else
               (f"{'À Bruxelles' if region == BRUSSELS else 'En Wallonie'}, je n'ai pas pu vérifier officiellement les règles de coupure en hiver, donc je préfère ne rien affirmer. "
                f"Renseigne-toi auprès de {body} et du CPAS de ta commune.")),
        "en": (f"For {'Brussels' if region == BRUSSELS else 'Wallonia'}, I couldn't verify the winter disconnection rules officially, so I won't state them. "
               f"Check with {'Brugel' if region == BRUSSELS else 'the CWaPE'} and the CPAS/OCMW of your town."),
    }[lang]
    return _reply(text, "winter")


def _distress_answer(question: str, region: str | None) -> dict[str, Any]:
    lang, vous = _lang(question), _vous(question)
    folded = _fold(question)
    if _SELF_HARM.search(folded):
        text = {
            "nl": "Wat je schrijft, klinkt heel zwaar, en je hoeft dit niet alleen te dragen. Praat er vandaag nog over: bel de Zelfmoordlijn op 1813 (gratis, anoniem, 24/7). Bij direct gevaar: 112. Over je energiefactuur helpen we daarna samen verder.",
            "fr": "Ce que vous écrivez semble très lourd, et vous n'avez pas à le porter seul·e. Parlez-en aujourd'hui : appelez le Centre de Prévention du Suicide au 0800 32 123 (gratuit, anonyme, 24h/24). En cas de danger immédiat : 112.",
            "en": "What you write sounds very heavy, and you don't have to carry it alone. Please talk to someone today: in Belgium call 1813 (Dutch) or 0800 32 123 (French), free, day and night. In immediate danger: 112.",
        }[lang]
        return _reply(text, "distress")
    if _SHAME.search(folded):
        text = {
            "nl": ("Schaamte is heel begrijpelijk, maar het OCMW is er precies voor zulke vragen, en je bent zeker niet de enige.\n"
                   "- **Eerst zonder OCMW:** vraag je leverancier zelf om een afbetalingsplan, en bel gratis de FOD Economie (0800 120 33) om te kijken of je recht hebt op het sociaal tarief.\n"
                   "- **Iemand om mee te praten:** het CAW, gratis en vertrouwelijk: 0800 13 500 (werkdagen 9 tot 17 uur) of caw.be."),
            "fr": (("La honte est compréhensible, mais le CPAS est là pour ce genre de questions, et vous n'êtes pas seul·e dans ce cas. "
                    "Sans passer par le CPAS, vous pouvez demander un plan de paiement à votre fournisseur et vérifier votre droit au tarif social au 0800 120 33 (SPF Économie, gratuit).")
                   if vous else
                   ("La honte est compréhensible, mais le CPAS est là pour ce genre de questions, et tu n'es pas seul·e dans ce cas. "
                    "Sans passer par le CPAS, tu peux demander un plan de paiement à ton fournisseur et vérifier ton droit au tarif social au 0800 120 33 (SPF Économie, gratuit).")),
            "en": ("Feeling ashamed is understandable, but the OCMW/CPAS is there for exactly these questions, and you're far from the only one. "
                   "Without it, you can ask your supplier for a payment plan and check the social tariff on 0800 120 33 (FPS Economy, free)."),
        }[lang]
        return _reply(text, "distress")
    if _CHILDREN.search(folded):
        text = {
            "nl": ("Ik begrijp dat je daar bang voor bent. Wat jeugdhulp beslist, kan ik niet beoordelen, maar je hoeft dit niet alleen op te lossen en hulp vragen is geen zwaktebod.\n"
                   "- **OCMW:** kan je helpen met schulden en je energiefactuur, ook met een afbetalingsplan.\n"
                   "- **CAW:** voor een gesprek over je zorgen, gratis en vertrouwelijk: 0800 13 500 (werkdagen 9 tot 17 uur) of caw.be.\n"
                   "- **Je energie:** vraag je leverancier om een afbetalingsplan en kijk of je recht hebt op het sociaal tarief."),
            "fr": ("Je comprends que cela vous fasse peur. Je ne peux pas juger ce que décide l'aide à la jeunesse, mais vous n'êtes pas seul·e : le CPAS peut aider pour les dettes et la facture d'énergie, y compris un plan de paiement."
                   if vous else
                   "Je comprends que ça te fasse peur. Je ne peux pas juger ce que décide l'aide à la jeunesse, mais tu n'es pas seul·e : le CPAS peut aider pour les dettes et la facture d'énergie, y compris un plan de paiement."),
            "en": ("I understand that frightens you. I can't judge what youth services decide, but you don't have to solve this alone: the OCMW/CPAS can help with debts and your energy bill, including a payment plan."),
        }[lang]
        return _reply(text, "distress")
    text = {
        "nl": ("Begrijpelijk dat je bang bent; je staat er niet alleen voor. Dit kan je deze week doen:\n"
               "1. Bel je leverancier en vraag een afbetalingsplan, vóór de volgende herinnering.\n"
               "2. Maak een afspraak bij het OCMW van je gemeente: het kan je helpen met je factuur en schulden.\n"
               "3. Kijk of je recht hebt op het sociaal tarief: bel gratis de FOD Economie op 0800 120 33."),
        "fr": (("Je comprends votre inquiétude ; vous n'êtes pas seul·e. Cette semaine : demandez un plan de paiement à votre fournisseur, prenez rendez-vous au CPAS de votre commune (aide pour la facture et les dettes) et vérifiez votre droit au tarif social au 0800 120 33 (SPF Économie, gratuit).")
               if vous else
               ("Je comprends ton inquiétude ; tu n'es pas seul·e. Cette semaine : demande un plan de paiement à ton fournisseur, prends rendez-vous au CPAS de ta commune (aide pour la facture et les dettes) et vérifie ton droit au tarif social au 0800 120 33 (SPF Économie, gratuit).")),
        "en": ("That's understandable; you're not alone in this. This week: ask your supplier for a payment plan, make an appointment at the OCMW/CPAS of your town (help with your bill and debts), "
               "and check whether you're entitled to the social tariff on 0800 120 33 (FPS Economy, free)."),
    }[lang]
    return _reply(text, "distress")


def _payment_plan_answer(question: str) -> dict[str, Any]:
    lang, vous = _lang(question), _vous(question)
    text = {
        "nl": ("Dat kan je vragen: een afbetalingsplan vraag je aan je leverancier. Het telefoonnummer staat op je factuur of de brief die je kreeg; vraag het liefst vóór de vervaldag.\n"
               "- **Lukt het niet of is het te veel:** het OCMW van je gemeente kan je helpen.\n"
               "- **Sociaal tarief:** kijk of je er recht op hebt via de FOD Economie, 0800 120 33 (gratis)."),
        "fr": (("Vous pouvez le demander : un plan de paiement se demande à votre fournisseur (le numéro est sur votre facture), de préférence avant l'échéance. "
                "Si c'est trop lourd, le CPAS de votre commune peut vous aider.") if vous else
               ("Tu peux le demander : un plan de paiement se demande à ton fournisseur (le numéro est sur ta facture), de préférence avant l'échéance. "
                "Si c'est trop lourd, le CPAS de ta commune peut t'aider.")),
        "en": ("You can ask for it: a payment plan is arranged with your supplier (the number is on your bill), ideally before the due date. "
               "If it's too much, the OCMW/CPAS of your town can help."),
    }[lang]
    return _reply(text, "payment-plan")


def _emergency_scene(question: str) -> tuple[str, str | None] | None:
    """(region, operator) of the place named in the sentence that reports the gas smell or outage.

    "Ik ruik gas in mijn huis in Brussel", "bij mijn zus in Liège, zij is bij RESA": that place is where
    the operator has to come, whoever lives there. Not "mijn zus woont in Brussel" (where she lives)
    and not the visitor's home named next to it. The operator is only one named in that same sentence
    (also someone else's: "zij is bij RESA"), never the visitor's own from elsewhere. None when the
    sentence names no place or several regions: then the visitor's home decides.
    """
    for sentence in re.split(r"[.!?;\n]", question):
        folded = _fold(sentence)
        if _is_emergency(folded, check_devices=False):
            mentions = _mentions(sentence)
            elsewhere = [m for m in mentions if m[1] == "there"]
            there = elsewhere or [m for m in mentions if m[1] == "neutral"]
            region = _one_region(there) if there else None
            if region:
                operator = _operator_in(folded, bool(elsewhere)) if region == WALLONIA else None
                return region, operator
    return None


_THIRD_PERSON = re.compile(r"\b(?:zij|hij|ze|elle|il|she|he|they)\b|\b(?:mijn|onze|ma|mon|mes|my|our)\s+\w+")
_FIRST_PERSON = re.compile(r"\b(?:ik|wij|we|je|j|nous|i)\b")


def _operator_in(folded_sentence: str, elsewhere: bool) -> str | None:
    """ORES or RESA named in the emergency sentence for the scene; None if denied, absent or both.

    At someone else's place ("bij mijn zus in Liège, zij is bij RESA") only a statement about them
    counts; at a plain place only one that isn't about someone else ("mijn zus is bij ORES" is hers).
    """
    found = set()
    for match in _STATED_OPERATOR.finditer(folded_sentence):
        clause = re.split(r",|\ben\b|\bet\b|\band\b|\bmaar\b|\bmais\b|\bbut\b", folded_sentence[:match.start()])[-1]
        if _DENIED.search(clause[-25:]):
            return None
        about_other = bool(_THIRD_PERSON.search(clause))
        if about_other != elsewhere and (about_other or _FIRST_PERSON.search(clause)):
            continue
        found.add(match.group(1).upper())
    return found.pop() if len(found) == 1 else None


# "Is een verzekering tegen gaslekken inbegrepen?", "wat kost een stroomonderbreking?": a question about
# leaks or outages in general. Only when the cost or insurance word comes right before the leak or
# outage words; "gas lekt uit mijn leiding, wat kost een interventie?" is still a report.
_ABOUT = re.compile(
    r"(?:verzeker\w*|assurance|insurance|inbegrepen|inclus\w*|included|kost(?:en|t)? (?:een|van)|cost of|coute (?:une|un)|"
    r"\bprijs van|\bprix d|price of|tegen|contre|against|wat als|stel dat|et si|what if)\W+(?:\w+\W+){0,2}$"
)


# A closed or sealed meter after a move ("de meter is afgesloten, ik zit zonder stroom") is not a grid
# outage: the outage number doesn't help; the other routes explain how to get it opened.
_CLOSED_METER = re.compile(
    r"meter\W+(?:\w+\W+){0,2}(?:afgesloten|dichtgezet|verzegeld|geblokkeerd)|afgesloten meter|"
    r"compteur\W+(?:\w+\W+){0,2}(?:ferme|scelle|plombe|coupe|bloque)|meter (?:was|is) (?:closed|sealed|locked)"
)


def _is_emergency(folded: str, check_devices: bool = True) -> bool:
    """A gas smell, a leak or an outage being reported (not asked about in general)."""
    if _CLOSED_METER.search(folded) and not (_GAS.search(folded) or _STRONG_OUTAGE.search(folded) or _STREET_OUTAGE.search(folded)):
        return False
    def reported(pattern: re.Pattern[str]) -> bool:
        return any(not _ABOUT.search(folded[max(0, m.start() - 40):m.start()]) for m in pattern.finditer(folded))
    if reported(_STRONG_OUTAGE) or reported(_STREET_OUTAGE) or reported(_POWER_GONE):
        return True
    return reported(_WEAK_OUTAGE) and not (check_devices and _NOT_OUTAGE.search(folded))


def _self_harm(folded: str) -> bool:
    """A self-harm signal, unless the visitor denies it ("je ne suis pas suicidaire", "niet suïcidaal")."""
    for match in _SELF_HARM.finditer(folded):
        if not re.search(r"\b(?:niet|geen|pas|not|nooit|never|jamais)\W+(?:\w+\W+){0,1}$", folded[max(0, match.start() - 20):match.start()]):
            return True
    return False


def answer_safety_question(question: str, history: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """A code-decided answer for a safety question, or None to let the other routes answer."""
    folded = _fold(question)
    earlier = [str(turn.get("question", "")) for turn in (history or []) if turn.get("question")]
    region, walloon_operator = visitor_region(question, earlier)
    recent = [folded, *(_fold(q) for q in earlier[-3:])]
    # Money trouble: the words above, or a real disconnection question (not a contract) in this or recent turns.
    money_trouble = any(
        _MONEY_TROUBLE.search(text) or ((_DISCONNECT.search(text) or _WINTER.search(text)) and not _CONTRACT_SIGN.search(text))
        for text in recent
    )
    if _self_harm(folded):
        distress = _distress_answer(question, region)
        if _is_emergency(folded):
            # Gas and "wil niet meer leven" in one message: the gas steps first, then the help line.
            emergency = _emergency_reply(question, earlier, folded, money_trouble=False)
            return _reply(emergency["answer"] + "\n\n" + distress["answer"], "emergency")
        return distress
    # A gas smell or an outage comes before everything else, also before fear or debts in the same message.
    if _is_emergency(folded):
        return _emergency_reply(question, earlier, folded, money_trouble)
    if _CHILDREN.search(folded):
        return _distress_answer(question, region)
    if (_FEAR.search(folded) or _SHAME.search(folded)) and money_trouble:
        return _distress_answer(question, region)
    signing = bool(_CONTRACT_SIGN.search(folded))
    if _WINTER.search(folded) and not signing:
        return _winter_answer(question, region)
    if earlier and _WINTER.search(_fold(earlier[-1])) and _WINTER_FOLLOW_UP.search(folded):
        return _winter_answer(question, region)
    if (_SOCIAL_TARIFF.search(folded) and _SOCIAL_ASK.search(folded) and not _SOCIAL_NOT.search(folded)
            and not _PROTECTED_CUSTOMER.search(folded)):
        return _social_tariff_answer(question)
    if _OPERATOR_QUESTION.search(folded) and not _PROTECTED_CUSTOMER.search(folded):
        return _operator_answer(question, region, walloon_operator)
    if _DISCONNECT.search(folded) and not signing:
        return _winter_answer(question, region, disconnect=True)
    plan = _PAYMENT_PLAN.search(folded)
    if plan:
        clause = re.split(r"[,;:]|\bmaar\b|\bmais\b|\bbut\b", folded[:plan.start()])[-1]
        denied = re.search(r"\b(?:niet|geen|pas|not|sans|zonder|without)\W+(?:\w+\W+){0,2}$", clause)
        # "kan mijn factuur niet betalen" carries its own "niet": that is the trouble, not a denial.
        if not denied or re.search(r"niet|pas|cannot|can.?t|unable", plan.group(0)):
            return _payment_plan_answer(question)
    if _HELP_REQUEST.search(folded) and money_trouble:
        return _payment_plan_answer(question)
    return None
