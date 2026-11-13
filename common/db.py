"""SQLite 访问层:连接管理、建库、时间工具。

约定:
- 时间统一存本地时间字符串 "%Y-%m-%d %H:%M:%S"(字典序即时间序,可直接用于 SQL 范围比较)。
- 开启 WAL 模式:允许多进程同时读 + 单进程写,
  这是"采集服务写 / 看板读"解耦、互不阻塞的基础。
- 每个进程/查询使用独立短连接,避免多线程复用连接的问题。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from common.config import AppConfig

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
TIME_FMT = "%Y-%m-%d %H:%M:%S"


def now_str() -> str:
    return datetime.now().strftime(TIME_FMT)


def connect(db_path: Path | str) -> sqlite3.Connection:
    """建立带合理超时/资源模式的连接(WAL 为库级设置,建库时已开启)。"""
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def ensure_db(cfg: AppConfig) -> Path:
    """建库(幂等):创建目录、表结构,并开启 WAL。两个服务启动时都会调用。"""
    db_path = cfg.db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()
    return db_path
