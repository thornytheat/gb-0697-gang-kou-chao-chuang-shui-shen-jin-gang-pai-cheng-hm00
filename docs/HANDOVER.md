# M8 引航交接规则

面向夜班长航次：必须在潮窗截止（如 22:40）前通过，单名引航员无法全程覆盖时，
由两名引航员各顶半程，在航道锚地交接。交接停航期间船舶持续占用航道。

## 输入（handover CSV，沿用主表头）

`id,kind,start,end,value,aux,version,unit`

| kind | start/end 含义 | value | aux |
|---|---|---|---|
| voyage | 最早可开航 / 潮窗截止 | 半程分钟 | 交接停航分钟 |
| pilot | 引航员可执飞窗口 | — | — |
| booking | 既有占班区间 | 被占引航员 id | — |
| anchorage | 航道锚地开放窗口 | — | — |

时刻带时区，统一转 UTC 秒；区间一律半开 `[start,end)`，结束同刻可开始下一班。
逐行异常进入 quarantine，不中断整批；同 `(kind,id,version)` 重复隔离，同 id 取最高版本。

## 候选排法

- FULL：单一引航员全程跟随。要求引航窗口覆盖整个航次，且完成时刻不晚于潮窗截止。
  无人窗口够长时全部记 `PILOT_WINDOW_MISMATCH`；窗口够但错过潮窗记 `FULL_MISSES_TIDE_WINDOW`。
- SPLIT：两名不同引航员各执一段，锚地停航 `stop` 分钟。锚地窗口须容纳停航块，
  两段班次分别落在两人窗口内且不与既有占班重叠，完成时刻不晚于潮窗截止。
  - 同一引航员两段：`SPLIT_SAME_PILOT`
  - 锚地放不下停航：`SPLIT_NO_ANCHORAGE_SLOT`
  - 引航员时段已占：`PILOT_DOUBLE_BOOKED`（同一引航员不能同时接两艘船，跨船日历复用）
  - 错过潮窗：`SPLIT_MISSES_TIDE_WINDOW`

选择规则：完成时刻最早（潮窗余量最大）→ 停航最短 → 类型/锚地/引航员字典序，保证确定性。
所有未采用排法带原因写入 `rejected_plans`，证据 CSV 可逐条核对。

## 待定与耽误潮窗

任一可行排法都不存在时，航次置 `PENDING`，记录 `missed_tide_window.end_s`
（被耽误的潮窗截止时刻）与原因：存在错过潮窗的排法为 `NO_PLAN_MEETS_TIDE`，
否则为 `NO_FEASIBLE_PILOT_OR_CHANNEL`（引航员/锚地/双班约束耗尽）。

## 交接状态机与班次版本

仅 SPLIT 航次建立交接记录，班次带 `state` 与 `revision`：

| 事件 | 迁移 | 旧段班次 | 新段班次 |
|---|---|---|---|
| BEGIN | PLANNED→IN_PROGRESS | CURRENT（保留） | PENDING |
| INTERRUPT | IN_PROGRESS→INTERRUPTED | CURRENT（恢复旧版） | PENDING（不生效） |
| RESUME | INTERRUPTED→IN_PROGRESS | CURRENT | PENDING |
| COMPLETE | IN_PROGRESS→COMPLETED | SUPERSEDED（留痕） | CURRENT（正式切换） |
| ABORT | →PLANNED | CURRENT | PENDING |

中断期间旧版班次始终保留并恢复，只有 COMPLETE 后才切换；非法迁移返回 409。
事件以 `event_id` 为幂等键，重放返回 `applied:false`，不重复占班、不新增版本。

## 幂等与占班台账

- 批次 id = `sha256(rule_version + 输入)` 前 16 位，同输入重跑同批次。
- `pilot_bookings` 以 `(run_id,ship_id,pilot,start_s)` 为主键：同批次同船重放为空操作；
  与其他占用半开重叠时抛 `DOUBLE_BOOKED` 409，端点相接不冲突。
- 锚地停航块进入航道日历，后续航次的停航窗口自动避让。

## 证据

`GET /api/8/export/{id}` 输出分段 CSV：manifest、section_index、schedule、
shift、handover_event、pending、rejected_plan、quarantine、pilot_booking，
每行带 run_id、sha256、rule_version，可逐船核对班次、交接与未采用排法。
