"""球员评估：StatsBomb 事件聚合的每 90 分钟指标 → 同位置百分位披萨图 → 相似球员 → 散点探索。"""

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from mplsoccer import PyPizza

from fwb.analytics import players as pl
from fwb.app import data, ui
from fwb.settings import Season, current_season, source_cfg

p = ui.palette()
st.title("球员评估与球探")
st.caption("球员级高阶数据目前没有合规的免费当季来源（Understat 的 robots.txt 禁止抓取；FBref 拒绝脚本访问），"
           "因此本页基于 StatsBomb 开放数据中完整的历史赛季（2015/16 英超/西甲/意甲/法甲）做方法完整的球探分析；"
           "接入有授权的数据源后，只需产出同结构的 player_match 表即可复用本页。数据 © StatsBomb。")

tbl = data.table("sb_player_season")
if tbl.empty:
    st.info("尚未生成球员赛季表。运行 `python -m fwb.pipeline --mode statsbomb`（约 20–30 分钟，一次性）。")
    st.stop()

names = {2: "英超", 11: "西甲", 9: "德甲", 12: "意甲", 7: "法甲"}
comps = [c for c in source_cfg("statsbomb_open_data")["competitions"]
         if ((tbl["competition_id"] == c[0]) & (tbl["season_id"] == c[1])).any()]
c1, c2, c3 = st.columns([2, 1, 2])
cid, sid = c1.selectbox("赛事", comps, format_func=lambda c: f"{names.get(c[0], c[0])} 2015/16" if c[1] == 27
                        else f"{names.get(c[0], c[0])} season_id {c[1]}")
group = c2.selectbox("位置组", ["FW", "MF", "DF", "GK"], format_func={"FW": "前锋", "MF": "中场", "DF": "后卫",
                                                                     "GK": "门将"}.get)
min_minutes = c3.slider("最低出场分钟（参与百分位排名）", 0, 3000, 900, step=90)

season = tbl[(tbl["competition_id"] == cid) & (tbl["season_id"] == sid)]
metrics = pl.RADAR_PRESETS[group]
ranked = pl.percentiles(season, metrics, min_minutes)
pool = ranked[(ranked["position_group"] == group) & (ranked["minutes"] >= min_minutes)].sort_values(
    "minutes", ascending=False)
if pool.empty:
    st.warning("没有满足条件的球员，请降低最低出场分钟。")
    st.stop()

keys = list(zip(pool["player_id"], pool["team"]))
lab = {k: f"{r.player}（{r.team}，{r.minutes:.0f} 分钟）" for k, r in zip(keys, pool.itertuples())}
sel = st.selectbox("球员", keys, format_func=lab.get)
row = pool[(pool["player_id"] == sel[0]) & (pool["team"] == sel[1])].iloc[0]

left, right = st.columns([1.1, 1])
with left:
    vals = [int(round(row[f"{m}_pct"])) if pd.notna(row[f"{m}_pct"]) else 0 for m in metrics]
    baker = PyPizza(params=[pl.label(m) for m in metrics], background_color=p["surface"],
                    straight_line_color=p["grid"], straight_line_lw=1, last_circle_lw=1,
                    last_circle_color=p["axis"], other_circle_lw=0, inner_circle_size=18)
    fig, ax = baker.make_pizza(
        vals, figsize=(6.5, 6.8), color_blank_space="same", blank_alpha=0.15,
        slice_colors=[p["series"][0]] * len(vals), value_colors=[p["ink"]] * len(vals),
        value_bck_colors=[p["surface"]] * len(vals),
        kwargs_slices=dict(edgecolor=p["surface"], zorder=2, linewidth=2),
        kwargs_params=dict(color=p["ink2"], fontsize=10), kwargs_values=dict(color=p["ink"], fontsize=10,
                                                                            zorder=3),
    )
    fig.set_facecolor(p["surface"])
    fig.text(0.5, 0.975, f"{row['player']} · {row['team']}", ha="center", color=p["ink"], fontsize=13)
    fig.text(0.5, 0.945, f"同位置百分位（≥{min_minutes} 分钟，样本 {len(pool)} 人）", ha="center",
             color=p["muted"], fontsize=9)
    st.pyplot(fig, width="stretch")
    plt.close(fig)
with right:
    med = pool[[pl.metric_col(m) for m in metrics]].median()
    detail = pd.DataFrame({
        "指标": [pl.label(m) for m in metrics],
        "本人": [row[pl.metric_col(m)] for m in metrics],
        "同位置中位数": [med[pl.metric_col(m)] for m in metrics],
        "百分位": [row[f"{m}_pct"] for m in metrics],
    })
    st.dataframe(detail, hide_index=True, width="stretch", column_config={
        "本人": st.column_config.NumberColumn(format="%.2f"),
        "同位置中位数": st.column_config.NumberColumn(format="%.2f"),
        "百分位": st.column_config.ProgressColumn(format="%.0f", min_value=0, max_value=100),
    })
    st.caption(f"出场 {row['games']:.0f} 场 / {row['minutes']:.0f} 分钟 · 计数指标为每 90 分钟；"
               "“成功率”类为比率。推进传球采用 Wyscout 距离阈值定义；xT 为 penaltyblog 预训练曲面。")

st.subheader("风格最相似的球员")
sim = pl.similar_players(season, sel[0], sel[1], metrics, min_minutes, n=10)
if not sim.empty:
    show = sim[["player", "team", "minutes", "相似度"] + [pl.metric_col(m) for m in metrics[:5]]].rename(
        columns={"player": "球员", "team": "球队", "minutes": "分钟", **{pl.metric_col(m): pl.label(m) for m in metrics[:5]}})
    st.dataframe(show, hide_index=True, width="stretch", column_config={
        "相似度": st.column_config.ProgressColumn(format="%.2f", min_value=-1, max_value=1),
        "分钟": st.column_config.NumberColumn(format="%.0f"),
        **{pl.label(m): st.column_config.NumberColumn(format="%.2f") for m in metrics[:5]}})
    st.caption("相似度 = 所选指标在同位置组内 z-score 标准化后的余弦相似度（1 = 风格完全一致）。")

st.subheader("散点探索")
opts = list(pl.COUNT_METRICS) + list(pl.RATIO_METRICS)
a, b = st.columns(2)
mx = a.selectbox("横轴", opts, index=opts.index(metrics[0]), format_func=pl.label)
my = b.selectbox("纵轴", opts, index=opts.index(metrics[1]), format_func=pl.label)
fig = go.Figure()
top_sim = set(zip(sim["player_id"], sim["team"])) if not sim.empty else set()
is_sel = np.array([k == sel for k in keys])
is_sim = np.array([k in top_sim for k in keys])
layers = ((~is_sel & ~is_sim, "其他球员", p["muted"], 8), (is_sim, "相似球员", p["series"][0], 10),
          (is_sel, str(row["player"]), p["series"][1], 14))
for mask, name, color, size in layers:
    d = pool[mask]
    fig.add_trace(go.Scatter(
        x=d[pl.metric_col(mx)], y=d[pl.metric_col(my)], mode="markers", name=name,
        marker=dict(size=size, color=color, line=dict(width=2, color=p["surface"])),
        text=d["player"] + "（" + d["team"] + "）",
        hovertemplate="%{text}<br>" + pl.label(mx) + " %{x:.2f}<br>" + pl.label(my) + " %{y:.2f}<extra></extra>"))
fig.update_xaxes(title=pl.label(mx))
fig.update_yaxes(title=pl.label(my))
st.plotly_chart(ui.style(fig, height=480), width="stretch")

cur = current_season()
sc = data.table("fdorg_scorers", league=st.session_state["league"], season=cur)
st.subheader(f"当季射手榜（football-data.org · {Season(cur).label}）")
if sc.empty:
    st.caption("本季暂无数据。配置免费的 FOOTBALL_DATA_ORG_TOKEN 后，这里显示当季射手/助攻榜（基础数据）。")
else:
    st.dataframe(sc.sort_values("goals", ascending=False)[["player", "team_raw", "played", "goals", "assists",
                                                            "penalties"]].rename(columns={
        "player": "球员", "team_raw": "球队", "played": "出场", "goals": "进球", "assists": "助攻", "penalties": "点球"}),
        hide_index=True, width="stretch")
