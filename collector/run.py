"""采集服务独立入口(便于脱离 main.py 单独部署):
    uv run python -m collector [--cycles 3] [--interval 2]
"""
from __future__ import annotations

import argparse

from collector.service import CollectorService
from common.config import load_config


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="设备数据采集服务(独立进程,只负责采集与写库)")
    parser.add_argument("--config", default=None, help="配置文件路径(默认 项目根/config.toml)")
    parser.add_argument("--cycles", type=int, default=None, help="运行 N 轮后退出(调试用)")
    parser.add_argument("--interval", type=float, default=None, help="覆盖采集间隔(秒)")
    args = parser.parse_args(argv)

    cfg = load_config(args.config) if args.config else load_config()
    CollectorService(cfg).run(cycles=args.cycles, interval=args.interval)


if __name__ == "__main__":
    main()
