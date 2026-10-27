"""球员评估：赛季聚合 → 每 90 分钟指标 → 同位置百分位 → 相似球员。

- 百分位只在"同位置组 + 达到最低出场分钟"的样本内计算，避免小样本噪声（默认 900 分钟 ≈ 10 场）。
- 相似度：对所选指标在同位置组内做 z-score 标准化，再算余弦相似度（量纲无关、解释直观）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# 计数型指标 → 页面显示名。均换算为每 90 分钟。
COUNT_METRICS = {
    "goals": "进球", "np_goals": "非点球进球", "shots": "射门", "xg": "xG", "npxg": "非点球xG", "xa": "xA",
    "assists": "助攻", "key_passes": "关键传球", "passes_cmp": "传球成功", "prog_passes": "推进传球",
    "passes_final_third": "传入前场三区", "passes_into_box": "传入禁区", "crosses_cmp": "成功传中",
    "prog_carries": "推进带球", "dribbles_won": "过人成功", "pressures": "施压", "tackles_won": "抢断成功",
    "interceptions": "拦截", "recoveries": "夺回球权", "clearances": "解围", "blocks": "封堵",
    "aerials_won": "争顶成功", "turnovers": "丢失球权", "xt_added": "xT增量",
}
RATIO_METRICS = {
    "pass_pct": ("passes_cmp", "passes_att", "传球成功率"),
    "tackle_pct": ("tackles_won", "tackles_att", "抢断成功率"),
    "dribble_pct": ("dribbles_won", "dribbles_att", "过人成功率"),
}
LOWER_IS_BETTER = {"turnovers"}

# 各位置组默认雷达指标
RADAR_PRESETS = {
    "FW": ["npxg", "np_goals", "shots", "xa", "key_passes", "dribbles_won", "prog_carries", "passes_into_box",
           "pressures", "xt_added"],
    "MF": ["xa", "key_passes", "prog_passes", "passes_final_third", "pass_pct", "prog_carries", "tackles_won",
           "interceptions", "pressures", "xt_added"],
    "DF": ["prog_passes", "pass_pct", "passes_final_third", "tackles_won", "tackle_pct", "interceptions",
           "clearances", "blocks", "aerials_won", "recoveries"],
    "GK": ["pass_pct", "passes_cmp", "prog_passes", "recoveries", "clearances"],
}


def label(metric: str) -> str:
    if metric in COUNT_METRICS:
        return COUNT_METRICS[metric]
    if metric in RATIO_METRICS:
        return RATIO_METRICS[metric][2]
    return metric


def season_table(player_match: pd.DataFrame) -> pd.DataFrame:
    """player_match（每人每场一行）→ 每人每赛季每队一行，含每 90 指标与比率指标。"""
    if player_match.empty:
        return pd.DataFrame()
    keys = ["competition_id", "season_id", "player_id", "team"]
    sums = [c for c in (*COUNT_METRICS, "passes_att", "tackles_att", "dribbles_att", "aerials_lost") if c in player_match]
    g = player_match.groupby(keys)
    agg = g[sums + ["minutes"]].sum()
    agg["games"] = g.size()
    main = (player_match.groupby([*keys, "position_group"])["minutes"].sum().reset_index()
            .sort_values("minutes").drop_duplicates(keys, keep="last").set_index(keys)["position_group"])
    agg["position_group"] = main
    pos = (player_match.groupby([*keys, "position"])["minutes"].sum().reset_index()
           .sort_values("minutes").drop_duplicates(keys, keep="last").set_index(keys)["position"])
    agg["position"] = pos
    agg["player"] = g["player"].last()
    agg = agg.reset_index()
    per90 = 90 / agg["minutes"].clip(lower=1)
    for m in COUNT_METRICS:
        agg[f"{m}_p90"] = agg[m] * per90
    for m, (num, den, _) in RATIO_METRICS.items():
        agg[m] = agg[num] / agg[den].replace(0, np.nan)
    return agg


def metric_col(metric: str) -> str:
    return metric if metric in RATIO_METRICS else f"{metric}_p90"


def percentiles(table: pd.DataFrame, metrics: list[str], min_minutes: float = 900) -> pd.DataFrame:
    """同位置组内百分位（0–100）；样本外（分钟不足）的球员不参与排名，结果为 NaN。"""
    out = table.copy()
    eligible = out["minutes"] >= min_minutes
    for m in metrics:
        col = metric_col(m)
        ranks = (out[eligible].groupby("position_group")[col]
                 .rank(pct=True, ascending=m not in LOWER_IS_BETTER) * 100)
        out[f"{m}_pct"] = ranks
    return out


def similar_players(table: pd.DataFrame, player_id: int, team: str, metrics: list[str],
                    min_minutes: float = 900, n: int = 10) -> pd.DataFrame:
    base = table[(table["player_id"] == player_id) & (table["team"] == team)]
    if base.empty:
        return pd.DataFrame()
    group = base["position_group"].iloc[0]
    pool = table[(table["position_group"] == group) & (table["minutes"] >= min_minutes)].copy()
    if (pool["player_id"] == player_id).sum() == 0:
        pool = pd.concat([pool, base])
    cols = [metric_col(m) for m in metrics]
    x = pool[cols].astype(float)
    z = ((x - x.mean()) / x.std(ddof=0).replace(0, 1)).fillna(0).to_numpy()
    target = z[np.flatnonzero((pool["player_id"] == player_id).to_numpy() & (pool["team"] == team).to_numpy())[0]]
    norms = np.linalg.norm(z, axis=1) * np.linalg.norm(target)
    pool["相似度"] = np.where(norms > 0, z @ target / np.where(norms == 0, 1, norms), 0)
    pool = pool[~((pool["player_id"] == player_id) & (pool["team"] == team))]
    return pool.sort_values("相似度", ascending=False).head(n)
