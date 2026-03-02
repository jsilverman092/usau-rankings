from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from usau_rankings.match_report_parser import MatchReportParseError, parse_match_report_html


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


@dataclass(frozen=True)
class FetchConfig:
    min_delay_s: float = 2.0
    max_delay_s: float = 5.0
    timeout_s: float = 30.0
    max_retries: int = 6


# -------------------------
# JSON UPSERT
# -------------------------


def upsert_game(path: Path, row: dict) -> None:
    games: list[dict] = []
    if path.exists():
        try:
            games = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(games, list):
                games = []
        except Exception:
            games = []

    key = row.get("event_game_id")
    replaced = False

    if key:
        for i, g in enumerate(games):
            if g.get("event_game_id") == key:
                games[i] = row
                replaced = True
                break

    if not replaced:
        games.append(row)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(games, indent=2, default=str), encoding="utf-8")


# -------------------------
# RANGE SUPPORT
# -------------------------


def apply_range(items: List[str], r: str | None) -> List[str]:
    if not r:
        return items

    m = re.fullmatch(r"(\d+)-(\d+)", r)
    if m:
        return items[int(m.group(1)) - 1 : int(m.group(2))]

    m = re.fullmatch(r"(\d+)\+", r)
    if m:
        return items[int(m.group(1)) - 1 :]

    if r.isdigit():
        return [items[int(r) - 1]]

    raise ValueError("Invalid range format (examples: 1-50, 40+, 12)")


# -------------------------
# NETWORK
# -------------------------


def sleep_jitter(cfg: FetchConfig) -> None:
    time.sleep(random.uniform(cfg.min_delay_s, cfg.max_delay_s))


def fetch(session: requests.Session, url: str, referer: str | None, cfg: FetchConfig) -> str:
    for attempt in range(cfg.max_retries):
        try:
            headers = {"Referer": referer} if referer else {}
            resp = session.get(url, headers=headers, timeout=cfg.timeout_s)

            if resp.status_code in (429, 500, 502, 503, 504):
                time.sleep(2**attempt)
                continue

            resp.raise_for_status()
            resp.encoding = resp.encoding or "utf-8"
            return resp.text

        except requests.RequestException:
            time.sleep(2**attempt)

    raise RuntimeError(f"Failed: {url}")


# -------------------------
# SCHEDULE EXTRACTION
# -------------------------


def extract_match_urls(schedule_html: str, base_url: str) -> List[str]:
    soup = BeautifulSoup(schedule_html, "html.parser")
    urls: list[str] = []

    for a in soup.find_all("a", href=True):
        if "match_report" in a["href"]:
            full = urljoin(base_url, a["href"])
            urls.append(full)

    # dedupe while preserving order
    seen: set[str] = set()
    ordered: list[str] = []
    for u in urls:
        if u not in seen:
            ordered.append(u)
            seen.add(u)

    return ordered


# -------------------------
# CLI
# -------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m usau_rankings.schedule_scraper",
        description="Parse USAU match reports (offline from saved HTML or online from a schedule page).",
    )

    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--html-file", help="Offline: path to a saved match report HTML file.")
    src.add_argument("--url", help="Online: a single match report URL to fetch and parse.")
    src.add_argument(
        "--event-url",
        help="Online: a schedule page URL; scraper extracts match report links and fetches them.",
    )

    # Only required for --url / --event-url (enforced in main())
    p.add_argument("--append-to", help="Path to JSON file (list) to upsert games into by event_game_id.")
    p.add_argument("--range", help="Optional range for --event-url: 1-50, 25-60, 40+, 12")

    return p


def _call_parser(html: str, *, source: str) -> dict:
    """
    Helper so tests can monkeypatch parse_match_report_html with a simpler signature.
    Prefer calling with source=..., but gracefully fallback if stub doesn't accept it.
    """
    try:
        return parse_match_report_html(html, source=source)
    except TypeError:
        # In tests / monkeypatch scenarios, a stub may not accept `source`.
        return parse_match_report_html(html)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # ---- Offline mode: parse file, print JSON ----
    if args.html_file:
        html_path = Path(args.html_file)
        if not html_path.exists():
            print(f"ERROR: file not found: {html_path}", file=sys.stderr)
            return 2

        html = html_path.read_text(encoding="utf-8", errors="replace")
        row = _call_parser(html, source=str(html_path))
        print(json.dumps(row, default=str))
        return 0

    # ---- Online modes require append-to ----
    if not args.append_to:
        print("ERROR: --append-to is required for --url / --event-url", file=sys.stderr)
        return 2

    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    cfg = FetchConfig()
    out = Path(args.append_to)

    # ---- Single match report URL ----
    if args.url:
        sleep_jitter(cfg)
        html = fetch(session, args.url, None, cfg)
        try:
            row = _call_parser(html, source=args.url)
        except MatchReportParseError as e:
            print(f"[skip] parse error for {args.url}: {e}", file=sys.stderr)
            return 0
        upsert_game(out, row)
        return 0

    # ---- Whole event from schedule page ----
    if args.event_url:
        schedule_html = fetch(session, args.event_url, None, cfg)
        urls = extract_match_urls(schedule_html, args.event_url)
        urls = apply_range(urls, args.range)

        for i, url in enumerate(urls, 1):
            print(f"[{i}/{len(urls)}] {url}")
            sleep_jitter(cfg)
            html = fetch(session, url, args.event_url, cfg)
            try:
                row = _call_parser(html, source=url)
            except MatchReportParseError as e:
                print(f"[skip] parse error for {url}: {e}", file=sys.stderr)
                continue
            upsert_game(out, row)

        return 0

    print("ERROR: unreachable (no mode selected)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
