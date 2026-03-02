from __future__ import annotations

import argparse
import random
import time
from pathlib import Path

import pandas as pd

from usau_rankings.schedule_scraper import main as scrape_main


EXCLUDE_EVENT_NAMES = {
    "The New York Minute 2025",
    "Cutthroat Winter Round Robin",
    # add more exact matches here
}


def apply_range(items: list[dict], range_arg: str | None) -> list[dict]:
    """
    Supports:
      1-5
      3+
      4
    """
    if not range_arg:
        return items

    if range_arg.endswith("+"):
        start = int(range_arg[:-1])
        return items[start - 1 :]

    if "-" in range_arg:
        a, b = range_arg.split("-", 1)
        start = int(a)
        end = int(b)
        return items[start - 1 : end]

    idx = int(range_arg)
    return items[idx - 1 : idx]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m usau_rankings.batch_scrape_schedules",
        description="Batch scrape schedule pages for filtered USAU events.",
    )

    p.add_argument("--division", required=True)
    p.add_argument("--gender", required=True)
    p.add_argument("--year", type=int, required=True)

    p.add_argument(
        "--include-series",
        action="store_true",
        help="Include series events (default excludes them).",
    )

    p.add_argument(
        "--range",
        help="Event range (e.g. 1-5, 3+, 2). Applies AFTER filters/exclusions.",
    )

    p.add_argument(
        "--append-to",
        required=True,
        help="JSON file to upsert games into (keyed by event_game_id).",
    )

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    df = pd.read_csv("data/events_normalized.csv")

    # Base filters
    df = df[
        (df["division"] == args.division)
        & (df["gender"] == args.gender)
        & (df["listing_year"] == args.year)
        & (df["cancelled"] == False)
    ].copy()

    # Default: exclude series unless explicitly included
    if not args.include_series:
        df = df[df["series_event"] == False]

    # Always-on exclusions (exact match)
    df = df[~df["event_name"].isin(EXCLUDE_EVENT_NAMES)]

    # Need schedule_url to scrape
    df = df.dropna(subset=["schedule_url"]).copy()

    # Deterministic ordering + deterministic dedupe
    # 1) Pre-sort so drop_duplicates(keep="first") is stable across runs
    df = df.sort_values(
        ["schedule_url", "event_url", "competition_group"],
        kind="mergesort",
    )
    df = df.drop_duplicates(subset=["schedule_url"], keep="first")

    # 2) Final presentation order (stable)
    df = df.sort_values(["event_name", "schedule_url"], kind="mergesort")

    records = df[["event_name", "schedule_url"]].to_dict("records")
    records = apply_range(records, args.range)

    out_path = Path(args.append_to)

    print(
        f"\n[batch_scrape] events={len(records)} "
        f"division={args.division} gender={args.gender} year={args.year} "
        f"include_series={args.include_series} output={out_path}\n"
    )

    for i, row in enumerate(records, 1):
        name = row["event_name"]
        url = row["schedule_url"]

        print(f"===== [{i}/{len(records)}] {name} =====")
        print(url)

        # Reuse the existing scraper CLI entrypoint programmatically
        scrape_main(
            [
                "--event-url",
                url,
                "--append-to",
                str(out_path),
            ]
        )

        # Extra politeness between tournaments (in addition to per-match jitter)
        time.sleep(random.uniform(1.0, 3.0))

    print("\n[batch_scrape] complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
