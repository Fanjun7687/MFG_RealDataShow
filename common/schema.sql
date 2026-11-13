-- ============================================================
-- 工厂设备实时看板 - SQLite 表结构
-- 时间统一为本地时间字符串 "%Y-%m-%d %H:%M:%S"(字典序 = 时间序)
-- ============================================================

-- 设备台账:采集服务首次启动时从 config.toml 播种
CREATE TABLE IF NOT EXISTS devices (
    device_id  TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    location   TEXT DEFAULT '',
    type       TEXT DEFAULT 'mock',
    enabled    INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

-- 采集数据:窄表,一行 = 某设备某指标某时刻的一个数值
CREATE TABLE IF NOT EXISTS readings (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    metric    TEXT NOT NULL,
    value     REAL NOT NULL,
    ts        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_readings_dev_metric_ts ON readings (device_id, metric, ts);
CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings (ts);

-- 告警:采集服务按规则产生;看板仅可执行"确认"(ack)这一低频写操作
CREATE TABLE IF NOT EXISTS alerts (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    metric    TEXT DEFAULT '',
    level     TEXT NOT NULL,                -- INFO / WARN / CRIT
    message   TEXT NOT NULL DEFAULT '',
    value     REAL,
    threshold REAL,
    ts        TEXT NOT NULL,
    ack       INTEGER NOT NULL DEFAULT 0,  -- 0 未确认 / 1 已确认
    ack_by    TEXT DEFAULT '',
    ack_ts    TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts (ts);
CREATE INDEX IF NOT EXISTS idx_alerts_open ON alerts (device_id, metric, ack, ts);

-- 告警规则:采集服务评估;UNIQUE 约束保证重复播种不会产生重复规则
CREATE TABLE IF NOT EXISTS alarm_rules (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,                -- 支持 "*" 表示全部设备
    metric    TEXT NOT NULL,
    op        TEXT NOT NULL,                -- >  <  >=  <=
    threshold REAL NOT NULL,
    level     TEXT NOT NULL DEFAULT 'WARN',
    message   TEXT NOT NULL DEFAULT '',
    enabled   INTEGER NOT NULL DEFAULT 1,
    UNIQUE (device_id, metric, op, threshold)
);

-- 采集服务心跳:看板据此判断采集进程是否存活(全局单行)
CREATE TABLE IF NOT EXISTS collector_heartbeat (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    last_ts       TEXT,
    cycle_seconds REAL,
    status        TEXT DEFAULT ''
);
