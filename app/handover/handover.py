"""交接中断/恢复状态机。

规则：
- 交接开始前旧引航员（前段）持有班次；BEGIN 后进入交接，旧班次转为 SUPERSEDED 留痕，
  但在实际完成前仍保留 CURRENT 旧版（中断回退用）。
- 中断 INTERRUPT：旧版班次恢复为 CURRENT，新班次不生效，交接状态回到 INTERRUPTED。
- 恢复 RESUME：仅允许从 INTERRUPTED 重新发起交接。
- 完成 COMPLETE：旧版班次置 SUPERSEDED，后段引航员班次置 CURRENT，正式切换。
- 同一 event_id 重放为幂等空操作，重跑不会重复占班。
"""

# 交接状态
PLANNED = "PLANNED"
IN_PROGRESS = "IN_PROGRESS"
INTERRUPTED = "INTERRUPTED"
COMPLETED = "COMPLETED"

# 班次版本状态
CURRENT = "CURRENT"
SUPERSEDED = "SUPERSEDED"
PENDING = "PENDING"

VALID_EVENTS = {"BEGIN", "INTERRUPT", "RESUME", "COMPLETE", "ABORT"}
_TRANSITIONS = {
    PLANNED: {"BEGIN"},
    IN_PROGRESS: {"INTERRUPT", "COMPLETE", "ABORT"},
    INTERRUPTED: {"RESUME", "ABORT"},
    COMPLETED: set(),
}


class HandoverError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def initial_shift_record(scheduled_ship):
    """从已排航次生成带版本的班次记录。"""
    plan = scheduled_ship["plan"]
    shifts = []
    for idx, leg in enumerate(plan["legs"], start=1):
        shifts.append({
            "seq": idx, "pilot": leg["pilot"],
            "start_s": leg["start_s"], "end_s": leg["end_s"],
            "state": CURRENT if idx == 1 else PENDING,
            "revision": 1,
        })
    return {
        "ship_id": scheduled_ship["id"],
        "anchorage": plan.get("anchorage"),
        "stop_start_s": plan.get("stop_start_s"),
        "stop_end_s": plan.get("stop_end_s"),
        "status": PLANNED,
        "events": [],
        "shifts": shifts,
        "applied_event_ids": [],
    }


def apply_event(record, event_id, action, at_s):
    """对交接记录施加事件；返回 (record, applied)。applied=False 表示幂等重放。"""
    if action not in VALID_EVENTS:
        raise HandoverError("EVENT", "交接事件无效")
    # 幂等：同一事件重放直接返回，不产生新版本、不重复占班
    if event_id in record["applied_event_ids"]:
        return record, False
    if action not in _TRANSITIONS[record["status"]]:
        raise HandoverError(
            "TRANSITION",
            f"当前状态 {record['status']} 不接受事件 {action}")

    record["events"].append({"event_id": event_id, "action": action, "at_s": int(at_s)})
    record["applied_event_ids"].append(event_id)
    old, new = record["shifts"][0], record["shifts"][-1]

    if action == "BEGIN":
        record["status"] = IN_PROGRESS
        # 旧版仍 CURRENT，新版保持 PENDING：交接未完成不切换
    elif action == "INTERRUPT":
        record["status"] = INTERRUPTED
        old["state"] = CURRENT          # 保留并恢复旧版班次
        new["state"] = PENDING          # 新班次不生效
    elif action == "RESUME":
        record["status"] = IN_PROGRESS
    elif action == "COMPLETE":
        record["status"] = COMPLETED
        old["state"] = SUPERSEDED       # 旧版留痕，不再生效
        new["state"] = CURRENT          # 恢复后完成，正式切换
        for shift in record["shifts"]:
            shift["revision"] += 1
    elif action == "ABORT":
        record["status"] = PLANNED
        old["state"] = CURRENT
        new["state"] = PENDING
    return record, True
