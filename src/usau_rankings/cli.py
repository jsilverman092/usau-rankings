"""CLI for USAU rankings solver."""

from __future__ import annotations

import argparse
import csv
from datetime import date
from pathlib import Path

from .rating_engine import Game, solve_ratings
from .usau_ingest import fetch_games_with_metadata


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


def ingest(season_year: int, division: str, out_path: Path) -> None:
    ingested_games = fetch_games_with_metadata(season_year, division)
    include_source_url = any(game.source_url for game in ingested_games)

    columns = ["date", "team_a", "team_b", "score_a", "score_b"]
    if include_source_url:
        columns.append("source_url")

    with out_path.open("w", encoding="utf-8", newline="") as outfile:
        writer = csv.DictWriter(outfile, fieldnames=columns)
        writer.writeheader()
        for ingested in ingested_games:
            row = {
                "date": ingested.game.date.isoformat(),
                "team_a": ingested.game.team_a,
                "team_b": ingested.game.team_b,
                "score_a": ingested.game.score_a,
                "score_b": ingested.game.score_b,
            }
            if include_source_url:
                row["source_url"] = ingested.source_url or ""
            writer.writerow(row)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m usau_rankings.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run the rankings solver")
    run_parser.add_argument("--input", required=True, type=Path)
    run_parser.add_argument("--season-start", required=True, type=_parse_date)
    run_parser.add_argument("--season-end", required=True, type=_parse_date)
    run_parser.add_argument("--out", required=True, type=Path)

    ingest_parser = subparsers.add_parser("ingest", help="Ingest USAU games and write CSV")
    ingest_parser.add_argument("--season-year", required=True, type=int)
    ingest_parser.add_argument("--division", required=True)
    ingest_parser.add_argument("--out", required=True, type=Path)

    args = parser.parse_args()

    if args.command == "run":
        run(args.input, args.season_start, args.season_end, args.out)
    elif args.command == "ingest":
        ingest(args.season_year, args.division, args.out)


if __name__ == "__main__":
    main()
