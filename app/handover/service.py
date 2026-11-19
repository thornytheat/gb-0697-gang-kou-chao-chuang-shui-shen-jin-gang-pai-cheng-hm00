"""M8 引航交接服务入口与证据组装。"""
import hashlib
from app.config import RULE_VERSION
from app.errors import BusinessError
from app.timeops import display_full
from .parser import parse
from .planner import plan_all
from .handover import initial_shift_record
from . import storage


def digest_of(text):
    return hashlib.sha256((RULE_VERSION + "\n" + text).encode()).hexdigest()


def _display_legs(plan):
    rows = []
    for idx, leg in enumerate(plan["legs"], start=1):
        rows.append({"leg": idx, "pilot": leg["pilot"],
                     "start": display_full(leg["start_s"]),
                     "end": display_full(leg["end_s"])})
    return rows


def build_result(text):
    quarantine_list = []
    voyages, pilots, bookings, anchorages, blanks = parse(text, quarantine_list)
    scheduled, pending = plan_all(voyages, pilots, bookings, anchorages)

    schedule_rows, rejected_rows, pending_rows = [], [], []
    for ship in scheduled:
        plan = ship["plan"]
        schedule_rows.append({
            "id": ship["id"], "status": "SCHEDULED", "line": ship["line"],
            "kind": plan["kind"],
            "start_s": plan["start_s"], "end_s": plan["end_s"],
            "start": display_full(plan["start_s"]), "end": display_full(plan["end_s"]),
            "anchorage": plan.get("anchorage") or "—",
            "stop_minutes": round(plan["stop_s"] / 60),
            "stop_window": (display_full(plan["stop_start_s"]) + " → " +
                            display_full(plan["stop_end_s"]))
            if plan["kind"] == "SPLIT" else "无停航",
            "tide_deadline": display_full(ship["deadline_s"]),
            "slack_minutes": round(plan["tide_slack_s"] / 60),
            "legs": _display_legs(plan),
        })
        for r in ship["rejected_plans"]:
            rejected_rows.append({"ship_id": ship["id"], **r})
    for ship in pending:
        pending_rows.append({
            "id": ship["id"], "status": "PENDING", "line": ship["line"],
            "missed_tide_window": ship["missed_tide_window"],
            "missed_tide_window_end": display_full(ship["deadline_s"]),
            "earliest": display_full(ship["earliest_s"]),
            "transit_minutes": round(ship["transit_s"] / 60),
            "rejected_plans": ship["rejected_plans"],
        })
        for r in ship["rejected_plans"]:
            rejected_rows.append({"ship_id": ship["id"], **r})

    return {
        "module": 8,
        "rule_version": RULE_VERSION,
        "balance": {
            "voyages": len(voyages), "pilots": len(pilots),
            "bookings": len(bookings), "anchorages": len(anchorages),
            "quarantined": len(quarantine_list), "blank": blanks,
            "scheduled": len(scheduled), "pending": len(pending),
        },
        "schedule": schedule_rows,
        "pending": pending_rows,
        "rejected_plans": rejected_rows,
        "quarantine": quarantine_list,
        # 供交接状态机与台账使用的机器可读明细
        "_scheduled_raw": scheduled,
        "_pending_raw": pending,
    }


def execute(text, persist=True):
    result = build_result(text)
    digest = digest_of(text)
    ident = digest[:16]
    run = None
    if persist:
        run = storage.save_run(ident, digest, result)
        # 幂等占班：唯一约束保证重跑同一批次不重复登记
        storage.register_run_bookings(ident, result["_scheduled_raw"])
    return {"run_id": ident, "digest": digest, "result": result, "run": run}


def init_handover_records(ident):
    """为批次内每条 SPLIT 航次建立交接记录（已存在则跳过）。"""
    run = storage.get_run(ident)
    records = {}
    for ship in run["result"]["_scheduled_raw"]:
        if ship["plan"]["kind"] != "SPLIT":
            continue
        try:
            rec = storage.get_record(ident, ship["id"])
        except BusinessError:
            rec = initial_shift_record(ship)
            storage.upsert_record(ident, ship["id"], rec)
        records[ship["id"]] = rec
    return records
