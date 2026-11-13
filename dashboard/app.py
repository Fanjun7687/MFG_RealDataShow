"""看板应用入口(Gradio Blocks):4 个基础页面 + 启动函数。"""
from __future__ import annotations

import gradio as gr

from common.config import AppConfig, load_config
from common.db import ensure_db
from dashboard.pages import alerts, realtime, shift_report, trend

THEME = gr.themes.Soft(primary_hue="blue", neutral_hue="slate")


def build_app(cfg: AppConfig | None = None) -> gr.Blocks:
    cfg = cfg or load_config()
    ensure_db(cfg)  # 首次启动也能建库(看板不写采集数据,只建表结构)

    with gr.Blocks(title="工厂设备实时看板") as demo:
        gr.Markdown(
            "# 🏭 工厂设备实时看板\n"
            "实时状态 · 历史趋势 · 班次报表 · 告警 ｜ 采集服务独立进程写库,看板只读库"
        )
        with gr.Tabs():
            realtime.build(cfg, demo)
            trend.build(cfg, demo)
            shift_report.build(cfg, demo)
            alerts.build(cfg, demo)
    return demo


def main(cfg: AppConfig | None = None) -> None:
    cfg = cfg or load_config()
    demo = build_app(cfg)
    print(f"[看板] 启动: http://{cfg.dashboard.host}:{cfg.dashboard.port}")
    print("[看板] Ctrl+C 停止。看板与采集互相独立,可分别重启。")
    demo.launch(
        server_name=cfg.dashboard.host,
        server_port=cfg.dashboard.port,
        show_error=True,
        theme=THEME,
    )


if __name__ == "__main__":
    main()
