# Synthetische energiefacturen voor Karen

Download `synthetic-invoices.zip` via GitHub (open het bestand en kies Download raw file).

De ZIP bevat negen tekst-PDF’s met exact vastgelegde bedragen en één beeldgegenereerde factuur als PNG en OCR-PDF. Klanten heten John Doe, Jane Doe, Alex Doe of Doe Test BV. Leverancier Kanaal Energie is fictief. Alle documenten dragen de markering TESTFACTUUR / NIET BETALEN en bevatten geen geldige betaalgegevens.

De aangeleverde originele facturen, referentierenders en openbare leveranciersvoorbeelden zijn niet opgenomen. De synthetische lay-outs zijn nieuw ontworpen; ze zijn geen identieke kopieën van leveranciersfacturen.

## Testgevallen

| Bestandnummer | Scenario | Verwachte uitkomst |
| --- | --- | --- |
| 01 | Jaarafrekening met duizendpunten | Kosten 1234,56; voorschotten 1000,00; te betalen 234,56; nieuw voorschot 105,00 |
| 02 | Dezelfde bedragen zonder duizendpunten | Dezelfde waarden; 1234,56 mag niet als 123 worden herkend |
| 03 | Tegoed | Kosten 800,00; voorschotten 950,00; 150,00 terug te ontvangen |
| 04 | Geen expliciet saldo | Kosten 1200,00; voorschotten 900,00; berekend verschil 300,00 is geen bevestigd saldo |
| 05 | Nulafrekening | Kosten en voorschotten 1000,00; te betalen 0,00 |
| 06 | Maandelijkse voorschotfactuur | Elektriciteit 60,00 + gas 90,00 excl. btw; btw 9,00; te betalen 159,00 |
| 07 | Twee pagina’s | Verbruik op pagina 1; kosten en voorschotten op pagina 2; te betalen 350,00 |
| 08 | Franstalige zakelijke factuur | 5000,00 excl. btw + 1050,00 btw; voorschotten 3630,00; saldo 2420,00 |
| 09 | Ontbrekende bedragen | Alleen XXX,XX; geen bedragen verzinnen |
| 10 | Beeldgegenereerde factuur | John Doe; kosten 1234,56; voorschotten 1000,00; saldo 234,56; nieuw voorschot 105,00 |

Bij de NL-jaarafrekeningen met verbruik: dag 2208 kWh + nacht 500 kWh = 2708 kWh; gas 6000 kWh. Beginmeterstand 42400 kWh en eindmeterstand 44608 kWh zijn geen afzonderlijke verbruikswaarden. Bij de zakelijke factuur is het elektriciteitsverbruik 12000 kWh. De maandelijkse voorschotfactuur bevat geen werkelijk gemeten verbruik.

`expected-results.json` bevat de bronwaarden voor de negen exacte PDF’s. Geldbedragen zijn decimale strings. `due`, `credit` en `calculated_difference` hebben verschillende betekenissen en mogen niet als hetzelfde saldo worden behandeld. De JSON beschrijft verwachte documentfeiten, geen garantie dat de huidige parser ze al correct herkent. Bij onbekende of ontbrekende feiten hoort geen waarde te worden verzonnen.

## Gebruik

1. Pak de ZIP uit.
2. Upload een PDF uit `exacte-pdfs` in Karen en vergelijk de antwoorden met de tabel en JSON.
3. Vraag apart naar te betalen bedrag, eerder aangerekende voorschotten, nieuw voorschot, jaarverbruik en meterstanden.
4. Upload `10-beeldmodel-testfactuur.png` om foto/OCR te testen; de OCR-PDF test de PDF-tekstextractie.

De exacte PDF’s zijn met ReportLab gemaakt. Het beeldvoorbeeld is met een beeldgeneratiemodel gemaakt en visueel gecontroleerd; de naam en de belangrijkste bedragen zijn ook in de Nederlandse Tesseract-OCR-tekst teruggevonden. De gegenereerde afbeelding verwijst naar een volgende pagina die niet bestaat: gebruik dit als visueel testvoorbeeld, niet als financieel referentiedocument. OCR kan fouten bevatten.

Gecontroleerd: ZIP-integriteit, negen PDF’s met verwachte pagina-aantallen, fictieve namen, zichtbare testmarkeringen, rekenkundige consistentie en aanwezigheid van de opgegeven te betalen bedragen in de PDF-tekst. Niet volledig end-to-end getest op de live backend. Geen applicatielogica gewijzigd.
