"""球队战术：当季比赛级指标（xG 走势/射门质量） + StatsBomb 事件实验室（传球网络、射门图、PPDA、xT）。"""

import matplotlib.pyplot as plt
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from mplsoccer import Pitch, VerticalPitch

from fwb.analytics import events as ev
from fwb.analytics.team import league_profile, match_log
from fwb.app import data, ui
from fwb.http import FETCH_ERRORS
from fwb.settings import Season, source_cfg

lg, season = st.session_state["league"], st.session_state["season"]
p = ui.palette()
st.title("球队战术")
tab_cur, tab_lab = st.tabs([f"当季比赛级分析（{Season(season).label}）", "事件实验室 · StatsBomb 开放数据"])

# ------------------------------------------------------------------ 当季
with tab_cur:
    m = data.table("matches", league=lg, season=season)
    if m.empty:
        st.info("暂无数据，请先运行数据管道。")
    else:
        prof = league_profile(m)
        teams = sorted(prof["球队"]) if not prof.empty else []
        team = st.selectbox("球队", teams, key="team_cur")
        log = match_log(m, team) if team else pd.DataFrame()
        has_xg = not log.empty and log["xgf"].notna().any()
        if not log.empty:
            row = prof.set_index("球队").loc[team]
            c = st.columns(4)
            c[0].metric("场均 xG", f"{row['场均xG']:.2f}" if has_xg else "–")
            c[1].metric("场均 xGA", f"{row['场均xGA']:.2f}" if has_xg else "–")
            c[2].metric("终结超预期（进球−xG）", f"{row['终结超预期']:+.1f}" if has_xg else "–")
            c[3].metric("每次射门 xG", f"{row['每射xG']:.3f}" if has_xg else "–")
        if has_xg:
            fig = go.Figure()
            x = pd.to_datetime(log["match_date"])
            for col, name, color in (("xgf_roll", "xG（创造）", p["series"][0]), ("xga_roll", "xGA（被创造）", p["series"][1])):
                fig.add_trace(go.Scatter(x=x, y=log[col], name=name, mode="lines+markers",
                                         line=dict(width=2, color=color), marker=dict(size=8),
                                         customdata=log[["opponent", "venue", "gf", "ga"]],
                                         hovertemplate=name + " %{y:.2f}<br>%{x|%m-%d} %{customdata[1]} vs "
                                         "%{customdata[0]}（%{customdata[2]:.0f}-%{customdata[3]:.0f}）<extra></extra>"))
            fig.update_layout(title=f"{team}：5 场滚动平均 xG / xGA", hovermode="x unified")
            st.plotly_chart(ui.style(fig), width="stretch")

            st.markdown("**联赛攻防定位**：横轴场均 xG（进攻），纵轴场均 xGA（防守，越低越好，已反转）")
            fig = go.Figure()
            others = prof[prof["球队"] != team]
            me = prof[prof["球队"] == team]
            for d, name, color, size in ((others, "其他球队", p["muted"], 9), (me, team, p["series"][1], 14)):
                fig.add_trace(go.Scatter(
                    x=d["场均xG"], y=d["场均xGA"], mode="markers+text", name=name, text=d["球队"],
                    textposition="top center", textfont=dict(size=11, color=p["ink2"]),
                    marker=dict(size=size, color=color, line=dict(width=2, color=p["surface"])),
                    hovertemplate="%{text}<br>场均xG %{x:.2f}<br>场均xGA %{y:.2f}<extra></extra>"))
            fig.update_yaxes(autorange="reversed", title="场均 xGA")
            fig.update_xaxes(title="场均 xG")
            st.plotly_chart(ui.style(fig, height=460), width="stretch")
        elif not log.empty:
            st.info("该赛季 football-data.co.uk 未提供比赛级 xG（2026/27 起提供），仅显示射门数据。")
        if not log.empty:
            show = log[["match_date", "venue", "opponent", "gf", "ga", "xgf", "xga", "shots", "sot",
                        "shots_against", "result"]].rename(columns={
                "match_date": "日期", "venue": "主客", "opponent": "对手", "gf": "进", "ga": "失", "xgf": "xG",
                "xga": "xGA", "shots": "射门", "sot": "射正", "shots_against": "被射门", "result": "结果"})
            st.dataframe(show.sort_values("日期", ascending=False), hide_index=True, width="stretch")

# ------------------------------------------------------------------ StatsBomb 实验室
def event_lab() -> None:
    st.caption("StatsBomb 开放数据为历史赛季事件级数据（如 2015/16 英超/西甲/意甲/法甲整季），"
               "用于展示当季免费数据源无法提供的传球网络、PPDA、xT 等战术指标。数据 © StatsBomb。")
    comps = source_cfg("statsbomb_open_data")["competitions"]
    names = {2: "英超", 11: "西甲", 9: "德甲", 12: "意甲", 7: "法甲"}
    cid, sid = st.selectbox("赛事", comps, format_func=lambda c: f"{names.get(c[0], c[0])} "
                            + ("2015/16" if c[1] == 27 else f"season_id {c[1]}"))
    tm = data.table("sb_team_match", competition_id=cid, season_id=sid)
    if not tm.empty:
        st.markdown("**赛季战术画像**（由已回填比赛聚合）")
        agg = tm.groupby("team").agg(场次=("match_id", "nunique"), 场均xG=("xg", "mean"), 场均xGA=("xga", "mean"),
                                     控球率=("possession", "mean"), 场地倾斜=("field_tilt", "mean"),
                                     PPDA=("ppda", "mean"), 场均深度推进=("deep_completions", "mean"),
                                     场均xT增量=("xt_added", "mean"), 高位夺回=("high_recoveries", "mean"))
        st.dataframe(agg.sort_values("PPDA").reset_index().rename(columns={"team": "球队"}), hide_index=True,
                     width="stretch", column_config={
                         "控球率": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1),
                         "场地倾斜": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1),
                         "PPDA": st.column_config.NumberColumn(format="%.1f", help="越低 = 逼抢越积极"),
                     })
    else:
        st.info("尚未回填 StatsBomb 聚合数据（运行 `python -m fwb.pipeline --mode statsbomb`）。单场分析仍可在线使用。")

    st.markdown("**单场分析**（按需从 StatsBomb 开放数据仓库在线读取该场事件，缓存 7 天）")
    try:
        idx = data.sb_match_index(cid, sid)
    except FETCH_ERRORS as err:
        st.warning(f"暂时无法获取 StatsBomb 比赛列表：{err}")
        return
    if idx.empty:
        return
    ids = {**dict(zip(idx["home"], idx["home_team_id"])), **dict(zip(idx["away"], idx["away_team_id"]))}
    t_sel = st.selectbox("球队", sorted(ids), key="sb_team")
    t_id = int(ids[t_sel])
    games = idx[(idx["home_team_id"] == t_id) | (idx["away_team_id"] == t_id)].sort_values("match_date")
    label = {r.match_id: f"{r.match_date} {r.home} {r.home_score}-{r.away_score} {r.away}" for r in games.itertuples()}
    mid = st.selectbox("比赛", list(label), format_func=label.get)
    g = games[games["match_id"] == mid].iloc[0]
    try:
        e = data.sb_events(int(mid))
        tstat = ev.team_match(e, g["home"], g["away"], int(g["home_team_id"]), int(g["away_team_id"]))
    except FETCH_ERRORS as err:
        st.warning(f"暂时无法获取该场事件数据，请稍后重试：{err}")
        return
    except ValueError as err:
        st.warning(f"该场事件数据不完整，已跳过：{err}")
        return

    st.dataframe(tstat.set_index("team")[["goals", "xg", "shots", "possession", "field_tilt", "ppda",
                                           "deep_completions", "xt_added"]]
                 .rename(columns={"goals": "进球", "xg": "xG", "shots": "射门", "possession": "控球(传球占比)",
                                  "field_tilt": "场地倾斜", "ppda": "PPDA", "deep_completions": "深度推进",
                                  "xt_added": "xT增量"}).round(3), width="stretch")

    col1, col2 = st.columns(2)
    with col1:
        nodes, edges, cutoff = ev.pass_network(e, t_id)
        pitch = Pitch(pitch_type="statsbomb", pitch_color=p["surface"], line_color=p["muted"], linewidth=1)
        fig, ax = pitch.draw(figsize=(8, 5.6))
        fig.set_facecolor(p["surface"])
        if not edges.empty:
            w = 1 + 7 * edges["count"] / edges["count"].max()
            pitch.lines(edges["x"], edges["y"], edges["x_end"], edges["y_end"], lw=w, color=p["series"][0],
                        alpha=0.55, zorder=1, ax=ax)
        pitch.scatter(nodes["x"], nodes["y"], s=80 + 12 * nodes["passes"], color=p["series"][0],
                      edgecolors=p["surface"], linewidth=2, zorder=2, ax=ax)
        for r in nodes.itertuples():
            short = r.player.split()[-1] if isinstance(r.player, str) else ""
            pitch.annotate(short, (r.x, r.y - 3.2), ax=ax, ha="center", va="bottom", fontsize=8, color=p["ink"])
        ax.set_title(f"{t_sel} 传球网络（首次换人前，截至 {cutoff:.0f}′，≥3 次连线）", color=p["ink"], fontsize=10)
        st.pyplot(fig, width="stretch")
        plt.close(fig)
    with col2:
        sh = ev.shots(e)
        vp = VerticalPitch(pitch_type="statsbomb", half=True, pitch_color=p["surface"], line_color=p["muted"],
                           linewidth=1)
        fig, ax = vp.draw(figsize=(8, 5.6))
        fig.set_facecolor(p["surface"])
        mine = sh[sh["team_id"] == t_id]
        for goal, color, lab in ((False, p["muted"], "未进"), (True, p["series"][1], "进球")):
            d = mine[(mine["shot_outcome"] == "Goal") == goal]
            vp.scatter(d["x"], d["y"], s=60 + 900 * d["shot_xg"], color=color, edgecolors=p["surface"],
                       linewidth=2, alpha=0.9, label=lab, ax=ax)
        ax.legend(loc="lower left", frameon=False, labelcolor=p["ink2"], fontsize=8)
        ax.set_title(f"{t_sel} 射门图（点大小 = xG，合计 {mine['shot_xg'].sum():.2f}）", color=p["ink"], fontsize=10)
        st.pyplot(fig, width="stretch")
        plt.close(fig)

    ee = e.copy()
    ee["xt_added"] = ev.xt_added(ee)
    top = (ee[ee["team_id"] == t_id].groupby("player")["xt_added"].sum().sort_values(ascending=False).head(8)
           .reset_index().rename(columns={"player": "球员", "xt_added": "xT增量"}))
    st.markdown("**本场 xT 增量最高的球员**（成功传球 + 带球，penaltyblog 预训练 xT 曲面）")
    st.dataframe(top.round(3), hide_index=True, width="stretch")


with tab_lab:
    event_lab()
