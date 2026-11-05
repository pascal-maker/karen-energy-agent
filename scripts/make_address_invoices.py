"""Writes the fictional invoices 23-25 in tests/fixtures/invoices (addresses, tables, contract text).

Every name, address and number is made up. Run: python3 scripts/make_address_invoices.py
"""

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "invoices"
HEADER = ["Kanaal Energie — FICTIEF", "SYNTHETISCHE TESTFACTUUR — NIET BETALEN"]
FOOTER = ["Volledig fictieve gegevens. Geen IBAN, QR-code of geldige betaalopdracht. Uitsluitend voor softwaretests."]
TABLE = [
    "Samenstelling van uw factuur",
    "Omschrijving", "Bedrag excl. btw", "Btw 6%", "Bedrag incl. btw",
    "Elektriciteit", "€ 500,00", "€ 30,00", "€ 530,00",
    "Gas", "€ 700,00", "€ 42,00", "€ 742,00",
    "Totale energiekosten: € 1.272,00",
    "Voorschotten betaald: € 1.100,00",
    "Totaal te betalen: € 172,00",
    "Vervaldatum: 10/03/2026",
]
CONTRACT = [
    "Contractvoorwaarden",
    "Opzegtermijn: 3 weken, zonder verbrekingsvergoeding.",
    "Verhuist u? Geef uw nieuwe adres en de meterstanden van uw oude woning minstens een maand vooraf door aan onze klantendienst.",
]

INVOICES = {
    "23-nl-leveringsadres.pdf": [
        *HEADER, "Uw jaarafrekening", "Leverancier: Kanaal Energie — FICTIEF", "Klant: Joy Doe",
        "Klantnummer: TEST-KLANT-023", "Factuurnummer: TEST-2026-023", "Factuurdatum: 10/02/2026",
        "Leveringsadres:", "Zonnestraat 12", "0000 Teststad", "EAN elektriciteit: 000000000000000231",
        "Factuurperiode: 01/01/2025 tot 31/12/2025", *TABLE, *CONTRACT, *FOOTER,
    ],
    "24-nl-facturatie-en-leveringsadres.pdf": [
        *HEADER, "Uw jaarafrekening", "Leverancier: Kanaal Energie — FICTIEF", "Klant: Sam Doe",
        "Klantnummer: TEST-KLANT-024", "Factuurnummer: TEST-2026-024", "Factuurdatum: 10/02/2026",
        "Facturatieadres: Kerkweg 1, 0000 Teststad", "Leveringsadres: Molenlaan 7, 0001 Proefdorp",
        "Factuurperiode: 01/01/2025 tot 31/12/2025", *TABLE, *CONTRACT, *FOOTER,
    ],
    "25-nl-geen-adres.pdf": [
        *HEADER, "Uw jaarafrekening", "Leverancier: Kanaal Energie — FICTIEF", "Klant: Lee Doe",
        "Klantnummer: TEST-KLANT-025", "Factuurnummer: TEST-2026-025", "Factuurdatum: 10/02/2026",
        "Factuurperiode: 01/01/2025 tot 31/12/2025", *TABLE, *FOOTER,
    ],
}


def write(name: str, lines: list[str]) -> None:
    page = canvas.Canvas(str(OUT / name), pagesize=A4, invariant=True)
    page.setFont("Helvetica", 9)
    y = 800
    for line in lines:
        page.drawString(40, y, line)
        y -= 14
    page.save()


if __name__ == "__main__":
    for name, lines in INVOICES.items():
        write(name, lines)
