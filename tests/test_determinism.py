"""确定性回归测试：相同输入在不同进程（不同 PYTHONHASHSEED）下必须产出完全相同的衍生数据，
否则每次定时运行都会重写文件、产生无意义的 git 提交。"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SCRIPT = r"""
import sys
sys.path.insert(0, sys.argv[1])
import pandas as pd
from fwb.analytics.models import SeasonElo
from fwb.storage import content_hash
rows = []
teams = {"2425": list("ABCDEFGH"), "2526": list("ABCDEIJK")}  # 3 队降级、3 队升级
for s, ts in teams.items():
    for i, h in enumerate(ts):
        for j, a in enumerate(ts):
            if h != a:
                rows.append({"league": "E0", "season": s, "match_date": f"20{s[:2]}-09-{1 + (i + j) % 28:02d}",
                             "home": h, "away": a, "hg": (i * 7 + j) % 4, "ag": (j * 5 + i) % 3})
pre, hist = SeasonElo().run(pd.DataFrame(rows))
print(content_hash(hist))
"""


def test_elo_is_hashseed_independent():
    outs = set()
    for seed in ("1", "2", "3"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        r = subprocess.run([sys.executable, "-c", SCRIPT, str(ROOT)], capture_output=True, text=True, env=env,
                           check=True)
        outs.add(r.stdout.strip())
    assert len(outs) == 1, outs
