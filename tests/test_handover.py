import os
from pathlib import Path
os.environ["DATA_DIR"] = "/tmp/handover-pytest"
import pytest

from app.handover.domain import (
    Voyage, Pilot, Booking, Anchorage, PilotCalendar, Channel, overlap)
from app.handover.planner import plan_all, plan_voyage
from app.handover.handover import (
    initial_shift_record, apply_event, HandoverError,
    PLANNED, IN_PROGRESS, INTERRUPTED, COMPLETED, CURRENT, PENDING, SUPERSEDED)

E = 1795118400  # 2026-11-19 20:00 UTC
D = E + 160 * 60  # 22:40 潮窗截止


def voyage(leg=70, stop=20, earliest=E - 20 * 60, deadline=D):
    row = {"id": "V1"}
    return Voyage(row, earliest, deadline, leg * 60, stop * 60, 1)


def pilots():
    return [
        Pilot("P1", E - 30 * 60, E + 90 * 60, 2),
        Pilot("P2", E + 50 * 60, E + 230 * 60, 3),
        Pilot("P3", E - 30 * 60, E + 70 * 60, 4),
    ]


def anchorage():
    return [Anchorage("A1", E - 20 * 60, E + 140 * 60, 5)]


def test_half_open_intervals_do_not_conflict_at_endpoint():
    assert overlap((0, 10), (10, 20)) is False
    assert overlap((0, 10), (9, 20)) is True


def test_full_plan_rejected_when_no_pilot_covers_whole_voyage():
    chosen, rejected, tide = plan_voyage(voyage(), pilots(), [], anchorage(),
                                         Channel(anchorage()))
    # 没有任何引航员窗口能覆盖 160 分钟全程
    assert all(r["kind"] == "FULL" for r in rejected if r["kind"] == "FULL")
    assert any(r["reason"] == "PILOT_WINDOW_MISMATCH" for r in rejected)


def test_split_plan_uses_two_pilots_and_meets_tide_window():
    chosen, rejected, tide = plan_voyage(voyage(), pilots(), [], anchorage(),
                                         Channel(anchorage()))
    assert chosen["kind"] == "SPLIT"
    assert [l["pilot"] for l in chosen["legs"]] == ["P1", "P2"]
    assert chosen["stop_s"] == 20 * 60
    assert chosen["end_s"] <= D
    assert chosen["tide_slack_s"] == D - chosen["end_s"]


def test_stop_occupies_channel_so_second_ship_is_blocked():
    # 同一日历跨船复用：第一艘占走 P1/P2 同时段与锚地停航块
    ch = Channel(anchorage())
    from app.handover.domain import PilotCalendar
    calendar = PilotCalendar(pilots(), [])
    chosen1, _, _ = plan_voyage(voyage(), pilots(), [], anchorage(), ch, calendar)
    assert chosen1
    row2 = {"id": "V2"}
    v2 = Voyage(row2, E - 20 * 60, D, 70 * 60, 20 * 60, 6)
    chosen2, rejected2, _ = plan_voyage(v2, pilots(), [], anchorage(), ch, calendar)
    # 同一引航员不能同时接两艘船：可行组合被双班冲突耗尽
    assert chosen2 is None
    assert any(r["reason"] == "PILOT_DOUBLE_BOOKED" for r in rejected2)


def test_existing_booking_blocks_pilot():
    booking = [Booking("B1", "P2", E + 80 * 60, E + 200 * 60, 7)]
    chosen, rejected, _ = plan_voyage(voyage(), pilots(), booking, anchorage(),
                                      Channel(anchorage()))
    # P2 后段被既有占班挡住：凡用 P2 执飞后段的候选都因双班冲突被拒
    p2_leg2 = [r for r in rejected
               if r["detail"].get("pilots", [None, None])[1] == "P2"]
    assert p2_leg2 and all(r["reason"] == "PILOT_DOUBLE_BOOKED" for r in p2_leg2)


def test_voyage_that_cannot_meet_tide_is_pending_with_window():
    row = {"id": "VLATE"}
    late = Voyage(row, E, D, 100 * 60, 20 * 60, 8)  # 最早 20:00，需 220 分钟
    scheduled, pending = plan_all([late], pilots(), [], anchorage())
    assert scheduled == [] and len(pending) == 1
    p = pending[0]
    assert p["status"] == "PENDING"
    assert p["missed_tide_window"]["end_s"] == D
    assert p["rejected_plans"]


def test_physically_too_slow_voyage_records_missed_tide_reason():
    # 引航员窗口足够长，纯粹因全程/半程总耗时越过潮窗而待定
    long_window = [Pilot("PX", E - 60 * 60, E + 400 * 60, 2)]
    row = {"id": "VSLOW"}
    slow = Voyage(row, E, D, 100 * 60, 20 * 60, 9)  # 220 分钟 > 160 分钟窗
    scheduled, pending = plan_all([slow], long_window, [], anchorage())
    assert scheduled == []
    p = pending[0]
    assert p["missed_tide_window"]["reason"] == "NO_PLAN_MEETS_TIDE"
    assert any(r["reason"].endswith("MISSES_TIDE_WINDOW")
               for r in p["rejected_plans"])


def test_handover_interrupt_keeps_old_shift_until_completion():
    chosen, _, _ = plan_voyage(voyage(), pilots(), [], anchorage(),
                               Channel(anchorage()))
    ship = {"id": "V1", "plan": chosen}
    rec = initial_shift_record(ship)
    assert [s["state"] for s in rec["shifts"]] == [CURRENT, PENDING]

    rec, _ = apply_event(rec, "E1", "BEGIN", chosen["stop_start_s"])
    assert rec["status"] == IN_PROGRESS
    rec, _ = apply_event(rec, "E2", "INTERRUPT", chosen["stop_start_s"] + 60)
    assert rec["status"] == INTERRUPTED
    # 中断：旧版班次保留恢复，新班次不生效
    assert rec["shifts"][0]["state"] == CURRENT
    assert rec["shifts"][1]["state"] == PENDING

    with pytest.raises(HandoverError):
        apply_event(rec, "E3", "COMPLETE", chosen["stop_end_s"])

    rec, _ = apply_event(rec, "E4", "RESUME", chosen["stop_start_s"] + 120)
    rec, _ = apply_event(rec, "E5", "COMPLETE", chosen["stop_end_s"])
    assert rec["status"] == COMPLETED
    assert [s["state"] for s in rec["shifts"]] == [SUPERSEDED, CURRENT]


def test_handover_event_is_idempotent():
    chosen, _, _ = plan_voyage(voyage(), pilots(), [], anchorage(),
                               Channel(anchorage()))
    rec = initial_shift_record({"id": "V1", "plan": chosen})
    rec, applied_first = apply_event(rec, "E1", "BEGIN", chosen["stop_start_s"])
    rec, applied_replay = apply_event(rec, "E1", "BEGIN", chosen["stop_start_s"])
    assert applied_first is True and applied_replay is False
    assert len(rec["events"]) == 1


def test_anchorage_window_must_fit_stop_duration():
    small = [Anchorage("A1", E, E + 10 * 60, 5)]  # 只有 10 分钟，停航需 20
    chosen, rejected, _ = plan_voyage(voyage(), pilots(), [], small,
                                      Channel(small))
    assert chosen is None
    assert any(r["reason"] == "SPLIT_NO_ANCHORAGE_SLOT" for r in rejected)
