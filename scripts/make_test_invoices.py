"""Make the fictional bills 11-20 in tests/fixtures/invoices. All names, suppliers and numbers are made up.

Usage: python3 scripts/make_test_invoices.py [out_dir]   (needs reportlab)
"""
import json, sys
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib import colors

OUT = sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures/invoices"
W, H = A4
HEADER = "SYNTHETISCHE TESTFACTUUR — NIET BETALEN"
SUPPLIER = "Kanaal Energie — FICTIEF"


def page(c, lines, title, number=1, total=1):
    c.setFillColor(colors.HexColor("#0b5d4b")); c.rect(0, H - 70, W, 70, fill=1, stroke=0)
    c.setFillColor(colors.white); c.setFont("Helvetica-Bold", 16); c.drawString(40, H - 42, SUPPLIER)
    c.setFont("Helvetica", 9); c.drawRightString(W - 40, H - 42, f"{HEADER} · pagina {number}/{total}")
    c.setFillColor(colors.black); c.setFont("Helvetica-Bold", 13); c.drawString(40, H - 100, title)
    y = H - 125
    for line in lines:
        if line == "":
            y -= 8; continue
        bold = line.startswith("**")
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 10)
        text = line.strip("*")
        if "\t" in text:
            x = 40
            for cell in text.split("\t"):
                c.drawString(x, y, cell); x += 125
        else:
            c.drawString(40, y, text)
        y -= 15
    c.setFont("Helvetica-Oblique", 8); c.setFillColor(colors.grey)
    c.drawString(40, 30, "Volledig fictieve gegevens. Geen IBAN, QR-code of geldige betaalopdracht. Uitsluitend voor softwaretests.")
    c.setFillColor(colors.black); c.showPage()


def make(name, title, pages):
    c = canvas.Canvas(f"{OUT}/{name}.pdf", pagesize=A4)
    for i, lines in enumerate(pages, 1):
        page(c, lines, title if i == 1 else title + " (vervolg)", i, len(pages))
    c.save()


def person(name, number):
    return [f"Leverancier: {SUPPLIER}", f"Klant: {name}", "Adres: Teststraat 0, 0000 Teststad",
            f"Klantnummer: TEST-KLANT-{number}", f"Factuurnummer: TEST-2026-{number}", "Factuurdatum: 13.10.2026", ""]


INVOICES = []

# 11: monthly advance in the layout most suppliers use: amounts after the number, EAN per energy, direct debit.
make("11-nl-voorschot-domiciliering", "Uw maandelijks voorschot", [person("John Doe", "011") + [
    "**Omschrijving\tPeriode\tBedrag",
    "Elektriciteit (EAN 000000000000000011)\tokt 2026\t63,21 €",
    "Gas (EAN 000000000000000012)\tokt 2026\t105,66 €",
    "Totaal (excl. BTW)\t\t168,87 €",
    "BTW\t\t10,13 €",
    "**Totaal factuurbedrag (incl. BTW) 179,00 €",
    "",
    "Uw bank zal op 31.10.2026 het bedrag van € 179,00 overschrijven (domiciliëring, testgegevens).",
    "Contract: Vast tarief, onbepaalde duur, opzegtermijn 3 weken zonder verbrekingsvergoeding.",
]])
INVOICES.append({"file": "11-nl-voorschot-domiciliering.pdf", "invoice_type": "voorschotfactuur", "amount_due": "179.00",
                 "advance_lines": {"elektriciteit": "63.21", "gas": "105.66"}, "due_date": "31.10.2026", "direct_debit": True,
                 "total_energy_cost": None, "advances_paid": None, "electricity_kwh": None, "gas_kwh": None,
                 "customer": "John Doe", "supplier": "Kanaal Energie"})

# 12: bimonthly advance, electricity only, paid by transfer before a date.
make("12-nl-tweemaandelijks-voorschot", "Uw tweemaandelijkse voorschotfactuur", [person("Jane Doe", "012") + [
    "**Omschrijving\tPeriode\tBedrag",
    "Elektriciteit\tnov 2026\t€ 90,57",
    "Btw 6%\t\t€ 5,43",
    "**Te betalen bedrag: € 96,00",
    "Gelieve vóór 20.11.2026 te betalen met de gestructureerde mededeling (fictief).",
]])
INVOICES.append({"file": "12-nl-tweemaandelijks-voorschot.pdf", "invoice_type": "voorschotfactuur", "amount_due": "96.00",
                 "advance_lines": {"elektriciteit": "90.57"}, "due_date": "20.11.2026", "gas_kwh": None, "electricity_kwh": None})

# 13: annual settlement with a new-advance table per energy (old and new amount).
make("13-nl-jaarafrekening-nieuw-voorschot-tabel", "Uw jaarafrekening", [person("Alex Doe", "013") + [
    "Factuurperiode: 01/10/2025 tot 30/09/2026",
    "**Samenstelling van uw factuur",
    "Totale energiekosten € 2.732,30",
    "Aangerekende voorschotten - € 2.320,00",
    "**Totaal te betalen € 412,30",
    "Vervaldatum: 28/10/2026",
    "",
    "**Uw nieuw voorschot\tHuidig\tNieuw",
    "Elektriciteit maandelijks € 80,00 € 95,00",
    "Gas maandelijks € 110,00 € 120,00",
    "",
    "**Uw verbruik",
    "Elektriciteit dag 1.850 kWh",
    "Elektriciteit nacht 1.200 kWh",
    "Gasverbruik: 9.400 kWh",
]])
INVOICES.append({"file": "13-nl-jaarafrekening-nieuw-voorschot-tabel.pdf", "invoice_type": "jaarafrekening", "amount_due": "412.30",
                 "total_energy_cost": "2732.30", "advances_paid": "2320.00", "electricity_kwh": "3050", "gas_kwh": "9400",
                 "new_advance": ["95.00", "120.00"], "due_date": "28/10/2026", "customer": "Alex Doe"})

# 14: final bill after a move, money back, written with a minus.
make("14-nl-slotfactuur-verhuis-tegoed", "Uw slotfactuur", [person("Jane Doe", "014") + [
    "Factuurperiode: 01/01/2026 tot 15/09/2026 (verhuis, energieovernamedocument ontvangen)",
    "Totale energiekosten € 514,60",
    "Voorschotten betaald € 601,00",
    "**Terug te krijgen - € 86,40",
    "Wij storten dit bedrag binnen 15 dagen terug op je gekende rekening (fictief).",
    "Elektriciteitsverbruik: 1.420 kWh",
]])
INVOICES.append({"file": "14-nl-slotfactuur-verhuis-tegoed.pdf", "invoice_type": "slotfactuur", "amount_due": "-86.40",
                 "total_energy_cost": "514.60", "advances_paid": "601.00", "electricity_kwh": "1420", "gas_kwh": None})

# 15: gas in cubic metres.
make("15-nl-jaarafrekening-gas-m3", "Uw jaarafrekening aardgas", [person("John Doe", "015") + [
    "Factuurperiode: 01/03/2025 tot 28/02/2026",
    "Totale energiekosten € 1.580,20",
    "Voorschotten betaald € 1.440,00",
    "**Totaal te betalen € 140,20",
    "Gasverbruik: 1.150 m³ (omgerekend 12.190 kWh)",
]])
INVOICES.append({"file": "15-nl-jaarafrekening-gas-m3.pdf", "amount_due": "140.20", "total_energy_cost": "1580.20",
                 "advances_paid": "1440.00", "gas_kwh": "1150", "gas_unit": "m³", "electricity_kwh": None})

# 16: prosumer with injection; injection is not consumption.
make("16-nl-prosument-injectie", "Uw jaarafrekening", [person("Alex Doe", "016") + [
    "Factuurperiode: 01/01/2025 tot 31/12/2025",
    "**Uw verbruik",
    "Elektriciteit dag 2.400 kWh",
    "Elektriciteit nacht 1.500 kWh",
    "Injectie op het net 2.100 kWh (terugleveringsvergoeding - € 84,00)",
    "",
    "Totale energiekosten € 1.315,10",
    "Voorschotten betaald € 660,00",
    "**Totaal te betalen € 655,10",
    "Capaciteitstarief: gemiddelde maandpiek 3,4 kW (fictief).",
]])
INVOICES.append({"file": "16-nl-prosument-injectie.pdf", "amount_due": "655.10", "total_energy_cost": "1315.10",
                 "advances_paid": "660.00", "electricity_kwh": "3900", "gas_kwh": None})

# 17: thousands written with a space and the currency after the amount.
make("17-nl-spatie-duizendtal", "Uw jaarafrekening", [person("Jane Doe", "017") + [
    "Factuurperiode: 01/06/2025 tot 31/05/2026",
    "Totale energiekosten: 3 268,40 EUR",
    "Voorschotten betaald: 2 250,00 EUR",
    "**Totaal te betalen: 1 018,40 EUR",
    "Elektriciteitsverbruik: 4 100 kWh",
]])
INVOICES.append({"file": "17-nl-spatie-duizendtal.pdf", "amount_due": "1018.40", "total_energy_cost": "3268.40",
                 "advances_paid": "2250.00"})

# 18: French monthly advance with a due date.
make("18-fr-facture-acompte", "Votre facture d'acompte mensuelle", [[
    f"Fournisseur : Canal Energie — FICTIF", "Client : Jane Doe", "Adresse : Rue du Test 0, 0000 Ville-Test",
    "Facture TEST-2026-018 · Date : 13/11/2026", "",
    "**Description\tPériode\tMontant",
    "Électricité\tdéc 2026\t58,00 €",
    "Gaz\tdéc 2026\t76,00 €",
    "TVA 6%\t\t8,04 €",
    "**Montant à payer 142,04 €",
    "Échéance : 05/12/2026",
]])
INVOICES.append({"file": "18-fr-facture-acompte.pdf", "amount_due": "142.04", "due_date": "05/12/2026",
                 "electricity_kwh": None, "gas_kwh": None})

# 19: Brussels single-rate meter, balance in the customer's favour.
make("19-nl-brussel-saldo-in-uw-voordeel", "Uw jaarafrekening", [person("John Doe", "019") + [
    "Factuurperiode: 01/04/2025 tot 31/03/2026 · leveringsadres in het Brussels Gewest (fictief)",
    "Elektriciteit enkelvoudig 2.950 kWh",
    "Totale energiekosten € 954,90",
    "Voorschotten betaald € 1.000,00",
    "**Saldo in uw voordeel € 45,10",
]])
INVOICES.append({"file": "19-nl-brussel-saldo-in-uw-voordeel.pdf", "amount_due": "-45.10", "total_energy_cost": "954.90",
                 "advances_paid": "1000.00", "electricity_kwh": "2950", "gas_kwh": None})

# 20: meter table (begin, end, consumption), advances as a deduction, total incl. btw, two pages.
make("20-nl-metertabel-twee-paginas", "Uw jaarafrekening", [
    person("Alex Doe", "020") + [
        "Factuurperiode: 01/11/2025 tot 31/10/2026",
        "**Overzicht van uw verbruik",
        "**Omschrijving\tBeginstand\tEindstand\tVerbruik",
        "Elektriciteit dag 12.000 kWh 13.600 kWh 1.600 kWh",
        "Elektriciteit nacht 5.000 kWh 5.900 kWh 900 kWh",
        "De meterstanden zijn tellerstanden, geen verbruik.",
    ],
    [
        "**Samenstelling",
        "Totaal energiekosten € 1.275,90",
        "Aangerekende voorschotten - € 1.200,00",
        "**Totaal factuurbedrag (incl. btw) € 75,90",
        "Vervaldatum: 12/12/2026",
    ],
])
INVOICES.append({"file": "20-nl-metertabel-twee-paginas.pdf", "amount_due": "75.90", "total_energy_cost": "1275.90",
                 "advances_paid": "1200.00", "electricity_kwh": "2500", "due_date": "12/12/2026"})

# 21: French business settlement: MWh, "période du ... au ...", due date in words, new intermediate bill.
make("21-fr-b2b-regularisation-mwh", "Facture annuelle de régularisation", [[
    "Fournisseur : Canal Energie Entreprises — FICTIF", "Client : Doe Test BV", "Adresse : Rue du Test 0, 0000 Ville-Test",
    "Facture TEST-2018-021 · Date : 08/10/2018", "",
    "Électricité – période du 03/10/2017 au 30/06/2018",
    "Électricité heures pleines 15 MWh",
    "Électricité heures creuses 10 MWh",
    "",
    "**Récapitulatif\tMontant",
    "Consommation hors TVA\t€ 5.106,75",
    "TVA 21%\t€ 1.072,42",
    "Coût total TVAC\t€ 6.179,17",
    "Décomptes facturés TVAC\t− € 4.399,38",
    "**Montant à payer € 1.779,79",
    "À payer avant le 7 novembre 2018.",
    "Votre nouvelle facture intermédiaire s'élève à 2.371,59 € TVAC.",
]])

# Expected values for these bills live in tests/fixtures/invoices/expected.json.
print(f"{len(INVOICES)} invoices written to {OUT}")
