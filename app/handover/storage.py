"""M8 持久化：交接状态机记录与引航员占班台账。

- handover_runs：同一输入摘要复用同一批次，重跑幂等。
- handover_records：按航次保存交接事件与班次版本（旧版 SUPERSEDED 留痕）。
- pilot_bookings：跨批次占班台账，(run_id, ship_id, pilot, start_s) 唯一，
  重跑同一批次不会重复占班；占班前校验半开区间重叠（同一引航员不能同时接两艘船）。
"""
import json
import sqlite3
from datetime import datetime, timezone
from app.config import DATA_DIR, RULE_VERSION
from app.errors import BusinessError
from .domain import overlap

# 测试可覆盖为独立数据库路径，避免用例间共享状态
DB_PATH = None


def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DB_PATH or (DATA_DIR / "runs.sqlite")
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute(
        "CREATE TABLE IF NOT EXISTS handover_runs ("
        "id TEXT PRIMARY KEY, digest TEXT NOT NULL, result TEXT NOT NULL, "
        "created TEXT NOT NULL)")
    db.execute(
        "CREATE TABLE IF NOT EXISTS handover_records ("
        "run_id TEXT NOT NULL, ship_id TEXT NOT NULL, record TEXT NOT NULL, "
        "updated TEXT NOT NULL, PRIMARY KEY(run_id, ship_id))")
    db.execute(
        "CREATE TABLE IF NOT EXISTS pilot_bookings ("
        "run_id TEXT NOT NULL, ship_id TEXT NOT NULL, pilot TEXT NOT NULL, "
        "start_s INTEGER NOT NULL, end_s INTEGER NOT NULL, "
        "PRIMARY KEY(run_id, ship_id, pilot, start_s))")
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_booking_pilot "
        "ON pilot_bookings(pilot, start_s, end_s)")
    return db


def save_run(ident, digest, result):
    with connect() as db:
        db.execute(
            "INSERT OR IGNORE INTO handover_runs(id,digest,result,created) "
            "VALUES(?,?,?,?)",
            (ident, digest, json.dumps(result, ensure_ascii=False),
             datetime.now(timezone.utc).isoformat()))
    return get_run(ident)


def get_run(ident):
    with connect() as db:
        row = db.execute("SELECT * FROM handover_runs WHERE id=?", (ident,)).fetchone()
    if row is None:
        raise BusinessError("NOT_FOUND", "交接批次不存在", 404)
    item = dict(row)
    item["result"] = json.loads(item["result"])
    item["rule_version"] = RULE_VERSION
    return item


def list_runs():
    with connect() as db:
        return [dict(r) for r in db.execute(
            "SELECT id,digest,created FROM handover_runs ORDER BY created DESC LIMIT 40")]


def list_records(ident):
    with connect() as db:
        rows = db.execute(
            "SELECT record FROM handover_records WHERE run_id=? ORDER BY ship_id",
            (ident,)).fetchall()
    return [json.loads(r["record"]) for r in rows]


def get_record(ident, ship_id):
    with connect() as db:
        row = db.execute(
            "SELECT record FROM handover_records WHERE run_id=? AND ship_id=?",
            (ident, ship_id)).fetchone()
    if row is None:
        raise BusinessError("NOT_FOUND", "该航次无交接记录", 404)
    return json.loads(row["record"])


def upsert_record(ident, ship_id, record):
    with connect() as db:
        db.execute(
            "INSERT INTO handover_records(run_id,ship_id,record,updated) "
            "VALUES(?,?,?,?) ON CONFLICT(run_id,ship_id) DO UPDATE SET "
            "record=excluded.record, updated=excluded.updated",
            (ident, ship_id, json.dumps(record, ensure_ascii=False),
             datetime.now(timezone.utc).isoformat()))


def booking_conflicts(pilot, start_s, end_s, exclude_run=None):
    """半开区间重叠的既有占班（端点相接不算冲突）。"""
    with connect() as db:
        rows = db.execute(
            "SELECT run_id,ship_id,pilot,start_s,end_s FROM pilot_bookings "
            "WHERE pilot=? AND start_s<? AND end_s>?",
            (pilot, end_s, start_s)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if exclude_run and d["run_id"] == exclude_run:
            continue
        if overlap((d["start_s"], d["end_s"]), (start_s, end_s)):
            out.append(d)
    return out


def register_booking(run_id, ship_id, pilot, start_s, end_s):
    """幂等占班：同批次同船同班重放为空操作；与其他占用冲突则 409。"""
    with connect() as db:
        existing = db.execute(
            "SELECT 1 FROM pilot_bookings WHERE run_id=? AND ship_id=? "
            "AND pilot=? AND start_s=?",
            (run_id, ship_id, pilot, int(start_s))).fetchone()
        if existing:
            return  # 幂等重放，不重复占班
        # 同批次内不同船的重叠也属于双班冲突
        conflicts = booking_conflicts(pilot, start_s, end_s)
        conflicts = [c for c in conflicts
                     if not (c["run_id"] == run_id and c["ship_id"] == ship_id)]
        if conflicts:
            raise BusinessError(
                "DOUBLE_BOOKED",
                f"引航员 {pilot} 时段已被占用: {conflicts[0]['ship_id']}", 409)
        db.execute(
            "INSERT INTO pilot_bookings"
            "(run_id,ship_id,pilot,start_s,end_s) VALUES(?,?,?,?,?)",
            (run_id, ship_id, pilot, int(start_s), int(end_s)))


def register_run_bookings(run_id, scheduled):
    """将一次计划的全部班次登记入台账，逐班幂等。"""
    booked = []
    for ship in scheduled:
        for leg in ship["plan"]["legs"]:
            register_booking(run_id, ship["id"], leg["pilot"],
                             leg["start_s"], leg["end_s"])
            booked.append({"run_id": run_id, "ship_id": ship["id"],
                           "pilot": leg["pilot"],
                           "start_s": leg["start_s"], "end_s": leg["end_s"]})
    return booked


def list_bookings(run_id=None):
    sql = ("SELECT run_id,ship_id,pilot,start_s,end_s FROM pilot_bookings")
    args = ()
    if run_id:
        sql += " WHERE run_id=?"
        args = (run_id,)
    sql += " ORDER BY pilot,start_s"
    with connect() as db:
        return [dict(r) for r in db.execute(sql, args)]
