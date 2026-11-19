# 深度登记 港口潮窗水深与进港排程

- 规模：中型
- 核心规则：潮位线性插值求达到最低水深的时刻区间，与航道占用和引航窗口求交，安全端点含义明确

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 潮位接收 | 运行潮位接收并核对证据 | `POST /api/1` | `app/modules/m1_tides/rules.py` | `tests/test_tides.py` |
| M2 船舶条件 | 运行船舶条件并核对证据 | `POST /api/2` | `app/modules/m2_vessels/rules.py` | `tests/test_vessels.py` |
| M3 水深求窗 | 运行水深求窗并核对证据 | `POST /api/3` | `app/modules/m3_windows/rules.py` | `tests/test_windows.py` |
| M4 航道冲突 | 运行航道冲突并核对证据 | `POST /api/4` | `app/modules/m4_channel/rules.py` | `tests/test_channel.py` |
| M5 引航排程 | 运行引航排程并核对证据 | `POST /api/5` | `app/modules/m5_schedule/rules.py` | `tests/test_schedule.py` |
| M6 计划对账 | 运行计划对账并核对证据 | `POST /api/6` | `app/modules/m6_balance/rules.py` | `tests/test_balance.py` |
| M7 变更复核 | 运行变更复核并核对证据 | `POST /api/7` | `app/modules/m7_changes/rules.py` | `tests/test_changes.py` |
| M8 引航交接 | 运行引航交接并核对证据 | `POST /api/8/run` | `app/handover/planner.py`、`app/handover/handover.py` | `tests/test_handover.py`、`tests/test_handover_service.py`、`tests/test_handover_api.py` |

## 陷阱登记

| TRAP | 现象 | 朴素实现会怎样 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-02 | BOM | 静默采信错误值 | 消除BOM | `app/modules/m1_tides/rules.py` | `tests/test_tides.py::test_tides_bom` |
| TRAP-06 | 全角单位 | 静默采信错误值 | NFKC归一 | `app/modules/m2_vessels/rules.py` | `tests/test_vessels.py::test_vessel_unit` |
| TRAP-07 | 空值 | 静默采信错误值 | 隔离空值 | `app/modules/m2_vessels/rules.py` | `tests/test_vessels.py::test_vessel_missing` |
| TRAP-11 | 偏移混用 | 静默采信错误值 | 转换UTC秒 | `app/modules/m2_vessels/rules.py` | `tests/test_vessels.py::test_vessel_timezone` |
| TRAP-13 | 乱序潮位 | 静默采信错误值 | 先排序后插值 | `app/modules/m3_windows/rules.py` | `tests/test_windows.py::test_window_sample` |
| TRAP-14 | 同版冲突 | 静默采信错误值 | 隔离冲突 | `app/modules/m1_tides/rules.py` | `tests/test_tides.py::test_tides_duplicate` |
| TRAP-23 | 相邻端点 | 静默采信错误值 | 半开区间 | `app/modules/m4_channel/rules.py` | `tests/test_channel.py::test_channel_end` |
| TRAP-25 | 旧修订 | 静默采信错误值 | 拒绝陈旧版本 | `app/modules/m7_changes/rules.py` | `tests/test_changes.py::test_change_stale` |
| TRAP-31 | 引航员双班 | 两船排进同一时段 | 跨船复用日历，半开重叠即拒 | `app/handover/planner.py`、`storage.py` | `tests/test_handover.py::test_stop_occupies_channel_so_second_ship_is_blocked` |
| TRAP-32 | 交接中断误切换 | 中断即换新班次 | 旧版 CURRENT 保留，COMPLETE 才 SUPERSEDED | `app/handover/handover.py` | `tests/test_handover.py::test_handover_interrupt_keeps_old_shift_until_completion` |
| TRAP-33 | 重跑重复占班 | 每次运行新增台账 | 摘要定批次 + 主键幂等 + event_id 去重 | `app/handover/storage.py` | `tests/test_handover_service.py::test_persistent_run_idempotent_and_no_duplicate_booking` |
| TRAP-34 | 锚地停航漏占航道 | 只算航行不扣停航 | 停航块进航道日历，后续航次避让 | `app/handover/domain.py` | `tests/test_handover.py::test_anchorage_window_must_fit_stop_duration` |
| TRAP-35 | 赶不上仍硬排 | 输出越过潮窗的计划 | 置 PENDING 并注明耽误潮窗 | `app/handover/planner.py` | `tests/test_handover.py::test_voyage_that_cannot_meet_tide_is_pending_with_window` |

## 对账规则

输入记录=采信记录+隔离记录+空行；重复记录归隔离；算法输出另记业务单位，不冒充输入行数。金额/浓度/时长使用Decimal；证据含原始行号。

## 已知不覆盖

不支持超过2MB输入，不执行生产自动放行；异常值需人员复核。
