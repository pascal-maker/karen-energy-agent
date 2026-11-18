"""Make the fictional bills 27-29 in tests/fixtures/invoices: bills that print the contract (product, energy price
per kWh, fixed fee, meter, delivery postcode), for the contract check. All names, suppliers and numbers are made up.

Usage: python3 scripts/make_contract_invoices.py [out_dir]   (needs reportlab)
"""
import sys

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

OUT = sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures/invoices"
W, H = A4
HEADER = "SYNTHETISCHE TESTFACTUUR — NIET BETALEN"
SUPPLIER = "Kanaal Energie — FICTIEF"


def make(name, title, lines):
    c = canvas.Canvas(f"{OUT}/{name}.pdf", pagesize=A4)
    c.setFillColor(colors.HexColor("#0b5d4b")); c.rect(0, H - 70, W, 70, fill=1, stroke=0)
    c.setFillColor(colors.white); c.setFont("Helvetica-Bold", 16); c.drawString(40, H - 42, SUPPLIER)
    c.setFont("Helvetica", 9); c.drawRightString(W - 40, H - 42, f"{HEADER} · pagina 1/1")
    c.setFillColor(colors.black); c.setFont("Helvetica-Bold", 13); c.drawString(40, H - 100, title)
    y = H - 125
    for line in lines:
        if line == "":
            y -= 8; continue
        c.setFont("Helvetica-Bold" if line.startswith("**") else "Helvetica", 10)
        c.drawString(40, y, line.strip("*")); y -= 15
    c.setFont("Helvetica-Oblique", 8); c.setFillColor(colors.grey)
    c.drawString(40, 30, "Volledig fictieve gegevens. Geen IBAN, QR-code of geldige betaalopdracht. Uitsluitend voor softwaretests.")
    c.save()


def head(number, address):
    return [f"Leverancier: {SUPPLIER}", "Klant: Jane Doe", f"Leveringsadres: {address}",
            f"Klantnummer: TEST-KLANT-{number}", f"Factuurnummer: TEST-2026-{number}", "Factuurdatum: 13.10.2026", ""]


# 27: electricity only, single meter, Flanders; detail prices excl. btw, network costs per kWh alongside.
make("27-nl-contract-enkelvoudige-meter", "Uw jaarafrekening", head("027", "Teststraat 0, 9000 Teststad") + [
    "Factuurperiode: 01/01/2025 tot 31/12/2025",
    "Product: Kanaal Variabel Stroom",
    "Metertype: enkelvoudige meter",
    "",
    "**Detail elektriciteit (prijzen excl. btw)",
    "Energieprijs 3.500 kWh x 0,2100 €/kWh € 735,00",
    "Vaste vergoeding 01/01/2025 - 31/12/2025 € 40,00",
    "Distributie 3.500 kWh x 0,0950 €/kWh € 332,50",
    "Bijzondere accijns 3.500 kWh x 0,0503 €/kWh € 176,05",
    "Elektriciteitsverbruik: 3.500 kWh",
    "",
    "Totale energiekosten € 1.360,20",
    "Voorschotten betaald € 1.200,00",
    "**Totaal te betalen € 160,20",
])

# 28: electricity on a day/night meter and gas; prices in c€/kWh incl. btw, gas fee per month.
make("28-nl-contract-dag-nacht-en-gas", "Uw jaarafrekening", head("028", "Proeflaan 7, 2000 Proefstad") + [
    "Factuurperiode: 01/01/2025 tot 31/12/2025",
    "",
    "**Elektriciteit — Product: Kanaal Dag Nacht Stroom",
    "Metertype: tweevoudige meter (dag/nacht)",
    "Energieprijs dag 1.800 kWh x 23,10 c€/kWh (incl. btw)",
    "Energieprijs nacht 1.400 kWh x 17,40 c€/kWh (incl. btw)",
    "Vaste vergoeding € 50,00 per jaar",
    "Elektriciteit dag 1.800 kWh",
    "Elektriciteit nacht 1.400 kWh",
    "",
    "**Aardgas — Product: Kanaal Vast Gas",
    "Energieprijs 12.000 kWh x 9,50 c€/kWh (incl. btw)",
    "Vaste vergoeding € 5,00 / maand",
    "Gasverbruik: 12.000 kWh",
    "",
    "Totale energiekosten € 2.990,40",
    "Voorschotten betaald € 2.880,00",
    "**Totaal te betalen € 110,40",
])

# 29: electricity in Brussels: the tariff cards Karen has are for Flanders, so no saving is shown.
make("29-nl-contract-brussel", "Uw jaarafrekening", head("029", "Proefplein 3, 1050 Proefstad") + [
    "Factuurperiode: 01/01/2025 tot 31/12/2025",
    "Product: Kanaal Variabel Stroom",
    "Energieprijs 2.600 kWh x 0,2000 €/kWh (excl. btw) € 520,00",
    "Vaste vergoeding € 40,00 per jaar (excl. btw)",
    "Elektriciteitsverbruik: 2.600 kWh",
    "Totale energiekosten € 980,00",
    "Voorschotten betaald € 900,00",
    "**Totaal te betalen € 80,00",
])

# 30 and 31 follow the layout of the sample bills a large Belgian supplier publishes ("voorbeeldfactuur"): a
# contract box, a line-item table "Product | BTW | Hoeveelheid | Eenheidsprijs (excl. btw) | Bedrag" with the
# product name in the row and network costs and levies in the same table, a fixed fee as a yearly rate counted
# in days, and a product change during the period. Supplier, products and numbers are made up.

# 30: annual bill, single meter, product changed on 01.04.2025; the current product is the one "vanaf".
# Like every Flemish bill since July 2025 it prints the V-check code of the product version (a made-up one).
make("30-nl-contract-tabel-productwissel", "Uw jaarafrekening", [
    f"Leverancier: {SUPPLIER}", "Klant: Jane Doe", "Leveradres: Teststraat 0, 3500 Teststad",
    "Uw huidig contract Kanaal Groen Plus Elektriciteit", "Elektriciteit (EAN 540000000000000030)",
    "Factuurnummer: TEST-2026-030", "Facturatieperiode: 01.01.2025 - 31.12.2025",
    "V-check: vergelijk dit contract met het marktaanbod op www.vtest.be/v-check",
    "Unieke identificatiecode van uw product: KNGP2504 00030125", "",
    "**Elektriciteit: Samenstelling van uw bedrag",
    "Product                                  BTW   Hoeveelheid   Eenheidsprijs (excl. btw)   Bedrag",
    "**Dit betaalt u voor Kanaal Energie (Energiekosten)",
    "Kanaal Basis                             6%    800 kWh       0,1890000 €/kWh             151,20 €",
    "Kanaal Groen Plus (vanaf 01.04.2025)     6%    2.600 kWh     0,1750000 €/kWh             455,00 €",
    "Kosten groene stroom                     6%    3.400 kWh     0,0150000 €/kWh              51,00 €",
    "Kosten WKK                               6%    3.400 kWh     0,0040000 €/kWh              13,60 €",
    "Vaste vergoeding Kanaal Basis            6%    90 dagen      30,00 €/jaar                  7,40 €",
    "Vaste vergoeding Kanaal Groen Plus (vanaf 01.04.2025)  6%  275 dagen  60,00 €/jaar        45,21 €",
    "**Dit betaalt u voor de netbeheerder (Nettarieven)",
    "Transportkosten                          6%    3.400 kWh     0,0280000 €/kWh              95,20 €",
    "Distributiekosten                        6%    3.400 kWh     0,0910000 €/kWh             309,40 €",
    "Meet- en telactiviteit                   6%    365 dagen     15,00 €/jaar                 15,00 €",
    "**Dit betaalt u voor de overheid (Heffingen en toeslagen)",
    "Bijzondere accijns                       6%    3.400 kWh     0,0503000 €/kWh             171,02 €",
    "Bijdrage Energiefonds                    0%    365 dagen     5,07 €/maand                 60,84 €",
    "",
    "Verbruik in kWh: 3.400 kWh",
    "Totale energiekosten € 1.453,87",
    "Aangerekende voorschotten - € 1.380,00",
    "**Totaal te betalen € 73,87",
])

# 31: the short format: contract in "Essentiële contractinformatie", unit price per product, no fixed-fee line.
make("31-nl-contract-kort-formaat", "Uw jaarafrekening", [
    f"Leverancier: {SUPPLIER}", "Klant: Jane Doe", "Factuurnummer: TEST-2026-031", "",
    "**Essentiële contractinformatie",
    "Elektriciteit - EAN 540000000000000031 / Teststraat 0, 2800 Teststad - Variabel tarief Kanaal Actief elektriciteit, contract van 1 jaar",
    "Facturatieperiode: 01.01.2025 - 31.12.2025",
    "",
    "**Overzicht van uw verbruik",
    "Product                          Verbruik      Eenheidsprijs (excl. btw)",
    "Kanaal Actief elektriciteit      3.100 kWh     0,1450000 €/kWh",
    "Elektriciteitsverbruik: 3.100 kWh",
    "",
    "**Samenstelling van uw factuurbedrag",
    "Dit betaalt u aan Kanaal Energie (energiekosten) € 476,47",
    "Dit betaalt u aan de netbeheerder (nettarieven) € 402,10",
    "Dit betaalt u aan de overheid (heffingen en toeslagen) € 165,30",
    "Totale energiekosten € 1.043,87",
    "Aangerekende voorschotten - € 960,00",
    "**Totaal te betalen € 83,87",
])
