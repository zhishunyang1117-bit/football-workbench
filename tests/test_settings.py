from datetime import date

from fwb.settings import Season, all_seasons, current_season


def test_season_codes():
    s = Season("2627")
    assert (s.start_year, s.label, s.openfootball, s.fdorg) == (2026, "2026/27", "2026-27", 2026)
    assert Season.from_start_year(1999).code == "9900"


def test_settings_structure():
    from fwb.settings import load_settings

    s = load_settings()
    assert {"leagues", "seasons", "update", "sources", "models", "http", "app"} <= set(s)
    assert set(s["sources"]) == {"football_data_uk", "openfootball", "football_data_org", "statsbomb_open_data"}
    assert all("timezone" in lg and "openfootball" in lg for lg in s["leagues"].values())


def test_auto_season_rollover():
    assert current_season(date(2026, 9, 26)) == "2627"
    assert current_season(date(2027, 6, 30)) == "2627"
    assert current_season(date(2027, 7, 1)) == "2728"
    assert all_seasons(date(2026, 9, 26)) == ["2324", "2425", "2526", "2627"]
