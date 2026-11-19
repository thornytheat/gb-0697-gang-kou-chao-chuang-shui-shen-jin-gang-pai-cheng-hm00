"""M8 引航交接：领域对象与可行性原语。

所有时刻统一为整数 UTC 秒，区间均为半开 [start, end)，结束同刻可开始下一班，
与 M4 航道冲突模块的端点约定保持一致。
"""


def overlap(a, b):
    """半开区间重叠判定：端点相接不算冲突。"""
    return a[0] < b[1] and b[0] < a[1]


def fits(interval, window):
    return window[0] <= interval[0] and interval[1] <= window[1]


def window_meet(deadline, window):
    return window[1] <= deadline


class Voyage:
    def __init__(self, row, earliest_s, deadline_s, leg_s, stop_s, line):
        self.id = row["id"]
        self.earliest_s = int(earliest_s)          # 最早可开航
        self.deadline_s = int(deadline_s)          # 必须通过潮窗的截止时刻
        self.leg_s = int(leg_s)                    # 半程航行时长
        self.stop_s = int(stop_s)                  # 锚地交接停航时长（占用航道）
        self.line = line

    @property
    def transit_s(self):
        """含停航的全程占用时长。"""
        return self.leg_s * 2 + self.stop_s


class Pilot:
    def __init__(self, pid, start_s, end_s, line):
        self.id = pid
        self.window = (int(start_s), int(end_s))
        self.line = line


class Booking:
    """已存在的引航员占班（同一引航员不能同时接两艘船）。"""
    def __init__(self, bid, pid, start_s, end_s, line):
        self.id = bid
        self.pid = pid
        self.interval = (int(start_s), int(end_s))
        self.line = line


class Anchorage:
    """航道锚地：交接停航期间持续占用航道。"""
    def __init__(self, aid, start_s, end_s, line):
        self.id = aid
        self.window = (int(start_s), int(end_s))
        self.line = line


class PilotCalendar:
    """引航员班次日历：窗口 + 既有占班 + 本次新排班次。"""

    def __init__(self, pilots, bookings):
        self.windows = {p.id: p.window for p in pilots}
        self.busy = {}
        for b in bookings:
            self.busy.setdefault(b.pid, []).append(b.interval)

    def available(self, pid, interval):
        window = self.windows.get(pid)
        if window is None or not fits(interval, window):
            return False
        return not any(overlap(interval, block) for block in self.busy.get(pid, ()))
    def reserve(self, pid, interval):
        self.busy.setdefault(pid, []).append(interval)


class Channel:
    """航道占用日历：锚地停航块 + 已排航次的停航块。"""

    def __init__(self, anchorages):
        self.anchorages = {a.id: a.window for a in anchorages}
        self.blocks = []

    def stop_window(self, aid, stop_s, earliest_stop=None):
        """返回锚地内可容纳 stop_s 停航的最早半开窗口片段列表。

        earliest_stop 给出停航不得早于的时刻（最早开航 + 半程航行）。
        """
        window = self.anchorages[aid]
        lo = window[0] if earliest_stop is None else max(window[0], earliest_stop)
        fragments = [(lo, window[1])]
        for ba, bb in sorted(self.blocks):
            updated = []
            for a, b in fragments:
                if bb <= a or ba >= b:
                    updated.append((a, b))
                    continue
                if a < ba:
                    updated.append((a, ba))
                if bb < b:
                    updated.append((bb, b))
            fragments = updated
        return [(a, a + stop_s) for a, b in fragments if b - a >= stop_s]

    def occupy(self, interval):
        self.blocks.append(interval)
