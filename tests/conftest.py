import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def synthetic_league():
    """两个赛季、10 支球队的双循环，比分由已知攻防强度的泊松过程生成（可复现）。"""
    rng = np.random.default_rng(42)
    teams = [f"T{i}" for i in range(10)]
    attack = dict(zip(teams, np.linspace(0.6, 1.6, 10)))
    defence = dict(zip(teams, np.linspace(1.4, 0.7, 10)))
    rows = []
    for season, start in (("2425", "2024-08-10"), ("2526", "2025-08-09")):
        day = pd.Timestamp(start)
        fixtures = [(h, a) for h in teams for a in teams if h != a]
        rng.shuffle(fixtures)
        for i, (h, a) in enumerate(fixtures):
            d = day + pd.Timedelta(days=7 * (i // 5) + (i % 2))
            hg = rng.poisson(1.35 * attack[h] * defence[a] * 1.15)
            ag = rng.poisson(1.35 * attack[a] * defence[h] * 0.85)
            rows.append({"league": "E0", "season": season, "match_date": d.date(), "kickoff": "15:00",
                         "home": h, "away": a, "hg": hg, "ag": ag, "status": "FINISHED",
                         "odds_close_avg_h": 2.4, "odds_close_avg_d": 3.3, "odds_close_avg_a": 3.1})
    df = pd.DataFrame(rows)
    df["hg"] = df["hg"].astype("Int64")
    df["ag"] = df["ag"].astype("Int64")
    return df
