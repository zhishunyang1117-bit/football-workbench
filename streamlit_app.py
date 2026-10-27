"""足球分析工作台 · Streamlit 入口（Streamlit Community Cloud 的 Main file path 填本文件）。

本地运行：streamlit run streamlit_app.py
"""

import streamlit as st

from fwb.app import data, ui

st.set_page_config(page_title="足球分析工作台", page_icon="⚽", layout="wide")

pages = st.navigation(
    [
        st.Page("app_pages/live.py", title="实时比分", icon=":material/sports_soccer:", default=True),
        st.Page("app_pages/league.py", title="联赛看板", icon=":material/leaderboard:"),
        st.Page("app_pages/team.py", title="球队战术", icon=":material/strategy:"),
        st.Page("app_pages/player.py", title="球员评估", icon=":material/person_search:"),
        st.Page("app_pages/predict.py", title="比赛预测", icon=":material/query_stats:"),
        st.Page("app_pages/status.py", title="数据状态", icon=":material/database:"),
    ]
)

# 全局筛选放在入口文件：切换页面时保持选择
st.sidebar.title("⚽ 足球分析工作台")
ui.sidebar_filters()
ui.freshness(data.manifest(data.manifest_key()))

pages.run()
ui.attribution()
