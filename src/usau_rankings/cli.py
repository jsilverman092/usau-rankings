"""CLI for USAU rankings solver."""

from __future__ import annotations

import argparse
import csv
from datetime import date
from pathlib import Path

from .rating_engine import Game, solve_ratings


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def run(input_path: Path, season_start: date, season_end: date, out_path: Path) -> None:
    games: list[Game] = []
    games_count: dict[str, int] = {}

    with input_path.open("r", encoding="utf-8", newline="") as infile:
        reader = csv.DictReader(infile)
        required = {"date", "team_a", "team_b", "score_a", "score_b"}
        if reader.fieldnames is None or not required.issubset(set(reader.fieldnames)):
            missing = sorted(required - set(reader.fieldnames or []))
            raise ValueError(f"Input CSV missing required columns: {', '.join(missing)}")

        for row in reader:
            game = Game(
                date=_parse_date(row["date"]),
                team_a=row["team_a"],
                team_b=row["team_b"],
                score_a=int(row["score_a"]),
                score_b=int(row["score_b"]),
            )
            games.append(game)
            games_count[game.team_a] = games_count.get(game.team_a, 0) + 1
            games_count[game.team_b] = games_count.get(game.team_b, 0) + 1

    ratings = solve_ratings(games, season_start, season_end)

    with out_path.open("w", encoding="utf-8", newline="") as outfile:
        writer = csv.writer(outfile)
        writer.writerow(["team", "rating", "games_count"])
        for team in sorted(ratings):
            writer.writerow([team, f"{ratings[team]:.6f}", games_count.get(team, 0)])


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m usau_rankings.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run the rankings solver")
    run_parser.add_argument("--input", required=True, type=Path)
    run_parser.add_argument("--season-start", required=True, type=_parse_date)
    run_parser.add_argument("--season-end", required=True, type=_parse_date)
    run_parser.add_argument("--out", required=True, type=Path)

    args = parser.parse_args()

    if args.command == "run":
        run(args.input, args.season_start, args.season_end, args.out)


if __name__ == "__main__":
    main()
