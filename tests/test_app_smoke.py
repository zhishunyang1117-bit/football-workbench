"""应用冒烟测试：用 Streamlit AppTest 在无浏览器环境下逐页执行脚本，断言无异常。

依赖仓库中已提交的 data/（CI 与 Streamlit Cloud 使用同一份数据）；没有数据时跳过。
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PAGES = ["app_pages/live.py", "app_pages/league.py", "app_pages/team.py", "app_pages/player.py", "app_pages/predict.py",
         "app_pages/status.py"]

pytestmark = pytest.mark.skipif(not (ROOT / "data" / "manifest.json").exists(), reason="尚无数据")


@pytest.mark.parametrize("page", PAGES)
def test_page_runs_without_exception(page, monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.chdir(ROOT)
    at = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=180)
    at.run()
    assert not at.exception, at.exception
    at.switch_page(page)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
