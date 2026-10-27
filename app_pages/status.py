"""数据状态：版本、运行日志、分区清单（按 实时/历史/衍生 分类）、数据源合规说明。"""

import pandas as pd
import streamlit as st

from fwb.app import data
from fwb.storage import TABLES

st.title("数据状态与合规")
man = data.manifest(data.manifest_key())
run = man.get("last_run") or {}

c = st.columns(4)
c[0].metric("数据版本", man.get("data_version", "–"))
c[1].metric("最近运行", run.get("status", "–"))
c[2].metric("模式", run.get("mode", "–"))
c[3].metric("耗时", f"{run.get('duration_s', 0):.0f} 秒")
for w in run.get("warnings", []):
    st.warning(w)

st.subheader("运行记录（最近 30 次）")
runs = pd.DataFrame(man.get("runs", []))
if not runs.empty:
    st.dataframe(runs[["finished_at", "mode", "status", "partitions_changed", "row_delta", "not_modified_skips",
                       "duration_s"]].rename(columns={
        "finished_at": "完成时间(UTC)", "mode": "模式", "status": "状态", "partitions_changed": "变更分区",
        "row_delta": "行数变化", "not_modified_skips": "304跳过", "duration_s": "耗时(秒)"}),
        hide_index=True, width="stretch")

st.subheader("数据分区")
cls = {"realtime": "实时", "historical": "历史", "derived": "衍生"}
rows = []
for rel, meta in man.get("partitions", {}).items():
    layer, name, *parts = rel.split("/")
    spec = TABLES.get(name)
    rows.append({"类别": cls.get(spec.data_class, "?") if spec else "?", "表": name, "分区": "/".join(parts),
                 "行数": meta["rows"], "更新时间(UTC)": meta["updated_at"], "说明": spec.description if spec else ""})
if rows:
    df = pd.DataFrame(rows).sort_values(["类别", "表", "分区"])
    kind = st.segmented_control("筛选类别", ["实时", "历史", "衍生"], selection_mode="multi",
                                default=["实时", "历史", "衍生"])
    st.dataframe(df[df["类别"].isin(kind or [])], hide_index=True, width="stretch")

st.subheader("三类数据的更新策略")
st.markdown("""
| 类别 | 内容 | 更新方式 | 频率 |
|---|---|---|---|
| **实时** | 未来赛程、比赛状态、赛前赔率、射手榜 | 每次运行整体覆盖为最新快照 | 每日 + 周末比赛日加跑 |
| **历史** | 已完赛赛果/技术统计/收盘赔率/xG、StatsBomb 事件 | 当季按整季文件替换分区（吸收修正，行数骤降保护）；往季冻结，仅全量模式重拉 | 增量每日；全量每周一 |
| **衍生** | Elo、Dixon-Coles 预测、回测、球队/球员聚合 | 每次由原始层重算，带 model_version，永不回写原始层 | 随每次运行 |
""")

st.subheader("数据源与合规")
st.markdown("""
| 数据源 | 用途 | 许可 / 条款 | robots.txt（2026-09 核实） |
|---|---|---|---|
| football-data.co.uk | 赛果、统计、赔率、xG | 免费下载；免责声明未限制分析用途 | 通用 UA 无限制（仅屏蔽 AI 爬虫） |
| openfootball/football.json | 赛程 | **CC0-1.0** 公有领域 | raw.githubusercontent.com 无 robots（404 = 不限制） |
| football-data.org（可选） | 实时状态、射手榜 | 官方 API，需免费 token，遵守限流 | API 无 robots；站点仅屏蔽 dotbot |
| StatsBomb Open Data | 事件级历史数据 | 使用须注明 StatsBomb 并使用其 logo | 同 raw.githubusercontent.com |
| ~~Understat~~ | 不接入 | — | `Disallow: /`（全站禁止） |
| ~~FBref~~ | 不接入 | — | 对脚本请求返回 403 |
| ~~FotMob / Sofascore / WhoScored / Transfermarkt~~ | 不接入 | 服务条款禁止自动抓取 | FotMob 禁止 /api/* |
""")
