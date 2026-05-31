Copyright © 2026 MCIPhil ，Human-Intelligence-CN. All rights reserved.

This repository, including all code, documentation, and related materials (collectively "the Work"), is private and protected by copyright law and international treaties.

Unauthorized use, copying, modification, distribution, or creation of derivative works from this Work is strictly prohibited. No part of the Work may be reproduced, transmitted, or utilized in any form without explicit written permission from the copyright owner.

For permission requests, contact: zqcllwldd@126.com

# 开放通行公共安全治理系统

本 demo 通行路过-道路、地铁/公交、开放广场/景区。系统覆盖视频、人脸、车牌、MAC、RFID 接入，并提供总人数、总流量、趋势分析、片区密度、在逃、毒驾、外来车、首次出现、网约车、电瓶车防盗、徘徊、跟踪、逆行、同人同车频次、超速等治理能力。



## 启动

双击 `run_server.bat`，或在 PowerShell 中运行：

```powershell
.\run_server.ps1
```

也可以手动启动：

```powershell
& python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

打开浏览器访问：

```text
http://127.0.0.1:8000
```

API 文档：

```text
http://127.0.0.1:8000/docs
```

## 主要接口

- `GET /api/dashboard`：仪表盘统计、趋势、密度、预警、流水。
- `GET /api/zones`：片区和传感器清单。
- `GET /api/events`：通行事件流水。
- `POST /api/events`：接入单条视频/人脸/车牌/MAC/RFID 事件。
- `POST /api/events/bulk`：批量接入事件。
- `GET /api/alerts`：查询预警。
- `PATCH /api/alerts/{alert_id}/status`：闭环处理预警。
- `POST /api/simulate?count=35`：模拟实时接入。

## 数据说明

首次启动会自动创建 SQLite 数据库：

```text
data/scene1.sqlite3
```

如果想重新生成演示数据，停止服务后删除该数据库文件，再重新启动即可。
