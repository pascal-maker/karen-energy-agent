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
