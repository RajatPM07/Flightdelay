"""
Carrier-designator resolution: IATA (2-char) -> ICAO (3-char).

Why this exists
---------------
Customers enter the IATA ident printed on their ticket ("6E1341"), but AeroAPI's
/flights and /alerts endpoints resolve flights by their ICAO ident ("IGO1341").
A query by the IATA ident returns zero flights, which would silently produce an
empty baseline at issuance. We translate the *carrier designator* and leave the
flight number untouched: 6E1341 -> IGO1341.

This is a bounded reference problem — there are ~1,400 carriers worldwide, not
millions of flights. The table below is a curated MVP subset weighted toward the
Indian market and its common international/Gulf connections. Phase 2 should swap
this for the full published IATA/ICAO dataset plus an AeroAPI /operators fallback
(cached) for long-tail carriers.

Pure module: no I/O, no vendor SDK — safe to unit-test in isolation.
"""
from __future__ import annotations

import re

# IATA 2-char airline designator -> ICAO 3-char designator.
# Sources: published IATA/ICAO airline designator lists.
IATA_TO_ICAO: dict[str, str] = {
    # --- India ---
    "6E": "IGO",  # IndiGo
    "AI": "AIC",  # Air India
    "IX": "AXB",  # Air India Express
    "UK": "VTI",  # Vistara
    "SG": "SEJ",  # SpiceJet
    "QP": "AKJ",  # Akasa Air
    "9I": "LLR",  # Alliance Air
    # --- Gulf / Middle East ---
    "EK": "UAE",  # Emirates
    "EY": "ETD",  # Etihad Airways
    "QR": "QTR",  # Qatar Airways
    "FZ": "FDB",  # flydubai
    "G9": "ABY",  # Air Arabia
    "WY": "OMA",  # Oman Air
    "GF": "GFA",  # Gulf Air
    "SV": "SVA",  # Saudia
    "KU": "KAC",  # Kuwait Airways
    "RJ": "RJA",  # Royal Jordanian
    "ME": "MEA",  # Middle East Airlines
    "MS": "MSR",  # EgyptAir
    # --- Asia-Pacific ---
    "SQ": "SIA",  # Singapore Airlines
    "CX": "CPA",  # Cathay Pacific
    "TG": "THA",  # Thai Airways
    "MH": "MAS",  # Malaysia Airlines
    "NH": "ANA",  # All Nippon Airways
    "JL": "JAL",  # Japan Airlines
    "QF": "QFA",  # Qantas
    "UL": "ALK",  # SriLankan Airlines
    "BG": "BBC",  # Biman Bangladesh
    "TK": "THY",  # Turkish Airlines
    # --- Europe ---
    "LH": "DLH",  # Lufthansa
    "BA": "BAW",  # British Airways
    "AF": "AFR",  # Air France
    "KL": "KLM",  # KLM
    "LX": "SWR",  # Swiss
    "VS": "VIR",  # Virgin Atlantic
    "AY": "FIN",  # Finnair
    "IB": "IBE",  # Iberia
    # --- Americas ---
    "UA": "UAL",  # United Airlines
    "AA": "AAL",  # American Airlines
    "DL": "DAL",  # Delta Air Lines
    "AC": "ACA",  # Air Canada
    # --- Africa ---
    "ET": "ETH",  # Ethiopian Airlines
    "KQ": "KQA",  # Kenya Airways
    "SA": "SAA",  # South African Airways
}

# A flight ident is <designator><number>, optionally with a trailing letter on
# the number (e.g. AI191A). The number must be digit-led, which lets the regex
# disambiguate a 2-char IATA designator from a 3-char ICAO one by backtracking.
_IDENT_RE = re.compile(r"^([A-Z0-9]{2,3}?)(\d{1,4}[A-Z]?)$")


def normalize_flight_number(number: str) -> str:
    """Canonical form for flight-number matching: strip spaces, uppercase."""
    return number.replace(" ", "").upper()


def resolve_icao_ident(flight_number: str) -> str:
    """
    Return the ICAO ident AeroAPI expects, given a customer-entered flight number.

    - Known 2-char IATA designator      -> mapped ICAO designator + number.
    - Unknown 2-char designator         -> compacted ident unchanged (provider 404s).
    - 3-char designator                 -> assumed already ICAO, passed through.
    - Registration / unparseable input  -> returned verbatim, untouched.
    """
    raw = flight_number.strip()
    compact = raw.replace(" ", "").upper()

    match = _IDENT_RE.match(compact)
    if match is None:
        # Tail number, registration, or anything we can't confidently split.
        return raw

    designator, number = match.group(1), match.group(2)

    if len(designator) == 2:
        icao = IATA_TO_ICAO.get(designator)
        return f"{icao}{number}" if icao else f"{designator}{number}"

    # 3-char designator — already ICAO.
    return f"{designator}{number}"
