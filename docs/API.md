# 接口

`POST /api/run`：{"text":"CSV内容"}。同规则版本与输入摘要只产生一条批次。

`GET/POST /api/1` 至 `/api/7`：独立样例或自定义文件计算。各模块返回 report、quarantine、balance。

`POST /api/review`：id、整数revision、decision取通过或退回。旧版本409；阻断项不能通过。

`GET /api/export/{id}`：唯一证据CSV。`GET /health`：存活检查。

## M8 引航交接

`POST /api/8/run`：{"text":"handover CSV"}，缺省取 `sample_data/handover.csv`。同规则版本与输入摘要复用同一批次，重跑幂等、不重复占班。返回 schedule、pending、rejected_plans、quarantine、handovers、bookings。

`GET /api/8/run`：样例计算。`GET /api/8/runs`：交接批次列表。`GET /api/8/runs/{id}`：批次明细（含交接记录与台账）。

`GET /api/8/runs/{id}/handovers`：批次内全部交接状态机记录。

`POST /api/8/runs/{id}/handovers/{ship_id}/events`：{"event_id","action","at"}。action 取 BEGIN/INTERRUPT/RESUME/COMPLETE/ABORT。同 event_id 重放返回 `applied:false`，不产生新版本、不重复占班；非法状态迁移 409；航次无交接记录 404。

`GET /api/8/bookings`：跨批次引航员占班台账。`GET /api/8/export/{id}`：交接证据 CSV，含 manifest、schedule、shift（班次版本）、handover_event、pending（耽误潮窗）、rejected_plan（全部未采用排法）、quarantine、pilot_booking 各段。

错误：结构/数值422，状态迁移与双班冲突409，不存在404，文件过大413。逐行异常进入quarantine。
