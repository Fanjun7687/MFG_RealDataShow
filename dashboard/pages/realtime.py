"""实时状态页:采集服务心跳 + 各设备指标最新值,定时自动刷新。"""
from __future__ import annotations

import gradio as gr

from dashboard import queries


def _status_html(status: dict) -> str:
    if status.get("alive"):
        detail = f"心跳 {status.get('last_ts', '')}"
        if status.get("cycle") is not None:
            detail += f",单轮耗时 {status['cycle']}s"
        text, dot, color, bg = f"采集服务正常({detail})", "🟢", "#1a7f37", "#e6f4ea"
    else:
        text, dot, color, bg = f"采集服务异常:{status.get('text', '未运行')}", "🔴", "#b42318", "#fdecea"
    return (
        "<div style='display:inline-block;padding:8px 14px;border-radius:8px;"
        f"background:{bg};color:{color};font-size:14px'>{dot} <b>{text}</b></div>"
    )


def build(cfg, demo) -> None:
    with gr.Tab("📡 实时状态"):
        status_html = gr.HTML(_status_html({"alive": False, "text": "加载中..."}))
        table = gr.Dataframe(
            label=f"设备实时数据(每 {cfg.dashboard.refresh_seconds} 秒自动刷新)",
            interactive=False,
            max_height=460,
        )

        def refresh():
            return _status_html(queries.get_collector_status(cfg)), queries.get_latest_readings(cfg)

        timer = gr.Timer(cfg.dashboard.refresh_seconds)
        timer.tick(refresh, outputs=[status_html, table])
        demo.load(refresh, outputs=[status_html, table])
