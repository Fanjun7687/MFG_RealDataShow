"""全局配置加载:读取 config.toml,提供带默认值的配置对象。

配置只包含"静态"信息(设备台账、规则、班次、路径等);
运行时状态(实时值、告警、心跳)全部在 SQLite 中。
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT_DIR / "config.toml"


@dataclass
class DeviceConfig:
    id: str
    name: str
    location: str = ""
    type: str = "mock"
    enabled: bool = True
    # 协议专属参数:host/port/url/points 等,由各适配器自行解析
    options: dict = field(default_factory=dict)


@dataclass
class RuleConfig:
    device_id: str
    metric: str
    op: str
    threshold: float
    level: str = "WARN"
    message: str = ""


@dataclass
class ShiftConfig:
    name: str
    start: str  # "08:00"
    end: str    # "16:00" / "24:00"


@dataclass
class DatabaseConfig:
    path: str = "data/factory.db"


@dataclass
class CollectorConfig:
    interval_seconds: float = 5.0
    stale_threshold_seconds: int = 90
    alert_suppress_seconds: int = 300
    retention_days: int = 90


@dataclass
class DashboardConfig:
    host: str = "0.0.0.0"
    port: int = 7860
    refresh_seconds: int = 5
    max_plot_points: int = 2000


@dataclass
class AppConfig:
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    collector: CollectorConfig = field(default_factory=CollectorConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    devices: list[DeviceConfig] = field(default_factory=list)
    rules: list[RuleConfig] = field(default_factory=list)
    shifts: list[ShiftConfig] = field(
        default_factory=lambda: [
            ShiftConfig("早班", "08:00", "16:00"),
            ShiftConfig("中班", "16:00", "24:00"),
            ShiftConfig("夜班", "00:00", "08:00"),
        ]
    )

    # ---------- 路径 ----------
    def db_path(self) -> Path:
        path = Path(self.database.path)
        return path if path.is_absolute() else ROOT_DIR / path

    # ---------- 班次时间窗 ----------
    def shift_window(self, date_str: str, shift_name: str) -> tuple[str, str] | None:
        """返回某日期某班次的 [start_ts, end_ts) 文本区间(可直接用于 SQL 比较)。"""
        for shift in self.shifts:
            if shift.name != shift_name:
                continue
            day = datetime.strptime(date_str, "%Y-%m-%d")
            start_h, start_m = map(int, shift.start.split(":"))
            start = day + timedelta(hours=start_h, minutes=start_m)
            end_h, end_m = map(int, shift.end.split(":"))
            end = day + timedelta(days=end_h // 24, hours=end_h % 24, minutes=end_m)
            fmt = "%Y-%m-%d %H:%M:%S"
            return start.strftime(fmt), end.strftime(fmt)
        return None


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> AppConfig:
    """读取配置文件;缺失字段使用默认值,文件不存在时返回全默认配置。"""
    cfg = AppConfig()
    path = Path(path)
    if not path.exists():
        return cfg

    with open(path, "rb") as f:
        raw = tomllib.load(f)

    db = raw.get("database", {})
    cfg.database = DatabaseConfig(path=db.get("path", cfg.database.path))

    c = raw.get("collector", {})
    cfg.collector = CollectorConfig(
        interval_seconds=float(c.get("interval_seconds", cfg.collector.interval_seconds)),
        stale_threshold_seconds=int(c.get("stale_threshold_seconds", cfg.collector.stale_threshold_seconds)),
        alert_suppress_seconds=int(c.get("alert_suppress_seconds", cfg.collector.alert_suppress_seconds)),
        retention_days=int(c.get("retention_days", cfg.collector.retention_days)),
    )

    d = raw.get("dashboard", {})
    cfg.dashboard = DashboardConfig(
        host=d.get("host", cfg.dashboard.host),
        port=int(d.get("port", cfg.dashboard.port)),
        refresh_seconds=int(d.get("refresh_seconds", cfg.dashboard.refresh_seconds)),
        max_plot_points=int(d.get("max_plot_points", cfg.dashboard.max_plot_points)),
    )

    cfg.devices = [DeviceConfig(**item) for item in raw.get("devices", [])]
    cfg.rules = [RuleConfig(**item) for item in raw.get("rules", [])]
    shifts = [ShiftConfig(**item) for item in raw.get("shifts", [])]
    if shifts:
        cfg.shifts = shifts

    return cfg
