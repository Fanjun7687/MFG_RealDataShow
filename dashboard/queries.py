"""看板数据访问层。

设计原则:
- 看板进程只「读」采集数据(readings / alerts / devices / heartbeat),不做采集;
- 唯一写操作是告警确认(ack_alert,低频小事务,WAL 下与采集写入互不阻塞);
- 每个查询用独立短连接,避免 Gradio 多线程复用连接的问题。
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

import pandas as pd

from common.config import AppConfig
from common.db import TIME_FMT, connect, now_str

METRIC_LABELS = {
    "temperature": "温度(°C)",
    "vibration": "振动(mm/s)",
    "spindle_speed": "转速(rpm)",
    "output_count": "产量计数",
}
LEVEL_ICONS = {"INFO": "ℹ️ INFO", "WARN": "⚠️ WARN", "CRIT": "🔴 CRIT"}
LEVEL_ORDER = ["CRIT", "WARN", "INFO"]

REALTIME_COLUMNS = ["设备ID", "名称", "位置", "状态", "最后上报"]
ALERT_COLUMNS = ["ID", "时间", "设备", "指标", "级别", "消息", "数值", "阈值", "状态"]
STAT_COLUMNS = ["级别", "未确认数"]
STATS_COLUMNS = ["设备", "指标", "平均", "最大", "最小", "样本数"]
OUTPUT_COLUMNS = ["设备", "本班产量(件)", "采样点数"]
RULE_COLUMNS = ["设备", "指标", "触发条件", "级别", "说明", "启用"]


def _metric_label(metric: str) -> str:
    return METRIC_LABELS.get(metric, metric)


def _empty(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


def _range_start(range_key: str) -> str | None:
    seconds = {"最近30分钟": 1800, "最近1小时": 3600, "最近8小时": 8 * 3600,
               "最近24小时": 24 * 3600, "最近7天": 7 * 86400}.get(range_key)
    if seconds is None:
        return None
    return (datetime.now() - timedelta(seconds=seconds)).strftime(TIME_FMT)


# ---------------------------------------------------------------- 采集服务状态
def get_collector_status(cfg: AppConfig) -> dict:
    """读取采集服务心跳;超过 2 倍离线阈值没有心跳视为异常。"""
    conn = connect(cfg.db_path())
    try:
        row = conn.execute(
            "SELECT last_ts, cycle_seconds, status FROM collector_heartbeat WHERE id = 1"
        ).fetchone()
    finally:
        conn.close()

    if row is None or not row["last_ts"]:
        return {"alive": False, "text": "尚未启动", "last_ts": "", "cycle": None}

    last = datetime.strptime(row["last_ts"], TIME_FMT)
    seconds_ago = int((datetime.now() - last).total_seconds())
    alive = seconds_ago <= max(cfg.collector.stale_threshold_seconds * 2, 60)
    return {
        "alive": alive,
        "text": "正常" if alive else f"心跳中断({seconds_ago}s 前)",
        "last_ts": row["last_ts"],
        "cycle": row["cycle_seconds"],
    }


# ---------------------------------------------------------------- 实时状态页
def get_latest_readings(cfg: AppConfig) -> pd.DataFrame:
    """每台设备各指标的最新值(一设备一行)。"""
    conn = connect(cfg.db_path())
    try:
        devices = conn.execute(
            "SELECT device_id, name, location FROM devices WHERE enabled = 1 ORDER BY device_id"
        ).fetchall()
        latest = conn.execute(
            """
            SELECT r.device_id, r.metric, r.value, r.ts
            FROM readings r
            JOIN (
                SELECT device_id, metric, MAX(ts) AS max_ts
                FROM readings GROUP BY device_id, metric
            ) m ON r.device_id = m.device_id AND r.metric = m.metric AND r.ts = m.max_ts
            """
        ).fetchall()
    finally:
        conn.close()

    metrics: list[str] = []
    for row in latest:
        if row["metric"] not in metrics:
            metrics.append(row["metric"])
    ordered_metrics = [m for m in METRIC_LABELS if m in metrics] + [
        m for m in metrics if m not in METRIC_LABELS
    ]

    devices_map: dict[str, dict] = {}
    for dev in devices:
        devices_map[dev["device_id"]] = {
            "设备ID": dev["device_id"],
            "名称": dev["name"],
            "位置": dev["location"],
            "状态": "⚪ 无数据",
            "最后上报": "-",
            **{metric: None for metric in ordered_metrics},
        }

    now = datetime.now()
    for row in latest:
        item = devices_map.setdefault(
            row["device_id"],
            {"设备ID": row["device_id"], "名称": "", "位置": "", "状态": "-", "最后上报": "-"},
        )
        item[row["metric"]] = round(row["value"], 2)
        if item["最后上报"] == "-" or row["ts"] > item["最后上报"]:
            item["最后上报"] = row["ts"]

    stale_seconds = cfg.collector.stale_threshold_seconds
    for item in devices_map.values():
        if item["最后上报"] == "-":
            continue
        ago = (now - datetime.strptime(item["最后上报"], TIME_FMT)).total_seconds()
        item["状态"] = "🟢 在线" if ago <= stale_seconds else "🔴 离线"

    columns = REALTIME_COLUMNS + ordered_metrics
    if not devices_map:
        return _empty(columns)
    return pd.DataFrame(list(devices_map.values()), columns=columns)


# ---------------------------------------------------------------- 历史趋势页
def get_device_options(cfg: AppConfig) -> list[str]:
    conn = connect(cfg.db_path())
    try:
        rows = conn.execute(
            "SELECT device_id FROM devices WHERE enabled = 1 ORDER BY device_id"
        ).fetchall()
    finally:
        conn.close()
    return [row[0] for row in rows]


def get_metric_options(cfg: AppConfig, device_id: str) -> list[str]:
    if not device_id:
        return []
    conn = connect(cfg.db_path())
    try:
        rows = conn.execute(
            "SELECT DISTINCT metric FROM readings WHERE device_id = ? ORDER BY metric",
            (device_id,),
        ).fetchall()
    finally:
        conn.close()
    metrics = [row[0] for row in rows]
    return [m for m in METRIC_LABELS if m in metrics] + [m for m in metrics if m not in METRIC_LABELS]


def get_series(cfg: AppConfig, device_id: str, metrics: list[str],
               start_ts: str, end_ts: str, max_points: int = 2000) -> pd.DataFrame:
    """查询 [start_ts, end_ts) 区间数据;点数过多时等间隔抽样。"""
    if not device_id or not metrics:
        return _empty(["ts", "metric", "value"])

    placeholders = ",".join("?" * len(metrics))
    conn = connect(cfg.db_path())
    try:
        rows = conn.execute(
            f"SELECT ts, metric, value FROM readings "
            f"WHERE device_id = ? AND metric IN ({placeholders}) AND ts >= ? AND ts < ? "
            f"ORDER BY ts",
            (device_id, *metrics, start_ts, end_ts),
        ).fetchall()
    finally:
        conn.close()

    df = pd.DataFrame([tuple(row) for row in rows], columns=["ts", "metric", "value"])
    limit = max_points * max(1, len(metrics))
    if len(df) > limit:
        stride = math.ceil(len(df) / limit)
        df = df.iloc[::stride]
    return df


# ---------------------------------------------------------------- 班次报表页
def get_shift_report(cfg: AppConfig, date_str: str, shift_name: str):
    """返回 (指标统计 df, 产量 df)。产量 = 班次内 output_count 的 max-min。"""
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return (
            pd.DataFrame(["日期格式应为 YYYY-MM-DD"], columns=["提示"]),
            pd.DataFrame(["请检查日期"], columns=["提示"]),
        )

    if shift_name == "全天":
        start_ts, end_ts = f"{date_str} 00:00:00", f"{date_str} 24:00:00"
    else:
        window = cfg.shift_window(date_str, shift_name)
        if window is None:
            return (
                pd.DataFrame([f"未定义班次: {shift_name}"], columns=["提示"]),
                pd.DataFrame(["请检查 config.toml"], columns=["提示"]),
            )
        start_ts, end_ts = window

    conn = connect(cfg.db_path())
    try:
        rows = conn.execute(
            """
            SELECT device_id, metric, AVG(value), MAX(value), MIN(value), COUNT(*)
            FROM readings
            WHERE ts >= ? AND ts < ?
            GROUP BY device_id, metric
            ORDER BY device_id, metric
            """,
            (start_ts, end_ts),
        ).fetchall()
    finally:
        conn.close()

    stats: list[tuple] = []
    output: list[tuple] = []
    for device_id, metric, avg_v, max_v, min_v, count in rows:
        if metric == "output_count":
            output.append((device_id, int(round(max_v - min_v)), int(count)))
        else:
            stats.append(
                (device_id, _metric_label(metric), round(avg_v, 2), round(max_v, 2),
                 round(min_v, 2), int(count))
            )
    return (
        pd.DataFrame(stats, columns=STATS_COLUMNS),
        pd.DataFrame(output, columns=OUTPUT_COLUMNS),
    )


# ---------------------------------------------------------------- 告警页
def get_alerts(cfg: AppConfig, levels: list[str], status: str,
               range_key: str, limit: int = 500) -> pd.DataFrame:
    clauses: list[str] = []
    params: list = []

    if levels:
        clauses.append(f"level IN ({','.join('?' * len(levels))})")
        params.extend(levels)
    if status == "未确认":
        clauses.append("ack = 0")
    elif status == "已确认":
        clauses.append("ack = 1")

    start_ts = _range_start(range_key)
    if start_ts:
        clauses.append("ts >= ?")
        params.append(start_ts)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    conn = connect(cfg.db_path())
    try:
        rows = conn.execute(
            f"SELECT id, ts, device_id, metric, level, message, value, threshold, ack, ack_by "
            f"FROM alerts {where} ORDER BY ts DESC, id DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return _empty(ALERT_COLUMNS)

    df = pd.DataFrame(
        [tuple(row) for row in rows],
        columns=["id", "ts", "device_id", "metric", "level", "message",
                 "value", "threshold", "ack", "ack_by"],
    )
    return pd.DataFrame(
        {
            "ID": df["id"],
            "时间": df["ts"],
            "设备": df["device_id"],
            "指标": df["metric"],
            "级别": df["level"].map(lambda v: LEVEL_ICONS.get(v, v)),
            "消息": df["message"],
            "数值": df["value"].round(2),
            "阈值": df["threshold"].round(2),
            "状态": [
                "未确认" if not ack else f"✅ {by or '已确认'}"
                for ack, by in zip(df["ack"], df["ack_by"])
            ],
        }
    )


def get_alert_stats(cfg: AppConfig) -> pd.DataFrame:
    conn = connect(cfg.db_path())
    try:
        rows = conn.execute(
            "SELECT level, COUNT(*) FROM alerts WHERE ack = 0 GROUP BY level"
        ).fetchall()
    finally:
        conn.close()
    counts = {row[0]: row[1] for row in rows}
    return pd.DataFrame(
        [(LEVEL_ICONS.get(level, level), counts.get(level, 0)) for level in LEVEL_ORDER],
        columns=STAT_COLUMNS,
    )


def get_rules(cfg: AppConfig) -> pd.DataFrame:
    conn = connect(cfg.db_path())
    try:
        rows = conn.execute(
            "SELECT device_id, metric, op, threshold, level, message, enabled "
            "FROM alarm_rules ORDER BY device_id, metric, op"
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return _empty(RULE_COLUMNS)
    return pd.DataFrame(
        [
            (r["device_id"], r["metric"], f"{r['op']} {r['threshold']:g}",
             r["level"], r["message"], "✓" if r["enabled"] else "✗")
            for r in rows
        ],
        columns=RULE_COLUMNS,
    )


def ack_alert(cfg: AppConfig, alert_id: int, operator: str) -> str:
    """看板唯一写操作:告警确认(低频小事务)。"""
    conn = connect(cfg.db_path())
    try:
        cursor = conn.execute(
            "UPDATE alerts SET ack = 1, ack_by = ?, ack_ts = ? WHERE id = ? AND ack = 0",
            (operator, now_str(), alert_id),
        )
        conn.commit()
        changed = cursor.rowcount
    finally:
        conn.close()
    if changed:
        return f"✅ 告警 #{alert_id} 已由 {operator} 确认"
    return f"⚠️ 告警 #{alert_id} 不存在或已被确认"
