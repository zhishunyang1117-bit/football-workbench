"""公共 UI：配色（已验证的参考调色板）、Plotly 模板、侧边栏筛选、署名页脚。

配色规则（dataviz 方法）：分类色按固定顺序分配、颜色跟随实体而非排名；
散点等"全配对"图最多 3 个系列；偏差用蓝↔红两极 + 灰色中点；深色模式使用各自的色阶而非自动反转。
"""

from __future__ import annotations

from datetime import datetime, timezone

import matplotlib as mpl
import plotly.graph_objects as go
import streamlit as st

from fwb.settings import Season, all_seasons, current_season, leagues

# matplotlib（mplsoccer 球场图/披萨图）中文字体回退链：
# Windows 自带微软雅黑/黑体；Streamlit Cloud(Debian) 通过 packages.txt 安装 fonts-noto-cjk。
mpl.use("Agg")  # 服务器端无界面后端，避免 Tk 线程问题
mpl.rcParams["font.family"] = "sans-serif"
mpl.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "Noto Sans CJK JP",
                                   "WenQuanYi Zen Hei", "PingFang SC", "DejaVu Sans"]
mpl.rcParams["axes.unicode_minus"] = False

LIGHT = {
    "surface": "#fcfcfb", "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781",
    "grid": "#e1e0d9", "axis": "#c3c2b7", "mid": "#f0efec",
    "series": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"],
    "pos": "#2a78d6", "neg": "#e34948",
}
DARK = {
    "surface": "#1a1a19", "ink": "#ffffff", "ink2": "#c3c2b7", "muted": "#898781",
    "grid": "#2c2c2a", "axis": "#383835", "mid": "#383835",
    "series": ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"],
    "pos": "#3987e5", "neg": "#e66767",
}


def palette() -> dict:
    """优先服从 .streamlit/config.toml 固定的 theme.base；未固定时跟随用户当前主题。"""
    base = st.get_option("theme.base")
    if base in ("light", "dark"):
        return DARK if base == "dark" else LIGHT
    try:
        return DARK if st.context.theme.type == "dark" else LIGHT
    except Exception:  # noqa: BLE001  旧版本 Streamlit 无 st.context.theme
        return LIGHT


def style(fig: go.Figure, height: int = 380, legend: bool = True) -> go.Figure:
    p = palette()
    has_title = bool(fig.layout.title and fig.layout.title.text)
    top = (34 if has_title else 8) + (30 if legend else 0)
    fig.update_layout(
        height=height, margin=dict(l=8, r=8, t=top, b=8),
        paper_bgcolor=p["surface"], plot_bgcolor=p["surface"],
        font=dict(family='system-ui, -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif',
                  color=p["ink2"], size=13),
        # 标题贴容器顶部，图例在标题下方、绘图区上方，两者不重叠
        title=dict(font=dict(color=p["ink"], size=15), x=0, xanchor="left", y=1, yanchor="top",
                   yref="container", pad=dict(t=8, l=4)),
        colorway=p["series"], showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.01, x=0, xanchor="left", font=dict(color=p["ink2"])),
        hoverlabel=dict(bgcolor=p["surface"], font_color=p["ink"], bordercolor=p["axis"]),
        bargap=0.25,
    )
    axis = dict(gridcolor=p["grid"], linecolor=p["axis"], zerolinecolor=p["axis"],
                tickfont=dict(color=p["muted"]), title_font=dict(color=p["ink2"]))
    fig.update_xaxes(**axis)
    fig.update_yaxes(**axis)
    return fig


def league_label(code: str) -> str:
    return f"{leagues()[code]['name']}（{leagues()[code]['name_en']}）"


def sidebar_filters(season: bool = True) -> tuple[str, str]:
    """侧边栏：联赛 + 赛季。选择保存在 session_state，跨页面共享。"""
    lg = st.sidebar.selectbox("联赛", list(leagues()), format_func=league_label, key="league")
    ss = current_season()
    if season:
        seasons = sorted(all_seasons(), reverse=True)
        ss = st.sidebar.selectbox("赛季", seasons, format_func=lambda s: Season(s).label, key="season")
    return lg, ss


def freshness(manifest: dict) -> None:
    # 显示最近一次"赛果类"运行（增量/全量），StatsBomb 回填不代表当季数据新鲜度
    runs = [r for r in manifest.get("runs", []) if r.get("mode") in ("incremental", "full")]
    run = runs[0] if runs else (manifest.get("last_run") or {})
    ts = run.get("finished_at")
    if not ts:
        st.sidebar.warning("尚无数据：请先运行 `python -m fwb.pipeline --mode full`")
        return
    age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds() / 3600
    icon = "✅" if run.get("status") == "success" and age_h < 36 else "⚠️"
    st.sidebar.caption(f"{icon} 数据更新：{ts[:16].replace('T', ' ')} UTC（{age_h:.0f} 小时前）· "
                       f"版本 `{manifest.get('data_version', '-')}` · 模式 {run.get('mode')}")


def attribution() -> None:
    st.divider()
    st.caption(
        "数据来源：football-data.co.uk（赛果/统计/赔率/xG）· openfootball/football.json（CC0 赛程）· "
        "football-data.org（可选，实时）· **StatsBomb Open Data**（事件级历史数据，按其条款需署名 StatsBomb）。"
        " 模型与可视化：penaltyblog（MIT）· mplsoccer（MIT）。本工作台仅用于数据分析与研究，不构成任何投注建议。"
    )
