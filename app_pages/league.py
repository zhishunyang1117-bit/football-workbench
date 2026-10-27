"""联赛看板：积分榜（含 xG / xPts）、运气偏差、Elo 走势、近期赛果与未来赛程。"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from fwb.analytics.league import standings
from fwb.app import data, ui
from fwb.settings import Season

lg, season = st.session_state["league"], st.session_state["season"]
p = ui.palette()
st.title(f"{ui.league_label(lg)} · {Season(season).label}")

m = data.table("matches", league=lg, season=season)
if m.empty:
    st.info("该联赛/赛季暂无数据。请先运行数据管道：`python -m fwb.pipeline --mode full`")
    st.stop()

table = standings(m)
fin = m[m["status"] == "FINISHED"]
upcoming = m[m["status"] != "FINISHED"].sort_values(["match_date", "kickoff"])

c1, c2, c3, c4 = st.columns(4)
c1.metric("已赛场次", f"{len(fin)} / {len(m)}")
c2.metric("场均进球", f"{(fin['hg'] + fin['ag']).mean():.2f}" if len(fin) else "–")
c3.metric("主队胜率", f"{(fin['hg'] > fin['ag']).mean():.0%}" if len(fin) else "–")
has_xg = fin["hxg"].notna().any() if "hxg" in fin else False
c4.metric("场均 xG（双方合计）", f"{(fin['hxg'] + fin['axg']).mean():.2f}" if has_xg else "本季无 xG")

st.subheader("积分榜")
if not has_xg:
    table = table.drop(columns=["xG场次", "xG", "xGA", "xGD", "xPts", "积分-xPts"])
    st.caption("football-data.co.uk 自 2026/27 赛季起提供比赛级 xG，往季积分榜不含 xG 列。")
elif (table["xG场次"] < table["赛"]).any():
    st.caption("ℹ️ 部分最新赛果来自 openfootball / football-data.org，暂无 xG（co.uk 通常 1–3 天内补齐）；"
               "xG 相关列与“积分−xPts”只统计“xG场次”内的比赛，口径一致。")
st.dataframe(
    table, hide_index=True, width="stretch",
    column_config={
        "xG": st.column_config.NumberColumn(format="%.1f"), "xGA": st.column_config.NumberColumn(format="%.1f"),
        "xGD": st.column_config.NumberColumn(format="%+.1f"), "xPts": st.column_config.NumberColumn(format="%.1f"),
        "积分-xPts": st.column_config.NumberColumn(format="%+.1f", help="实际积分 − 由 xG 推算的期望积分；正值=表现好于机会质量"),
        "近5场": st.column_config.TextColumn(help="从左到右由远及近"),
    },
)
st.caption("排序：积分 → 净胜球 → 进球。各联赛的相互战绩等细则未实现，赛季末名次以官方为准。")

if has_xg:
    st.subheader("谁的积分“跑赢”了机会质量？")
    d = table[["球队", "积分-xPts"]].sort_values("积分-xPts")
    colors = [p["pos"] if v >= 0 else p["neg"] for v in d["积分-xPts"]]
    fig = go.Figure(go.Bar(
        x=d["积分-xPts"], y=d["球队"], orientation="h", marker=dict(color=colors, cornerradius=4),
        hovertemplate="%{y}<br>积分 − xPts：%{x:+.1f}<extra></extra>",
    ))
    fig.update_layout(title="积分 − xPts（蓝 = 高于期望，红 = 低于期望）")
    st.plotly_chart(ui.style(fig, height=max(360, 22 * len(d)), legend=False), width="stretch")
    st.caption("xPts 假设双方进球服从以 xG 为均值的独立泊松分布。短期偏差大多会回归，常被用来识别“运气”成分。")

st.subheader("Elo 评分走势")
elo = data.table("elo_history", league=lg)
if not elo.empty:
    elo = elo[elo["season"] <= season]
    latest = elo.sort_values("match_date").groupby("team").tail(1).sort_values("rating", ascending=False)
    teams_now = set(m["home"]) | set(m["away"])
    options = [t for t in latest["team"] if t in teams_now]
    pick = st.multiselect("选择球队（最多 6 支）", options, default=options[:4], max_selections=6)
    fig = go.Figure()
    for i, t in enumerate(pick):  # 颜色按选择顺序固定分配
        s = elo[elo["team"] == t].sort_values("match_date")
        fig.add_trace(go.Scatter(x=pd.to_datetime(s["match_date"]), y=s["rating"], name=t, mode="lines",
                                 line=dict(width=2, color=p["series"][i]),
                                 hovertemplate=f"{t}<br>%{{x|%Y-%m-%d}}<br>Elo %{{y:.0f}}<extra></extra>"))
    fig.update_layout(title="赛后 Elo 评分（赛季间向 1500 回归 20%）", hovermode="x unified")
    st.plotly_chart(ui.style(fig), width="stretch")

left, right = st.columns(2)
with left:
    st.subheader("最近赛果")
    recent = fin.sort_values("match_date", ascending=False).head(12).copy()
    recent["比分"] = recent["hg"].astype(str) + " : " + recent["ag"].astype(str)
    cols = ["match_date", "home", "比分", "away"] + (["hxg", "axg"] if has_xg else [])
    st.dataframe(recent[cols].rename(columns={"match_date": "日期", "home": "主队", "away": "客队",
                                               "hxg": "主队xG", "axg": "客队xG"}),
                 hide_index=True, width="stretch")
with right:
    st.subheader("未来赛程")
    st.dataframe(upcoming.head(12)[["match_date", "kickoff", "home", "away", "result_source"]].rename(
        columns={"match_date": "日期", "kickoff": "开球(当地)", "home": "主队", "away": "客队", "result_source": "来源"}),
        hide_index=True, width="stretch")
