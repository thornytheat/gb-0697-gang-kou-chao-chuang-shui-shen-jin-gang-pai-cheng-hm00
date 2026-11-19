import os
os.environ["DATA_DIR"] = "/tmp/handover-api-pytest"
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app, raise_server_exceptions=False)


def test_module8_run_and_idempotent_replay():
    r1 = client.post("/api/8/run", json={})
    assert r1.status_code == 200
    body1 = r1.json()
    r2 = client.post("/api/8/run", json={})
    body2 = r2.json()
    assert body1["run_id"] == body2["run_id"]
    assert len(body1["bookings"]) == len(body2["bookings"])
    assert body1["balance"]["scheduled"] == 1


def test_handover_event_lifecycle_and_idempotency():
    run_id = client.post("/api/8/run", json={}).json()["run_id"]
    base = f"/api/8/runs/{run_id}/handovers/V9001/events"
    payload = {"event_id": "EV1", "action": "BEGIN",
               "at": "2026-11-19T20:50:00+00:00"}
    first = client.post(base, json=payload).json()
    replay = client.post(base, json=payload).json()
    assert first["applied"] is True and replay["applied"] is False

    client.post(base, json={"event_id": "EV2", "action": "INTERRUPT",
                            "at": "2026-11-19T20:55:00+00:00"})
    illegal = client.post(base, json={"event_id": "EV3", "action": "COMPLETE",
                                      "at": "2026-11-19T21:00:00+00:00"})
    assert illegal.status_code == 409
    client.post(base, json={"event_id": "EV4", "action": "RESUME",
                            "at": "2026-11-19T21:00:00+00:00"})
    done = client.post(base, json={"event_id": "EV5", "action": "COMPLETE",
                                   "at": "2026-11-19T21:10:00+00:00"}).json()
    states = [s["state"] for s in done["record"]["shifts"]]
    assert states == ["SUPERSEDED", "CURRENT"]


def test_unknown_ship_handover_404():
    run_id = client.post("/api/8/run", json={}).json()["run_id"]
    resp = client.post(
        f"/api/8/runs/{run_id}/handovers/NOPE/events",
        json={"event_id": "X", "action": "BEGIN",
              "at": "2026-11-19T20:50:00+00:00"})
    assert resp.status_code == 404


def test_export_evidence_csv():
    run_id = client.post("/api/8/run", json={}).json()["run_id"]
    resp = client.get(f"/api/8/export/{run_id}")
    assert resp.status_code == 200
    assert "rejected_plan" in resp.text and "pilot_booking" in resp.text


def test_handover_page_renders():
    resp = client.get("/module/8")
    assert resp.status_code == 200 and "引航交接" in resp.text


def test_invalid_event_payload_rejected():
    run_id = client.post("/api/8/run", json={}).json()["run_id"]
    resp = client.post(
        f"/api/8/runs/{run_id}/handovers/V9001/events",
        json={"event_id": "", "action": "BEGIN", "at": "x"})
    assert resp.status_code == 422
