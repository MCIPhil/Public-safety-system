const state = {
  hours: 24,
  zoneId: "",
};

const severityText = {
  low: "提示",
  medium: "中危",
  high: "高危",
  critical: "紧急",
};

const eventTypeText = {
  person_pass: "人脸/视频",
  vehicle_pass: "车牌/车辆",
  mac_seen: "MAC",
  rfid_seen: "RFID",
};

document.addEventListener("DOMContentLoaded", async () => {
  bindControls();
  await loadZones();
  await loadDashboard();
});

function bindControls() {
  document.querySelector("#hoursSelect").addEventListener("change", (event) => {
    state.hours = Number(event.target.value);
    loadDashboard();
  });
  document.querySelector("#zoneSelect").addEventListener("change", (event) => {
    state.zoneId = event.target.value;
    loadDashboard();
  });
  document.querySelector("#refreshBtn").addEventListener("click", loadDashboard);
  document.querySelector("#simulateBtn").addEventListener("click", simulateEvents);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || response.statusText);
  }
  return response.json();
}

async function loadZones() {
  const zones = await api("/api/zones");
  const select = document.querySelector("#zoneSelect");
  zones.forEach((zone) => {
    const option = document.createElement("option");
    option.value = zone.id;
    option.textContent = zone.name;
    select.appendChild(option);
  });
}

async function loadDashboard() {
  setBusy(true);
  try {
    const query = new URLSearchParams({ hours: state.hours });
    if (state.zoneId) query.set("zone_id", state.zoneId);
    const data = await api(`/api/dashboard?${query.toString()}`);
    renderSummary(data.summary);
    renderTrend(data.trend);
    renderZones(data.zones);
    renderAlerts(data.alerts);
    renderTargets(data.top_targets);
    renderRules(data.rule_mix);
    renderEvents(data.events);
    document.querySelector("#lastUpdated").textContent = `更新 ${formatTime(data.window.end)}`;
  } catch (error) {
    showError(error.message);
  } finally {
    setBusy(false);
  }
}

async function simulateEvents() {
  const button = document.querySelector("#simulateBtn");
  button.disabled = true;
  button.textContent = "接入中";
  try {
    await api("/api/simulate?count=35", { method: "POST" });
    await loadDashboard();
  } catch (error) {
    showError(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "模拟接入";
  }
}

function renderSummary(summary) {
  document.querySelector("#kpiTotal").textContent = number(summary.total_flow);
  document.querySelector("#kpiPeople").textContent = number(summary.person_flow);
  document.querySelector("#kpiVehicles").textContent = number(summary.vehicle_flow);
  document.querySelector("#kpiAlerts").textContent = number(summary.open_alerts);
  document.querySelector("#kpiHigh").textContent = `高危 ${number(summary.high_alerts)} 条 · 首现 ${number(summary.first_seen)} 条`;
}

function renderTrend(trend) {
  const el = document.querySelector("#trendChart");
  if (!trend.length) {
    el.innerHTML = `<div class="empty">暂无趋势数据</div>`;
    return;
  }
  const width = 720;
  const height = 268;
  const pad = { left: 42, right: 14, top: 18, bottom: 34 };
  const chartW = width - pad.left - pad.right;
  const chartH = height - pad.top - pad.bottom;
  const max = Math.max(...trend.map((d) => d.total), 1);
  const step = chartW / trend.length;
  const barW = Math.max(6, step * 0.58);
  const bars = trend
    .map((d, idx) => {
      const x = pad.left + idx * step + (step - barW) / 2;
      const personH = (d.person / max) * chartH;
      const vehicleH = (d.vehicle / max) * chartH;
      const deviceH = (d.device / max) * chartH;
      const base = pad.top + chartH;
      const label = idx % Math.ceil(trend.length / 6) === 0 ? `<text class="axis-text" x="${x}" y="${height - 10}">${hourLabel(d.hour)}</text>` : "";
      return `
        <g>
          <rect x="${x}" y="${base - personH}" width="${barW}" height="${personH}" rx="2" fill="#16715f"></rect>
          <rect x="${x}" y="${base - personH - vehicleH}" width="${barW}" height="${vehicleH}" rx="2" fill="#2b65c8"></rect>
          <rect x="${x}" y="${base - personH - vehicleH - deviceH}" width="${barW}" height="${deviceH}" rx="2" fill="#b86b00"></rect>
          ${label}
        </g>`;
    })
    .join("");
  const grid = [0, 0.25, 0.5, 0.75, 1]
    .map((ratio) => {
      const y = pad.top + chartH - chartH * ratio;
      return `<line x1="${pad.left}" x2="${width - pad.right}" y1="${y}" y2="${y}" stroke="#dce4df" stroke-width="1"></line>
        <text class="axis-text" x="8" y="${y + 4}">${Math.round(max * ratio)}</text>`;
    })
    .join("");
  el.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="通行流量趋势">
      ${grid}
      ${bars}
      <g transform="translate(${width - 246}, 12)">
        <rect width="232" height="28" rx="6" fill="#ffffff" stroke="#dce4df"></rect>
        <circle cx="16" cy="14" r="5" fill="#16715f"></circle><text class="axis-text" x="26" y="18">人员</text>
        <circle cx="82" cy="14" r="5" fill="#2b65c8"></circle><text class="axis-text" x="92" y="18">车辆</text>
        <circle cx="148" cy="14" r="5" fill="#b86b00"></circle><text class="axis-text" x="158" y="18">MAC/RFID</text>
      </g>
    </svg>`;
}

function renderZones(zones) {
  const el = document.querySelector("#zoneDensity");
  el.innerHTML = zones
    .map((zone) => {
      const pct = Math.min(zone.load * 100, 140);
      const cls = zone.load >= 1 ? "danger" : zone.load >= 0.75 ? "warn" : "";
      return `
        <div class="zone-item">
          <div class="zone-title"><span>${zone.name}</span><span>${zone.status}</span></div>
          <div class="load-track"><div class="load-bar ${cls}" style="width:${Math.min(pct, 100)}%"></div></div>
          <div class="zone-meta">
            <span>15 分钟 ${number(zone.recent_count)} 个对象</span>
            <span>容量 ${number(zone.capacity)} · 负荷 ${Math.round(zone.load * 100)}%</span>
          </div>
        </div>`;
    })
    .join("");
}

function renderAlerts(alerts) {
  const el = document.querySelector("#alertsList");
  if (!alerts.length) {
    el.innerHTML = `<div class="empty">当前没有打开预警</div>`;
    return;
  }
  el.innerHTML = alerts
    .map((alert) => {
      return `
      <div class="alert-item">
        <div class="alert-title-row">
          <strong>${alert.title}</strong>
          <span class="severity ${alert.severity}">${severityText[alert.severity] || alert.severity}</span>
        </div>
        <p>${alert.description}</p>
        <div class="alert-meta">
          <span>${alert.zone_name || alert.zone_id} · ${alert.target_id || "未知对象"}</span>
          <span>${formatTime(alert.ts)}</span>
        </div>
        <button class="close-alert" type="button" data-alert-id="${alert.id}">闭环处理</button>
      </div>`;
    })
    .join("");
  el.querySelectorAll(".close-alert").forEach((button) => {
    button.addEventListener("click", () => closeAlert(button.dataset.alertId));
  });
}

async function closeAlert(alertId) {
  await api(`/api/alerts/${alertId}/status`, {
    method: "PATCH",
    body: JSON.stringify({ status: "closed" }),
  });
  await loadDashboard();
}

function renderTargets(targets) {
  const el = document.querySelector("#topTargets");
  if (!targets.length) {
    el.innerHTML = `<div class="empty">暂无高频对象</div>`;
    return;
  }
  el.innerHTML = targets
    .map((target) => {
      return `
      <div class="target-item">
        <div class="target-title">
          <span>${target.target_id}</span>
          <span>${number(target.total)} 次</span>
        </div>
        <div class="target-meta">
          <span>涉及 ${number(target.zones)} 个片区</span>
          <span>同人/同车频次</span>
        </div>
      </div>`;
    })
    .join("");
}

function renderRules(rules) {
  const el = document.querySelector("#ruleMix");
  if (!rules.length) {
    el.innerHTML = `<div class="empty">暂无规则命中</div>`;
    return;
  }
  el.innerHTML = rules
    .map((rule) => {
      return `
      <div class="rule-item">
        <strong>${number(rule.total)}</strong>
        <span>${ruleName(rule.rule_code)} · ${severityText[rule.severity] || rule.severity}</span>
      </div>`;
    })
    .join("");
}

function renderEvents(events) {
  const el = document.querySelector("#eventRows");
  el.innerHTML = events
    .map((event) => {
      const target = event.subject_id || event.plate_no || event.device_id || "未知对象";
      const movement = event.speed_kmh ? `${event.direction || "通行"} · ${event.speed_kmh} km/h` : event.direction || "经过";
      return `
        <tr>
          <td>${formatTime(event.ts)}</td>
          <td><span class="event-type">${eventTypeText[event.event_type] || event.event_type}</span></td>
          <td><strong>${event.zone_name || event.zone_id}</strong><span class="muted">${event.sensor_name || event.sensor_id || ""}</span></td>
          <td>${target}</td>
          <td>${movement}</td>
        </tr>`;
    })
    .join("");
}

function ruleName(code) {
  const map = {
    WATCH_FUGITIVE: "在逃命中",
    WATCH_DRUG_DRIVE: "毒驾命中",
    WATCH_KEY_PERSON: "关注人员",
    FIRST_SEEN: "首次出现",
    EXTERNAL_VEHICLE: "外来车辆",
    RIDE_HAILING: "网约车",
    E_BIKE_THEFT: "电瓶车防盗",
    DENSITY_HIGH: "密度过高",
    LOITERING: "徘徊",
    TRACKING: "跟踪",
    WRONG_WAY: "逆行",
    PERSON_FREQUENCY: "同人频次",
    VEHICLE_FREQUENCY: "同车频次",
    SPEEDING: "超速",
    NIGHT_ACTIVITY: "昼伏夜出",
  };
  return map[code] || code;
}

function formatTime(value) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function hourLabel(value) {
  return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit" }).format(new Date(value));
}

function number(value) {
  return new Intl.NumberFormat("zh-CN").format(value || 0);
}

function setBusy(isBusy) {
  document.querySelector("#refreshBtn").disabled = isBusy;
}

function showError(message) {
  console.error(message);
  document.querySelector("#alertsList").innerHTML = `<div class="empty">加载失败：${message}</div>`;
}
