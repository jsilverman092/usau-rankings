from datetime import date

from usau_rankings.cli import run


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
