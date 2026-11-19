"""M8 引航计划引擎。

每条长航次枚举两类排法：
  FULL  单一引航员全程跟随（若赶不上潮窗则不采用）；
  SPLIT 两名引航员各顶半程，在航道锚地交接，停航期间持续占用航道。

所有未采用的排法都带拒绝原因留痕；同一引航员不得同时接两艘船；
任何排法都无法在潮窗截止前通过时，航次置为 PENDING 并注明耽误的潮窗。
"""
from .domain import PilotCalendar, Channel, fits, overlap, window_meet
R_NO_FULL_PILOT = "FULL_NO_AVAILABLE_PILOT"     # 无人能全程跟随
R_FULL_MISSES_TIDE = "FULL_MISSES_TIDE_WINDOW"  # 全程方案错过潮窗
R_SAME_PILOT = "SPLIT_SAME_PILOT"               # 半程不能同一人
R_ANCHORAGE = "SPLIT_NO_ANCHORAGE_SLOT"         # 锚地无可用停航窗口
R_PILOT_BUSY = "PILOT_DOUBLE_BOOKED"            # 引航员同时段已占班
R_PILOT_WINDOW = "PILOT_WINDOW_MISMATCH"        # 引航窗口不覆盖班次
R_OUTSIDE_REQUEST = "OUTSIDE_REQUEST_WINDOW"    # 早于最早开航/晚于截止
R_SPLIT_MISSES_TIDE = "SPLIT_MISSES_TIDE_WINDOW"


def _reject(plans, kind, reason, detail):
    plans.append({"kind": kind, "feasible": False, "reason": reason, "detail": detail})


def _full_candidates(voyage, pilots):
    """全程方案：枚举每个能在申请窗内开始的起点。"""
    out = []
    for p in pilots:
        start = max(voyage.earliest_s, p.window[0])
        end = start + voyage.transit_s
        if start > voyage.deadline_s:
            continue
        out.append({"kind": "FULL", "pilot": p.id, "start_s": start,
                    "end_s": end, "stop_s": 0, "anchorage": None,
                    "legs": [{"pilot": p.id, "start_s": start, "end_s": end}]})
    return out


def _split_candidates(voyage, pilots, anchorages, channel):
    """半程交接方案：锚地窗口 × 两名引航员组合，确定性枚举。"""
    out = []
    if not anchorages:
        return out
    for anchorage in anchorages:
        earliest_stop = voyage.earliest_s + voyage.leg_s
        for stop_start, stop_end in channel.stop_window(
                anchorage.id, voyage.stop_s, earliest_stop):
            leg1_start = stop_start - voyage.leg_s
            leg2_end = stop_end + voyage.leg_s
            for p1 in pilots:
                for p2 in pilots:
                    if p1.id == p2.id:
                        continue
                    out.append({
                        "kind": "SPLIT", "pilot": None,
                        "start_s": leg1_start, "end_s": leg2_end,
                        "stop_s": voyage.stop_s, "anchorage": anchorage.id,
                        "stop_start_s": stop_start, "stop_end_s": stop_end,
                        "legs": [
                            {"pilot": p1.id, "start_s": leg1_start,
                             "end_s": stop_start},
                            {"pilot": p2.id, "start_s": stop_end,
                             "end_s": leg2_end},
                        ],
                    })
    return out


def _reject_reason(calendar, pid, interval):
    """返回该引航员不能执飞该班次的原因码；可行则 None。"""
    window = calendar.windows.get(pid)
    if window is None or not fits(interval, window):
        return R_PILOT_WINDOW
    if any(overlap(interval, block) for block in calendar.busy.get(pid, ())):
        return R_PILOT_BUSY
    return None


def _check_full(candidate, voyage, calendar):
    p = candidate["pilot"]
    interval = (candidate["start_s"], candidate["end_s"])
    reason = _reject_reason(calendar, p, interval)
    if reason is not None:
        return reason
    if not window_meet(voyage.deadline_s, interval):
        return R_FULL_MISSES_TIDE
    return None


def _check_split(candidate, voyage, calendar):
    p1, p2 = candidate["legs"][0]["pilot"], candidate["legs"][1]["pilot"]
    if p1 == p2:
        return R_SAME_PILOT
    for leg in candidate["legs"]:
        interval = (leg["start_s"], leg["end_s"])
        reason = _reject_reason(calendar, leg["pilot"], interval)
        if reason is not None:
            return reason
    # 两段班次不得重叠（停航隔开，天然半开不相交，这里做防御性校验）
    if overlap((candidate["legs"][0]["start_s"], candidate["legs"][0]["end_s"]),
               (candidate["legs"][1]["start_s"], candidate["legs"][1]["end_s"])):
        return R_SAME_PILOT
    if not window_meet(voyage.deadline_s, (candidate["start_s"], candidate["end_s"])):
        return R_SPLIT_MISSES_TIDE
    return None


def _rank(plan):
    # 优先潮窗余量最大（结束最早），再少占航道，再确定性字典序
    return (plan["end_s"], plan["stop_s"], plan["kind"],
            plan.get("anchorage") or "",
            tuple((l["pilot"], l["start_s"]) for l in plan["legs"]))


def plan_voyage(voyage, pilots, bookings, anchorages, channel, calendar=None):
    calendar = calendar or PilotCalendar(pilots, bookings)
    candidates = []
    rejected = []

    fulls = _full_candidates(voyage, pilots)
    if not fulls:
        _reject(rejected, "FULL", R_NO_FULL_PILOT,
                "没有引航员能在申请窗内开始全程班次")
    for c in fulls:
        reason = _check_full(c, voyage, calendar)
        if reason is None:
            candidates.append(c)
        else:
            rejected.append({"kind": "FULL", "feasible": False, "reason": reason,
                             "detail": {"pilot": c["pilot"],
                                        "start_s": c["start_s"], "end_s": c["end_s"]}})

    splits = _split_candidates(voyage, pilots, anchorages, channel)
    if anchorages and not splits:
        _reject(rejected, "SPLIT", R_ANCHORAGE,
                "锚地窗口无法容纳交接停航或早于最早开航")
    for c in splits:
        reason = _check_split(c, voyage, calendar)
        if reason is None:
            candidates.append(c)
        else:
            rejected.append({"kind": "SPLIT", "feasible": False, "reason": reason,
                             "detail": {"pilots": [c["legs"][0]["pilot"],
                                                   c["legs"][1]["pilot"]],
                                        "anchorage": c["anchorage"],
                                        "stop_start_s": c["stop_start_s"],
                                        "stop_end_s": c["stop_end_s"]}})

    if not candidates:
        tide_rejects = [r for r in rejected
                        if r["reason"] in (R_FULL_MISSES_TIDE, R_SPLIT_MISSES_TIDE)]
        return None, rejected, bool(tide_rejects)

    chosen = min(candidates, key=_rank)
    chosen["feasible"] = True
    chosen["tide_slack_s"] = voyage.deadline_s - chosen["end_s"]
    # 落账：占引航员班次、占航道停航块
    for leg in chosen["legs"]:
        calendar.reserve(leg["pilot"], (leg["start_s"], leg["end_s"]))
    if chosen["kind"] == "SPLIT":
        channel.occupy((chosen["stop_start_s"], chosen["stop_end_s"]))
    return chosen, rejected, False


def plan_all(voyages, pilots, bookings, anchorages):
    channel = Channel(anchorages)
    # 引航员日历跨船复用：同一引航员不能同时接两艘船
    calendar = PilotCalendar(pilots, bookings)
    scheduled, pending, all_rejected = [], [], []
    for voyage in voyages:
        chosen, rejected, tide_blocked = plan_voyage(
            voyage, pilots, bookings, anchorages, channel, calendar)
        if chosen is None:
            pending.append({
                "id": voyage.id, "status": "PENDING", "line": voyage.line,
                "deadline_s": voyage.deadline_s,
                "missed_tide_window": {
                    "end_s": voyage.deadline_s,
                    "reason": ("NO_PLAN_MEETS_TIDE" if tide_blocked
                               else "NO_FEASIBLE_PILOT_OR_CHANNEL"),
                },
                "earliest_s": voyage.earliest_s,
                "transit_s": voyage.transit_s,
                "rejected_plans": rejected,
            })
        else:
            scheduled.append({
                "id": voyage.id, "status": "SCHEDULED", "line": voyage.line,
                "plan": chosen, "rejected_plans": rejected,
                "deadline_s": voyage.deadline_s,
            })
        all_rejected.extend((voyage.id, r) for r in rejected)
    return scheduled, pending
