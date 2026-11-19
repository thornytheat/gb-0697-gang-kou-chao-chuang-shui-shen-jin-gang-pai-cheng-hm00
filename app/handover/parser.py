"""M8 输入解析：夜班长航次、引航员窗口、既有占班、航道锚地。

沿用主流水线 CSV 表头 id,kind,start,end,value,aux,version,unit，
逐行异常进入 quarantine，不中断整批计算。
"""
from app.reader import read_rows
from app.errors import BusinessError
from app.timeops import instant
from app.numbers import number
from .domain import Voyage, Pilot, Booking, Anchorage

FIELDS = "id,kind,start,end,value,aux,version,unit".split(",")
KINDS = {"voyage", "pilot", "booking", "anchorage"}


def _isolate(quarantine_list, row, reason):
    quarantine_list.append({"line": row.get("line", 0),
                            "id": row.get("id", ""), "reason": reason,
                            "raw": row})


def _version(row):
    return int(row["version"]) if row["version"] else 1


def parse(text, quarantine_list):
    rows, bad, blanks = read_rows(text, FIELDS)
    quarantine_list.extend(bad)
    kept, seen = [], set()
    for r in rows:
        key = (r["kind"], r["id"], _version(r))
        if not r["id"]:
            _isolate(quarantine_list, r, "ID_REQUIRED")
            continue
        if key in seen:
            _isolate(quarantine_list, r, "KEY_CONFLICT")
            continue
        seen.add(key)
        kept.append(r)

    latest = {}
    for r in kept:
        key = (r["kind"], r["id"])
        if key not in latest or _version(r) > _version(latest[key]):
            latest[key] = r

    voyages, pilots, bookings, anchorages = [], [], [], []
    for r in latest.values():
        try:
            if r["kind"] not in KINDS:
                raise BusinessError("KIND", "M8 记录类型无效")
            a, b = instant(r["start"]), instant(r["end"])
            if b <= a:
                raise BusinessError("INTERVAL", "结束须晚于开始")
            if r["kind"] == "voyage":
                leg = number(r["value"])          # 半程分钟
                stop = number(r["aux"])           # 交接停航分钟
                if leg <= 0 or stop < 0:
                    raise BusinessError("VOYAGE", "航次时长无效")
                voyages.append(Voyage(r, a, b, int(leg) * 60, int(stop) * 60,
                                      r["line"]))
            elif r["kind"] == "pilot":
                pilots.append(Pilot(r["id"], a, b, r["line"]))
            elif r["kind"] == "booking":
                if not r["value"]:
                    raise BusinessError("BOOKING", "占班须注明引航员")
                bookings.append(Booking(r["id"], r["value"], a, b, r["line"]))
            else:
                anchorages.append(Anchorage(r["id"], a, b, r["line"]))
        except (BusinessError, ValueError) as exc:
            _isolate(quarantine_list, r, getattr(exc, "code", "TIME"))

    # 占班引用了不存在的引航员：隔离该占班
    pids = {p.id for p in pilots}
    valid_bookings = []
    for b in bookings:
        if b.pid in pids:
            valid_bookings.append(b)
        else:
            _isolate(quarantine_list, {"line": b.line, "id": b.id}, "UNKNOWN_PILOT")
    voyages.sort(key=lambda v: (v.earliest_s, v.id))
    return voyages, pilots, valid_bookings, anchorages, blanks
