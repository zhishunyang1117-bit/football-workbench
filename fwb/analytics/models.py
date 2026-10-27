"""比赛预测与评估。

模型（均复用 penaltyblog，MIT 许可，经论文与测试验证）：
- Dixon-Coles (1997)：带低比分修正的双泊松模型 + 时间衰减权重 exp(-ξ·Δdays)。
- Elo：penaltyblog.ratings.Elo（含主场优势与高斯型平局概率），本模块补充赛季间处理：
  新赛季向 1500 回归 r；升班马继承降级队平均分（首次出现且无可继承时用 promoted_default）。
- 市场基准：收盘平均赔率去除抽水后的隐含概率（Shin 方法，penaltyblog.implied）。

回测：严格"向前滚动"——每个比赛窗口（周二至周四 / 周五至周一）只用窗口开始前的数据训练，
与市场收盘概率在同一样本上比较 RPS / 对数损失 / Brier / 命中率。回测结果是评估模型的唯一依据。
"""

from __future__ import annotations

import logging
from datetime import timedelta

import numpy as np
import pandas as pd
import penaltyblog as pb

from fwb.analytics.league import finished

log = logging.getLogger(__name__)
MODEL_VERSION = "dc1-elo1"
OUTCOMES = ("h", "d", "a")


# ------------------------------------------------------------------ utils
def outcome(hg, ag) -> np.ndarray:
    hg, ag = np.asarray(hg, float), np.asarray(ag, float)
    return np.where(hg > ag, 0, np.where(hg == ag, 1, 2))


def implied_probs(odds: pd.DataFrame, method: str = "shin") -> np.ndarray:
    """(n,3) 赔率 → (n,3) 去抽水隐含概率；缺失或非法赔率行返回 NaN。"""
    arr = odds.to_numpy(dtype=float, na_value=np.nan)
    out = np.full(arr.shape, np.nan)
    valid = np.isfinite(arr).all(axis=1) & (arr > 1.0).all(axis=1)
    for i in np.flatnonzero(valid):
        try:
            out[i] = pb.implied.calculate_implied(list(arr[i]), method=method).probabilities
        except Exception:  # noqa: BLE001  数值失败时退回乘法归一化
            inv = 1 / arr[i]
            out[i] = inv / inv.sum()
    return out


def _to_ts(s) -> pd.Series:
    return pd.to_datetime(pd.Series(s))


# ------------------------------------------------------------------ Dixon-Coles
class DixonColes:
    def __init__(self, xi: float = 0.0018):
        self.xi = xi
        self.model = None
        self.teams: set[str] = set()
        self.failures = 0

    def fit(self, train: pd.DataFrame, ref_date) -> "DixonColes":
        dates = _to_ts(train["match_date"]).tolist()
        w = pb.models.dixon_coles_weights(dates, xi=self.xi, base_date=pd.Timestamp(ref_date))
        self.model = pb.models.DixonColesGoalModel(
            train["hg"].astype(int).to_numpy(), train["ag"].astype(int).to_numpy(),
            train["home"].astype(str).to_numpy(), train["away"].astype(str).to_numpy(), weights=w,
        )
        self.model.fit()
        self.teams = set(train["home"]) | set(train["away"])
        return self

    def predict(self, home: str, away: str) -> dict | None:
        if home not in self.teams or away not in self.teams:
            return None
        try:
            g = self.model.predict(home, away)
        except ValueError as e:
            # 极端参数下 DC 的低比分修正（ρ）可能使个别格子概率为负，penaltyblog 会拒绝；记为无预测
            self.failures += 1
            log.debug("DC 预测失败 %s vs %s: %s", home, away, e)
            return None
        k = np.arange(len(g.home_goal_distribution()))
        mat = np.array([[g.exact_score(i, j) for j in range(6)] for i in range(6)])
        i, j = np.unravel_index(mat.argmax(), mat.shape)
        return {
            "dc_h": float(g.home_win), "dc_d": float(g.draw), "dc_a": float(g.away_win),
            "dc_xg_h": float((k * g.home_goal_distribution()).sum()),
            "dc_xg_a": float((k * g.away_goal_distribution()).sum()),
            "dc_over25": float(g.total_goals("over", 2.5)),
            "dc_btts": float(g.btts_yes),
            "dc_top_score": f"{i}-{j}",
        }


# ------------------------------------------------------------------ Elo
class SeasonElo:
    def __init__(self, k: float = 20, home_field_advantage: float = 60, season_regression: float = 0.2,
                 promoted_default: float = 1420):
        self.elo = pb.ratings.Elo(k=k, home_field_advantage=home_field_advantage)
        self.reg = season_regression
        self.promoted_default = promoted_default
        self._season_teams: set[str] = set()
        self._promoted_base = promoted_default

    def start_season(self, teams: set[str]) -> None:
        """teams 必须是该赛季的完整参赛队（含尚未出场的），否则保级队会被误判为"离开"。"""
        r = self.elo.ratings
        if self._season_teams:
            for t in r:
                r[t] = 1500 + (1 - self.reg) * (r[t] - 1500)
            # 排序后求和：集合迭代顺序随进程哈希种子变化，浮点加法顺序不同会让结果末位抖动，
            # 进而让衍生表内容哈希每次都变（产生无意义的 git 提交）
            leaving = sorted(self._season_teams - teams)
            self._promoted_base = float(np.mean([r[t] for t in leaving])) if leaving else self.promoted_default
            for t in sorted(teams - set(r)):
                r[t] = self._promoted_base
        self._season_teams = set(teams)

    def ensure(self, team: str) -> None:
        if team not in self.elo.ratings:
            self.elo.ratings[team] = self._promoted_base if self._season_teams else 1500.0

    def probs(self, home: str, away: str) -> tuple[float, float, float]:
        self.ensure(home)
        self.ensure(away)
        p = self.elo.calculate_match_probabilities(home, away)
        return p["home_win"], p["draw"], p["away_win"]

    def run(self, fin: pd.DataFrame, teams_by_season: dict[str, set[str]] | None = None
            ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """按时间顺序跑完所有已完赛比赛。返回 (每场赛前概率, 评分轨迹)。

        teams_by_season：各赛季完整参赛队（取自含未开赛比赛的完整赛程）。缺省时退化为"已完赛比赛中出现的球队"，
        新赛季首轮未踢完时会低估保级队、误判升班马，因此管道总是传入完整集合。
        """
        pre, hist = [], []
        fin = fin.sort_values(["match_date", "home"])
        teams_by_season = teams_by_season or {}
        for season, sm in fin.groupby("season", sort=True):
            self.start_season(teams_by_season.get(season, set()) | set(sm["home"]) | set(sm["away"]))
            for r in sm.itertuples(index=False):
                ph, pd_, pa = self.probs(r.home, r.away)
                rh, ra = self.elo.get_team_rating(r.home), self.elo.get_team_rating(r.away)
                self.elo.update_ratings(r.home, r.away, int(outcome(r.hg, r.ag)))
                pre.append({"league": r.league, "season": season, "home": r.home, "away": r.away,
                            "elo_h": ph, "elo_d": pd_, "elo_a": pa, "elo_home_pre": rh, "elo_away_pre": ra})
                for team, before in ((r.home, rh), (r.away, ra)):
                    hist.append({"league": r.league, "season": season, "match_date": r.match_date, "team": team,
                                 "rating_pre": before, "rating": self.elo.get_team_rating(team)})
        return pd.DataFrame(pre), pd.DataFrame(hist)


def season_teams(matches: pd.DataFrame) -> dict[str, set[str]]:
    """各赛季完整参赛队（含未开赛比赛），供 Elo 赛季切换使用。"""
    return {s: set(g["home"]) | set(g["away"]) for s, g in matches.groupby("season")}


def _elo_from_cfg(cfg: dict) -> SeasonElo:
    return SeasonElo(cfg["k"], cfg["home_field_advantage"], cfg["season_regression"], cfg["promoted_default"])


# ------------------------------------------------------------------ backtest
def window_start(d: pd.Timestamp) -> pd.Timestamp:
    """把比赛日归入"周二–周四"或"周五–周一"窗口，返回窗口起始日。"""
    wd = d.weekday()
    back = wd - 1 if wd in (1, 2, 3) else (wd - 4) % 7
    return (d - timedelta(days=back)).normalize()


def backtest(matches: pd.DataFrame, test_seasons: list[str], dc_cfg: dict, elo_cfg: dict) -> pd.DataFrame:
    fin = finished(matches).copy()
    if fin.empty:
        return pd.DataFrame()
    fin["ts"] = _to_ts(fin["match_date"]).to_numpy()
    elo_pre, _ = _elo_from_cfg(elo_cfg).run(fin, season_teams(matches))
    fin = fin.merge(elo_pre[["league", "season", "home", "away", "elo_h", "elo_d", "elo_a"]],
                    on=["league", "season", "home", "away"], how="left")
    mkt = implied_probs(fin[["odds_close_avg_h", "odds_close_avg_d", "odds_close_avg_a"]])
    fin[["mkt_h", "mkt_d", "mkt_a"]] = mkt
    test = fin[fin["season"].isin(test_seasons)].copy()
    test["window"] = test["ts"].map(window_start)
    rows, failures = [], 0
    for w, wm in test.groupby("window", sort=True):
        train = fin[(fin["ts"] < w) & (fin["ts"] >= w - timedelta(days=dc_cfg["train_days"]))]
        if len(train) < dc_cfg["min_train_matches"]:
            continue
        try:
            dc = DixonColes(dc_cfg["xi"]).fit(train, w)
        except Exception as e:  # noqa: BLE001
            log.warning("DC 拟合失败 %s: %s", w.date(), e)
            continue
        for r in wm.itertuples(index=False):
            p = dc.predict(r.home, r.away) or {}
            rows.append({
                "league": r.league, "season": r.season, "match_date": r.match_date, "home": r.home, "away": r.away,
                "hg": r.hg, "ag": r.ag, "outcome": int(outcome(r.hg, r.ag)),
                "dc_h": p.get("dc_h"), "dc_d": p.get("dc_d"), "dc_a": p.get("dc_a"),
                "elo_h": r.elo_h, "elo_d": r.elo_d, "elo_a": r.elo_a,
                "mkt_h": r.mkt_h, "mkt_d": r.mkt_d, "mkt_a": r.mkt_a,
                "window_start": w.date(), "n_train": len(train), "model_version": MODEL_VERSION,
            })
        failures += dc.failures
    out = pd.DataFrame(rows)
    for c in [f"{m}_{o}" for m in ("dc", "elo", "mkt") for o in OUTCOMES]:
        if c in out:
            out[c] = pd.to_numeric(out[c], errors="coerce").astype(float)
    out.attrs["dc_failures"] = failures
    return out


def evaluate(bt: pd.DataFrame, models=("dc", "elo", "mkt")) -> pd.DataFrame:
    """在"所有模型都有预测"的共同样本上比较，保证公平。RPS 越低越好。"""
    cols = [f"{m}_{o}" for m in models for o in OUTCOMES]
    s = bt.dropna(subset=cols)
    if s.empty:
        return pd.DataFrame()
    y = s["outcome"].to_numpy(int)
    onehot = np.eye(3)[y]
    rows = []
    names = {"dc": "Dixon-Coles", "elo": "Elo", "mkt": "市场收盘赔率"}
    for m in models:
        p = s[[f"{m}_{o}" for o in OUTCOMES]].to_numpy(float)
        p = p / p.sum(axis=1, keepdims=True)
        rows.append({
            "模型": names.get(m, m), "样本数": len(s),
            "RPS": float(pb.metrics.rps_array(p, y).mean()),
            "对数损失": float(-np.log(np.clip(p[np.arange(len(y)), y], 1e-12, 1)).mean()),
            "Brier": float(((p - onehot) ** 2).sum(axis=1).mean()),
            "命中率": float((p.argmax(axis=1) == y).mean()),
        })
    return pd.DataFrame(rows)


def calibration(bt: pd.DataFrame, model: str, n_bins: int = 10) -> pd.DataFrame:
    cols = [f"{model}_{o}" for o in OUTCOMES]
    s = bt.dropna(subset=cols)
    if s.empty:
        return pd.DataFrame()
    p = s[cols].to_numpy(float).ravel()
    hit = (np.eye(3)[s["outcome"].to_numpy(int)]).ravel()
    bins = np.clip((p * n_bins).astype(int), 0, n_bins - 1)
    df = pd.DataFrame({"bin": bins, "p": p, "hit": hit})
    g = df.groupby("bin").agg(预测概率=("p", "mean"), 实际频率=("hit", "mean"), 样本数=("p", "size"))
    return g.reset_index(drop=True)


# ------------------------------------------------------------------ upcoming
def predict_upcoming(matches: pd.DataFrame, fixtures: pd.DataFrame, dc_cfg: dict, elo_cfg: dict,
                     today, horizon_days: int) -> pd.DataFrame:
    today = pd.Timestamp(today).normalize()
    fin = finished(matches)
    dates = _to_ts(matches["match_date"]).to_numpy()
    upcoming = matches[(matches["status"] != "FINISHED") & (dates >= today)
                       & (dates <= today + timedelta(days=horizon_days))]
    if upcoming.empty or fin.empty:
        return pd.DataFrame()
    fin_ts = _to_ts(fin["match_date"]).to_numpy()
    train = fin[fin_ts >= today - timedelta(days=dc_cfg["train_days"])]
    dc = DixonColes(dc_cfg["xi"]).fit(train, today) if len(train) >= dc_cfg["min_train_matches"] else None
    elo = _elo_from_cfg(elo_cfg)
    teams = season_teams(matches)
    elo.run(fin, teams)
    cur = upcoming["season"].iloc[0]
    if cur not in set(fin["season"]):  # 新赛季尚无完赛比赛：先做赛季切换
        elo.start_season(teams.get(cur, set()) | set(upcoming["home"]) | set(upcoming["away"]))
    odds = pd.DataFrame()
    if fixtures is not None and not fixtures.empty:
        odds = fixtures[["league", "home", "away", "odds_avg_h", "odds_avg_d", "odds_avg_a"]].drop_duplicates(
            ["league", "home", "away"], keep="last")
    rows = []
    for r in upcoming.sort_values(["match_date", "kickoff", "home"]).itertuples(index=False):
        p = (dc.predict(r.home, r.away) if dc else None) or {}
        eh, ed, ea = elo.probs(r.home, r.away)
        rows.append({
            "league": r.league, "season": r.season, "match_date": r.match_date, "kickoff": r.kickoff,
            "home": r.home, "away": r.away, **p,
            "elo_h": eh, "elo_d": ed, "elo_a": ea,
            "elo_home": elo.elo.get_team_rating(r.home), "elo_away": elo.elo.get_team_rating(r.away),
            "model_version": MODEL_VERSION,
        })
    out = pd.DataFrame(rows)
    if not odds.empty:
        out = out.merge(odds, on=["league", "home", "away"], how="left")
        out[["mkt_h", "mkt_d", "mkt_a"]] = implied_probs(out[["odds_avg_h", "odds_avg_d", "odds_avg_a"]])
        out = out.drop(columns=["odds_avg_h", "odds_avg_d", "odds_avg_a"])
    for c in ("mkt_h", "mkt_d", "mkt_a", "dc_h", "dc_d", "dc_a", "dc_xg_h", "dc_xg_a", "dc_over25", "dc_btts"):
        if c not in out:
            out[c] = np.nan
        out[c] = pd.to_numeric(out[c], errors="coerce").astype(float)
    if "dc_top_score" not in out:
        out["dc_top_score"] = pd.NA
    return out
