import pytest

from fwb.teams import normalize, resolve

EPL = {"Arsenal", "Man United", "Man City", "Wolves", "Nott'm Forest", "Newcastle", "Brighton", "West Ham",
       "Bournemouth", "Tottenham", "Leeds", "Hull", "Coventry"}
LIGA = {"Ath Bilbao", "Ath Madrid", "Real Madrid", "Sociedad", "Betis", "Vallecano", "Espanol", "Celta", "Alaves"}
BUNDES = {"Bayern Munich", "Leverkusen", "Dortmund", "M'gladbach", "Ein Frankfurt", "Stuttgart", "FC Koln",
          "Mainz", "Hoffenheim", "St Pauli", "Hamburg", "Werder Bremen"}
SERIE_A = {"Inter", "Milan", "Verona", "Fiorentina", "Atalanta", "Genoa", "Como"}
LIGUE_1 = {"Paris SG", "Paris FC", "Marseille", "Lyon", "Rennes", "Brest", "Strasbourg", "Le Havre", "Lille"}


@pytest.mark.parametrize("raw,cands,expected", [
    ("Manchester United FC", EPL, "Man United"),
    ("Manchester City FC", EPL, "Man City"),
    ("Wolverhampton Wanderers FC", EPL, "Wolves"),
    ("Nottingham Forest FC", EPL, "Nott'm Forest"),
    ("Newcastle United FC", EPL, "Newcastle"),
    ("Brighton & Hove Albion FC", EPL, "Brighton"),
    ("AFC Bournemouth", EPL, "Bournemouth"),
    ("Hull City AFC", EPL, "Hull"),
    ("Athletic Club", LIGA, "Ath Bilbao"),
    ("Club Atlético de Madrid", LIGA, "Ath Madrid"),
    ("Real Madrid CF", LIGA, "Real Madrid"),
    ("Real Sociedad de Fútbol", LIGA, "Sociedad"),
    ("Real Betis Balompié", LIGA, "Betis"),
    ("Rayo Vallecano de Madrid", LIGA, "Vallecano"),
    ("RCD Espanyol de Barcelona", LIGA, "Espanol"),
    ("RC Celta de Vigo", LIGA, "Celta"),
    ("Deportivo Alavés", LIGA, "Alaves"),
    ("FC Bayern München", BUNDES, "Bayern Munich"),
    ("Bayer 04 Leverkusen", BUNDES, "Leverkusen"),
    ("Borussia Mönchengladbach", BUNDES, "M'gladbach"),
    ("Eintracht Frankfurt", BUNDES, "Ein Frankfurt"),
    ("VfB Stuttgart", BUNDES, "Stuttgart"),
    ("1. FC Köln", BUNDES, "FC Koln"),
    ("1. FSV Mainz 05", BUNDES, "Mainz"),
    ("TSG 1899 Hoffenheim", BUNDES, "Hoffenheim"),
    ("FC St. Pauli", BUNDES, "St Pauli"),
    ("Hamburger SV", BUNDES, "Hamburg"),
    ("SV Werder Bremen", BUNDES, "Werder Bremen"),
    ("FC Internazionale Milano", SERIE_A, "Inter"),
    ("AC Milan", SERIE_A, "Milan"),
    ("Hellas Verona FC", SERIE_A, "Verona"),
    ("ACF Fiorentina", SERIE_A, "Fiorentina"),
    ("Como 1907", SERIE_A, "Como"),
    ("Paris Saint-Germain FC", LIGUE_1, "Paris SG"),
    ("Paris FC", LIGUE_1, "Paris FC"),
    ("Olympique de Marseille", LIGUE_1, "Marseille"),
    ("Olympique Lyonnais", LIGUE_1, "Lyon"),
    ("Stade Rennais FC 1901", LIGUE_1, "Rennes"),
    ("RC Strasbourg Alsace", LIGUE_1, "Strasbourg"),
    ("Le Havre AC", LIGUE_1, "Le Havre"),
    ("Lille OSC", LIGUE_1, "Lille"),
])
def test_resolve(raw, cands, expected):
    assert resolve(raw, cands) == expected


def test_unknown_team_is_not_guessed():
    assert resolve("Totally Unknown United", EPL) is None


def test_scoped_to_candidates():
    # 同名映射只在候选集合内生效：别名指向的规范名不在集合里时不能返回它
    assert resolve("Manchester United FC", {"Arsenal", "Chelsea"}) is None


def test_normalize_strips_accents_and_noise():
    assert normalize("1. FC Köln") == "koln"
    assert normalize("Brighton & Hove Albion FC") == "brighton"
