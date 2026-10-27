"""实时比分：五大联赛进行中 / 即将开赛 / 刚结束的比赛，每 60 秒自动刷新（football-data.org）。"""

from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st

from fwb.app import data
from fwb.http import FETCH_ERRORS
from fwb.settings import display_timezone, fdorg_token, load_settings

LEAGUE = {"PL": "英超", "PD": "西甲", "BL1": "德甲", "SA": "意甲", "FL1": "法甲"}
STATUS = {"IN_PLAY": "🔴 进行中", "PAUSED": "⏸ 中场", "FINISHED": "✅ 完场", "AWARDED": "✅ 判定",
          "SCHEDULED": "未开赛", "TIMED": "未开赛", "POSTPONED": "推迟", "SUSPENDED": "中断", "CANCELLED": "取消"}
REFRESH = int(load_settings().get("app", {}).get("live_refresh_s", 60))
TZ = display_timezone()
TZ_LABEL = "北京时间" if TZ == "Asia/Shanghai" else TZ

st.title("实时比分")

if not fdorg_token():
    st.info(
        "实时比分需要 football-data.org 的免费 API token：\n\n"
        "1. 打开 https://www.football-data.org/client/register 免费注册，token 会发到你的邮箱；\n"
        "2. 重新运行 `start_share.bat`，按提示粘贴 token（保存在本机 `.streamlit/secrets.toml`，不会上传）。"
    )
else:
    @st.fragment(run_every=f"{REFRESH}s")
    def board() -> None:
        now = datetime.now(timezone.utc)
        try:
            df, fetched = data.live_matches((now - timedelta(days=1)).date().isoformat(),
                                            (now + timedelta(days=1)).date().isoformat())
        except FETCH_ERRORS as err:
            st.warning(f"暂时无法获取实时比分（{type(err).__name__}），{REFRESH} 秒后自动重试。")
            return
        fetched_local = pd.Timestamp(fetched).tz_convert(TZ).strftime("%H:%M:%S")
        st.caption(f"每 {REFRESH} 秒自动刷新 · 最近获取 {fetched_local}（{TZ_LABEL}）· "
                   "数据来源 football-data.org（免费层，比分可能有少量延迟）")
        if df.empty:
            st.info("前后 24 小时内五大联赛没有比赛（可能是国家队比赛日）。")
            return
        df = df.assign(
            联赛=df["competition"].map(LEAGUE).fillna(df["competition"]),
            开球=df["utc_kickoff"].dt.tz_convert(TZ).dt.strftime("%m-%d %H:%M"),
            状态=df["status"].map(STATUS).fillna(df["status"]),
            比分=[f"{h} : {a}" if pd.notna(h) else "–" for h, a in zip(df["hg"], df["ag"])],
            半场=[f"({h}:{a})" if pd.notna(h) else "" for h, a in zip(df["hthg"], df["htag"])],
        )
        if df["minute"].notna().any():
            df["状态"] = [f"{s} {int(m)}′" if pd.notna(m) and s.startswith("🔴") else s
                        for s, m in zip(df["状态"], df["minute"])]
        live = df[df["status"].isin(["IN_PLAY", "PAUSED"])]
        upcoming = df[df["status"].isin(["SCHEDULED", "TIMED"]) & (df["utc_kickoff"] <= now + timedelta(hours=30))]
        done = df[df["status"].isin(["FINISHED", "AWARDED"]) & (df["utc_kickoff"] >= now - timedelta(hours=16))]
        other = df[df["status"].isin(["POSTPONED", "SUSPENDED", "CANCELLED"])]

        c = st.columns(3)
        c[0].metric("进行中", len(live))
        c[1].metric("30 小时内开赛", len(upcoming))
        c[2].metric("16 小时内完场", len(done))
        cols = ["联赛", "开球", "home", "比分", "away", "半场", "状态"]
        rename = {"home": "主队", "away": "客队"}
        for title, part in (("进行中", live), ("即将开赛", upcoming), ("刚刚结束", done), ("推迟 / 中断", other)):
            if part.empty:
                continue
            st.subheader(title)
            st.dataframe(part[cols].rename(columns=rename), hide_index=True, width="stretch")

    board()

st.caption("积分榜、预测等其余页面由后台管道定时更新（本机分享模式默认每 30 分钟一次）。")
