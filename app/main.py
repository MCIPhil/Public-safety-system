from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .database import BASE_DIR, get_conn, init_db, table_count
from .risk_engine import RULE_DEFINITIONS, ingest_event, load_json, now_local, parse_ts
from .simulator import build_live_events, build_seed_events, ensure_reference_data

FRONTEND_DIR = BASE_DIR / "frontend"


class EventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: datetime | None = None
    event_type: str = Field(..., examples=["person_pass", "vehicle_pass", "mac_seen", "rfid_seen"])
    zone_id: str
    sensor_id: str | None = None
    subject_type: str | None = None
    subject_id: str | None = None
    plate_no: str | None = None
    device_id: str | None = None
    direction: str | None = None
    speed_kmh: float | None = None
    confidence: float = 0.86
    metadata: dict[str, Any] = Field(default_factory=dict)


class BulkEventsIn(BaseModel):
    events: list[EventIn]


class AlertStatusIn(BaseModel):
    status: Literal["open", "processing", "closed"]


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    with get_conn() as conn:
        ensure_reference_data(conn)
        if table_count(conn, "events") == 0:
            for event in build_seed_events():
                ingest_event(conn, event)
    yield


app = FastAPI(
    title="场景一：开放通行公共安全治理系统",
    description="面向道路、地铁/公交、开放广场和景区的通行路过风险治理 demo。",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    with get_conn() as conn:
        return {
            "status": "ok",
            "time": now_local().isoformat(),
            "events": table_count(conn, "events"),
            "alerts": table_count(conn, "alerts"),
        }


@app.get("/api/dashboard")
def dashboard(
    hours: int = Query(24, ge=1, le=168),
    zone_id: str | None = Query(default=None),
) -> dict[str, Any]:
    end = now_local()
    start = end - timedelta(hours=hours)
    params: list[Any] = [start.isoformat()]
    zone_sql = ""
    if zone_id:
        zone_sql = " AND zone_id = ?"
        params.append(zone_id)

    with get_conn() as conn:
        summary = _summary(conn, params, zone_sql)
        trend = _trend(conn, start, end, zone_id)
        zones = _zones_with_density(conn, end)
        alerts = _recent_alerts(conn, limit=8, status="open")
        event_mix = conn.execute(
            f"""
            SELECT event_type, COUNT(*) AS total
            FROM events
            WHERE ts >= ? {zone_sql}
            GROUP BY event_type
            ORDER BY total DESC
            """,
            params,
        ).fetchall()
        rule_mix = conn.execute(
            f"""
            SELECT rule_code, severity, COUNT(*) AS total
            FROM alerts
            WHERE ts >= ? {zone_sql}
            GROUP BY rule_code, severity
            ORDER BY total DESC
            LIMIT 8
            """,
            params,
        ).fetchall()
        top_targets = _top_targets(conn, params, zone_sql)
        events = _events(conn, limit=12, zone_id=zone_id)

    return {
        "window": {"hours": hours, "start": start.isoformat(), "end": end.isoformat(), "zone_id": zone_id},
        "summary": summary,
        "trend": trend,
        "zones": zones,
        "alerts": alerts,
        "event_mix": event_mix,
        "rule_mix": rule_mix,
        "top_targets": top_targets,
        "events": events,
        "rules": RULE_DEFINITIONS,
    }


@app.get("/api/zones")
def zones() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM zones ORDER BY id").fetchall()
        for row in rows:
            row["sensors"] = conn.execute(
                "SELECT id, name, sensor_type, status FROM sensors WHERE zone_id = ? ORDER BY id",
                (row["id"],),
            ).fetchall()
        return rows


@app.get("/api/events")
def list_events(
    limit: int = Query(100, ge=1, le=500),
    zone_id: str | None = None,
    event_type: str | None = None,
) -> list[dict[str, Any]]:
    with get_conn() as conn:
        return _events(conn, limit=limit, zone_id=zone_id, event_type=event_type)


@app.post("/api/events")
def create_event(payload: EventIn) -> dict[str, Any]:
    with get_conn() as conn:
        _assert_zone(conn, payload.zone_id)
        event, alerts = ingest_event(conn, payload.model_dump())
        event["metadata"] = event["metadata"]
        return {"event": event, "alerts": alerts}


@app.post("/api/events/bulk")
def create_events(payload: BulkEventsIn) -> dict[str, Any]:
    inserted = []
    alerts = []
    with get_conn() as conn:
        for item in payload.events:
            _assert_zone(conn, item.zone_id)
            event, new_alerts = ingest_event(conn, item.model_dump())
            inserted.append(event)
            alerts.extend(new_alerts)
    return {"inserted": len(inserted), "alerts": alerts}


@app.get("/api/alerts")
def list_alerts(
    status: str = Query("open", pattern="^(open|processing|closed|all)$"),
    severity: str | None = Query(default=None, pattern="^(low|medium|high|critical)$"),
    limit: int = Query(100, ge=1, le=500),
) -> list[dict[str, Any]]:
    with get_conn() as conn:
        where = []
        params: list[Any] = []
        if status != "all":
            where.append("a.status = ?")
            params.append(status)
        if severity:
            where.append("a.severity = ?")
            params.append(severity)
        where_sql = "WHERE " + " AND ".join(where) if where else ""
        rows = conn.execute(
            f"""
            SELECT a.*, z.name AS zone_name
            FROM alerts a
            LEFT JOIN zones z ON z.id = a.zone_id
            {where_sql}
            ORDER BY a.ts DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        return [_format_alert(row) for row in rows]


@app.patch("/api/alerts/{alert_id}/status")
def update_alert_status(alert_id: str, payload: AlertStatusIn) -> dict[str, Any]:
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM alerts WHERE id = ?", (alert_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Alert not found")
        conn.execute("UPDATE alerts SET status = ? WHERE id = ?", (payload.status, alert_id))
        conn.commit()
    return {"id": alert_id, "status": payload.status}


@app.post("/api/simulate")
def simulate(count: int = Query(30, ge=1, le=200)) -> dict[str, Any]:
    inserted = 0
    alerts: list[dict[str, Any]] = []
    with get_conn() as conn:
        ensure_reference_data(conn)
        for event in build_live_events(count):
            _, new_alerts = ingest_event(conn, event)
            inserted += 1
            alerts.extend(new_alerts)
    return {"inserted": inserted, "alerts": alerts}


@app.get("/api/rules")
def rules() -> list[dict[str, Any]]:
    return RULE_DEFINITIONS


def _assert_zone(conn, zone_id: str) -> None:
    if not conn.execute("SELECT id FROM zones WHERE id = ?", (zone_id,)).fetchone():
        raise HTTPException(status_code=422, detail=f"Unknown zone_id: {zone_id}")


def _summary(conn, params: list[Any], zone_sql: str) -> dict[str, Any]:
    person_flow = conn.execute(
        f"SELECT COUNT(*) AS total FROM events WHERE event_type = 'person_pass' AND ts >= ? {zone_sql}",
        params,
    ).fetchone()["total"]
    vehicle_flow = conn.execute(
        f"SELECT COUNT(*) AS total FROM events WHERE event_type = 'vehicle_pass' AND ts >= ? {zone_sql}",
        params,
    ).fetchone()["total"]
    total_flow = conn.execute(
        f"SELECT COUNT(*) AS total FROM events WHERE ts >= ? {zone_sql}",
        params,
    ).fetchone()["total"]
    open_alerts = conn.execute(
        f"SELECT COUNT(*) AS total FROM alerts WHERE status = 'open' AND ts >= ? {zone_sql}",
        params,
    ).fetchone()["total"]
    high_alerts = conn.execute(
        f"""
        SELECT COUNT(*) AS total
        FROM alerts
        WHERE status = 'open' AND severity IN ('high', 'critical') AND ts >= ? {zone_sql}
        """,
        params,
    ).fetchone()["total"]
    first_seen = conn.execute(
        f"SELECT COUNT(*) AS total FROM alerts WHERE rule_code = 'FIRST_SEEN' AND ts >= ? {zone_sql}",
        params,
    ).fetchone()["total"]
    return {
        "total_flow": total_flow,
        "person_flow": person_flow,
        "vehicle_flow": vehicle_flow,
        "open_alerts": open_alerts,
        "high_alerts": high_alerts,
        "first_seen": first_seen,
    }


def _trend(conn, start: datetime, end: datetime, zone_id: str | None) -> list[dict[str, Any]]:
    rows = _events_since(conn, start, zone_id)
    bucket_count = max(1, int((end - start).total_seconds() // 3600) + 1)
    buckets: dict[str, dict[str, Any]] = {}
    for idx in range(bucket_count):
        hour = (start + timedelta(hours=idx)).replace(minute=0, second=0, microsecond=0)
        key = hour.isoformat()
        buckets[key] = {"hour": key, "person": 0, "vehicle": 0, "device": 0, "total": 0}

    for row in rows:
        ts = parse_ts(row["ts"]).replace(minute=0, second=0, microsecond=0).isoformat()
        if ts not in buckets:
            buckets[ts] = {"hour": ts, "person": 0, "vehicle": 0, "device": 0, "total": 0}
        buckets[ts]["total"] += 1
        if row["event_type"] == "person_pass":
            buckets[ts]["person"] += 1
        elif row["event_type"] == "vehicle_pass":
            buckets[ts]["vehicle"] += 1
        else:
            buckets[ts]["device"] += 1
    return list(sorted(buckets.values(), key=lambda item: item["hour"]))[-24:]


def _events_since(conn, start: datetime, zone_id: str | None) -> list[dict[str, Any]]:
    params: list[Any] = [start.isoformat()]
    zone_sql = ""
    if zone_id:
        zone_sql = " AND zone_id = ?"
        params.append(zone_id)
    return conn.execute(
        f"SELECT ts, event_type FROM events WHERE ts >= ? {zone_sql} ORDER BY ts",
        params,
    ).fetchall()


def _zones_with_density(conn, end: datetime) -> list[dict[str, Any]]:
    cutoff = (end - timedelta(minutes=15)).isoformat()
    zones = conn.execute("SELECT * FROM zones ORDER BY id").fetchall()
    result = []
    for zone in zones:
        row = conn.execute(
            """
            SELECT COUNT(DISTINCT COALESCE(subject_id, plate_no, device_id, id)) AS total
            FROM events
            WHERE zone_id = ? AND ts >= ?
            """,
            (zone["id"], cutoff),
        ).fetchone()
        count = int(row["total"])
        load = round(count / max(int(zone["capacity"]), 1), 2)
        status = "拥挤" if load >= 1 else "偏高" if load >= 0.75 else "平稳"
        zone = dict(zone)
        zone.update({"recent_count": count, "load": load, "status": status})
        result.append(zone)
    return result


def _recent_alerts(conn, limit: int, status: str | None = None) -> list[dict[str, Any]]:
    params: list[Any] = []
    where = ""
    if status:
        where = "WHERE a.status = ?"
        params.append(status)
    rows = conn.execute(
        f"""
        SELECT a.*, z.name AS zone_name
        FROM alerts a
        LEFT JOIN zones z ON z.id = a.zone_id
        {where}
        ORDER BY a.ts DESC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    return [_format_alert(row) for row in rows]


def _top_targets(conn, params: list[Any], zone_sql: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"""
        SELECT COALESCE(subject_id, plate_no, device_id) AS target_id,
               COUNT(*) AS total,
               COUNT(DISTINCT zone_id) AS zones
        FROM events
        WHERE ts >= ? {zone_sql}
          AND COALESCE(subject_id, plate_no, device_id) IS NOT NULL
        GROUP BY target_id
        ORDER BY total DESC
        LIMIT 8
        """,
        params,
    ).fetchall()
    return rows


def _events(
    conn,
    *,
    limit: int,
    zone_id: str | None = None,
    event_type: str | None = None,
) -> list[dict[str, Any]]:
    where = []
    params: list[Any] = []
    if zone_id:
        where.append("e.zone_id = ?")
        params.append(zone_id)
    if event_type:
        where.append("e.event_type = ?")
        params.append(event_type)
    where_sql = "WHERE " + " AND ".join(where) if where else ""
    rows = conn.execute(
        f"""
        SELECT e.*, z.name AS zone_name, s.name AS sensor_name
        FROM events e
        LEFT JOIN zones z ON z.id = e.zone_id
        LEFT JOIN sensors s ON s.id = e.sensor_id
        {where_sql}
        ORDER BY e.ts DESC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    for row in rows:
        row["metadata"] = load_json(row["metadata"])
    return rows


def _format_alert(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["evidence"] = load_json(item.get("evidence"))
    return item
