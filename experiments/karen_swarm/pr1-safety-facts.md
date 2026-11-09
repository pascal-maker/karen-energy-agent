# PR 1: feiten voor veiligheid, met bron en betrouwbaarheid

Opgezocht op 2026-11-09. Regel: **niets gaat in Karen zonder bron.** Is iets alleen door een commerciële site
bevestigd (Selectra, CallMePower), dan noteren we het als "derde partij". Zo'n waarde gaat pas in Karen als
de eigenaar of een officiële pagina het bevestigt.

| Status | Betekenis |
|---|---|
| ✅ officieel | bevestigd op een officiële site (overheid, regulator of netbeheerder) |
| 🟡 derde partij | meerdere commerciële bronnen zijn het eens, maar de officiële pagina is niet gelezen |
| ❓ open | de bronnen spreken elkaar tegen, of er is geen actuele bron |

## Sociaal tarief (federaal)

| Feit | Status | Bron |
|---|---|---|
| Het sociaal tarief is **federaal** en wordt sinds 2009 meestal **automatisch** toegekend via de FOD Economie, die elk kwartaal de lijst van rechthebbenden aan de leveranciers bezorgt. | ✅ | [vlaanderen.be: sociaal tarief](https://www.vlaanderen.be/sociaal-tarief-voor-energie-elektriciteit-aardgas-warmte), [socialsecurity.be](https://www.socialsecurity.be/citizen/nl/sociale-bijstand-en-ocmw/hulp-bij-je-energiekosten) |
| **IGO** (inkomensgarantie voor ouderen, van de Federale Pensioendienst) geeft recht, en dat gebeurt automatisch. Staat het na een paar maanden niet op de factuur, meld het dan aan de leverancier en de FOD Economie. | ✅ | idem |
| Een attest (papier) is nodig als het niet automatisch gaat, en altijd bij **sociale huurwoningen** (categorie 4). | ✅ | idem |
| FOD Economie, sociaal tarief: **0800 120 33** (gratis). Online controleren kan via sociaaltarief.economie.fgov.be. | ✅ | [socialsecurity.be](https://www.socialsecurity.be/citizen/nl/sociale-bijstand-en-ocmw/hulp-bij-je-energiekosten) |
| De **tijdelijke uitbreiding** naar de **verhoogde tegemoetkoming (VT/BIM)** is gestopt op **30 juni 2023**. Wie alleen VT heeft, heeft geen recht meer. De VT zelf blijft bestaan voor de gezondheidszorg. | 🟡 | [Forum for the Future](https://blog.forumforthefuture.be/nl/article/uitgebreid-sociaal-tarief-eindigt-op-30-juni-...-maar-niet-de-verhoogde-tegemoetkoming-maar-niet-de-verhoogde-tegemoetkoming/18885), [Test-Aankoop](https://www.test-aankoop.be/woning-energie/gas-elektriciteit-mazout-pellets/nieuws/geen-sociaal-tarief-voor-personen-met-verhoogde-tegemoetkoming). Nog te bevestigen op economie.fgov.be: geen nieuwe uitbreiding sinds 2023? |

**Fouten in de audit die dit oplost:**
- elderly-3: "IGO alleen is niet automatisch bepalend";
- social-2/1: "regel Vlaanderen";
- brussels-fr-3/2: "pas automatiquement fédéral";
- personal-data-3: "recht op basis van verhoogde tegemoetkoming".

## Winterbescherming (gewestelijk: per gewest anders)

| Gewest | Feit | Status | Bron |
|---|---|---|---|
| **Vlaanderen** | Van **1 december tot 1 april** gelden strengere regels: afsluiten mag dan alleen bij onveiligheid, leegstand, fraude of een slecht geregelde verhuizing. De minister kan de periode vroeger laten beginnen of verlengen. Wie vóór 1 december al afgesloten was, wordt niet automatisch heraangesloten. Dit geldt voor **alle huishoudens**, niet alleen voor beschermde afnemers. | ✅* | [VREG-FAQ "Kan ik altijd worden afgesloten?"](https://www.vreg.be/nl/veelgestelde-vragen/kan-ik-altijd-worden-afgesloten-van-elektriciteit-enof-aardgas). *De tekst komt uit het zoekresultaat van deze VREG-pagina. Rechtstreeks openen lukte niet (verbinding geweigerd): **de eigenaar moet de pagina vóór de merge nalezen.** Ook te checken: geldt er een verlenging voor 2026–27? |
| **Brussel** | Winterperiode van **1 oktober tot 31 maart**. Afsluiten van een huishouden is dan verboden, ook als de vrederechter het toegestaan heeft (behalve om veiligheidsredenen). Sibelga levert als leverancier van laatste toevlucht. | 🟡 | [socialenergie.be](https://www.socialenergie.be/nl/sociale-maatregelen/gas-elek/winterperiode/), [BX1 (2020)](https://bx1.be/categories/news/precarite-energetique-la-region-bruxelloise-prolonge-la-periode-hivernale-jusquen-2021/). Nog te bevestigen op brugel.brussels. |
| **Wallonië** | Trêve hivernale van **1 november tot 31 maart** (vroeger 15 maart, gewijzigd door decreten van 2022). Voor een **beschermde klant met budgetmeter** loopt de gaslevering door. Voor elektriciteit is er een minimale levering (1300 W, aan te vragen via het OCMW). | ❓ / 🟡 | [Selectra](https://selectra.be/fr/energie/actualites/treve-hivernale-coupure-gaz-electricite-wallonie-bruxelles), [CWaPE 432](https://www.CWAPE.be/sites/default/files/cwape-documents/432.pdf), [droitsquotidiens](https://www.droitsquotidiens.be/fr/question/peut-couper-mes-fournitures-de-gaz-et-delectricite-en-plein-hiver-wallonie). De einddatum (15 of 31 maart) is nog te bevestigen op cwape.be of wallex. |

**Fouten in de audit die dit oplost:**
- social-5/2: "1 november tot en met 31 maart" voor Vlaanderen;
- social-5/3: "alleen voor beschermde afnemers";
- social-5/4: Waalse info over prepaid voor Antwerpen.

## Noodnummers per netbeheerder

Als Karen geen bevestigd nummer heeft voor **de netbeheerder van de gebruiker**, geeft ze **geen** nummer. Ze zegt dan
waar het staat (op de meter, de factuur of de site van de netbeheerder), en noemt 112 bij direct gevaar.

| Netbeheerder (gebied) | Stroomstoring | Gasgeur | Klantendienst | Status |
|---|---|---|---|---|
| **Fluvius** (Vlaanderen) | 078 35 35 00 | 0800 65 065 | 078 35 35 34 | ✅ [fluvius.be/nl/contact](https://www.fluvius.be/nl/contact): "Voor noodoproepen … 078 35 35 00", "Een gasgeur? … gratis noodnummer (24/7)" 0800 65 0 65 |
| **Sibelga** (Brussel) | 02 274 40 66 | 0800 19 400 | 02 549 41 00 | ✅ [Sibelga/Energuide](https://www.energuide.be/en/questions-answers/do-i-have-to-pay-for-sibelga-to-come-out-if-there-is-a-power-cut-or-the-smell-of-gas/14/) |
| **ORES** (grootste deel van Wallonië, o.a. Namen) | 078 78 78 00 | 0800 87 087 | 078 15 78 01 | ✅ [ores.be/aide-et-contact](https://www.ores.be/aide-et-contact/): "Panne et défaillance: 078/78.78.00", "Fuite et odeur de gaz: 0800/87.087" |
| **RESA** (provincie Luik) | 04 263 18 80 | 04 362 98 38 (Franstalige zone) | 04 220 12 11 | ❓ alleen [Selectra](https://selectra.be/fr/energie/distributeurs/resa/panne) en [CallMePower](https://callmepower.be/fr/energie/guides/distributeurs/resa-contact); resa.be toonde geen nummers. **Niet in Karen**: zij verwijst naar de factuur en naar resa.be. |

**Fouten in de audit die dit oplost:**
- wallonia-fr-3/2: "à Bruxelles, appelle le 02 274 40 66" voor Namen;
- wallonia-fr-3/3: geen nummer van ORES;
- wallonia-fr-3/5: "ENEO/Fluvius";
- student-3/1: ORES voor Gent.

## Hulplijnen (voor angst, schaamte en zelfmoordgedachten)

| Dienst | Wat Karen zegt | Status | Bron |
|---|---|---|---|
| Zelfmoordlijn | 1813, gratis, anoniem, 24/7 | ✅ | [zelfmoord1813.be](https://www.zelfmoord1813.be/): "Elk gesprek is anoniem en gratis", "24/7 bereikbaar" |
| Centre de Prévention du Suicide | 0800 32 123, gratuit, anonyme, 24h/24 | ✅ | [preventionsuicide.be](https://www.preventionsuicide.be/la-ligne-decoute-0800-32-123): "anonyme, gratuite et ouverte 24h/24 et 7j/7" |
| CAW | gratis en vertrouwelijk, 0800 13 500 op werkdagen van 9 tot 17 uur, caw.be | ✅ | [caw.be](https://www.caw.be/): "Gratis, professioneel, vertrouwelijk", [caw.be/contacteer-ons](https://www.caw.be/contacteer-ons/). **Niet** "anoniem": dat staat alleen bij de chat. |
| OCMW/CPAS | "kan je helpen met je factuur en schulden" (geen "gratis" of "vertrouwelijk") | ✅ | [socialsecurity.be](https://www.socialsecurity.be/citizen/nl/sociale-bijstand-en-ocmw/hulp-bij-je-energiekosten) |

## Na de review van Codex (568b9f4)

Weggehaald, omdat er geen ✅ voor was:
- "OCMW helpt gratis en vertrouwelijk", "CAW anoniem", "hulp vragen is je recht", "je mag iemand meenemen";
- de bewering dat er in Brussel en Wallonië een winterbescherming *bestaat*;
- "(24/7)" bij de netbeheerders. Alleen Fluvius noemt dat zelf.

## Na de tweede review van Codex (46d4d91)

| Onderwerp | Wat Karen nu doet | Status |
|---|---|---|
| Gemeente → ORES of RESA | **Geen koppeling meer.** Op ores.be en resa.be staat geen gemeentelijst die we konden lezen. Karen gebruikt ORES of RESA alleen als de gebruiker die zelf noemt ("chez ORES"). Anders zegt ze: "staat ORES op je factuur: …; staat RESA: zie factuur en resa.be". | ❓ → niet gebruikt |
| Vlaanderen → Fluvius, Brussel → Sibelga | Karen noemt ze. fluvius.be/over-fluvius en sibelga.be gaven **HTTP 403**, dus er is geen ✅ uit een automatische controle. | **Eigenaar bevestigt** |
| Wat te doen bij gasgeur | "Open ramen en deuren, doe geen licht aan, verlaat de woning en bel pas als je buiten bent." Dat volgt het advies van Sibelga op [energuide.be](https://www.energuide.be/en/questions-answers/do-i-have-to-pay-for-sibelga-to-come-out-if-there-is-a-power-cut-or-the-smell-of-gas/14/): "Open all doors and windows… without turning on a light. Evacuate… once outside the house (!) call". | ✅* uit het zoekresultaat, **eigenaar leest na** |
| "De netbeheerder beheert kabels, leidingen en meter" | Weggehaald. | — |
| "Bel je leverancier niet, alleen de netbeheerder herstelt" | Weggehaald. Alleen "bij direct gevaar: 112" blijft. | — |

## Nog te bevestigen door de eigenaar of op een officiële pagina, voordat PR 1 gemerged wordt

1. Dat Fluvius heel Vlaanderen bedient en Sibelga de 19 Brusselse gemeenten (de sites gaven 403). Het advies bij gasgeur op de energuide-pagina van Sibelga.
2. De einddatum van de Waalse trêve hivernale (31 maart?) en de Brusselse periode (1 oktober tot 31 maart) op cwape.be en brugel.brussels.
3. Of er sinds juli 2023 een nieuwe uitbreiding van het sociaal tarief is (economie.fgov.be).
4. Of er een verlenging van de Vlaamse winterperiode geldt voor 2026–27.

## Wat in PR 1 in Karen zit (`luminus_harness/safety.py`)

- **Noodnummers:** Fluvius, Sibelga en ORES, alle drie ✅. RESA heeft geen nummer, met een verwijzing naar de factuur en naar resa.be. Bij een onbekende Waalse netbeheerder vraagt Karen de gemeente.
- **Sociaal tarief:** federaal, automatisch, IGO en leefloon/OCMW, elektriciteit, aardgas en warmte, 0800 120 33 (alles ✅). Voor verhoogde tegemoetkoming zegt Karen eerlijk dat ze het niet officieel kon nakijken.
- **Winter:** de Vlaamse regel ✅, met de VREG als bron. Voor Brussel en Wallonië zegt Karen dat de bescherming bestaat, maar geeft ze geen datums: die zijn niet officieel bevestigd, en ze verwijst naar Brugel/Sibelga, de CWaPE en het OCMW.
- **Afbetalingsplan, angst, schaamte, kinderen:** geen feitelijke regels, alleen verwijzingen naar de leverancier, het OCMW, het CAW en de FOD Economie. Bij zelfmoordgedachten zijn dat 1813 en 0800 32 123.
