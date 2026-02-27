from datetime import date

from usau_rankings.cli import ingest, run
from usau_rankings.rating_engine import Game
from usau_rankings.usau_ingest import IngestedGame


def test_cli_run_writes_ratings_csv(tmp_path):
    input_csv = tmp_path / "games.csv"
    output_csv = tmp_path / "ratings.csv"

    input_csv.write_text(
        "date,team_a,team_b,score_a,score_b\n"
        "2024-01-01,A,B,15,10\n"
        "2024-01-02,A,C,15,8\n"
        "2024-01-03,B,C,15,14\n",
        encoding="utf-8",
    )

    run(input_csv, date(2024, 1, 1), date(2024, 1, 31), output_csv)

    lines = output_csv.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0] == "team,rating,games_count"
    assert len(lines) == 4
    assert lines[1].startswith("A,")


def test_cli_ingest_writes_games_csv(monkeypatch, tmp_path):
    output_csv = tmp_path / "games.csv"

    monkeypatch.setattr(
        "usau_rankings.cli.fetch_games_with_metadata",
        lambda season_year, division: [
            IngestedGame(
                game=Game(date(2024, 4, 1), "A", "B", 15, 11),
                source_url="https://play.usaultimate.org/game/123",
            )
        ],
    )

    ingest(2024, "club-mens", output_csv)

    lines = output_csv.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0] == "date,team_a,team_b,score_a,score_b,source_url"
    assert lines[1] == "2024-04-01,A,B,15,11,https://play.usaultimate.org/game/123"
