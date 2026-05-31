from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")

RULE_DEFINITIONS = [
    {"code": "WATCH_FUGITIVE", "name": "在逃人员命中", "severity": "critical"},
    {"code": "WATCH_DRUG_DRIVE", "name": "毒驾车辆命中", "severity": "high"},
    {"code": "WATCH_KEY_PERSON", "name": "关注人员出现", "severity": "high"},
    {"code": "FIRST_SEEN", "name": "首次出现", "severity": "medium"},
    {"code": "EXTERNAL_VEHICLE", "name": "外来车辆关注", "severity": "low"},
    {"code": "RIDE_HAILING", "name": "网约车通行", "severity": "low"},
    {"code": "E_BIKE_THEFT", "name": "电瓶车防盗", "severity": "high"},
    {"code": "DENSITY_HIGH", "name": "片区密度过高", "severity": "high"},
    {"code": "LOITERING", "name": "徘徊行为", "severity": "medium"},
    {"code": "TRACKING", "name": "跟踪伴随", "severity": "medium"},
    {"code": "WRONG_WAY", "name": "逆行", "severity": "medium"},
    {"code": "PERSON_FREQUENCY", "name": "同人频次异常", "severity": "medium"},
    {"code": "VEHICLE_FREQUENCY", "name": "同车频次异常", "severity": "medium"},
    {"code": "SPEEDING", "name": "超速", "severity": "medium"},
    {"code": "NIGHT_ACTIVITY", "name": "昼伏夜出", "severity": "medium"},
]

WATCHLIST_RULE = {
    "在逃": ("WATCH_FUGITIVE", "在逃人员命中", "布控对象在开放通行场景出现。"),
    "毒驾": ("WATCH_DRUG_DRIVE", "毒驾车辆命中", "涉毒驾驶风险车辆经过卡口。"),
    "关注人口": ("WATCH_KEY_PERSON", "关注人员出现", "重点关注人员在开放区域出现。"),
    "被盗电瓶车": ("E_BIKE_THEFT", "被盗电瓶车出现", "被盗电瓶车或关联 RFID 在通道出现。"),
}

SEVERITY_SCORE = {"low": 35, "medium": 55, "high": 75, "critical": 95}


def now_local() -> datetime:
    return datetime.now(TZ).replace(microsecond=0)


def parse_ts(value: str | datetime | None) -> datetime:
    if value is None:
        return now_local()
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ).replace(microsecond=0)


def load_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {}


def normalize_event(payload: dict[str, Any]) -> dict[str, Any]:
    metadata = payload.get("metadata") or {}
    if isinstance(metadata, str):
        metadata = load_json(metadata)

    event_ts = parse_ts(payload.get("timestamp") or payload.get("ts"))
    return {
        "id": payload.get("id") or f"EVT-{uuid.uuid4().hex[:12].upper()}",
        "ts": event_ts.isoformat(),
        "event_type": payload["event_type"],
        "zone_id": payload["zone_id"],
        "sensor_id": payload.get("sensor_id"),
        "subject_type": payload.get("subject_type"),
        "subject_id": payload.get("subject_id"),
        "plate_no": payload.get("plate_no"),
        "device_id": payload.get("device_id"),
        "direction": payload.get("direction"),
        "speed_kmh": payload.get("speed_kmh"),
        "confidence": payload.get("confidence", 0.86),
        "metadata": metadata,
    }


def target_from_event(event: dict[str, Any]) -> tuple[str | None, str | None]:
    if event.get("subject_id"):
        return "person", event["subject_id"]
    if event.get("plate_no"):
        return "vehicle", event["plate_no"]
    if event.get("device_id"):
        return "device", event["device_id"]
    return None, None


def ingest_event(conn, payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    event = normalize_event(payload)
    conn.execute(
        """
        INSERT OR REPLACE INTO events (
            id, ts, event_type, zone_id, sensor_id, subject_type, subject_id,
            plate_no, device_id, direction, speed_kmh, confidence, metadata
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event["id"],
            event["ts"],
            event["event_type"],
            event["zone_id"],
            event["sensor_id"],
            event["subject_type"],
            event["subject_id"],
            event["plate_no"],
            event["device_id"],
            event["direction"],
            event["speed_kmh"],
            event["confidence"],
            json.dumps(event["metadata"], ensure_ascii=False),
        ),
    )
    alerts = evaluate_event(conn, event)
    conn.commit()
    return event, alerts


def evaluate_event(conn, event: dict[str, Any]) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    metadata = event["metadata"]
    event_dt = parse_ts(event["ts"])

    alerts.extend(_watchlist_alerts(conn, event))
    alert = _first_seen_alert(conn, event)
    if alert:
        alerts.append(alert)

    if event.get("plate_no") and metadata.get("vehicle_origin") == "外地":
        alert = _create_alert(
            conn,
            event,
            "EXTERNAL_VEHICLE",
            "外来车辆首次进入重点开放区域",
            f"车辆 {event['plate_no']} 标记为外地来源，建议核验通行轨迹。",
            "low",
            "vehicle",
            event["plate_no"],
            {"vehicle_origin": "外地", "source": "plate"},
            cooldown_minutes=120,
        )
        if alert:
            alerts.append(alert)

    if metadata.get("vehicle_use") == "网约车":
        alert = _create_alert(
            conn,
            event,
            "RIDE_HAILING",
            "网约车进入客流密集点",
            f"网约车 {event.get('plate_no', '未知车辆')} 经过换乘或广场区域。",
            "low",
            "vehicle",
            event.get("plate_no"),
            {"vehicle_use": "网约车"},
            cooldown_minutes=90,
        )
        if alert:
            alerts.append(alert)

    if metadata.get("vehicle_type") == "电瓶车" and metadata.get("owner_match") is False:
        target_id = event.get("plate_no") or event.get("device_id")
        alert = _create_alert(
            conn,
            event,
            "E_BIKE_THEFT",
            "电瓶车人车不一致",
            "电瓶车出入口记录与登记车主不匹配，触发防盗核验。",
            "high",
            "vehicle",
            target_id,
            {"owner_match": False, "vehicle_type": "电瓶车"},
            cooldown_minutes=180,
        )
        if alert:
            alerts.append(alert)

    if event.get("direction") == "逆行" or metadata.get("behavior") == "wrong_way":
        alert = _create_alert(
            conn,
            event,
            "WRONG_WAY",
            "逆行行为",
            "通行方向与场景设定流向不一致，可能影响人流或车流秩序。",
            "medium",
            *target_from_event(event),
            evidence={"direction": event.get("direction"), "behavior": metadata.get("behavior")},
            cooldown_minutes=45,
        )
        if alert:
            alerts.append(alert)

    if event.get("speed_kmh") is not None:
        speed_alert = _speeding_alert(conn, event)
        if speed_alert:
            alerts.append(speed_alert)

    density_alert = _density_alert(conn, event)
    if density_alert:
        alerts.append(density_alert)

    if event.get("subject_id"):
        loitering = _loitering_alert(conn, event)
        if loitering:
            alerts.append(loitering)
        person_freq = _frequency_alert(
            conn,
            event,
            field="subject_id",
            value=event["subject_id"],
            rule_code="PERSON_FREQUENCY",
            title="同人频次异常",
            threshold=6,
            minutes=60,
            target_type="person",
        )
        if person_freq:
            alerts.append(person_freq)
        tracking = _tracking_alert(conn, event)
        if tracking:
            alerts.append(tracking)

    if event.get("plate_no"):
        vehicle_freq = _frequency_alert(
            conn,
            event,
            field="plate_no",
            value=event["plate_no"],
            rule_code="VEHICLE_FREQUENCY",
            title="同车频次异常",
            threshold=5,
            minutes=60,
            target_type="vehicle",
        )
        if vehicle_freq:
            alerts.append(vehicle_freq)

    if 0 <= event_dt.hour <= 5:
        night_count = _target_count(conn, event, hours=168, night_only=True)
        if night_count >= 2:
            target_type, target_id = target_from_event(event)
            alert = _create_alert(
                conn,
                event,
                "NIGHT_ACTIVITY",
                "昼伏夜出规律出现",
                "同一对象在凌晨时段多次经过开放场景，建议结合轨迹研判。",
                "medium",
                target_type,
                target_id,
                {"night_pass_count_7d": night_count},
                cooldown_minutes=240,
            )
            if alert:
                alerts.append(alert)

    return alerts


def _watchlist_alerts(conn, event: dict[str, Any]) -> list[dict[str, Any]]:
    identifiers = [
        ("person", event.get("subject_id")),
        ("vehicle", event.get("plate_no")),
        ("device", event.get("device_id")),
    ]
    alerts = []
    for target_type, target_id in identifiers:
        if not target_id:
            continue
        rows = conn.execute(
            """
            SELECT * FROM watchlist
            WHERE target_type = ? AND target_id = ?
            """,
            (target_type, target_id),
        ).fetchall()
        for row in rows:
            rule_code, title, default_desc = WATCHLIST_RULE.get(
                row["category"],
                ("WATCH_KEY_PERSON", "布控对象命中", "对象命中关注名单。"),
            )
            alert = _create_alert(
                conn,
                event,
                rule_code,
                title,
                row["description"] or default_desc,
                row["severity"],
                target_type,
                target_id,
                {"watchlist_category": row["category"], "watchlist_id": row["id"]},
                score=row["risk_score"],
                cooldown_minutes=240,
            )
            if alert:
                alerts.append(alert)
    return alerts


def _first_seen_alert(conn, event: dict[str, Any]) -> dict[str, Any] | None:
    target_type, target_id = target_from_event(event)
    if not target_id:
        return None
    metadata = event["metadata"]
    should_focus = metadata.get("first_seen_focus") is True
    if event.get("plate_no") and metadata.get("vehicle_origin") == "外地":
        should_focus = True
    if not should_focus:
        return None

    field = {"person": "subject_id", "vehicle": "plate_no", "device": "device_id"}[target_type]
    row = conn.execute(
        f"SELECT COUNT(*) AS total FROM events WHERE {field} = ? AND ts < ?",
        (target_id, event["ts"]),
    ).fetchone()
    if int(row["total"]) != 0:
        return None
    return _create_alert(
        conn,
        event,
        "FIRST_SEEN",
        "重点对象首次出现",
        f"{target_id} 为系统首次捕获对象，已纳入后续轨迹观察。",
        "medium",
        target_type,
        target_id,
        {"first_seen": True},
        cooldown_minutes=360,
    )


def _speeding_alert(conn, event: dict[str, Any]) -> dict[str, Any] | None:
    zone = conn.execute("SELECT speed_limit FROM zones WHERE id = ?", (event["zone_id"],)).fetchone()
    if not zone:
        return None
    speed = float(event["speed_kmh"])
    speed_limit = float(zone["speed_limit"])
    if speed <= speed_limit:
        return None
    severity = "high" if speed >= speed_limit * 1.35 else "medium"
    return _create_alert(
        conn,
        event,
        "SPEEDING",
        "车辆超速通过",
        f"通行速度 {speed:.1f} km/h，高于场景限速 {speed_limit:.0f} km/h。",
        severity,
        "vehicle" if event.get("plate_no") else "object",
        event.get("plate_no") or event.get("device_id"),
        {"speed_kmh": speed, "speed_limit": speed_limit},
        cooldown_minutes=60,
    )


def _density_alert(conn, event: dict[str, Any]) -> dict[str, Any] | None:
    zone = conn.execute(
        "SELECT capacity, name FROM zones WHERE id = ?",
        (event["zone_id"],),
    ).fetchone()
    if not zone:
        return None

    event_dt = parse_ts(event["ts"])
    cutoff = (event_dt - timedelta(minutes=15)).isoformat()
    row = conn.execute(
        """
        SELECT COUNT(DISTINCT COALESCE(subject_id, plate_no, device_id, id)) AS total
        FROM events
        WHERE zone_id = ? AND ts >= ? AND ts <= ?
        """,
        (event["zone_id"], cutoff, event["ts"]),
    ).fetchone()
    total = int(row["total"])
    capacity = int(zone["capacity"])
    ratio = total / max(capacity, 1)
    if ratio < 0.85:
        return None
    severity = "critical" if ratio >= 1.15 else "high"
    return _create_alert(
        conn,
        event,
        "DENSITY_HIGH",
        "片区密度接近阈值",
        f"{zone['name']} 15 分钟内通行对象 {total} 个，达到容量阈值 {ratio:.0%}。",
        severity,
        "zone",
        event["zone_id"],
        {"recent_15m_count": total, "capacity": capacity, "load_ratio": round(ratio, 2)},
        cooldown_minutes=25,
    )


def _loitering_alert(conn, event: dict[str, Any]) -> dict[str, Any] | None:
    event_dt = parse_ts(event["ts"])
    cutoff = (event_dt - timedelta(minutes=30)).isoformat()
    row = conn.execute(
        """
        SELECT COUNT(*) AS total, COUNT(DISTINCT sensor_id) AS sensors
        FROM events
        WHERE subject_id = ? AND zone_id = ? AND ts >= ? AND ts <= ?
        """,
        (event["subject_id"], event["zone_id"], cutoff, event["ts"]),
    ).fetchone()
    total = int(row["total"])
    sensors = int(row["sensors"])
    if total < 4 or sensors < 2:
        return None
    return _create_alert(
        conn,
        event,
        "LOITERING",
        "疑似徘徊",
        f"人员 {event['subject_id']} 30 分钟内在同一片区出现 {total} 次。",
        "medium",
        "person",
        event["subject_id"],
        {"appearances_30m": total, "sensor_count": sensors},
        cooldown_minutes=60,
    )


def _frequency_alert(
    conn,
    event: dict[str, Any],
    *,
    field: str,
    value: str,
    rule_code: str,
    title: str,
    threshold: int,
    minutes: int,
    target_type: str,
) -> dict[str, Any] | None:
    event_dt = parse_ts(event["ts"])
    cutoff = (event_dt - timedelta(minutes=minutes)).isoformat()
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS total, COUNT(DISTINCT zone_id) AS zones
        FROM events
        WHERE {field} = ? AND ts >= ? AND ts <= ?
        """,
        (value, cutoff, event["ts"]),
    ).fetchone()
    total = int(row["total"])
    if total < threshold:
        return None
    return _create_alert(
        conn,
        event,
        rule_code,
        title,
        f"{value} 在 {minutes} 分钟内通行 {total} 次，涉及 {row['zones']} 个片区。",
        "medium",
        target_type,
        value,
        {"count": total, "minutes": minutes, "zone_count": row["zones"]},
        cooldown_minutes=90,
    )


def _tracking_alert(conn, event: dict[str, Any]) -> dict[str, Any] | None:
    near_subject = event["metadata"].get("near_subject")
    if not near_subject:
        return None
    event_dt = parse_ts(event["ts"])
    cutoff = (event_dt - timedelta(minutes=25)).isoformat()
    rows = conn.execute(
        """
        SELECT metadata FROM events
        WHERE subject_id = ? AND ts >= ? AND ts <= ?
        """,
        (event["subject_id"], cutoff, event["ts"]),
    ).fetchall()
    pair_count = sum(1 for row in rows if load_json(row["metadata"]).get("near_subject") == near_subject)
    if pair_count < 3:
        return None
    return _create_alert(
        conn,
        event,
        "TRACKING",
        "疑似跟踪伴随",
        f"人员 {event['subject_id']} 与 {near_subject} 在 25 分钟内多次伴随出现。",
        "medium",
        "person",
        event["subject_id"],
        {"near_subject": near_subject, "pair_count_25m": pair_count},
        cooldown_minutes=90,
    )


def _target_count(conn, event: dict[str, Any], *, hours: int, night_only: bool = False) -> int:
    target_type, target_id = target_from_event(event)
    if not target_id:
        return 0
    field = {"person": "subject_id", "vehicle": "plate_no", "device": "device_id"}[target_type]
    event_dt = parse_ts(event["ts"])
    cutoff = (event_dt - timedelta(hours=hours)).isoformat()
    rows = conn.execute(
        f"SELECT ts FROM events WHERE {field} = ? AND ts >= ? AND ts <= ?",
        (target_id, cutoff, event["ts"]),
    ).fetchall()
    if not night_only:
        return len(rows)
    return sum(1 for row in rows if 0 <= parse_ts(row["ts"]).hour <= 5)


def _create_alert(
    conn,
    event: dict[str, Any],
    rule_code: str,
    title: str,
    description: str,
    severity: str,
    target_type: str | None,
    target_id: str | None,
    evidence: dict[str, Any] | None = None,
    *,
    score: int | None = None,
    cooldown_minutes: int = 30,
) -> dict[str, Any] | None:
    if not target_id:
        target_id = event["id"]
    event_dt = parse_ts(event["ts"])
    cutoff = (event_dt - timedelta(minutes=cooldown_minutes)).isoformat()
    existing = conn.execute(
        """
        SELECT id FROM alerts
        WHERE status = 'open'
          AND rule_code = ?
          AND zone_id = ?
          AND target_id = ?
          AND ts >= ?
        LIMIT 1
        """,
        (rule_code, event["zone_id"], target_id, cutoff),
    ).fetchone()
    if existing:
        return None

    alert = {
        "id": f"ALT-{uuid.uuid4().hex[:10].upper()}",
        "ts": event["ts"],
        "severity": severity,
        "rule_code": rule_code,
        "title": title,
        "description": description,
        "zone_id": event["zone_id"],
        "event_id": event["id"],
        "target_type": target_type,
        "target_id": target_id,
        "score": score if score is not None else SEVERITY_SCORE[severity],
        "status": "open",
        "evidence": evidence or {},
    }
    conn.execute(
        """
        INSERT INTO alerts (
            id, ts, severity, rule_code, title, description, zone_id, event_id,
            target_type, target_id, score, status, evidence
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            alert["id"],
            alert["ts"],
            alert["severity"],
            alert["rule_code"],
            alert["title"],
            alert["description"],
            alert["zone_id"],
            alert["event_id"],
            alert["target_type"],
            alert["target_id"],
            alert["score"],
            alert["status"],
            json.dumps(alert["evidence"], ensure_ascii=False),
        ),
    )
    return alert
