from pathlib import Path
from fastapi import APIRouter, Body
from fastapi.responses import Response
from app.errors import BusinessError, object_payload
from app.timeops import instant
from app.handover import service, storage, handover as hsm
from app.handover.exporter import handover_evidence_csv

router = APIRouter()


def _sample():
    return (Path(__file__).parent.parent.parent.parent
            / "sample_data" / "handover.csv").read_text(encoding="utf-8-sig")


def _text(payload):
    text = payload.get("text", _sample())
    if not isinstance(text, str):
        raise BusinessError("TEXT", "输入须为文本")
    return text


@router.post("/api/8/run")
def run_plan(payload: object = Body(default=None)):
    payload = object_payload(payload)
    outcome = service.execute(_text(payload))
    # 为 SPLIT 航次建立交接记录（幂等，已存在则跳过）
    records = service.init_handover_records(outcome["run_id"])
    return {
        "run_id": outcome["run_id"], "digest": outcome["digest"],
        "balance": outcome["result"]["balance"],
        "schedule": outcome["result"]["schedule"],
        "pending": outcome["result"]["pending"],
        "rejected_plans": outcome["result"]["rejected_plans"],
        "quarantine": outcome["result"]["quarantine"],
        "handovers": list(records.values()),
        "bookings": storage.list_bookings(outcome["run_id"]),
    }


@router.get("/api/8/run")
def sample_run():
    return run_plan({"text": _sample()})


@router.get("/api/8/runs")
def runs():
    return storage.list_runs()


@router.get("/api/8/runs/{ident}")
def run_detail(ident: str):
    run = storage.get_run(ident)
    return {"run": run, "handovers": storage.list_records(ident),
            "bookings": storage.list_bookings(ident)}


@router.get("/api/8/runs/{ident}/handovers")
def handovers(ident: str):
    return storage.list_records(ident)


@router.post("/api/8/runs/{ident}/handovers/{ship_id}/events")
def handover_event(ident: str, ship_id: str, payload: object = Body(...)):
    payload = object_payload(payload)
    event_id = payload.get("event_id")
    action = payload.get("action")
    at_text = payload.get("at")
    if not all(isinstance(x, str) and x for x in (event_id, action, at_text)):
        raise BusinessError("EVENT", "event_id、action、at 须为非空文本")
    record = storage.get_record(ident, ship_id)
    try:
        at_s = int(instant(at_text))
        record, applied = hsm.apply_event(record, event_id, action, at_s)
    except hsm.HandoverError as exc:
        raise BusinessError(exc.code, str(exc), 409)
    if applied:
        storage.upsert_record(ident, ship_id, record)
    return {"applied": applied, "record": record}


@router.get("/api/8/bookings")
def bookings():
    return storage.list_bookings()


@router.get("/api/8/export/{ident}")
def export(ident: str):
    return Response(
        handover_evidence_csv(ident), media_type="text/csv",
        headers={"Content-Disposition":
                 f"attachment; filename=handover_evidence_{ident}.csv"})


@router.get("/api/8/sample")
def sample_text():
    return {"text": _sample()}
