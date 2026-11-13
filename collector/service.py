"""采集服务核心逻辑。"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

from collector.adapters import build_adapter
from common.config import AppConfig, RuleConfig
from common.db import TIME_FMT, connect, ensure_db, now_str

OPS = {
    ">": lambda v, t: v > t,
    "<": lambda v, t: v < t,
    ">=": lambda v, t: v >= t,
    "<=": lambda v, t: v <= t,
}


class CollectorService:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self._adapters: dict = {}
        self._last_cycle_seconds: float = 0.0
        self._last_prune: datetime | None = None

    # ---------- 启动准备 ----------
    def _seed(self, conn) -> None:
        """从 config.toml 播种设备台账与告警规则(幂等)。"""
        ts = now_str()
        for dev in self.cfg.devices:
            conn.execute(
                "INSERT OR IGNORE INTO devices (device_id, name, location, type, enabled, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (dev.id, dev.name, dev.location, dev.type, int(dev.enabled), ts),
            )
        for rule in self.cfg.rules:
            conn.execute(
                "INSERT OR IGNORE INTO alarm_rules (device_id, metric, op, threshold, level, message, enabled) "
                "VALUES (?, ?, ?, ?, ?, ?, 1)",
                (rule.device_id, rule.metric, rule.op, rule.threshold, rule.level, rule.message),
            )
        conn.commit()

    # ---------- 单周期采集 ----------
    def _collect_once(self) -> None:
        ts = now_str()
        rows: list[tuple[str, str, float, str]] = []
        for dev in self.cfg.devices:
            if not dev.enabled:
                continue
            adapter = self._adapters.get(dev.id)
            if adapter is None:
                continue
            try:
                values = adapter.read()
            except Exception as exc:  # 单台设备故障不影响其他设备,更不影响看板
                print(f"[采集] 设备 {dev.id} 读取失败: {exc}")
                continue
            for metric, value in values.items():
                rows.append((dev.id, metric, float(value), ts))

        if rows:
            self._write_readings(rows)
            self._evaluate_rules(rows, ts)
            print(f"[采集] {ts} 写入 {len(rows)} 条数据")

    def _write_readings(self, rows) -> None:
        conn = connect(self.cfg.db_path())
        try:
            conn.executemany(
                "INSERT INTO readings (device_id, metric, value, ts) VALUES (?, ?, ?, ?)", rows
            )
            conn.commit()
        finally:
            conn.close()

    # ---------- 告警规则评估 ----------
    def _load_rules(self) -> list[RuleConfig]:
        conn = connect(self.cfg.db_path())
        try:
            rows = conn.execute(
                "SELECT device_id, metric, op, threshold, level, message "
                "FROM alarm_rules WHERE enabled = 1"
            ).fetchall()
        finally:
            conn.close()
        return [
            RuleConfig(device_id=r["device_id"], metric=r["metric"], op=r["op"],
                       threshold=r["threshold"], level=r["level"], message=r["message"])
            for r in rows
        ]

    def _evaluate_rules(self, rows: list[tuple[str, str, float, str]], ts: str) -> None:
        rules = self._load_rules()
        if not rules:
            return

        values = {(dev_id, metric): value for dev_id, metric, value, _ in rows}
        suppress_floor = (
            datetime.strptime(ts, TIME_FMT)
            - timedelta(seconds=self.cfg.collector.alert_suppress_seconds)
        ).strftime(TIME_FMT)

        conn = connect(self.cfg.db_path())
        try:
            for rule in rules:
                op = OPS.get(rule.op)
                if op is None:
                    continue
                for (dev_id, metric), value in values.items():
                    if rule.metric != metric:
                        continue
                    if rule.device_id not in ("*", dev_id):
                        continue
                    if not op(value, rule.threshold):
                        continue
                    # 抑制窗口内同设备同指标的未确认告警不重复产生
                    dup = conn.execute(
                        "SELECT 1 FROM alerts WHERE device_id = ? AND metric = ? AND ack = 0 AND ts >= ? LIMIT 1",
                        (dev_id, metric, suppress_floor),
                    ).fetchone()
                    if dup:
                        continue
                    message = rule.message or f"{metric} {rule.op} {rule.threshold}"
                    conn.execute(
                        "INSERT INTO alerts (device_id, metric, level, message, value, threshold, ts) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (dev_id, metric, rule.level, message, value, rule.threshold, ts),
                    )
                    print(f"[告警] {ts} {dev_id} {metric}={value} [{rule.level}] {message}")
            conn.commit()
        finally:
            conn.close()

    # ---------- 心跳 / 清理 ----------
    def _write_heartbeat(self) -> None:
        conn = connect(self.cfg.db_path())
        try:
            conn.execute(
                "INSERT INTO collector_heartbeat (id, last_ts, cycle_seconds, status) "
                "VALUES (1, ?, ?, 'running') "
                "ON CONFLICT(id) DO UPDATE SET "
                "last_ts = excluded.last_ts, cycle_seconds = excluded.cycle_seconds, status = 'running'",
                (now_str(), round(self._last_cycle_seconds, 3)),
            )
            conn.commit()
        finally:
            conn.close()

    def _prune_if_needed(self, now: datetime) -> None:
        days = self.cfg.collector.retention_days
        if days <= 0:
            return
        if self._last_prune is not None and (now - self._last_prune) < timedelta(hours=1):
            return

        cutoff = (now - timedelta(days=days)).strftime(TIME_FMT)
        conn = connect(self.cfg.db_path())
        try:
            removed_readings = conn.execute("DELETE FROM readings WHERE ts < ?", (cutoff,)).rowcount
            removed_alerts = conn.execute("DELETE FROM alerts WHERE ts < ?", (cutoff,)).rowcount
            conn.commit()
        finally:
            conn.close()
        if removed_readings or removed_alerts:
            print(f"[清理] 删除 {removed_readings} 条采集数据 / {removed_alerts} 条历史告警(保留 {days} 天)")
        self._last_prune = now

    # ---------- 主循环 ----------
    def run(self, cycles: int | None = None, interval: float | None = None) -> None:
        cfg = self.cfg
        db_path = ensure_db(cfg)

        conn = connect(db_path)
        try:
            self._seed(conn)
        finally:
            conn.close()

        for dev in cfg.devices:
            if not dev.enabled:
                continue
            try:
                self._adapters[dev.id] = build_adapter(dev)
            except Exception as exc:
                print(f"[采集] 设备 {dev.id} 适配器创建失败,已跳过: {exc}")

        eff_interval = interval if interval is not None else cfg.collector.interval_seconds
        print(f"[采集] 启动: {len(self._adapters)} 台设备, 间隔 {eff_interval}s, 数据库 {db_path}")
        print("[采集] Ctrl+C 停止。采集与看板互相独立,可分别重启。")

        index = 0
        try:
            while cycles is None or index < cycles:
                started = time.monotonic()
                try:
                    self._collect_once()
                except Exception as exc:  # 任何异常都不终止服务主循环
                    print(f"[采集] 本轮异常(服务继续): {exc}")
                self._last_cycle_seconds = time.monotonic() - started

                try:
                    self._write_heartbeat()
                    self._prune_if_needed(datetime.now())
                except Exception as exc:
                    print(f"[采集] 心跳/清理失败: {exc}")

                index += 1
                if cycles is None or index < cycles:
                    time.sleep(max(0.0, eff_interval - (time.monotonic() - started)))
        except KeyboardInterrupt:
            pass
        finally:
            for adapter in self._adapters.values():
                try:
                    adapter.close()
                except Exception as exc:
                    print(f"[采集] 适配器清理失败: {exc}")
        print("[采集] 已停止。")
