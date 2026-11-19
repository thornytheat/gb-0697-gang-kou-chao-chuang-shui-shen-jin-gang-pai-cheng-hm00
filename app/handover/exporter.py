"""M8 证据 CSV 导出：可逐船核对班次、交接、未采用排法与占班台账。"""
import csv
import io
import json
from app.timeops import display_full
from . import storage


def _w(row):
    return json.dumps(row, ensure_ascii=False, sort_keys=True)


def handover_evidence_csv(ident):
    run = storage.get_run(ident)
    result = run["result"]
    records = {r["ship_id"]: r for r in storage.list_records(ident)}
    bookings = storage.list_bookings(ident)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["run_id", "sha256", "rule_version", "section", "line",
                "code", "evidence"])
    w.writerow([ident, run["digest"], result.get("rule_version"), "manifest", "",
                "", _w({"balance": result["balance"]})])

    # 始终输出各证据段标题，保证空批次也能逐段核对
    w.writerow([ident, run["digest"], result.get("rule_version"),
                "section_index", "", "",
                _w({"sections": ["schedule", "shift", "handover_event",
                                  "pending", "rejected_plan", "quarantine",
                                  "pilot_booking"]})])

    for ship in result["schedule"]:
        w.writerow([ident, run["digest"], result.get("rule_version"),
                    "schedule", ship.get("line", ""), ship["status"], _w(ship)])
        rec = records.get(ship["id"])
        if rec:
            for shift in rec["shifts"]:
                w.writerow([ident, run["digest"], result.get("rule_version"),
                            "shift", ship.get("line", ""), shift["state"],
                            _w({"ship_id": ship["id"], "seq": shift["seq"],
                                "pilot": shift["pilot"],
                                "start": display_full(shift["start_s"]),
                                "end": display_full(shift["end_s"]),
                                "revision": shift["revision"]})])
            for ev in rec["events"]:
                w.writerow([ident, run["digest"], result.get("rule_version"),
                            "handover_event", ship.get("line", ""), ev["action"],
                            _w({"ship_id": ship["id"], **ev,
                                "at": display_full(ev["at_s"])})])

    for ship in result["pending"]:
        w.writerow([ident, run["digest"], result.get("rule_version"),
                    "pending", ship.get("line", ""), "MISSED_TIDE_WINDOW",
                    _w(ship)])

    for plan in result["rejected_plans"]:
        w.writerow([ident, run["digest"], result.get("rule_version"),
                    "rejected_plan", "", plan["reason"], _w(plan)])

    for q in result["quarantine"]:
        w.writerow([ident, run["digest"], result.get("rule_version"),
                    "quarantine", q.get("line", ""), q["reason"], _w(q)])

    for b in bookings:
        w.writerow([ident, run["digest"], result.get("rule_version"),
                    "pilot_booking", "", b["pilot"],
                    _w({**b, "start": display_full(b["start_s"]),
                        "end": display_full(b["end_s"])})])
    return "﻿" + out.getvalue()
