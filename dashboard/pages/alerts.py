"""告警页:未确认统计、告警列表、确认操作(看板唯一写操作)。

注意:确认操作是低频小事务,在 WAL 模式下与采集写入互不阻塞;
如需严格"只读看板",可删除 ack_alert 及确认按钮。
"""
from __future__ import annotations

import gradio as gr
import pandas as pd

from dashboard import queries


def build(cfg, demo) -> None:
    with gr.Tab("🔔 告警"):
        with gr.Row():
            level_dd = gr.Dropdown(
                ["INFO", "WARN", "CRIT"],
                value=["WARN", "CRIT"],
                multiselect=True,
                label="告警级别",
            )
            status_dd = gr.Radio(["全部", "未确认", "已确认"], value="未确认", label="状态")
            range_dd = gr.Dropdown(["最近1小时", "最近24小时", "最近7天", "全部"],
                                   value="最近24小时", label="时间范围")
            refresh_btn = gr.Button("刷新", variant="primary")

        stats_df = gr.Dataframe(label="未确认告警统计", interactive=False)
        alert_df = gr.Dataframe(label="告警列表(点击行可选中)", interactive=False, max_height=420)
        table_state = gr.State(pd.DataFrame(columns=queries.ALERT_COLUMNS))

        with gr.Row():
            alert_id = gr.Number(label="告警 ID(点击上方行自动填入)", precision=0)
            operator = gr.Textbox(value="operator", label="操作员工号")
            ack_btn = gr.Button("确认告警", variant="stop")
        ack_msg = gr.Markdown()
        auto_ck = gr.Checkbox(value=False, label="自动刷新(15 秒)")

        with gr.Accordion("告警规则(由 config.toml 播种,只读)", open=False):
            rules_df = gr.Dataframe(interactive=False)

        def _fetch(levels, status, range_key):
            df = queries.get_alerts(cfg, levels, status, range_key)
            return queries.get_alert_stats(cfg), df, df

        outputs = [stats_df, alert_df, table_state]
        refresh_btn.click(_fetch, [level_dd, status_dd, range_dd], outputs)
        demo.load(_fetch, [level_dd, status_dd, range_dd], outputs)
        demo.load(lambda: queries.get_rules(cfg), outputs=[rules_df])

        def on_tick(auto, levels, status, range_key):
            if not auto:
                return gr.update(), gr.update(), gr.update()
            return _fetch(levels, status, range_key)

        timer = gr.Timer(15)
        timer.tick(on_tick, [auto_ck, level_dd, status_dd, range_dd], outputs)

        def on_select(current: pd.DataFrame, evt: gr.SelectData):
            try:
                row = int(evt.index[0])
                return float(current.iloc[row]["ID"])
            except Exception:
                return gr.update()

        alert_df.select(on_select, table_state, alert_id)

        def do_ack(alert_id_value, operator_value, levels, status, range_key):
            if not alert_id_value:
                return "⚠️ 请先点击告警行或手动输入告警 ID", gr.update(), gr.update(), gr.update()
            message = queries.ack_alert(
                cfg, int(alert_id_value), (operator_value or "").strip() or "operator"
            )
            stats, df, state = _fetch(levels, status, range_key)
            return message, stats, df, state

        ack_btn.click(
            do_ack,
            [alert_id, operator, level_dd, status_dd, range_dd],
            [ack_msg, stats_df, alert_df, table_state],
        )
