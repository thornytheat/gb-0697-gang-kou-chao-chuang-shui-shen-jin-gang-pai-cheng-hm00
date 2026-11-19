import os
from pathlib import Path
os.environ["DATA_DIR"] = "/tmp/handover-svc-pytest"
import pytest

from app.handover.parser import parse
from app.handover import service, storage
from app.handover.exporter import handover_evidence_csv
from app.errors import BusinessError

SAMPLE = (Path(__file__).parent.parent / "sample_data" / "handover.csv") \
    .read_text(encoding="utf-8-sig")


@pytest.fixture
def text():
    return SAMPLE


def test_parser_isolates_bad_rows_and_unknown_pilot():
    bad = (SAMPLE +
           "X1,unknown,2026-11-19T20:00:00+00:00,2026-11-19T21:00:00+00:00,1,,1,m\n"
           "BX,booking,2026-11-19T20:00:00+00:00,2026-11-19T21:00:00+00:00,GHOST,,1,m\n")
    q = []
    voyages, pilots, bookings, anchorages, blanks = parse(bad, q)
    reasons = {x["reason"] for x in q}
    assert "KIND" in reasons and "UNKNOWN_PILOT" in reasons
    assert all(b.pid in {p.id for p in pilots} for b in bookings)


def test_sample_outcome_one_split_two_pending(text):
    r = service.build_result(text)
    assert r["balance"]["scheduled"] == 1
    assert r["balance"]["pending"] == 2
    sched = r["schedule"][0]
    assert sched["id"] == "V9001" and sched["kind"] == "SPLIT"
    assert sched["stop_minutes"] == 20
    assert [l["pilot"] for l in sched["legs"]] == ["P1", "P2"]
    pending_ids = {p["id"] for p in r["pending"]}
    assert pending_ids == {"V9002", "V9003"}
    assert all(p["missed_tide_window"]["end_s"] for p in r["pending"])


def test_rejected_plans_are_all_recorded(text):
    r = service.build_result(text)
    ships = {x["ship_id"] for x in r["rejected_plans"]}
    assert ships == {"V9001", "V9002", "V9003"}
    assert any(x["reason"] == "PILOT_DOUBLE_BOOKED" for x in r["rejected_plans"])


def test_persistent_run_idempotent_and_no_duplicate_booking(text):
    a = service.execute(text)
    before = storage.list_bookings(a["run_id"])
    service.execute(text)
    service.execute(text)
    after = storage.list_bookings(a["run_id"])
    assert before == after
    keys = [(b["run_id"], b["ship_id"], b["pilot"], b["start_s"]) for b in after]
    assert len(keys) == len(set(keys))


def test_handover_records_retain_shift_versions(text):
    outcome = service.execute(text)
    rid = outcome["run_id"]
    service.init_handover_records(rid)
    rec = storage.get_record(rid, "V9001")
    rec, _ = __import__("app.handover.handover", fromlist=["apply_event"]) \
        .apply_event(rec, "T1", "BEGIN", rec["stop_start_s"])
    storage.upsert_record(rid, "V9001", rec)
    again = storage.get_record(rid, "V9001")
    assert again["events"][0]["event_id"] == "T1"
    assert again["shifts"][0]["state"] == "CURRENT"


def test_evidence_csv_covers_every_required_section(text):
    outcome = service.execute(text)
    rid = outcome["run_id"]
    service.init_handover_records(rid)
    csv_text = handover_evidence_csv(rid)
    for section in ("manifest", "schedule", "shift", "handover_event",
                    "pending", "rejected_plan", "pilot_booking"):
        assert section in csv_text
    assert rid in csv_text


def test_evidence_csv_includes_quarantine_when_present(text, tmp_path):
    from app.handover import storage as storage_mod
    storage_mod.DB_PATH = str(tmp_path / "iso.sqlite")
    try:
        bad = text + "X1,unknown,2026-11-19T20:00:00+00:00,2026-11-19T21:00:00+00:00,1,,1,m\n"
        outcome = service.execute(bad)
        rid = outcome["run_id"]
        csv_text = handover_evidence_csv(rid)
        assert "quarantine" in csv_text
    finally:
        storage_mod.DB_PATH = None


def test_cross_run_double_booking_raises_409(text, tmp_path):
    from app.handover import storage as storage_mod
    storage_mod.DB_PATH = str(tmp_path / "cross.sqlite")
    try:
        outcome = service.execute(text)
        leg = outcome["result"]["_scheduled_raw"][0]["plan"]["legs"][0]
        with pytest.raises(BusinessError) as exc:
            storage_mod.register_booking("different-run", "OTHER", leg["pilot"],
                                         leg["start_s"], leg["end_s"])
        assert exc.value.status == 409 and exc.value.code == "DOUBLE_BOOKED"
    finally:
        storage_mod.DB_PATH = None
