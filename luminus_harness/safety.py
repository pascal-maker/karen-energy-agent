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
    """Lower case without accents, so 'Liège', 'liege' and 'LIÈGE' match alike."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))


# Municipalities by region; names that are also common words ("ans", "spa", "boom", "geel", "bergen")
# are left out, so "depuis 3 ans" never reads as a town.
# Municipalities by region. Walloon ones carry their network operator when it is known; the others
# get None, and Karen then says the operator is on the bill instead of guessing.
_BRUSSELS_TOWNS = (
    "brussel", "bruxelles", "brussels", "anderlecht", "oudergem", "auderghem", "sint-agatha-berchem",
    "berchem-sainte-agathe", "etterbeek", "evere", "vorst", "forest", "ganshoren", "elsene", "ixelles", "jette",
    "koekelberg", "sint-jans-molenbeek", "molenbeek", "sint-gillis", "saint-gilles", "sint-joost-ten-node",
    "saint-josse", "schaarbeek", "schaerbeek", "ukkel", "uccle", "watermaal-bosvoorde", "watermael-boitsfort",
    "sint-lambrechts-woluwe", "woluwe-saint-lambert", "sint-pieters-woluwe", "woluwe-saint-pierre", "laken", "laeken",
)
_FLEMISH_TOWNS = (
    "antwerpen", "anvers", "gent", "gand", "brugge", "bruges", "leuven", "louvain", "mechelen", "malines",
    "aalst", "alost", "hasselt", "kortrijk", "courtrai", "oostende", "ostende", "genk", "sint-niklaas", "roeselare",
    "turnhout", "dendermonde", "lier", "beveren", "vilvoorde", "herentals", "lokeren", "ninove",
    "zaventem", "tienen", "tongeren", "ieper", "waregem", "lommel", "deinze", "eeklo", "oudenaarde",
    "knokke", "heist-op-den-berg", "brasschaat", "schoten", "mortsel", "wevelgem", "menen", "torhout", "diest",
    "aarschot", "zottegem", "geraardsbergen", "ronse", "maaseik", "bilzen", "sint-truiden", "beringen",
    "oud-heverlee", "kessel-lo", "heverlee", "merelbeke", "wetteren", "temse", "kapellen", "edegem",
)
_WALLOON_TOWNS = {
    "namur": "ORES", "namen": "ORES", "charleroi": "ORES", "mons": "ORES", "tournai": "ORES",
    "doornik": "ORES", "arlon": "ORES", "aarlen": "ORES", "wavre": "ORES", "waver": "ORES",
    "louvain-la-neuve": "ORES", "ottignies": "ORES", "verviers": "ORES", "mouscron": "ORES", "moeskroen": "ORES",
    "eupen": "ORES", "frameries": "ORES",
    "liege": "RESA", "luik": "RESA", "seraing": "RESA", "herstal": "RESA",
    "la louviere": None, "nivelles": None, "nijvel": None, "huy": None, "hoei": None, "dinant": None,
    "marche-en-famenne": None, "bastogne": None, "malmedy": None, "gembloux": None,
    "waterloo": None, "braine-l'alleud": None, "jodoigne": None, "binche": None,
}
_REGION_WORDS = (
    (FLANDERS, r"vlaanderen|flandre|flanders|vlaams|vlaamse"),
    (BRUSSELS, r"brussel|bruxelles|brussels|brusselse"),
    (WALLONIA, r"wallonie|wallonië|wallonia|waals|waalse|wallon|wallonne"),
)
_NEGATION = re.compile(r"\b(?:niet|geen|pas|not|plus)\s+(?:in\s+|en\s+|a\s+|à\s+)?$")


def _town_pattern(town: str) -> re.Pattern[str]:
    return re.compile(r"(?<![\w-])" + re.escape(_fold(town)) + r"(?![\w-])")


_TOWNS = (
    [(t, BRUSSELS, None) for t in _BRUSSELS_TOWNS]
    + [(t, FLANDERS, None) for t in _FLEMISH_TOWNS]
    + [(t, WALLONIA, op) for t, op in _WALLOON_TOWNS.items()]
)
_TOWN_PATTERNS = [(_town_pattern(t), region, op) for t, region, op in _TOWNS]
_REGION_PATTERNS = [(region, re.compile(r"(?<!\w)(?:" + _fold(words) + r")(?!\w)")) for region, words in _REGION_WORDS]


def _mentions(text: str) -> list[tuple[int, str, str | None]]:
    """(position, region, operator) for every town or region named in one message, skipping 'niet in Wallonië'."""
    folded = _fold(text)
    found = []
    for pattern, region, operator in _TOWN_PATTERNS:
        for match in pattern.finditer(folded):
            if not _NEGATION.search(folded[max(0, match.start() - 12):match.start()]):
                found.append((match.start(), region, operator))
    for region, pattern in _REGION_PATTERNS:
        for match in pattern.finditer(folded):
            if not _NEGATION.search(folded[max(0, match.start() - 12):match.start()]):
                found.append((match.start(), region, None))
    return sorted(found)


def visitor_region(question: str, earlier_questions: list[str]) -> tuple[str | None, str | None]:
    """The visitor's region and, in Wallonia, operator: from this question, else the latest earlier one.

    Only the visitor's own words count, never Karen's answers. Within one message a town beats a
    region word ('ik woon in Antwerpen, niet Wallonië') and the first town named wins; a message
    without a town or region falls back to the latest earlier message that has one.
    """
    for text in [question, *reversed(earlier_questions)]:
        mentions = _mentions(text)
        if not mentions:
            continue
        towns = [m for m in mentions if _is_town_mention(text, m)]
        _, region, operator = (towns or mentions)[0]
        return region, operator
    return None, None


def _is_town_mention(text: str, mention: tuple[int, str, str | None]) -> bool:
    folded = _fold(text)
    return any(pattern.match(folded, mention[0]) for pattern, _, _ in _TOWN_PATTERNS)


def _operator(region: str | None, walloon_operator: str | None) -> str | None:
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

_OUTAGE = re.compile(
    r"stroom ?(?:storing|panne|onderbreking|uitval)|geen stroom|stroom (?:is )?(?:uitgevallen|weg)|"
    r"gas ?(?:geur|lek)|ruik(?:t|en)? gas|panne (?:de courant|d.electricite|electrique)|panne\b|coupure de courant|"
    r"odeur de gaz|fuite de gaz|sent le gaz|power (?:cut|outage)|no (?:power|electricity)|gas (?:smell|leak)|smell(?:s)? (?:of )?gas|"
    r"noodnummer|storingsnummer|numero d.urgence|depannage|emergency number|black-?out|stroompanne"
)
_GAS = re.compile(r"gas ?(?:geur|lek)|ruik(?:t|en)? gas|odeur de gaz|fuite de gaz|sent le gaz|gas (?:smell|leak)|smell(?:s)? (?:of )?gas")
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
_DISCONNECT = re.compile(
    r"(?:mag|kan|kunnen|mogen|gaan) (?:de leverancier|ze|zij|fluvius|mijn leverancier)?.{0,30}(?:afsluiten|afsnijden|uitzetten)|"
    r"afgesloten worden|(?:peu(?:t|vent)|vont)[- ](?:ils|on)? ?(?:me )?couper|can they cut"
)
# "Client protégé" / "beschermde afnemer" questions are about the social supplier, not the tariff itself.
_PROTECTED_CUSTOMER = re.compile(r"client protege|beschermde (?:klant|afnemer)|fournisseur social|sociale leverancier|protected customer")
_WINTER_FOLLOW_UP = re.compile(r"geldt dat|voor wie|iedereen|beschermd|pour qui|tout le monde|protege|for whom|everyone")
_PAYMENT_PLAN = re.compile(
    r"afbetal|in (?:stukjes|delen|schijven|termijnen) (?:te )?betalen|gespreid (?:te )?betalen|plan de paiement|"
    r"payer en plusieurs fois|etaler|payment plan|pay in (?:parts|instalments|installments)|"
    r"(?:kan|kunnen) (?:mijn |de |onze )?(?:energie)?(?:factuur|rekening|afrekening)? ?niet (?:meer )?betalen|"
    r"ne (?:peux|pouvons) (?:pas|plus) payer|can.?t (?:afford|pay)"
)
# "Who helps me?" after money trouble: the same help as a payment plan.
_HELP_REQUEST = re.compile(r"wie (?:kan |helpt |zal )?(?:mij|me) (?:dan )?(?:helpen|help)|wie helpt (?:mij|me)|qui peut m.aider|who can help")
_ABOLISHED = re.compile(r"afgeschaft|bestaat (?:niet meer|nog)|supprime|aboli|abolished|no longer exists")
_INCREASED_ALLOWANCE = re.compile(r"verhoogde tegemoetkoming|\bvt\b|\bbim\b|intervention majoree|increased (?:allowance|reimbursement)")
_SELF_HARM = re.compile(
    r"zelfmoord|wil niet meer leven|er(?:uit| een eind aan) maken|suicide|me suicider|en finir|kill myself|end my life"
)
_CHILDREN = re.compile(r"kinderen (?:afpakken|afnemen|wegnemen|weghalen)|enfants? (?:retire|place|enleve|pris)|take (?:my|the) (?:kids|children)")
_FEAR = re.compile(r"\bbang\b|\bangst|j.ai (?:tres |vraiment |si )?peur|\bpeur\b|\bscared\b|\bafraid\b")
_SHAME = re.compile(r"\bschaam|\bhonte\b|\bashamed\b")
# Fear only counts as distress about money: "bang dat de prijzen stijgen" is a price question.
_MONEY_TROUBLE = re.compile(
    r"schuld|betaal|betalen|achterstand|afsluit|afgesloten|herinnering|aanmaning|deurwaarder|ocmw|niet rond|"
    r"dette|payer|paie|retard|coupe|cpas|huissier|debt|pay|cut off|kinderen|enfant"
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


def _outage_answer(question: str, region: str | None, walloon_operator: str | None) -> dict[str, Any]:
    lang, vous = _lang(question), _vous(question)
    gas = bool(_GAS.search(_fold(question)))
    operator = _operator(region, walloon_operator)
    numbers = _EMERGENCY.get(operator or "", {})
    if gas:
        danger = {
            "nl": "Ruik je gas? Open ramen en deuren, gebruik geen schakelaars of vuur, verlaat de woning en bel van buitenaf.",
            "fr": ("Vous sentez le gaz ? Ouvrez portes et fenêtres, n'actionnez aucun interrupteur ni flamme, quittez le "
                   "logement et appelez depuis l'extérieur." if vous else
                   "Tu sens le gaz ? Ouvre portes et fenêtres, n'actionne aucun interrupteur ni flamme, quitte le "
                   "logement et appelle depuis l'extérieur."),
            "en": "Smell gas? Open windows and doors, don't use switches or flames, leave the house and call from outside.",
        }[lang]
    else:
        danger = ""
    kind = "gas" if gas else "power"
    if operator and kind in numbers:
        line = {
            "nl": f"Bel {operator}, je netbeheerder: {'gasgeur' if gas else 'stroomstoring'} {numbers[kind]} (24/7).",
            "fr": (f"Appelez {operator}, votre gestionnaire de réseau : {'odeur de gaz' if gas else 'panne'} {numbers[kind]} (24h/24)."
                   if vous else
                   f"Appelle {operator}, ton gestionnaire de réseau : {'odeur de gaz' if gas else 'panne'} {numbers[kind]} (24h/24)."),
            "en": f"Call {operator}, your network operator: {'gas smell' if gas else 'power cut'} {numbers[kind]} (24/7).",
        }[lang]
    elif operator:
        line = {
            "nl": f"Je netbeheerder is {operator}. Het noodnummer staat op je factuur en op {numbers['site']}; dat nummer heb ik niet nagekeken, dus ik geef het niet uit het hoofd.",
            "fr": (f"Votre gestionnaire de réseau est {operator}. Le numéro d'urgence figure sur votre facture et sur {numbers['site']} ; je ne l'ai pas vérifié, donc je ne le donne pas de mémoire."
                   if vous else
                   f"Ton gestionnaire de réseau est {operator}. Le numéro d'urgence figure sur ta facture et sur {numbers['site']} ; je ne l'ai pas vérifié, donc je ne le donne pas de mémoire."),
            "en": f"Your network operator is {operator}. The emergency number is on your bill and on {numbers['site']}; I haven't verified it, so I won't guess it.",
        }[lang]
    elif region == WALLONIA:
        line = {
            "nl": "In Wallonië is je netbeheerder ORES of RESA (vooral rond Luik). Welke het is, staat op je factuur; geef me je gemeente, dan zeg ik het als ik het weet.",
            "fr": ("En Wallonie, votre gestionnaire est ORES ou RESA (surtout autour de Liège) : c'est indiqué sur votre facture. Donnez-moi votre commune et je vous le dis si je le sais."
                   if vous else
                   "En Wallonie, ton gestionnaire est ORES ou RESA (surtout autour de Liège) : c'est indiqué sur ta facture. Donne-moi ta commune et je te le dis si je le sais."),
            "en": "In Wallonia your operator is ORES or RESA (mainly around Liège); it's on your bill. Tell me your town and I'll say which, if I know.",
        }[lang]
    else:
        line = _ask_region(lang, vous, {"nl": "het juiste noodnummer", "fr": "le bon numéro d'urgence",
                                        "en": "the right emergency number"}[lang])
    tail = {
        "nl": "Bel je leverancier hier niet voor: alleen de netbeheerder herstelt het net. Bij direct gevaar: 112.",
        "fr": "Ce n'est pas le fournisseur qu'il faut appeler : seul le gestionnaire de réseau répare. En cas de danger immédiat : 112.",
        "en": "Don't call your supplier for this: only the network operator repairs the grid. In immediate danger: 112.",
    }[lang]
    return _reply(" ".join(part for part in (danger, line, tail) if part), "emergency")


def _operator_answer(question: str, region: str | None, walloon_operator: str | None) -> dict[str, Any]:
    lang, vous = _lang(question), _vous(question)
    operator = _operator(region, walloon_operator)
    if operator:
        answer = {
            "nl": f"Je netbeheerder is {operator}: die beheert de kabels, leidingen en je meter, los van je leverancier.",
            "fr": (f"Votre gestionnaire de réseau est {operator} : il gère les câbles, conduites et votre compteur, indépendamment de votre fournisseur."
                   if vous else
                   f"Ton gestionnaire de réseau est {operator} : il gère les câbles, conduites et ton compteur, indépendamment de ton fournisseur."),
            "en": f"Your network operator is {operator}: it runs the cables, pipes and your meter, separate from your supplier.",
        }[lang]
    elif region == WALLONIA:
        answer = {
            "nl": "In Wallonië is dat ORES of RESA (vooral rond Luik); welke het is, staat op je factuur.",
            "fr": "En Wallonie, c'est ORES ou RESA (surtout autour de Liège) : c'est indiqué sur " + ("votre" if vous else "ta") + " facture.",
            "en": "In Wallonia it's ORES or RESA (mainly around Liège); it's on your bill.",
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
            "- **Hulp:** het OCMW van je gemeente helpt gratis met je factuur en schulden.\n"
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
        "nl": (f"Ook in {'Brussel' if region == BRUSSELS else 'Wallonië'} geldt in de winter een bescherming tegen afsluiten, maar de precieze periode en voor wie ze geldt heb ik niet nagekeken, dus ik geef geen datums.\n"
               f"- **Navragen:** bij {'Brugel of Sibelga' if region == BRUSSELS else 'de CWaPE of je netbeheerder (ORES of RESA)'}.\n"
               "- **Hulp:** bij betaalproblemen helpt het OCMW van je gemeente."),
        "fr": ((f"{'À Bruxelles' if region == BRUSSELS else 'En Wallonie'}, il existe aussi une protection contre les coupures en hiver, mais je n'ai pas vérifié la période exacte ni qui elle protège, donc je ne donne pas de dates. "
                f"Renseignez-vous auprès de {body} et du CPAS de votre commune.") if vous else
               (f"{'À Bruxelles' if region == BRUSSELS else 'En Wallonie'}, il existe aussi une protection contre les coupures en hiver, mais je n'ai pas vérifié la période exacte ni qui elle protège, donc je ne donne pas de dates. "
                f"Renseigne-toi auprès de {body} et du CPAS de ta commune.")),
        "en": (f"{'Brussels' if region == BRUSSELS else 'Wallonia'} also protects households from disconnection in winter, but I haven't verified the exact period or who it covers, so I won't give dates. "
               f"Check with {'Brugel' if region == BRUSSELS else 'the CWaPE'} and the CPAS/OCMW of your town."),
    }[lang]
    return _reply(text, "winter")


def _distress_answer(question: str, region: str | None) -> dict[str, Any]:
    lang, vous = _lang(question), _vous(question)
    folded = _fold(question)
    if _SELF_HARM.search(folded):
        text = {
            "nl": "Wat je schrijft, klinkt heel zwaar, en je hoeft dit niet alleen te dragen. Praat er vandaag nog over: bel de Zelfmoordlijn op 1813 (gratis, dag en nacht). Bij direct gevaar: 112. Over je energiefactuur helpen we daarna samen verder.",
            "fr": "Ce que vous écrivez semble très lourd, et vous n'avez pas à le porter seul·e. Parlez-en aujourd'hui : appelez le Centre de Prévention du Suicide au 0800 32 123 (gratuit, 24h/24). En cas de danger immédiat : 112.",
            "en": "What you write sounds very heavy, and you don't have to carry it alone. Please talk to someone today: in Belgium call 1813 (Dutch) or 0800 32 123 (French), free, day and night. In immediate danger: 112.",
        }[lang]
        return _reply(text, "distress")
    if _SHAME.search(folded):
        text = {
            "nl": ("Schaamte is heel begrijpelijk, maar het OCMW is er precies hiervoor: hulp vragen is je recht, en wat je vertelt blijft vertrouwelijk.\n"
                   "- **Eerst zonder OCMW:** vraag je leverancier zelf om een afbetalingsplan, en bel gratis de FOD Economie (0800 120 33) om te kijken of je recht hebt op het sociaal tarief.\n"
                   "- **Iemand om mee te praten:** het CAW helpt gratis en anoniem als je wil (caw.be).\n"
                   "- **Ga je toch naar het OCMW:** je mag iemand meenemen die je vertrouwt."),
            "fr": (("La honte est compréhensible, mais le CPAS est là pour ça : demander de l'aide est un droit, et c'est confidentiel. "
                    "Sans passer par le CPAS, vous pouvez demander un plan de paiement à votre fournisseur et vérifier votre droit au tarif social au 0800 120 33 (SPF Économie, gratuit).")
                   if vous else
                   ("La honte est compréhensible, mais le CPAS est là pour ça : demander de l'aide est un droit, et c'est confidentiel. "
                    "Sans passer par le CPAS, tu peux demander un plan de paiement à ton fournisseur et vérifier ton droit au tarif social au 0800 120 33 (SPF Économie, gratuit).")),
            "en": ("Feeling ashamed is understandable, but the OCMW/CPAS exists for exactly this: asking for help is your right, and it's confidential. "
                   "Without it, you can ask your supplier for a payment plan and check the social tariff on 0800 120 33 (FPS Economy, free)."),
        }[lang]
        return _reply(text, "distress")
    if _CHILDREN.search(folded):
        text = {
            "nl": ("Ik begrijp dat je daar bang voor bent. Wat jeugdhulp beslist, kan ik niet beoordelen, maar je hoeft dit niet alleen op te lossen en hulp vragen is geen zwaktebod.\n"
                   "- **OCMW:** helpt gratis en vertrouwelijk met schulden en je energiefactuur, ook met een afbetalingsplan.\n"
                   "- **CAW:** voor een gesprek over je zorgen, gratis en anoniem als je wil (caw.be).\n"
                   "- **Je energie:** vraag je leverancier om een afbetalingsplan en kijk of je recht hebt op het sociaal tarief."),
            "fr": ("Je comprends que cela vous fasse peur. Je ne peux pas juger ce que décide l'aide à la jeunesse, mais vous n'êtes pas seul·e : le CPAS aide gratuitement et en toute confidentialité pour les dettes et la facture d'énergie, y compris un plan de paiement."
                   if vous else
                   "Je comprends que ça te fasse peur. Je ne peux pas juger ce que décide l'aide à la jeunesse, mais tu n'es pas seul·e : le CPAS aide gratuitement et en toute confidentialité pour les dettes et la facture d'énergie, y compris un plan de paiement."),
            "en": ("I understand that frightens you. I can't judge what youth services decide, but you don't have to solve this alone: the OCMW/CPAS helps for free and confidentially with debts and your energy bill, including a payment plan."),
        }[lang]
        return _reply(text, "distress")
    text = {
        "nl": ("Begrijpelijk dat je bang bent; je staat er niet alleen voor. Dit kan je deze week doen:\n"
               "1. Bel je leverancier en vraag een afbetalingsplan, vóór de volgende herinnering.\n"
               "2. Maak een afspraak bij het OCMW van je gemeente: het helpt gratis met je factuur en schulden.\n"
               "3. Kijk of je recht hebt op het sociaal tarief: bel gratis de FOD Economie op 0800 120 33."),
        "fr": (("Je comprends votre inquiétude ; vous n'êtes pas seul·e. Cette semaine : demandez un plan de paiement à votre fournisseur, prenez rendez-vous au CPAS de votre commune (aide gratuite pour la facture et les dettes) et vérifiez votre droit au tarif social au 0800 120 33 (SPF Économie, gratuit).")
               if vous else
               ("Je comprends ton inquiétude ; tu n'es pas seul·e. Cette semaine : demande un plan de paiement à ton fournisseur, prends rendez-vous au CPAS de ta commune (aide gratuite pour la facture et les dettes) et vérifie ton droit au tarif social au 0800 120 33 (SPF Économie, gratuit).")),
        "en": ("That's understandable; you're not alone in this. This week: ask your supplier for a payment plan, make an appointment at the OCMW/CPAS of your town (free help with your bill and debts), "
               "and check whether you're entitled to the social tariff on 0800 120 33 (FPS Economy, free)."),
    }[lang]
    return _reply(text, "distress")


def _payment_plan_answer(question: str) -> dict[str, Any]:
    lang, vous = _lang(question), _vous(question)
    text = {
        "nl": ("Dat kan je vragen: een afbetalingsplan vraag je aan je leverancier. Het telefoonnummer staat op je factuur of de brief die je kreeg; vraag het liefst vóór de vervaldag.\n"
               "- **Lukt het niet of is het te veel:** het OCMW van je gemeente helpt gratis, ook met de leverancier praten.\n"
               "- **Sociaal tarief:** kijk of je er recht op hebt via de FOD Economie, 0800 120 33 (gratis)."),
        "fr": (("Vous pouvez le demander : un plan de paiement se demande à votre fournisseur (le numéro est sur votre facture), de préférence avant l'échéance. "
                "Si c'est trop lourd, le CPAS de votre commune aide gratuitement.") if vous else
               ("Tu peux le demander : un plan de paiement se demande à ton fournisseur (le numéro est sur ta facture), de préférence avant l'échéance. "
                "Si c'est trop lourd, le CPAS de ta commune aide gratuitement.")),
        "en": ("You can ask for it: a payment plan is arranged with your supplier (the number is on your bill), ideally before the due date. "
               "If it's too much, the OCMW/CPAS of your town helps for free."),
    }[lang]
    return _reply(text, "payment-plan")


def answer_safety_question(question: str, history: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """A code-decided answer for a safety question, or None to let the other routes answer."""
    folded = _fold(question)
    earlier = [str(turn.get("question", "")) for turn in (history or []) if turn.get("question")]
    region, walloon_operator = visitor_region(question, earlier)
    context = " ".join([folded, *(_fold(q) for q in earlier[-3:])])
    if _SELF_HARM.search(folded) or _CHILDREN.search(folded):
        return _distress_answer(question, region)
    if (_FEAR.search(folded) or _SHAME.search(folded)) and _MONEY_TROUBLE.search(context):
        return _distress_answer(question, region)
    if _OUTAGE.search(folded):
        return _outage_answer(question, region, walloon_operator)
    if _WINTER.search(folded):
        return _winter_answer(question, region)
    if earlier and _WINTER.search(_fold(earlier[-1])) and _WINTER_FOLLOW_UP.search(folded):
        return _winter_answer(question, region)
    if _SOCIAL_TARIFF.search(folded) and not _PROTECTED_CUSTOMER.search(folded):
        return _social_tariff_answer(question)
    if _OPERATOR_QUESTION.search(folded) and not _PROTECTED_CUSTOMER.search(folded):
        return _operator_answer(question, region, walloon_operator)
    if _DISCONNECT.search(folded):
        return _winter_answer(question, region, disconnect=True)
    if _PAYMENT_PLAN.search(folded):
        return _payment_plan_answer(question)
    if _HELP_REQUEST.search(folded) and _MONEY_TROUBLE.search(context):
        return _payment_plan_answer(question)
    return None
