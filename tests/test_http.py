"""robots.txt 语义（RFC 9309）与限速：用假 Session 离线测试。"""

import pytest

from fwb.http import PoliteClient, RobotsDisallowed


class FakeResp:
    def __init__(self, status, text="", headers=None):
        self.status_code, self.text, self.content = status, text, text.encode()
        self.headers = headers or {"content-type": "text/plain"}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeSession:
    def __init__(self, robots: dict[str, FakeResp]):
        self.robots, self.headers, self.calls = robots, {}, []

    def get(self, url, **kw):
        self.calls.append(url)
        if url.endswith("/robots.txt"):
            return self.robots[url.split("/robots.txt")[0]]
        return FakeResp(200, "ok")


def client(robots):
    c = PoliteClient(user_agent="football-workbench/1.0 (+test)")
    c.session = FakeSession(robots)
    return c


def test_robots_disallow_all_blocks():
    c = client({"https://understat.com": FakeResp(200, "User-agent: *\nDisallow: /\n")})
    with pytest.raises(RobotsDisallowed):
        c.check_robots("https://understat.com/league/EPL/2026")


def test_robots_404_means_allowed():
    c = client({"https://raw.githubusercontent.com": FakeResp(404, "404: Not Found")})
    c.check_robots("https://raw.githubusercontent.com/statsbomb/open-data/master/data/competitions.json")


def test_robots_5xx_is_conservative_but_not_permanent(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("fwb.http.time.monotonic", lambda: now[0])
    c = client({"http://api.clubelo.com": FakeResp(502)})
    with pytest.raises(RobotsDisallowed):
        c.check_robots("http://api.clubelo.com/2026-09-20")
    c.session.robots["http://api.clubelo.com"] = FakeResp(404)  # 对方恢复
    with pytest.raises(RobotsDisallowed):  # 10 分钟内仍用缓存结论
        c.check_robots("http://api.clubelo.com/2026-09-20")
    now[0] += 601
    c.check_robots("http://api.clubelo.com/2026-09-20")  # 过期后重新获取 → 允许


def test_ai_bot_rules_do_not_apply_to_our_agent():
    txt = "User-agent: *\nDisallow:\n\nUser-agent: ClaudeBot\nDisallow: /\n"
    c = client({"https://www.football-data.co.uk": FakeResp(200, txt)})
    c.check_robots("https://www.football-data.co.uk/mmz4281/2627/E0.csv")


def test_throttle_spaces_requests(monkeypatch):
    c = client({"https://x.test": FakeResp(404)})
    c.set_min_interval("x.test", 5.0)
    slept = []
    now = [100.0]
    monkeypatch.setattr("fwb.http.time.monotonic", lambda: now[0])
    monkeypatch.setattr("fwb.http.time.sleep", lambda s: slept.append(s))
    c.get("https://x.test/a")
    c.get("https://x.test/b")
    assert slept and abs(slept[0] - 5.0) < 1e-9
