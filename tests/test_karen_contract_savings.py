"""Contract check, phase 1: the contract read from a bill and the yearly saving against verified tariff cards.

Bills 27-29 are fictional (scripts/make_contract_invoices.py). The tariff cards are a small fixed set here, so
the expected euros do not move when market/tariffs-phase2.json is updated; one test uses the real file.
"""

from decimal import Decimal
from pathlib import Path

import pytest

from luminus_harness.contract_savings import check_invoice_contract, local_contracts
from luminus_harness.contract_terms import read_contract_terms, region_for_postcode
from luminus_harness.knowledge import KnowledgeChunk, load_pdf_chunks

FIXTURES = Path(__file__).parent / "fixtures" / "invoices"

CARDS = {
    "as_of": "2026-10-19",
    "suppliers": [
        {"name": "Alfa", "status": "verified", "product_name": "Alfa Flex", "price_per_kwh_eur": 0.19,
         "standing_charge_amount": 25.0, "standing_charge_unit": "per_year", "source_url": "https://example.test/alfa"},
        {"name": "Beta", "status": "verified", "product_name": "Beta Vast", "contract_type": "fixed", "duration_months": 36,
         "price_per_kwh_eur": 0.14, "standing_charge_amount": 75.0, "standing_charge_unit": "per_year"},
        {"name": "Gamma", "status": "verified", "product_name": "Gamma Variabel", "price_per_kwh_eur": 0.18,
         "standing_charge_amount": 4.0, "standing_charge_unit": "per_month"},
        {"name": "Delta", "status": "unverified", "product_name": "Delta Flex", "price_per_kwh_eur": 0.01},
    ],
    "gas_suppliers": [
        {"name": "Alfa", "status": "verified", "product_name": "Alfa Gas Flex", "price_per_kwh_eur": 0.085,
         "standing_charge_amount": 20.0, "standing_charge_unit": "per_year"},
        {"name": "Beta", "status": "verified", "product_name": "Beta Gas Vast", "contract_type": "fixed",
         "price_per_kwh_eur": 0.078, "standing_charge_amount": 35.0, "standing_charge_unit": "per_year"},
    ],
}


def _bill(name: str, **kwargs):
    return check_invoice_contract(load_pdf_chunks(FIXTURES / name, source=name), CARDS, **kwargs)


def _text(text: str, **kwargs):
    return check_invoice_contract([KnowledgeChunk(page=1, text=text, urls=(), source="tekst.txt")], CARDS, **kwargs)


# --- reading the contract from the bill -------------------------------------------------------------

def test_energy_price_fee_meter_and_postcode_are_read_with_their_evidence():
    chunks = load_pdf_chunks(FIXTURES / "27-nl-contract-enkelvoudige-meter.pdf", source="27.pdf")
    terms = read_contract_terms(chunks, ("electricity",))
    electricity = terms["electricity"]
    assert electricity["product"]["value"] == "Kanaal Variabel Stroom"
    assert electricity["product"]["contract_type"] == "variable"
    assert electricity["price"]["value"] == Decimal("0.2100")  # not distributie 0,0950 or accijns 0,0503
    assert electricity["price"]["vat"] == "excl"
    assert "Energieprijs" in electricity["price"]["field"]
    assert electricity["standing_charge"]["value"] == Decimal("40.00")  # not "2025" from the date range
    assert electricity["standing_charge"]["period_days"] == 365
    assert terms["meter"]["value"] == "single"
    assert terms["postcode"]["value"] == "9000" and terms["postcode"]["region"] == "flanders"
    assert terms["postcode"]["delivery"] is True
    assert electricity["price"]["page"] == 1 and electricity["price"]["source"] == "27.pdf"


def test_day_and_night_prices_and_gas_are_kept_apart():
    chunks = load_pdf_chunks(FIXTURES / "28-nl-contract-dag-nacht-en-gas.pdf", source="28.pdf")
    terms = read_contract_terms(chunks, ("electricity", "gas"))
    assert terms["meter"]["value"] == "day_night"
    assert terms["electricity"]["day_price"]["value"] == Decimal("0.231")
    assert terms["electricity"]["night_price"]["value"] == Decimal("0.174")
    assert terms["electricity"]["price"] is None
    assert terms["gas"]["price"]["value"] == Decimal("0.095")
    assert terms["gas"]["price"]["vat"] == "incl"
    assert terms["gas"]["standing_charge"]["unit"] == "per_month"
    assert terms["gas"]["product"]["value"] == "Kanaal Vast Gas"
    assert terms["gas"]["product"]["contract_type"] == "fixed"


def test_french_labels():
    chunk = KnowledgeChunk(page=2, text=(
        "Adresse de fourniture : Rue du Test 0, 5000 Ville-Test Électricité Votre produit : Test Variable "
        "Prix de l'énergie 3 200 kWh x 0,2050 €/kWh hors TVA Redevance fixe 4,17 €/mois "
        "Tarif de réseau de distribution 3 200 kWh x 0,1100 €/kWh"
    ), urls=(), source="facture.pdf")
    terms = read_contract_terms([chunk], ("electricity",))
    assert terms["electricity"]["price"]["value"] == Decimal("0.2050")
    assert terms["electricity"]["price"]["vat"] == "excl"
    assert terms["electricity"]["standing_charge"]["value"] == Decimal("4.17")
    assert terms["electricity"]["standing_charge"]["unit"] == "per_month"
    assert terms["postcode"]["region"] == "wallonia"


@pytest.mark.parametrize("postcode, region", [
    ("1000", "brussels"), ("1299", "brussels"), ("1300", "wallonia"), ("1500", "flanders"), ("3999", "flanders"),
    ("4000", "wallonia"), ("7999", "wallonia"), ("8000", "flanders"), ("9990", "flanders"), ("0000", None), ("12", None),
])
def test_region_for_postcode(postcode, region):
    assert region_for_postcode(postcode) == region


# --- the yearly comparison --------------------------------------------------------------------------

def test_saving_on_a_single_meter_bill():
    result = _bill("27-nl-contract-enkelvoudige-meter.pdf")
    electricity = result["energies"]["electricity"]
    assert electricity["status"] == "compared"
    assert electricity["usage_kwh"] == "3500"
    # 3.500 kWh × 0,2100 × 1,06 + 40,00 × 1,06 = 779,10 + 42,40
    assert electricity["current"]["price_per_kwh_eur"] == "0.2226"
    assert electricity["current"]["fixed_fee_eur_per_year"] == "42.40"
    assert electricity["current"]["yearly_eur"] == "821.50"
    assert electricity["current"]["price_source"] == "invoice"
    assert electricity["current"]["evidence"]
    # Beta: 3.500 × 0,14 + 75 = 565,00; Gamma 3.500 × 0,18 + 48 = 678,00; Alfa 690,00; Delta is unverified.
    assert [offer["yearly_eur"] for offer in electricity["offers"]] == ["565.00", "678.00", "690.00"]
    assert electricity["best"]["supplier"] == "Beta" and electricity["best"]["duration_months"] == 36
    assert electricity["best_by_type"]["variable"]["supplier"] == "Gamma"
    assert electricity["saving_eur_per_year"] == "256.50"
    assert electricity["saving_eur_per_month"] == "21.38"
    assert result["total"] == {"saving_eur_per_year": "256.50", "saving_eur_per_month": "21.38"}
    assert result["region"] == "flanders" and result["tariffs_as_of"] == "2026-10-19"
    assert electricity["warnings"] == []


def test_day_night_meter_is_not_compared_but_gas_is():
    result = _bill("28-nl-contract-dag-nacht-en-gas.pdf")
    assert result["energies"]["electricity"]["status"] == "not_comparable"
    assert result["energies"]["electricity"]["reason"] == "day_night_meter"
    assert result["energies"]["electricity"]["saving_eur_per_year"] is None
    gas = result["energies"]["gas"]
    # 12.000 × 0,095 + 5,00 × 12 = 1.200,00 against Beta Gas 12.000 × 0,078 + 35 = 971,00
    assert gas["current"]["yearly_eur"] == "1200.00"
    assert gas["best"]["product"] == "Beta Gas Vast"
    assert gas["saving_eur_per_year"] == "229.00"
    assert result["total"]["saving_eur_per_year"] == "229.00"


def test_region_without_verified_cards_shows_no_saving():
    result = _bill("29-nl-contract-brussel.pdf")
    electricity = result["energies"]["electricity"]
    assert result["region"] == "brussels"
    assert electricity["status"] == "not_comparable" and electricity["reason"] == "region_not_covered"
    assert electricity["offers"] == [] and result["total"] is None
    assert "BruSim" in electricity["message"]


def test_day_night_consumption_rows_mean_a_day_night_meter():
    result = _bill("13-nl-jaarafrekening-nieuw-voorschot-tabel.pdf")
    assert result["energies"]["electricity"]["reason"] == "day_night_meter"
    gas = result["energies"]["gas"]
    assert gas["status"] == "offers_only" and gas["reason"] == "no_current_price"
    assert gas["offers"] and gas["saving_eur_per_year"] is None
    assert "region_unknown" in [warning["code"] for warning in gas["warnings"]]


def test_advance_bill_has_no_yearly_usage():
    result = _bill("11-nl-voorschot-domiciliering.pdf")
    assert {entry["reason"] for entry in result["energies"].values()} == {"no_usage"}


def test_gas_in_cubic_metres_needs_kwh_and_a_typed_usage_is_used():
    assert _bill("15-nl-jaarafrekening-gas-m3.pdf")["energies"]["gas"]["reason"] == "gas_in_m3"
    typed = _bill("15-nl-jaarafrekening-gas-m3.pdf", usage_kwh={"gas": "12190"})["energies"]["gas"]
    assert typed["usage_kwh"] == "12190" and typed["status"] == "offers_only"


def test_price_from_the_products_tariff_card_and_the_current_card_is_not_the_best_offer():
    result = _text(
        "Leverancier: Alfa Leveringsadres: Teststraat 1, 8500 Teststad Factuurperiode: 01/01/2025 tot 31/12/2025 "
        "Product: Alfa Flex Elektriciteitsverbruik: 1.000 kWh Totale energiekosten € 400,00"
    )
    electricity = result["energies"]["electricity"]
    assert electricity["current"]["price_source"] == "tariff_card"
    assert electricity["current"]["yearly_eur"] == "215.00"  # 1.000 × 0,19 + 25
    assert [offer["supplier"] for offer in electricity["offers"] if offer["is_current"]] == ["Alfa"]
    assert electricity["best"]["supplier"] == "Beta"  # 1.000 × 0,14 + 75 = 215,00, a tie: no saving
    assert electricity["status"] == "already_cheapest"
    assert "price_from_tariff_card" in [warning["code"] for warning in electricity["warnings"]]


def test_unknown_vat_is_taken_as_incl_and_said():
    result = _text(
        "Leveringsadres: Teststraat 1, 2000 Teststad Factuurperiode: 01/01/2025 tot 31/12/2025 "
        "Energieprijs 2.000 kWh x 0,2500 €/kWh Vaste vergoeding € 50,00 per jaar Elektriciteitsverbruik: 2.000 kWh"
    )
    electricity = result["energies"]["electricity"]
    assert electricity["current"]["yearly_eur"] == "550.00"  # 2.000 × 0,25 + 50, no btw added
    assert electricity["warnings"][0]["code"] == "vat_assumed_incl"
    assert electricity["saving_eur_per_year"] == "195.00"  # 550 − Beta 2.000 × 0,14 + 75 = 355 (Gamma 408, Alfa 405)


def test_part_of_a_year_is_scaled_to_a_year():
    result = _text(
        "Leveringsadres: Teststraat 1, 2000 Teststad Factuurperiode: 01/01/2026 tot 30/06/2026 "
        "Energieprijs 1.500 kWh x 0,2000 €/kWh (incl. btw) Vaste vergoeding € 20,00 Elektriciteitsverbruik: 1.500 kWh"
    )
    electricity = result["energies"]["electricity"]
    assert electricity["usage_kwh"] == "3025"  # 1.500 × 365 / 181
    assert "usage_annualised" in [warning["code"] for warning in electricity["warnings"]]
    assert electricity["current"]["fixed_fee_eur_per_year"] == "40.33"  # € 20 for 181 days


def test_the_real_tariff_cards_load_and_compare():
    result = check_invoice_contract(
        load_pdf_chunks(FIXTURES / "27-nl-contract-enkelvoudige-meter.pdf", source="27.pdf"), local_contracts()
    )
    electricity = result["energies"]["electricity"]
    assert electricity["status"] == "compared" and electricity["offers"]
    assert all(offer["source_url"] for offer in electricity["offers"])
    assert Decimal(electricity["saving_eur_per_year"]) > 0


# --- the layout suppliers publish as their sample bill ---------------------------------------------

def test_supplier_table_layout_takes_the_current_product_after_a_change():
    chunks = load_pdf_chunks(FIXTURES / "30-nl-contract-tabel-productwissel.pdf", source="30.pdf")
    terms = read_contract_terms(chunks, ("electricity",))
    electricity = terms["electricity"]
    assert electricity["product"]["value"] == "Kanaal Groen Plus Elektriciteit"  # from "Uw huidig contract"
    # The "(vanaf 01.04.2025)" product, not the earlier Kanaal Basis line or green-power, WKK or network rows.
    assert electricity["price"]["value"] == Decimal("0.1750000")
    assert electricity["price"]["vat"] == "excl"  # from the column header "Eenheidsprijs (excl. btw)"
    assert electricity["standing_charge"]["value"] == Decimal("60.00")
    assert electricity["standing_charge"]["unit"] == "per_year" and electricity["standing_charge"]["vat"] == "excl"
    assert terms["postcode"]["value"] == "3500" and terms["postcode"]["delivery"] is True  # "Leveradres:"

    result = _bill("30-nl-contract-tabel-productwissel.pdf")["energies"]["electricity"]
    # 3.400 × 0,1750 × 1,06 + 60,00 × 1,06 = 630,70 + 63,60; Beta 3.400 × 0,14 + 75 = 551,00
    assert result["current"]["yearly_eur"] == "694.30"
    assert result["saving_eur_per_year"] == "143.30"


def test_short_format_reads_the_tariff_line_and_says_the_fee_is_missing():
    chunks = load_pdf_chunks(FIXTURES / "31-nl-contract-kort-formaat.pdf", source="31.pdf")
    terms = read_contract_terms(chunks, ("electricity",))
    assert terms["electricity"]["product"]["value"] == "Kanaal Actief elektriciteit"
    assert terms["electricity"]["product"]["contract_type"] == "variable"  # "Variabel tarief …"
    assert terms["postcode"]["value"] == "2800"  # the address after the EAN
    result = _bill("31-nl-contract-kort-formaat.pdf")["energies"]["electricity"]
    assert result["current"]["yearly_eur"] == "476.47"  # 3.100 × 0,1450 × 1,06, no fee on the bill
    assert result["status"] == "already_cheapest"  # Beta 3.100 × 0,14 + 75 = 509,00
    assert "fee_not_found" in [warning["code"] for warning in result["warnings"]]


# --- the V-check code (Energiebesluit art. 3.2.18, 20°: on Flemish bills since 1 July 2025) ----------

def test_v_check_code_and_link_come_with_the_yearly_usage_to_type_in():
    result = _bill("30-nl-contract-tabel-productwissel.pdf")
    assert result["v_check"] == {
        "url": "https://www.vtest.be/v-check",
        "codes": [{"code": "KNGP250400030125", "energy": "electricity", "page": 1, "usage_kwh": "3400"}],
    }
    # "Unieke identificatiecode van uw product: …" names the code, not the product.
    assert result["energies"]["electricity"]["current"]["product"] == "Kanaal Groen Plus Elektriciteit"


def test_no_v_check_without_a_labelled_code_or_link():
    assert _bill("27-nl-contract-enkelvoudige-meter.pdf")["v_check"] is None
    # A 16-character number without its label (here a customer number) is never taken for the code.
    assert _text(
        "Leveringsadres: Teststraat 1, 2000 Teststad Klantnummer: ABCD1234 56789012 Energieprijs 2.000 kWh x "
        "0,2500 €/kWh (incl. btw) Elektriciteitsverbruik: 2.000 kWh"
    )["v_check"] is None


def test_v_check_per_energy_and_a_link_without_scheme():
    result = _text(
        "Leveringsadres: Teststraat 1, 2000 Teststad Factuurperiode: 01/01/2025 tot 31/12/2025 "
        "Elektriciteit V-check-code: AB12CD34-5678-9012 Elektriciteitsverbruik: 3.000 kWh "
        "Aardgas Unieke identificatiecode AB12EF56 1111 2222 Gasverbruik: 10.000 kWh "
        "Vergelijk op vtest.be/v-check of scan de QR-code."
    )
    assert result["v_check"]["codes"] == [
        {"code": "AB12CD3456789012", "energy": "electricity", "page": 1, "usage_kwh": "3000"},
        {"code": "AB12EF5611112222", "energy": "gas", "page": 1, "usage_kwh": "10000"},
    ]
    assert result["v_check"]["url"] == "https://vtest.be/v-check"


def test_no_v_check_outside_flanders():
    assert _text(
        "Leveringsadres: Teststraat 1, 1050 Teststad V-check-code: AB12CD34 5678 9012 Elektriciteitsverbruik: 3.000 kWh"
    )["v_check"] is None
