"""工厂设备实时看板 - 统一入口。

两个服务请分别启动(解耦:任一进程重启/崩溃互不影响):
    uv run python main.py collector     # 终端1:采集服务(读设备 → 写 SQLite)
    uv run python main.py dashboard     # 终端2:看板 Web(只读 SQLite)

调试:
    uv run python main.py collector --cycles 3     # 只跑 3 轮采集后退出
"""
from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="工厂设备实时看板 - 统一入口")
    sub = parser.add_subparsers(dest="mode", required=True)

    p_col = sub.add_parser("collector", help="启动采集服务(独立进程:读设备 → 写库)")
    p_col.add_argument("--cycles", type=int, default=None, help="运行 N 轮后退出(调试)")
    p_col.add_argument("--interval", type=float, default=None, help="覆盖采集间隔(秒)")

    p_dash = sub.add_parser("dashboard", help="启动看板(独立进程:Gradio Web,只读库)")
    p_dash.add_argument("--host", default=None, help="监听地址(默认取 config.toml)")
    p_dash.add_argument("--port", type=int, default=None, help="监听端口(默认取 config.toml)")

    args = parser.parse_args()

    from common.config import load_config

    cfg = load_config()

    if args.mode == "collector":
        from collector.service import CollectorService

        CollectorService(cfg).run(cycles=args.cycles, interval=args.interval)
    else:
        if args.host:
            cfg.dashboard.host = args.host
        if args.port:
            cfg.dashboard.port = args.port
        from dashboard.app import main as dashboard_main

        dashboard_main(cfg)


if __name__ == "__main__":
    main()
