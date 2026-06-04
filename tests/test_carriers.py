"""
Tests for IATA -> ICAO flight-ident resolution.

Customers type the IATA ident printed on their boarding pass (e.g. "6E1341"),
but AeroAPI resolves flights by their ICAO ident ("IGO1341"). The resolver
maps the *carrier designator* (2-char IATA -> 3-char ICAO) and preserves the
flight number untouched. It is a pure function — no I/O, easily unit-tested.
"""
from app.providers.flightdata.carriers import resolve_icao_ident


def test_known_iata_carrier_maps_to_icao():
    assert resolve_icao_ident("6E1341") == "IGO1341"
    assert resolve_icao_ident("AI131") == "AIC131"
    assert resolve_icao_ident("EK500") == "UAE500"
    assert resolve_icao_ident("QR556") == "QTR556"


def test_space_between_designator_and_number_is_handled():
    assert resolve_icao_ident("6E 1341") == "IGO1341"
    assert resolve_icao_ident("  AI 131 ") == "AIC131"


def test_lowercase_input_is_normalised():
    assert resolve_icao_ident("ai131") == "AIC131"
    assert resolve_icao_ident("6e1341") == "IGO1341"


def test_already_icao_passes_through():
    # 3-char designator is assumed to already be ICAO.
    assert resolve_icao_ident("IGO1341") == "IGO1341"
    assert resolve_icao_ident("UAE500") == "UAE500"


def test_unknown_iata_carrier_passes_through_compacted():
    # Unknown 2-char carrier: keep the ident (compacted), let the provider 404.
    assert resolve_icao_ident("ZZ999") == "ZZ999"
    assert resolve_icao_ident("ZZ 999") == "ZZ999"


def test_flight_number_with_trailing_letter_is_preserved():
    assert resolve_icao_ident("AI191A") == "AIC191A"


def test_registration_or_unparseable_passes_through_unchanged():
    # Tail numbers / registrations have no IATA carrier prefix — leave them be.
    assert resolve_icao_ident("VT-ABC") == "VT-ABC"
    assert resolve_icao_ident("N12345") == "N12345"


def test_digit_letter_iata_designator():
    # IndiGo's 6E and similar mixed alphanumeric designators must resolve.
    assert resolve_icao_ident("6E2043") == "IGO2043"
