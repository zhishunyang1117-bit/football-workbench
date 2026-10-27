"""比赛预测：未来赛程概率（Dixon-Coles / Elo / 市场）+ 滚动回测评估（RPS、校准）。"""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from fwb.analytics import models
from fwb.app import data, ui
from fwb.settings import Season, leagues

lg = st.session_state["league"]
p = ui.palette()
st.title("比赛预测")
st.caption("⚠️ 仅用于统计建模研究与教学。模型概率不构成投注建议；博彩有风险，请遵守当地法律。")

tab_next, tab_bt = st.tabs(["未来赛程", "回测评估"])

with tab_next:
    pred = data.table("predictions", league=lg)
    if pred.empty:
        st.info("未来 14 天内暂无该联赛赛程的预测（可能处于国家队比赛日），或尚未运行数据管道。")
    else:
        pred = pred.sort_values(["match_date", "kickoff", "home"]).reset_index(drop=True)
        model = st.radio("展示模型", ["dc", "elo", "mkt"], horizontal=True,
                         format_func={"dc": "Dixon-Coles", "elo": "Elo", "mkt": "市场赔率(去抽水)"}.get)
        cols = [f"{model}_h", f"{model}_d", f"{model}_a"]
        show = pred.dropna(subset=cols)
        if show.empty:
            st.info("所选模型暂无可用概率（市场赔率仅在 football-data.co.uk 发布 fixtures.csv 后可得）。")
        else:
            labels = [f"{r.match_date} {r.home} vs {r.away}" for r in show.itertuples()]
            fig = go.Figure()
            for i, (c, name) in enumerate(zip(cols, ["主胜", "平局", "客胜"])):
                v = show[c].to_numpy()
                fig.add_trace(go.Bar(
                    y=labels, x=v, name=name, orientation="h",
                    marker=dict(color=p["series"][i], line=dict(width=2, color=p["surface"])),
                    text=[f"{x:.0%}" if x >= 0.12 else "" for x in v], textposition="inside",
                    insidetextfont=dict(color=p["ink"]),
                    hovertemplate="%{y}<br>" + name + " %{x:.1%}<extra></extra>"))
            fig.update_layout(barmode="stack", title="胜平负概率", xaxis=dict(tickformat=".0%", range=[0, 1]))
            fig.update_yaxes(autorange="reversed")
            st.plotly_chart(ui.style(fig, height=max(320, 42 * len(show))), width="stretch")

        table = pred.assign(对阵=pred["home"] + " vs " + pred["away"])[
            ["match_date", "kickoff", "对阵", "dc_h", "dc_d", "dc_a", "elo_h", "elo_d", "elo_a", "mkt_h", "mkt_d",
             "mkt_a", "dc_xg_h", "dc_xg_a", "dc_top_score", "dc_over25", "dc_btts"]]
        pct = st.column_config.NumberColumn(format="percent")
        st.dataframe(table.rename(columns={"match_date": "日期", "kickoff": "开球", "dc_xg_h": "主队期望进球",
                                           "dc_xg_a": "客队期望进球", "dc_top_score": "最可能比分",
                                           "dc_over25": "大2.5球", "dc_btts": "双方进球"}),
                     hide_index=True, width="stretch",
                     column_config={c: pct for c in ["dc_h", "dc_d", "dc_a", "elo_h", "elo_d", "elo_a", "mkt_h",
                                                     "mkt_d", "mkt_a", "大2.5球", "双方进球"]}
                     | {"主队期望进球": st.column_config.NumberColumn(format="%.2f"),
                        "客队期望进球": st.column_config.NumberColumn(format="%.2f")})
        st.caption("dc = Dixon-Coles（近 730 天、时间衰减 ξ=0.0018/天）；elo = 赛季回归 Elo；"
                   "mkt = football-data.co.uk 赛前平均赔率经 Shin 方法去抽水。")

with tab_bt:
    bt_all = pd.concat([data.table("backtest", league=code) for code in leagues()], ignore_index=True)
    if bt_all.empty:
        st.info("暂无回测结果。运行 `python -m fwb.pipeline --mode full` 生成。")
        st.stop()
    st.markdown("**方法**：每个比赛窗口（周二–周四 / 周五–周一）只用窗口之前的比赛重新拟合，"
                "对窗口内比赛做样本外预测；与**收盘**市场概率在同一样本上比较。RPS 越低越好。")
    rows = []
    for code in leagues():
        e = models.evaluate(bt_all[bt_all["league"] == code])
        if not e.empty:
            rows.append(e.assign(联赛=leagues()[code]["name"]))
    if rows:
        ev_tbl = pd.concat(rows)[["联赛", "模型", "样本数", "RPS", "对数损失", "Brier", "命中率"]]
        st.dataframe(ev_tbl, hide_index=True, width="stretch", column_config={
            "RPS": st.column_config.NumberColumn(format="%.4f"),
            "对数损失": st.column_config.NumberColumn(format="%.4f"),
            "Brier": st.column_config.NumberColumn(format="%.4f"),
            "命中率": st.column_config.NumberColumn(format="percent")})

    bt = bt_all[bt_all["league"] == lg].dropna(subset=["dc_h", "elo_h", "mkt_h"]).sort_values("match_date")
    if not bt.empty:
        y = bt["outcome"].to_numpy(int)
        rps = {m: models.pb.metrics.rps_array(bt[[f"{m}_h", f"{m}_d", f"{m}_a"]].to_numpy(float), y)
               for m in ("dc", "elo", "mkt")}
        x = pd.to_datetime(bt["match_date"])
        fig = go.Figure()
        fig.add_hline(y=0, line=dict(color=p["axis"], width=1))
        for i, (m, name) in enumerate((("dc", "Dixon-Coles − 市场"), ("elo", "Elo − 市场"))):
            diff = np.cumsum(rps[m] - rps["mkt"]) / np.arange(1, len(bt) + 1)
            fig.add_trace(go.Scatter(x=x, y=diff, name=name, mode="lines", line=dict(width=2, color=p["series"][i]),
                                     hovertemplate=name + "<br>%{x|%Y-%m-%d}<br>累计平均 RPS 差 %{y:+.4f}<extra></extra>"))
        fig.update_layout(title=f"{ui.league_label(lg)}：累计平均 RPS 差（>0 表示不如市场）", hovermode="x unified")
        st.plotly_chart(ui.style(fig), width="stretch")

        mdl = st.radio("校准曲线", ["dc", "elo", "mkt"], horizontal=True, key="calib",
                       format_func={"dc": "Dixon-Coles", "elo": "Elo", "mkt": "市场"}.get)
        cal = models.calibration(bt, mdl)
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=[0, 1], y=[0, 1], mode="lines", name="完美校准",
                                 line=dict(color=p["axis"], width=1, dash="dash"), hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=cal["预测概率"], y=cal["实际频率"], mode="lines+markers", name="模型",
                                 line=dict(width=2, color=p["series"][0]), marker=dict(size=9),
                                 customdata=cal["样本数"],
                                 hovertemplate="预测 %{x:.0%}<br>实际 %{y:.0%}<br>样本 %{customdata}<extra></extra>"))
        fig.update_layout(title="校准：预测概率 vs 实际发生频率（胜/平/负合并，10 分箱）")
        fig.update_xaxes(tickformat=".0%", range=[0, 1], title="预测概率")
        fig.update_yaxes(tickformat=".0%", range=[0, 1], title="实际频率")
        st.plotly_chart(ui.style(fig, height=420), width="stretch")
        seasons = ", ".join(Season(s).label for s in sorted(bt["season"].unique()))
        st.caption(f"样本：{len(bt)} 场（{seasons}），仅计三个模型都有预测的比赛。")
