"""历史趋势页:按设备 + 指标 + 时间范围查询曲线,支持自动刷新。"""
from __future__ import annotations

from datetime import datetime, timedelta

import gradio as gr
import plotly.express as px
import plotly.graph_objects as go

from common.db import TIME_FMT
from dashboard import queries

RANGES = {
    "最近30分钟": 1800,
    "最近1小时": 3600,
    "最近8小时": 8 * 3600,
    "最近24小时": 24 * 3600,
    "最近7天": 7 * 86400,
}


def _empty_fig(message: str) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        template="plotly_white",
        height=430,
        annotations=[dict(text=message, x=0.5, y=0.5, showarrow=False, font=dict(size=16))],
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
    )
    return fig


def build(cfg, demo) -> None:
    devices = queries.get_device_options(cfg)
    with gr.Tab("📈 历史趋势"):
        with gr.Row():
            device_dd = gr.Dropdown(
                choices=devices,
                value=devices[0] if devices else None,
                label="设备",
                scale=2,
            )
            metric_dd = gr.Dropdown(
                choices=queries.get_metric_options(cfg, devices[0]) if devices else [],
                value=None,
                multiselect=True,
                label="指标(可多选,建议选择同单位指标)",
                scale=3,
            )
        with gr.Row():
            range_dd = gr.Dropdown(list(RANGES), value="最近1小时", label="时间范围")
            auto_ck = gr.Checkbox(value=False, label="自动刷新(30 秒)")
            query_btn = gr.Button("查询", variant="primary")
        plot = gr.Plot(label="趋势曲线")

        def on_device_change(device_id):
            return gr.update(choices=queries.get_metric_options(cfg, device_id), value=None)

        device_dd.change(on_device_change, device_dd, metric_dd)

        def render(device_id, metrics, range_key):
            if not device_id:
                return _empty_fig("请选择设备")
            metrics = metrics or queries.get_metric_options(cfg, device_id)
            if not metrics:
                return _empty_fig("该设备暂无数据")
            now = datetime.now()
            start = (now - timedelta(seconds=RANGES.get(range_key, 3600))).strftime(TIME_FMT)
            end = now.strftime(TIME_FMT)
            df = queries.get_series(cfg, device_id, metrics, start, end, cfg.dashboard.max_plot_points)
            if df.empty:
                return _empty_fig("该时间段暂无数据")
            fig = px.line(df, x="ts", y="value", color="metric", template="plotly_white")
            fig.update_layout(
                height=440,
                xaxis_title="时间",
                yaxis_title="数值",
                legend_title="指标",
                margin=dict(l=40, r=20, t=30, b=40),
            )
            return fig

        def on_tick(auto, device_id, metrics, range_key):
            if not auto:
                return gr.update()
            return render(device_id, metrics, range_key)

        inputs = [device_dd, metric_dd, range_dd]
        query_btn.click(render, inputs, plot)
        timer = gr.Timer(30)
        timer.tick(on_tick, [auto_ck] + inputs, plot)
        demo.load(render, inputs, plot)
