"""StatsBomb 事件数据 → 战术/球员指标。

坐标：StatsBomb 球场 120×80（码），每个事件的坐标都以"执行该事件的球队从左向右进攻"为参照，
对方球门中心为 (120, 40)，禁区为 x≥102 且 18≤y≤62。

指标口径（均为业界公开定义，便于与其他平台对照）：
- xG：直接使用 StatsBomb 提供的 shot.statsbomb_xg（其自有模型，已验证），npxG 剔除点球。
- xA：被助攻射门的 xG 归属给传球者（shot.key_pass_id → pass.id）。
- 推进传球：完成的运动战传球，起点不在本方 40% 区域（x≥48），且
  到球门距离缩短 ≥30m（本半场内）/ ≥15m（跨半场）/ ≥10m（对方半场内）（Wyscout 定义），或传入禁区。
- PPDA：对手在其本方 60% 区域（x<72）的传球数 ÷ 本队在前 60% 区域（x>48）的防守动作
  （抢断尝试 + 拦截 + 犯规）。数值越低 = 高位逼抢越凶。
- xT：penaltyblog 预训练 xT 曲面（16×12，MIT 许可），对成功传球/带球计算 xT(终点) − xT(起点)。
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

YD_PER_M = 1 / 0.9144
GOAL = np.array([120.0, 40.0])
TACKLE_WON = {"Won", "Success In Play", "Success Out"}

POSITION_GROUP = {
    "Goalkeeper": "GK",
    "Right Back": "DF", "Left Back": "DF", "Right Center Back": "DF", "Left Center Back": "DF",
    "Center Back": "DF", "Right Wing Back": "DF", "Left Wing Back": "DF",
    "Right Defensive Midfield": "MF", "Left Defensive Midfield": "MF", "Center Defensive Midfield": "MF",
    "Right Center Midfield": "MF", "Left Center Midfield": "MF", "Center Midfield": "MF",
    "Right Midfield": "MF", "Left Midfield": "MF", "Center Attacking Midfield": "MF",
    "Right Attacking Midfield": "MF", "Left Attacking Midfield": "MF",
    "Right Wing": "FW", "Left Wing": "FW", "Center Forward": "FW",
    "Right Center Forward": "FW", "Left Center Forward": "FW", "Secondary Striker": "FW",
}


def _g(d, *path):
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def flatten(events: list[dict]) -> pd.DataFrame:
    """把嵌套 JSON 事件展平成一行一事件的 DataFrame（只取分析需要的字段）。"""
    rows = []
    for e in events:
        t = e["type"]["name"]
        loc = e.get("location") or [np.nan, np.nan]
        end = (_g(e, "pass", "end_location") or _g(e, "carry", "end_location")
               or _g(e, "shot", "end_location") or [np.nan, np.nan])
        rows.append(
            {
                "id": e["id"],
                "index": e["index"],
                "period": e["period"],
                "minute": e["minute"],
                "second": e["second"],
                "type": t,
                # 同一球队在事件文件与比赛索引中的名字可能不同（实测 'Caen' vs 'Stade Malherbe Caen'），
                # 关联一律用 team_id，名字只用于显示
                "team_id": _g(e, "team", "id"),
                "team": _g(e, "team", "name"),
                "possession_team": _g(e, "possession_team", "name"),
                "player_id": _g(e, "player", "id"),
                "player": _g(e, "player", "name"),
                "position": _g(e, "position", "name"),
                "x": loc[0], "y": loc[1], "end_x": end[0], "end_y": end[1],
                "duration": e.get("duration") or 0.0,
                "play_pattern": _g(e, "play_pattern", "name"),
                "pass_outcome": _g(e, "pass", "outcome", "name"),
                "pass_type": _g(e, "pass", "type", "name"),
                "pass_recipient_id": _g(e, "pass", "recipient", "id"),
                "pass_recipient": _g(e, "pass", "recipient", "name"),
                "pass_cross": bool(_g(e, "pass", "cross")),
                "shot_assist": bool(_g(e, "pass", "shot_assist")),
                "goal_assist": bool(_g(e, "pass", "goal_assist")),
                "shot_xg": _g(e, "shot", "statsbomb_xg"),
                "shot_outcome": _g(e, "shot", "outcome", "name"),
                "shot_type": _g(e, "shot", "type", "name"),
                "shot_body_part": _g(e, "shot", "body_part", "name"),
                "key_pass_id": _g(e, "shot", "key_pass_id"),
                "duel_type": _g(e, "duel", "type", "name"),
                "duel_outcome": _g(e, "duel", "outcome", "name"),
                "interception_outcome": _g(e, "interception", "outcome", "name"),
                "dribble_outcome": _g(e, "dribble", "outcome", "name"),
                "recovery_failure": bool(_g(e, "ball_recovery", "recovery_failure")),
                "aerial_won": bool(_g(e, "pass", "aerial_won") or _g(e, "clearance", "aerial_won")
                                   or _g(e, "shot", "aerial_won") or _g(e, "miscontrol", "aerial_won")),
                "sub_replacement_id": _g(e, "substitution", "replacement", "id"),
                "sub_replacement": _g(e, "substitution", "replacement", "name"),
                "card": _g(e, "foul_committed", "card", "name") or _g(e, "bad_behaviour", "card", "name"),
                # 首发名单：[(player_id, name, position), ...]，仅 Starting XI 事件有值
                "xi": [(p["player"]["id"], p["player"]["name"], _g(p, "position", "name"))
                       for p in (_g(e, "tactics", "lineup") or [])] if t == "Starting XI" else None,
            }
        )
    df = pd.DataFrame(rows)
    df["shot_xg"] = pd.to_numeric(df["shot_xg"]).astype(float)
    return df


# ---------------------------------------------------------------- helpers
def _dist_to_goal(x, y):
    return np.hypot(GOAL[0] - np.asarray(x, float), GOAL[1] - np.asarray(y, float))


def _in_box(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    return (x >= 102) & (y >= 18) & (y <= 62)


def progressive(df: pd.DataFrame) -> pd.Series:
    """Wyscout 推进定义（阈值由米换算为码）+ 传入禁区；排除本方 40% 区域起点。"""
    d0, d1 = _dist_to_goal(df["x"], df["y"]), _dist_to_goal(df["end_x"], df["end_y"])
    gain = d0 - d1
    own0, own1 = df["x"].to_numpy() < 60, df["end_x"].to_numpy() < 60
    need = np.where(own0 & own1, 30, np.where(own0 & ~own1, 15, 10)) * YD_PER_M
    box = _in_box(df["end_x"], df["end_y"]) & ~_in_box(df["x"], df["y"])
    return pd.Series(((gain >= need) | box) & (df["x"].to_numpy() >= 48), index=df.index)


@lru_cache(maxsize=1)
def _xt_model():
    import penaltyblog as pb

    return pb.xt.load_pretrained_xt()


def xt_added(df: pd.DataFrame) -> pd.Series:
    """成功传球/带球的 xT 增量；其它事件为 0。StatsBomb(120×80, y 向下) → Opta(0-100, y 向上)。"""
    ok = ((df["type"] == "Pass") & df["pass_outcome"].isna()) | (df["type"] == "Carry")
    ok &= df[["x", "y", "end_x", "end_y"]].notna().all(axis=1)
    out = pd.Series(0.0, index=df.index)
    if ok.any():
        m = _xt_model()
        s = df.loc[ok]
        to_x = lambda v: np.clip(v.to_numpy(float) / 120 * 100, 0, 100)  # noqa: E731
        to_y = lambda v: np.clip(100 - v.to_numpy(float) / 80 * 100, 0, 100)  # noqa: E731
        out.loc[ok] = m.values_at(to_x(s["end_x"]), to_y(s["end_y"])) - m.values_at(to_x(s["x"]), to_y(s["y"]))
    return out


def period_ends(ev: pd.DataFrame) -> dict[int, float]:
    t = ev["minute"] + ev["second"] / 60
    return t.groupby(ev["period"]).max().to_dict()


def _elapsed(clock_min: float, period: int, ends: dict[int, float]) -> float:
    """把"累计时钟"换算为真实经过分钟（处理上半场补时与下半场从 45:00 重新计时的重叠）。"""
    starts = {1: 0.0, 2: 45.0, 3: 90.0, 4: 105.0, 5: 120.0}
    elapsed = 0.0
    for p in range(1, period):
        elapsed += max(ends.get(p, starts.get(p + 1, 0)) - starts.get(p, 0), 0)
    return elapsed + max(clock_min - starts.get(period, 0), 0)


def minutes_played(ev: pd.DataFrame, lineups: list[dict] | None = None) -> pd.DataFrame:
    """由事件流推导出场时间：首发(Starting XI) / 换人(Substitution) / 罚下(红牌或两黄)。

    不用 lineups 的 positions 时间段：实测其不记录红牌罚下，且存在半场编号错乱。
    lineups 仅用于取球员常用名（nickname）。
    """
    ends = period_ends(ev)
    last = max(ends) if ends else 2
    total = _elapsed(ends.get(last, 90), last, ends)
    t = ev["minute"] + ev["second"] / 60
    when = [_elapsed(c, p, ends) for c, p in zip(t, ev["period"])]
    ev = ev.assign(elapsed=when)
    start: dict[int, float] = {}
    stop: dict[int, float] = {}
    team_of: dict[int, tuple] = {}
    name_of: dict[int, str] = {}
    for r in ev[ev["type"] == "Starting XI"].itertuples():
        for pid, pname, _pos in r.xi or []:
            start[pid], team_of[pid], name_of[pid] = 0.0, (r.team_id, r.team), pname
    for r in ev[ev["type"] == "Substitution"].itertuples():
        if pd.notna(r.player_id):
            stop[int(r.player_id)] = min(stop.get(int(r.player_id), total), r.elapsed)
        if pd.notna(r.sub_replacement_id):
            pid = int(r.sub_replacement_id)
            start[pid], team_of[pid] = r.elapsed, (r.team_id, r.team)
            name_of.setdefault(pid, r.sub_replacement)
    sent_off = ev[ev["card"].isin(["Red Card", "Second Yellow"]) & ev["player_id"].notna()]
    for r in sent_off.itertuples():
        stop[int(r.player_id)] = min(stop.get(int(r.player_id), total), r.elapsed)
    nick = {p["player_id"]: (p.get("player_nickname") or p["player_name"])
            for tm in (lineups or []) for p in tm["lineup"]}
    pos_mode = (ev.dropna(subset=["player_id", "position"]).groupby("player_id")["position"]
                .agg(lambda s: s.value_counts().index[0]))
    xi_pos = {pid: pos for r in ev[ev["type"] == "Starting XI"].itertuples() for pid, _, pos in (r.xi or [])}
    rows = []
    for pid, s in start.items():
        mins = max(stop.get(pid, total) - s, 0.0)
        if mins <= 0:
            continue
        pos = pos_mode.get(pid) or xi_pos.get(pid) or "Unknown"
        tid, tname = team_of[pid]
        rows.append({"team_id": tid, "team": tname, "player_id": pid, "player": nick.get(pid) or name_of.get(pid),
                     "position": pos, "position_group": POSITION_GROUP.get(pos, "MF"), "minutes": round(mins, 1)})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- player level
def player_match(ev: pd.DataFrame, lineups: list[dict]) -> pd.DataFrame:
    ev = ev.copy()
    ev["xt_added"] = xt_added(ev)
    is_pass = ev["type"] == "Pass"
    cmp_ = is_pass & ev["pass_outcome"].isna()
    open_pass = is_pass & (ev["pass_type"].isna() | ev["pass_type"].isin(["Recovery", "Interception"]))
    shots = ev["type"] == "Shot"
    np_shot = shots & (ev["shot_type"] != "Penalty")
    goal = shots & (ev["shot_outcome"] == "Goal")
    carry = ev["type"] == "Carry"
    prog = progressive(ev)

    xa = ev.loc[shots & ev["key_pass_id"].notna()].groupby("key_pass_id")["shot_xg"].sum()
    ev["xa"] = ev["id"].map(xa).fillna(0.0).where(is_pass, 0.0)

    f = {
        "goals": goal,
        "np_goals": goal & np_shot,
        "shots": shots,
        "xg": ev["shot_xg"].where(shots, 0.0).fillna(0.0),
        "npxg": ev["shot_xg"].where(np_shot, 0.0).fillna(0.0),
        "xa": ev["xa"],
        "assists": is_pass & ev["goal_assist"],
        "key_passes": is_pass & (ev["shot_assist"] | ev["goal_assist"]),
        "passes_att": is_pass,
        "passes_cmp": cmp_,
        "prog_passes": cmp_ & open_pass & prog,
        "passes_final_third": cmp_ & (ev["end_x"] >= 80) & (ev["x"] < 80),
        "passes_into_box": cmp_ & pd.Series(_in_box(ev["end_x"], ev["end_y"]) & ~_in_box(ev["x"], ev["y"]), index=ev.index),
        "crosses_cmp": cmp_ & ev["pass_cross"],
        "prog_carries": carry & prog,
        "dribbles_att": ev["type"] == "Dribble",
        "dribbles_won": (ev["type"] == "Dribble") & (ev["dribble_outcome"] == "Complete"),
        "pressures": ev["type"] == "Pressure",
        "tackles_att": (ev["type"] == "Duel") & (ev["duel_type"] == "Tackle"),
        "tackles_won": (ev["type"] == "Duel") & (ev["duel_type"] == "Tackle") & ev["duel_outcome"].isin(TACKLE_WON),
        "interceptions": ev["type"] == "Interception",
        "recoveries": (ev["type"] == "Ball Recovery") & ~ev["recovery_failure"],
        "clearances": ev["type"] == "Clearance",
        "blocks": ev["type"] == "Block",
        "aerials_won": ev["aerial_won"],
        "aerials_lost": (ev["type"] == "Duel") & (ev["duel_type"] == "Aerial Lost"),
        "fouls": ev["type"] == "Foul Committed",
        "turnovers": ev["type"].isin(["Dispossessed", "Miscontrol"]),
        "xt_added": ev["xt_added"],
    }
    stats = pd.DataFrame({k: v.astype(float) for k, v in f.items()})
    stats["player_id"] = ev["player_id"]
    agg = stats.dropna(subset=["player_id"]).groupby("player_id").sum().reset_index()
    mins = minutes_played(ev, lineups)
    out = mins.merge(agg, on="player_id", how="left").fillna({c: 0.0 for c in f})
    out["player_id"] = out["player_id"].astype(int)
    return out


# ---------------------------------------------------------------- team level
def _team_mask(ev: pd.DataFrame, team_id: int | None, name: str) -> pd.Series:
    return ev["team_id"] == team_id if team_id is not None else ev["team"] == name


def team_match(ev: pd.DataFrame, home: str, away: str, home_id: int | None = None,
               away_id: int | None = None) -> pd.DataFrame:
    """每队一行的单场战术指标。给出 team_id 时按 id 关联（推荐）；输出使用传入的 home/away 名称。

    任一方找不到事件时抛 ValueError（宁可跳过，也不写入全 0 的错误行）。
    """
    ev = ev.copy()
    ev["xt_added"] = xt_added(ev)
    masks = {home: _team_mask(ev, home_id, home), away: _team_mask(ev, away_id, away)}
    for name, mk in masks.items():
        if not mk.any():
            raise ValueError(f"事件中找不到球队 {name}（id={home_id if name == home else away_id}）")
    rows = []
    for team, opp in ((home, away), (away, home)):
        me, op = ev[masks[team]], ev[masks[opp]]
        shots_me, shots_op = me[me["type"] == "Shot"], op[op["type"] == "Shot"]
        passes_me, passes_op = me[me["type"] == "Pass"], op[op["type"] == "Pass"]
        def_actions = me[
            ((me["type"] == "Duel") & (me["duel_type"] == "Tackle"))
            | (me["type"] == "Interception")
            | (me["type"] == "Foul Committed")
        ]
        opp_build = passes_op[passes_op["x"] < 72]
        press_actions = def_actions[def_actions["x"] > 48]
        cmp_me = passes_me[passes_me["pass_outcome"].isna()]
        final_third_me = (passes_me["x"] >= 80).sum()
        final_third_op = (passes_op["x"] >= 80).sum()
        own_goals_for = (op["type"] == "Own Goal Against").sum()
        rows.append(
            {
                "team": team,
                "opponent": opp,
                "is_home": team == home,
                "goals": int((shots_me["shot_outcome"] == "Goal").sum() + own_goals_for),
                "goals_against": int((shots_op["shot_outcome"] == "Goal").sum() + (me["type"] == "Own Goal Against").sum()),
                "xg": float(shots_me["shot_xg"].sum()),
                "xga": float(shots_op["shot_xg"].sum()),
                "npxg": float(shots_me.loc[shots_me["shot_type"] != "Penalty", "shot_xg"].sum()),
                "shots": len(shots_me),
                "shots_against": len(shots_op),
                "passes": len(passes_me),
                "pass_pct": float(len(cmp_me) / max(len(passes_me), 1)),
                "possession": float(len(passes_me) / max(len(passes_me) + len(passes_op), 1)),
                "field_tilt": float(final_third_me / max(final_third_me + final_third_op, 1)),
                # 没有高位防守动作时 PPDA 无定义（记 NaN，赛季均值自动忽略），不能用 1 作分母
                "ppda": float(len(opp_build) / len(press_actions)) if len(press_actions) else float("nan"),
                "deep_completions": int(
                    (~cmp_me["pass_cross"] & (_dist_to_goal(cmp_me["end_x"], cmp_me["end_y"]) <= 20 * YD_PER_M)).sum()
                ),
                "high_recoveries": int(((me["type"] == "Ball Recovery") & (me["x"] >= 80)).sum()),
                "xt_added": float(me["xt_added"].sum()),
            }
        )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- visuals data
def shots(ev: pd.DataFrame) -> pd.DataFrame:
    s = ev[ev["type"] == "Shot"][["team_id", "team", "player", "minute", "x", "y", "shot_xg", "shot_outcome",
                                   "shot_type", "shot_body_part", "play_pattern"]]
    return s.reset_index(drop=True)


def pass_network(ev: pd.DataFrame, team_id: int, min_passes: int = 3) -> tuple[pd.DataFrame, pd.DataFrame, float]:
    """首次换人前的传球网络。返回 (节点, 边, 截止分钟)。节点位置 = 该球员传球起点与接球点的平均位置。"""
    t = ev[ev["team_id"] == team_id]
    subs = t[t["type"] == "Substitution"]
    cut_idx = int(subs["index"].min()) if len(subs) else int(t["index"].max()) + 1
    cutoff = float(subs.loc[subs["index"] == cut_idx, "minute"].iloc[0]) if len(subs) else float(t["minute"].max())
    p = t[(t["type"] == "Pass") & t["pass_outcome"].isna() & t["pass_recipient"].notna() & (t["index"] < cut_idx)]
    starts = p[["player", "x", "y"]]
    receipts = p[["pass_recipient", "end_x", "end_y"]].set_axis(["player", "x", "y"], axis=1)
    loc = pd.concat([starts, receipts]).groupby("player")[["x", "y"]].mean()
    cnt = p.groupby("player").size().rename("passes")
    nodes = loc.join(cnt, how="left").fillna({"passes": 0}).reset_index()
    pair = p.assign(a=p[["player", "pass_recipient"]].min(axis=1), b=p[["player", "pass_recipient"]].max(axis=1))
    edges = pair.groupby(["a", "b"]).size().rename("count").reset_index()
    edges = edges[edges["count"] >= min_passes]
    edges = edges.merge(nodes[["player", "x", "y"]].rename(columns={"player": "a"}), on="a")
    edges = edges.merge(nodes[["player", "x", "y"]].rename(columns={"player": "b"}), on="b", suffixes=("", "_end"))
    return nodes, edges, cutoff
