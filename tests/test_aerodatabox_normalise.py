import json, pathlib
import pytest
from app.providers.flightdata.aerodatabox import AeroDataBoxProvider

FIXTURE = json.loads((pathlib.Path(__file__).parent / "fixtures/aerodatabox_flight.json").read_text())


def _p():
    return AeroDataBoxProvider(api_key="k", base_url="https://x",
                               rapidapi_host="h", webhook_secret="s", public_webhook_base_url="https://pub")


def test_normalise_maps_scheduled_arrival():
    s = _p().normalise(FIXTURE)
    assert s.scheduled_in_utc.isoformat() == "2026-06-05T20:10:00+00:00"
    assert s.content_hash
    assert isinstance(s.cancelled, bool) and isinstance(s.diverted, bool) and isinstance(s.departed, bool)


def test_normalise_estimated_from_revised():
    s = _p().normalise(FIXTURE)
    assert s.estimated_in_utc.isoformat() == "2026-06-05T20:10:00+00:00"


def test_extract_subject_number_normalised_and_origin_date():
    subj = _p().extract_subject(FIXTURE)
    assert subj == ("6E1341", "2026-06-05")  # space stripped, origin-local departure date


def test_normalise_unwraps_webhook_envelope():
    # The adapter must also accept a notification that wraps the flight under a key.
    s = _p().normalise({"data": FIXTURE})
    assert s.scheduled_in_utc.isoformat() == "2026-06-05T20:10:00+00:00"


def test_cancelled_status_sets_flag():
    f = dict(FIXTURE); f["status"] = "Canceled"
    assert _p().normalise(f).cancelled is True


def test_arrived_status_sets_actual_in():
    f = dict(FIXTURE); f["status"] = "Arrived"
    s = _p().normalise(f)
    assert s.actual_in_utc is not None
    assert s.departed is True


def test_diverted_status_sets_flags():
    f = dict(FIXTURE); f["status"] = "Diverted"
    s = _p().normalise(f)
    assert s.diverted is True
    assert s.departed is True


def test_enroute_without_actual_departure_is_not_departed():
    # AeroDataBox's free-tier `status` can read "EnRoute" while the flight has NOT left
    # (no actual/runway departure time, scheduled departure still in the future).
    # Trust the concrete timestamp, not the label — symmetric with FlightAware's actual_off.
    import copy
    f = copy.deepcopy(FIXTURE)
    f["status"] = "EnRoute"
    f["departure"].pop("actualTime", None)
    f["departure"].pop("runwayTime", None)
    assert _p().normalise(f).departed is False


def test_departed_when_actual_departure_time_present():
    import copy
    f = copy.deepcopy(FIXTURE)
    f["status"] = "EnRoute"
    f["departure"]["runwayTime"] = {"utc": "2026-06-05 17:10Z", "local": "2026-06-05 22:40+05:30"}
    assert _p().normalise(f).departed is True


def test_missing_scheduled_arrival_raises():
    import copy
    f = copy.deepcopy(FIXTURE); f["arrival"].pop("scheduledTime", None)
    with pytest.raises(ValueError):
        _p().normalise(f)


def test_no_estimate_when_revised_and_predicted_absent():
    import copy
    f = copy.deepcopy(FIXTURE)
    f["arrival"].pop("revisedTime", None); f["arrival"].pop("predictedTime", None)
    assert _p().normalise(f).estimated_in_utc is None


def test_extract_subject_none_when_number_absent():
    f = dict(FIXTURE); f.pop("number", None)
    assert _p().extract_subject(f) is None
