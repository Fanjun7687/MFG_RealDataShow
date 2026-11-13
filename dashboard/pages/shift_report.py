"""班次报表页:按日期 + 班次聚合指标统计与产量,支持导出 CSV。"""
from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

import gradio as gr

from dashboard import queries


def build(cfg, demo) -> None:
    shift_names = ["全天"] + [shift.name for shift in cfg.shifts]
    with gr.Tab("📊 班次报表"):
        with gr.Row():
            date_tb = gr.Textbox(
                value=datetime.now().strftime("%Y-%m-%d"),
                label="日期(YYYY-MM-DD)",
            )
            shift_dd = gr.Dropdown(shift_names, value="全天", label="班次")
            gen_btn = gr.Button("生成报表", variant="primary")
        stats_df = gr.Dataframe(label="指标统计(平均 / 最大 / 最小)", interactive=False)
        output_df = gr.Dataframe(label="产量统计(产量计数增量)", interactive=False)
        export_btn = gr.DownloadButton("导出 CSV")
        gr.Markdown(
            "> 班次时段可在 `config.toml` 的 `[[shifts]]` 中调整;"
            "夜班 00:00-08:00 统计当日 0 点起的数据。"
        )

        def render(date_str, shift):
            return queries.get_shift_report(cfg, (date_str or "").strip(), shift)

        def export(date_str, shift):
            stats, output = queries.get_shift_report(cfg, (date_str or "").strip(), shift)
            safe_date = (date_str or "report").strip() or "report"
            path = Path(tempfile.gettempdir()) / f"shift_report_{safe_date}_{shift}.csv"
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                stats.to_csv(f, index=False)
                f.write("\n")
                output.to_csv(f, index=False)
            return str(path)

        gen_btn.click(render, [date_tb, shift_dd], [stats_df, output_df])
        export_btn.click(export, [date_tb, shift_dd], export_btn)
        demo.load(render, [date_tb, shift_dd], [stats_df, output_df])
