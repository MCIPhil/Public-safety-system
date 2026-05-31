from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")

ZONES = [
    {
        "id": "R001",
        "name": "主干道东向卡口",
        "scene_type": "道路",
        "capacity": 130,
        "area_m2": 1800,
        "speed_limit": 60,
        "description": "城市主干道与开放广场连接通道，重点关注车流、超速、外来车。",
    },
    {
        "id": "M001",
        "name": "地铁 A 口换乘通道",
        "scene_type": "地铁/公交",
        "capacity": 220,
        "area_m2": 950,
        "speed_limit": 20,
        "description": "早晚高峰通行密集区域，重点关注人流密度、逆行和随行异常。",
    },
    {
        "id": "P001",
        "name": "人民广场开放区",
        "scene_type": "开放广场",
        "capacity": 120,
        "area_m2": 4200,
        "speed_limit": 15,
        "description": "开放式人群聚集区域，重点关注徘徊、跟踪、在逃人员和片区密度。",
    },
    {
        "id": "S001",
        "name": "景区东入口",
        "scene_type": "景区",
        "capacity": 160,
        "area_m2": 2600,
        "speed_limit": 25,
        "description": "游客入口和网约车落客点，重点关注首次出现、外来车辆和走失风险。",
    },
]

SENSORS = [
    ("CAM-R001-1", "R001", "道路视频枪机", "video"),
    ("PLATE-R001-1", "R001", "东向车牌识别", "plate"),
    ("MAC-R001-1", "R001", "路侧 MAC 探针", "mac"),
    ("FACE-M001-1", "M001", "A 口人脸抓拍", "face"),
    ("CAM-M001-1", "M001", "换乘通道视频", "video"),
    ("MAC-M001-1", "M001", "地铁口 MAC 探针", "mac"),
    ("FACE-P001-1", "P001", "广场北侧人脸抓拍", "face"),
    ("CAM-P001-1", "P001", "广场全景视频", "video"),
    ("RFID-P001-1", "P001", "电瓶车 RFID 读写器", "rfid"),
    ("FACE-S001-1", "S001", "景区入口人脸抓拍", "face"),
    ("PLATE-S001-1", "S001", "景区落客区车牌识别", "plate"),
    ("MAC-S001-1", "S001", "景区入口 MAC 探针", "mac"),
]

WATCHLIST = [
    ("WL-P-001", "person", "PESCAPE001", "在逃", "critical", 96, "在逃人员库命中，需要立即核验身份。"),
    ("WL-P-009", "person", "PKEY009", "关注人口", "high", 82, "关注人口在开放区域出现，建议联动属地核查。"),
    ("WL-V-001", "vehicle", "京N5D234", "毒驾", "high", 88, "毒驾风险车辆，需结合驾驶人和轨迹快速研判。"),
    ("WL-D-001", "device", "RFID-EB-7788", "被盗电瓶车", "high", 86, "被盗电瓶车 RFID 标签命中。"),
]


def ensure_reference_data(conn) -> None:
    for zone in ZONES:
        conn.execute(
            """
            INSERT OR IGNORE INTO zones
            (id, name, scene_type, capacity, area_m2, speed_limit, description)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                zone["id"],
                zone["name"],
                zone["scene_type"],
                zone["capacity"],
                zone["area_m2"],
                zone["speed_limit"],
                zone["description"],
            ),
        )
    for sensor_id, zone_id, name, sensor_type in SENSORS:
        conn.execute(
            """
            INSERT OR IGNORE INTO sensors
            (id, zone_id, name, sensor_type, status)
            VALUES (?, ?, ?, ?, 'online')
            """,
            (sensor_id, zone_id, name, sensor_type),
        )
    for row in WATCHLIST:
        conn.execute(
            """
            INSERT OR IGNORE INTO watchlist
            (id, target_type, target_id, category, severity, risk_score, description)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            row,
        )
    conn.commit()


def build_seed_events(now: datetime | None = None) -> list[dict[str, Any]]:
    now = (now or datetime.now(TZ)).astimezone(TZ).replace(microsecond=0)
    rnd = random.Random(20260531)
    events: list[dict[str, Any]] = []

    for idx in range(180):
        minutes_ago = rnd.randint(20, 24 * 60)
        zone_id = rnd.choice(["R001", "M001", "P001", "S001"])
        if zone_id == "R001":
            events.append(_vehicle_event(rnd, now - timedelta(minutes=minutes_ago), zone_id))
        elif rnd.random() < 0.72:
            events.append(_person_event(rnd, now - timedelta(minutes=minutes_ago), zone_id))
        elif rnd.random() < 0.55:
            events.append(_vehicle_event(rnd, now - timedelta(minutes=minutes_ago), zone_id))
        else:
            events.append(_device_event(rnd, now - timedelta(minutes=minutes_ago), zone_id))

    for idx in range(125):
        ts = now - timedelta(minutes=rnd.randint(0, 14), seconds=rnd.randint(0, 59))
        events.append(
            {
                "timestamp": ts,
                "event_type": "person_pass",
                "zone_id": "P001",
                "sensor_id": rnd.choice(["FACE-P001-1", "CAM-P001-1"]),
                "subject_type": "person",
                "subject_id": f"P{2200 + idx:04d}",
                "direction": rnd.choice(["进入", "经过", "离开"]),
                "confidence": round(rnd.uniform(0.82, 0.97), 2),
                "metadata": {"crowd_batch": True, "source": "video_face"},
            }
        )

    events.extend(_risk_scenarios(now))
    events.sort(key=lambda item: item["timestamp"])
    return events


def build_live_events(count: int = 30, now: datetime | None = None) -> list[dict[str, Any]]:
    now = (now or datetime.now(TZ)).astimezone(TZ).replace(microsecond=0)
    rnd = random.Random()
    events = []
    for idx in range(count):
        zone_id = rnd.choice(["R001", "M001", "P001", "S001"])
        ts = now - timedelta(seconds=(count - idx) * rnd.randint(4, 12))
        roll = rnd.random()
        if roll < 0.55:
            events.append(_person_event(rnd, ts, zone_id))
        elif roll < 0.88:
            events.append(_vehicle_event(rnd, ts, zone_id))
        else:
            events.append(_device_event(rnd, ts, zone_id))

    if count >= 10:
        events.append(
            {
                "timestamp": now - timedelta(seconds=5),
                "event_type": "person_pass",
                "zone_id": "P001",
                "sensor_id": "FACE-P001-1",
                "subject_type": "person",
                "subject_id": "PKEY009",
                "direction": "经过",
                "confidence": 0.91,
                "metadata": {"simulation": "live_key_person"},
            }
        )
    events.sort(key=lambda item: item["timestamp"])
    return events


def _risk_scenarios(now: datetime) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = [
        {
            "timestamp": now - timedelta(minutes=10),
            "event_type": "person_pass",
            "zone_id": "P001",
            "sensor_id": "FACE-P001-1",
            "subject_type": "person",
            "subject_id": "PESCAPE001",
            "direction": "进入",
            "confidence": 0.94,
            "metadata": {"source": "face", "control_level": "red"},
        },
        {
            "timestamp": now - timedelta(minutes=9),
            "event_type": "vehicle_pass",
            "zone_id": "R001",
            "sensor_id": "PLATE-R001-1",
            "plate_no": "京N5D234",
            "direction": "东向西",
            "speed_kmh": 82,
            "confidence": 0.96,
            "metadata": {"vehicle_origin": "本地", "vehicle_type": "小客车"},
        },
        {
            "timestamp": now - timedelta(minutes=8),
            "event_type": "vehicle_pass",
            "zone_id": "S001",
            "sensor_id": "PLATE-S001-1",
            "plate_no": "沪A73K91",
            "direction": "进入",
            "speed_kmh": 18,
            "confidence": 0.93,
            "metadata": {"vehicle_origin": "外地", "first_seen_focus": True, "vehicle_type": "小客车"},
        },
        {
            "timestamp": now - timedelta(minutes=7),
            "event_type": "vehicle_pass",
            "zone_id": "M001",
            "sensor_id": "CAM-M001-1",
            "plate_no": "京BD12345",
            "direction": "临停",
            "speed_kmh": 12,
            "confidence": 0.88,
            "metadata": {"vehicle_use": "网约车", "vehicle_origin": "本地"},
        },
        {
            "timestamp": now - timedelta(minutes=6),
            "event_type": "rfid_seen",
            "zone_id": "P001",
            "sensor_id": "RFID-P001-1",
            "device_id": "RFID-EB-7788",
            "plate_no": "电A7788",
            "direction": "离开",
            "confidence": 0.98,
            "metadata": {"vehicle_type": "电瓶车", "owner_match": False},
        },
        {
            "timestamp": now - timedelta(minutes=5),
            "event_type": "person_pass",
            "zone_id": "M001",
            "sensor_id": "CAM-M001-1",
            "subject_type": "person",
            "subject_id": "PWRONG001",
            "direction": "逆行",
            "confidence": 0.89,
            "metadata": {"behavior": "wrong_way"},
        },
    ]

    for idx, minute in enumerate([29, 22, 16, 9]):
        events.append(
            {
                "timestamp": now - timedelta(minutes=minute),
                "event_type": "person_pass",
                "zone_id": "P001",
                "sensor_id": "FACE-P001-1" if idx % 2 == 0 else "CAM-P001-1",
                "subject_type": "person",
                "subject_id": "PLOITER007",
                "direction": "经过",
                "confidence": 0.9,
                "metadata": {"behavior_hint": "loitering"},
            }
        )

    for idx, minute in enumerate([24, 18, 12]):
        events.append(
            {
                "timestamp": now - timedelta(minutes=minute),
                "event_type": "person_pass",
                "zone_id": "P001",
                "sensor_id": "CAM-P001-1",
                "subject_type": "person",
                "subject_id": "PFOLLOW008",
                "direction": "经过",
                "confidence": 0.87,
                "metadata": {"near_subject": "PTARGET002"},
            }
        )

    for idx, minute in enumerate([54, 42, 31, 20, 11]):
        events.append(
            {
                "timestamp": now - timedelta(minutes=minute),
                "event_type": "vehicle_pass",
                "zone_id": "R001",
                "sensor_id": "PLATE-R001-1",
                "plate_no": "冀F8R888",
                "direction": "东向西",
                "speed_kmh": 48 + idx,
                "confidence": 0.91,
                "metadata": {"vehicle_origin": "外地", "vehicle_type": "小客车"},
            }
        )

    night_base = now.replace(hour=2, minute=20, second=0)
    if night_base > now:
        night_base = night_base - timedelta(days=1)
    for idx, day_offset in enumerate([1, 0]):
        events.append(
            {
                "timestamp": night_base - timedelta(days=day_offset),
                "event_type": "person_pass",
                "zone_id": "S001",
                "sensor_id": "FACE-S001-1",
                "subject_type": "person",
                "subject_id": "PNIGHT003",
                "direction": "经过",
                "confidence": 0.86,
                "metadata": {"night_activity": True},
            }
        )
    return events


def _person_event(rnd: random.Random, ts: datetime, zone_id: str) -> dict[str, Any]:
    sensor_map = {
        "M001": ["FACE-M001-1", "CAM-M001-1"],
        "P001": ["FACE-P001-1", "CAM-P001-1"],
        "S001": ["FACE-S001-1"],
        "R001": ["CAM-R001-1"],
    }
    return {
        "timestamp": ts,
        "event_type": "person_pass",
        "zone_id": zone_id,
        "sensor_id": rnd.choice(sensor_map[zone_id]),
        "subject_type": "person",
        "subject_id": f"P{rnd.randint(1000, 1160):04d}",
        "direction": rnd.choice(["进入", "离开", "经过"]),
        "confidence": round(rnd.uniform(0.78, 0.98), 2),
        "metadata": {"source": rnd.choice(["video", "face"])},
    }


def _vehicle_event(rnd: random.Random, ts: datetime, zone_id: str) -> dict[str, Any]:
    sensor_map = {
        "R001": ["PLATE-R001-1", "CAM-R001-1"],
        "S001": ["PLATE-S001-1"],
        "M001": ["CAM-M001-1"],
        "P001": ["CAM-P001-1"],
    }
    origin = rnd.choice(["本地", "本地", "本地", "外地"])
    vehicle_use = rnd.choice(["社会车辆", "社会车辆", "网约车", "物流"])
    return {
        "timestamp": ts,
        "event_type": "vehicle_pass",
        "zone_id": zone_id,
        "sensor_id": rnd.choice(sensor_map[zone_id]),
        "plate_no": _plate_no(rnd),
        "direction": rnd.choice(["进入", "离开", "东向西", "西向东", "临停"]),
        "speed_kmh": round(rnd.uniform(8, 72), 1),
        "confidence": round(rnd.uniform(0.82, 0.99), 2),
        "metadata": {
            "vehicle_origin": origin,
            "vehicle_type": rnd.choice(["小客车", "小客车", "电瓶车", "面包车"]),
            "vehicle_use": vehicle_use,
        },
    }


def _device_event(rnd: random.Random, ts: datetime, zone_id: str) -> dict[str, Any]:
    sensor_map = {
        "R001": ["MAC-R001-1"],
        "M001": ["MAC-M001-1"],
        "P001": ["RFID-P001-1"],
        "S001": ["MAC-S001-1"],
    }
    sensor_id = rnd.choice(sensor_map[zone_id])
    event_type = "rfid_seen" if sensor_id.startswith("RFID") else "mac_seen"
    prefix = "RFID" if event_type == "rfid_seen" else "MAC"
    return {
        "timestamp": ts,
        "event_type": event_type,
        "zone_id": zone_id,
        "sensor_id": sensor_id,
        "device_id": f"{prefix}-{rnd.randint(10000, 99999)}",
        "direction": rnd.choice(["进入", "离开", "经过"]),
        "confidence": round(rnd.uniform(0.75, 0.96), 2),
        "metadata": {"source": prefix.lower()},
    }


def _plate_no(rnd: random.Random) -> str:
    province = rnd.choice(["京A", "京B", "冀F", "津C", "沪A", "粤B"])
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ0123456789"
    return province + "".join(rnd.choice(chars) for _ in range(5))
