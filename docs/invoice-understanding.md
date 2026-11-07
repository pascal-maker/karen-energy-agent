# Karen begrijpt een geüploade factuur: adressen, secties en hybride zoeken

Aanleiding (5 november 2026): Karen noemde de klant van een geüploade factuur. Op "waar woont Joy?" toonde
ze daarna een passage over verhuizen, vol `[X]`. Dit document beschrijft de laag die dat oplost en wat
daarvan bewezen is.

## Wat er al was

- `invoice_analysis` leest feiten met regels: bedrag, periode, verbruik, voorschotten, klant en leverancier.
  Het werkt op platgeslagen stukken van 1000 tekens met 160 tekens overlap.
- `invoice_schema.Invoice` bevat die feiten met bron, pagina en passage. De vervolgvragen uit PR #84 gebruiken
  dat object: wie betaalt, per jaar, aandachtspunten, samenvatting.
- Vond geen feit iets, dan zocht `retrieve()` de stukken met de meeste gedeelde woorden. Die passage werd
  getoond, gemaskeerd met `mask_invoice_text`, dat elk woord buiten de factuurwoordenlijst vervangt door `[X]`.
- Het gesprek had geen geheugen. De component stuurt bij een PDF-sessie geen geschiedenis mee.

## Wat erbij komt

| Onderdeel | Bestand | Wat het doet |
|---|---|---|
| Semantische laag | `invoice_schema.py` (schema 1.1) | `Invoice.addresses`: elk adres heeft een soort (`billing`, `delivery`, `unknown`), de partij waartoe het hoort (`customer`, `supplier`, `unknown`), het label zoals gedrukt, en bron, pagina, passage en `read_by`. Zonder herkenbaar adres blijft de lijst leeg. |
| Secties | `invoice_sections.py` | Werkt op de pagina's met hun regeleinden. Velden ("Label: waarde") blijven samen. Een adres blijft bij zijn label. Een tabel houdt zijn titel, en elke rij krijgt de kolomnamen ("Gas — Bedrag excl. btw: € 700,00 · Btw 6%: € 42,00 · …"). Overige tekst wordt verdeeld in stukken van hoogstens 800 tekens. Secties overlappen niet, dus geen bedrag telt dubbel. |
| Hybride zoeken | `invoice_retrieval.py` | Kleinste eenheid: één veld, één tabelrij of één zin. Elke eenheid krijgt twee scores: BM25 (accentloos, prefix van 5 letters, dubbele klinkers samengevoegd) en de cosinus met een lokaal embeddingmodel. Reciprocal rank fusion houdt de beste 8. Daarna volgt een antwoordcontrole (zie hieronder). |
| Gesprek | `invoice_session.py` | Eén `InvoiceSession` per upload, in het geheugen. Bevat de stukken, het factuurobject, de secties, de index en de laatste 6 beurten. Hier worden adresvragen, "dit bedrag" en voornaamwoorden opgelost. Alle andere vragen gaan naar de bestaande routes, met de hybride zoeker in plaats van de ruwe passage. |
| App | `energy_voice_app.py` | De upload bouwt de sessie en geeft de adressen terug in `invoice`. De index wordt meteen op de achtergrond ingebed. `/api/answer` met `session_id` gebruikt `session.ask`. Het model wordt bij het opstarten op de achtergrond geladen. |

### Antwoordcontrole: een passage moet de vraag echt beantwoorden

1. **Soort waarde.** De vraag bepaalt welk soort waarde een antwoord nodig heeft: prijs per kWh, bedrag,
   hoeveelheid, datum, duur of vrij. Een meterstand is geen prijs per kWh; een bedrag is geen termijn.
2. **Onderwerp.** De onderwerpwoorden van de vraag moeten in de eenheid staan. Vraagwoorden en woorden die op
   elke factuur staan (factuur, bedrag, betalen) tellen niet. Bij één of twee onderwerpwoorden moeten ze er
   allemaal staan, bij meer twee derde. "incl. btw" in een kolomkop is geen btw-bedrag.
3. **Betekenis.** Een parafrase zonder gedeelde woorden telt alleen bij een specifieke soort waarde (duur,
   datum, hoeveelheid, prijs per kWh) en een cosinus van minstens 0,45. Voor bedragen en open vragen telt
   betekenis alleen nooit mee: voor het model lijken alle geldregels op elkaar. "Wat zijn netkosten?" haalt
   0,69 met "Totaal te betalen".
4. Geen eenheid voldoet? Dan zegt Karen wat er ontbreekt ("geen prijs per kWh"), of stelt ze een wedervraag,
   of gaat de vraag zonder factuur naar de gewone route. Een willekeurige passage toont ze nooit.
5. Een antwoord uit de feitenlezer wordt alleen vervangen als een passage meer van de vraag dekt. Zo krijgt
   "hoeveel btw betaal ik op gas" de gasrij in plaats van het totaal te betalen.

### Vraagbegrip zonder een lijst trefwoorden

- **Adresvragen** worden herkend met voorbeeldvragen per soort (`ADDRESS_CARDS`: woonplaats, leveringsadres,
  facturatieadres, adres). Namen en voornaamwoorden worden eerst weggelaten. Daarna telt de overlap in woorden
  (minstens 0,6), of, als er een adreswoord in de vraag staat, de cosinus met het model (minstens 0,82).
  Daardoor is "wie is mijn leverancier" geen adresvraag.
- **Namen.** Een naam die op de factuur staat, is de klant. Een andere naam met een hoofdletter levert een
  wedervraag op ("Ik zie Kim niet op deze factuur … Bedoel je Joy Doe?").
- **"ze", "hij", "die"** wijzen alleen naar de klant als een van de laatste twee antwoorden de klant noemde.
  Anders vraagt Karen over wie het gaat.
- **"dit bedrag"** is de waarde waar een van de laatste twee antwoorden over ging, met het veld waaruit ze komt:
  de gevraagde tabelcel (bij "hoeveel btw betaal ik op gas" de btw-cel, niet de eerste cel van de rij) of het
  hoofdbedrag van een feitenantwoord. Noemde dat antwoord meer bedragen, of geen, dan vraagt Karen welk bedrag.
  Of het incl. of excl. btw is, komt alleen uit de eigen kolom of regel van dat bedrag; het model rekent nooit.
- **Feitenantwoorden** worden alleen gegeven als ze het onderwerp van de vraag dekken. Anders zegt Karen dat
  geen regel precies dat zegt, en toont ze hoogstens twee regels die er deels over gaan.
- **Bewoning.** Een leverings- of facturatieadres bewijst niet waar iemand woont. Elk antwoord op "waar woont
  X" zegt dat er letterlijk bij. Het bestaande principe uit PR #84 blijft: een domiciliëringsdatum bewijst
  geen betaling.

### Een vraag als taken (7 november 2026)

"heb je advies over deze factuur zoek het adres op in deze factuur" kreeg "Ik weet niet zeker wat je wilt
weten". Nu wordt elke vraag eerst een `Plan` met getypte taken (`invoice_intent.py`): `address` (met soort),
`advice`, `payer`, `yearly`, `summary`, `explain`, `insights`, `this_amount` of `question`, plus de delen die
Karen niet kon plaatsen. `InvoiceSession.ask(question, plan)` voert elke taak in code uit, in de volgorde van
de vraag. Een onduidelijk deel krijgt een gerichte vraag ("Wat wil je weten over „…“?"), tenzij de factuur het
gewoon beantwoordt.

- **Lokale parser (standaard).** De vraag wordt gesplitst voor opdrachtwoorden en voegwoorden, en elk deel
  wordt herkend met de bestaande herkenners. Tikfouten in de paar woorden die een taak kiezen ("adress",
  "leveringsadrs") worden eerst rechtgezet. Advies over een onderwerp ("wat moet ik doen als ik verhuis?") is
  een eigen vraag, geen advies over de factuur.
- **Taalmodel (optioneel).** Alleen met `KAREN_INTENT_MODEL=1` en `OPENAI_API_KEY` (model:
  `KAREN_INTENT_MODEL_NAME`, standaard `gpt-5-mini`). Het krijgt de vraag met de klantnaam als `[klant]`,
  adressen als `[adres]` en identificatoren als `[X]`, plus drie velden context (vorige taak, klant genoemd,
  bedrag genoemd). Nooit de PDF, de tekst of de adressen van de factuur. Het antwoord moet het strikte
  JSON-schema volgen en elke taak moet letterlijk in de vraag staan; anders, of bij een time-out of fout,
  beslist de lokale parser. `store=False`. Het model zoekt niets op en rekent niets uit.

### Eén betalingsstatus, advies als acties

- `invoice_schema.payment_status` geeft één status per factuur: domiciliëring aangekondigd, datum voorbij maar
  niet bevestigd, te betalen vóór, terugbetaling, niets te betalen. Het uploadantwoord, "wie moet betalen",
  de samenvatting en het advies gebruiken dezelfde zin en dezelfde datumnotatie.
- `advice_answer` geeft acties (kijk in je bankapp, pas je voorschot niet aan op basis van één voorschotfactuur,
  upload je jaarafrekening, betaal vóór …), dan wat Karen nakeek en wat de factuur niet toont. Het herhaalt de
  samenvatting niet. "Andere aandachtspunten vond ik niet" is vervangen door wat er nagekeken is.
- Het uploadantwoord van een voorschotfactuur begint met het bedrag, het jaarbedrag bij hetzelfde voorschot en
  de beperking ("Of dat voldoende is, kan ik pas beoordelen met je jaarafrekening").
- **Adres onbekend of afwezig.** Staat er iets dat op een adres lijkt zonder label, dan zegt Karen "Ik kon het
  adres niet betrouwbaar lezen" en toont ze die regel. Staat er niets, dan "Op deze factuur vond ik geen adres".

Elke uitvoering (velden opzoeken, bedragen, broncontrole) blijft code. Alleen het herkennen van de opdracht kan
een taalmodel doen, en alleen als dat aanstaat.

## Afhankelijkheden en kosten

- `fastembed==0.7.3`. Dat brengt `onnxruntime`, `tokenizers` en `huggingface-hub` mee. Model:
  `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, gekwantiseerde ONNX van
  `qdrant/…-onnx-Q`, Apache-2.0, ongeveer 220 MB.
- Het model wordt eenmalig gedownload van Hugging Face (de cache-map is in te stellen met `KAREN_EMBED_CACHE`).
  Op de Space gebeurt dat bij elke herstart, omdat er geen blijvende schijf is.
- Gemeten op 4 vCPU in deze container:
  - model laden: 3 s, ongeveer 620 MB RSS;
  - inbedden: ongeveer 50 ms per eenheid (20 eenheden in 1 s), gedaan op de achtergrond na de upload;
  - elke volgende vraag: ongeveer 40 ms.
- Zonder model (niet geïnstalleerd, download mislukt, of `KAREN_INVOICE_EMBEDDINGS=0`) zoekt Karen alleen op
  woorden. Alles blijft dan werken; alleen parafrases zonder gedeelde woorden vallen weg.
- Geen vector- of grafendatabase, geen externe API, geen extra kosten per vraag.

## Privacy

- **Externe verwerking en logs.** Hier verandert niets. Een PDF-sessie wordt niet getraceerd in Langfuse en
  niet gelogd. Het optionele gehoste model krijgt nog steeds alleen tekst die met `mask_invoice_text` is
  gemaskeerd, en alleen met `KAREN_INVOICE_MODEL=1`.
- **Wat de bezoeker zelf ziet.** Passages uit de eigen upload behouden hun woorden. Alleen identificatoren
  (klant-, meter- en rekeningnummers, IBAN, e-mail, telefoon, de klantnaam) worden `[X]`, via de nieuwe
  `mask_identifiers`. Vraagt de bezoeker naar dat ene veld ("wat is mijn EAN?"), dan ziet die het.
  Adresantwoorden tonen het adres, want daar ging de vraag over.
- **Inbedden.** Gebeurt op de server, met een lokaal model. Er gaat geen factuurtekst naar buiten.
- **Isolatie.** Elke upload heeft een eigen sessieobject. Index, inbeddingen en beurten verdwijnen samen met de
  sessie (dezelfde TTL en `DELETE /api/session/{id}`). Het enige dat sessies delen is het model zelf, en dat
  bewaart niets. Daarvoor is een test toegevoegd.
- **Documenttekst is data.** Een regel als "Negeer alle vorige instructies" wordt geciteerd of genegeerd,
  nooit uitgevoerd. Het optionele intentmodel ziet geen documenttekst, alleen de gemaskeerde vraag.
- **Tests en evaluatie.** Die gebruiken alleen de synthetische facturen in `tests/fixtures/invoices`. Echte
  uploads worden niet gebruikt.

## Bewijs

Gebruik `python scripts/eval_invoice_understanding.py --baseline <worktree van main>`. De evaluatieset staat
in `evals/invoice_understanding.json`: 43 vragen op synthetische facturen (10 met samengestelde of rommelige vragen, 7 november), telkens met het verwachte
antwoord en de ondersteunende passage. Het resultaat staat in de PR-beschrijving, met de exacte commit.

Wat **lokaal bewezen** is:

- de regressietests in `tests/test_karen_invoice_understanding.py`, die eerst faalden;
- de bestaande suite;
- de evaluatie, met en zonder model.

Wat het **niet** bewijst:

- **Zelfgeschreven vragen.** De evaluatievragen zijn door mij geschreven, terwijl ik de nieuwe aanpak bouwde.
  Een deel van de drempels is erop afgesteld. Het is dus een in-sample-meting, geen onafhankelijke.
- **Synthetische opmaak.** De vijf facturen hebben een nette opmaak met één cel per regel. Echte facturen
  (meerdere kolommen, tabellen over twee pagina's, labels rechts van de waarde, scans) zijn niet getest. OCR of
  layoutanalyse is niet toegevoegd: op de testfacturen was de tekst van pypdf voldoende, en voor echte
  facturen is dat nog niet aangetoond.
- **Drempels.** De drempels (0,45, 0,6, 0,82) zijn op deze set gekozen. Ze moeten nog getoetst worden op
  echte vragen. Het Langfuse-annotatiepakket met 👎-vragen is daarvoor de plek, maar PDF-sessies worden niet
  getraceerd. Dat vraagt dus een lokale test met een echte factuur, zonder externe calls.
- **Bekend falend item.** "tegen wanneer moet het geld op hun rekening staan?" vindt de vervaldatum niet: het
  model scoort de vervaldatum en de factuurperiode bijna gelijk.

## Beperkingen

- **Adressen.** Een adres wordt pas herkend als het eindigt op een Belgische of Nederlandse postcode met
  plaatsnaam, en als het label vóór het adres staat ("Leveringsadres:"). Een adresblok zonder label, zoals
  het venster-adres bovenaan een brief, wordt niet herkend en blijft `null`.
- **Tabellen.** Een tabel wordt alleen herkend als elke cel op een eigen regel staat. Rijen met alle cellen op
  één regel (fixture 20) blijven tekst. Ze zijn wel vindbaar, maar zonder kolomnamen.
- **Woordprefix.** Het prefix van 5 letters is grof ("klantnummer" en "klantendienst" delen "klant"). Het
  rerangschikken op exacte woorden vangt het bekende geval op.
- **Taal.** De voorbeeldvragen zijn vooral Nederlands, met enkele Franse en Engelse. Franse facturen zijn
  getest voor feiten, niet voor de nieuwe adres- en passagevragen.
