"""Scrape USAU schedule pages from a single HTML fetch.

This module intentionally parses only the schedule page HTML and never follows
"match report" links. It supports three parser iterations selectable via
``--version``.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

EVENT_GAME_PATTERNS = [
    re.compile(r"EventGameId=(\d+)", flags=re.IGNORECASE),
    re.compile(r"[\"']EventGameId[\"']\s*[:=]\s*[\"']?(\d+)", flags=re.IGNORECASE),
]
STATUS_WORDS = (
    "final",
    "cancelled",
    "canceled",
    "forfeit",
    "in progress",
    "upcoming",
    "scheduled",
    "live",
    "postponed",
    "suspended",
)


class ScheduleScrapeError(RuntimeError):
    """Raised when schedule parsing fails."""


@dataclass
class ParseContext:
    soup: BeautifulSoup
    html: str
    debug: bool = False


class RequestCounterSession(requests.Session):
    """Session that tracks number of outgoing requests."""

    def __init__(self) -> None:
        super().__init__()
        self.request_count = 0

    def request(self, *args, **kwargs):  # type: ignore[override]
        self.request_count += 1
        return super().request(*args, **kwargs)


def _normalize_whitespace(value: str) -> str:
    return " ".join(value.split())


def fetch_schedule_html(url: str, timeout: int = 30) -> tuple[str, int]:
    """Fetch schedule page HTML once and return body + request count."""

    session = RequestCounterSession()
    response = session.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
    response.raise_for_status()
    return response.text, session.request_count


def extract_event_game_ids(html: str, debug: bool = False) -> tuple[list[str], Counter]:
    """Extract all EventGameId values from raw HTML using regex."""

    occurrences: list[str] = []
    for pattern in EVENT_GAME_PATTERNS:
        occurrences.extend(match.group(1) for match in pattern.finditer(html))

    if not occurrences:
        raise ScheduleScrapeError("No EventGameIds found in HTML via regex scan.")

    occurrence_counts = Counter(occurrences)
    unique_ids = sorted(occurrence_counts)

    if debug:
        print("[debug] EventGameId occurrences:")
        for game_id, count in sorted(occurrence_counts.items(), key=lambda item: int(item[0])):
            print(f"  EventGameId={game_id} x{count}")
        print(f"[debug] raw occurrences={len(occurrences)} unique={len(unique_ids)}")

    return unique_ids, occurrence_counts


def _extract_score_pair(text: str) -> tuple[int, int] | None:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text.strip()):
        return None
    if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{2,4}", text.strip()):
        return None
    match = re.search(r"\b(\d{1,2})\s*[-–]\s*(\d{1,2})\b", text)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _find_status(text: str) -> str | None:
    lowered = text.lower()
    for word in STATUS_WORDS:
        if word in lowered:
            return word.title()
    return None


def _is_probable_team_name(text: str) -> bool:
    if not text:
        return False
    if re.fullmatch(r"\d{1,2}", text):
        return False
    if re.search(r"\b(am|pm)\b", text.lower()):
        return False
    if re.search(r"\d{1,2}:\d{2}", text):
        return False
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return False
    if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{2,4}", text):
        return False
    if re.search(r"\b(field|final|cancelled|canceled|forfeit|bracket|pool)\b", text.lower()):
        return False
    if re.search(r"\b(match\s+report|box\s+score|preview)\b", text.lower()):
        return False
    return len(text) >= 2


def _normalize_team_candidates(teams: list[str]) -> list[str]:
    normalized: list[str] = []
    for team in teams:
        value = _normalize_whitespace(team)
        split = re.split(r"\s+vs\.?\s+", value, flags=re.IGNORECASE)
        if len(split) == 2 and all(part.strip() for part in split):
            normalized.extend(_normalize_whitespace(part) for part in split)
            continue
        normalized.append(value)

    unique: list[str] = []
    seen: set[str] = set()
    for team in normalized:
        key = team.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(team)
    return unique


def _extract_datetime_field(strings: list[str]) -> tuple[str | None, str | None, str | None]:
    date_value = None
    time_value = None
    field_value = None
    date_patterns = [
        re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
        re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"),
        re.compile(r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),?\s+[A-Za-z]{3,9}\s+\d{1,2}\b", re.IGNORECASE),
    ]

    for text in strings:
        if date_value is None:
            for pattern in date_patterns:
                hit = pattern.search(text)
                if hit:
                    date_value = hit.group(0)
                    break
        if time_value is None:
            hit = re.search(r"\b\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)?\b", text)
            if hit:
                time_value = hit.group(0)
        if field_value is None and "field" in text.lower():
            field_value = text

    return date_value, time_value, field_value


def _candidate_containers_for_id(context: ParseContext, event_game_id: str) -> list[Tag]:
    soup = context.soup
    candidates: list[Tag] = []
    selectors = [
        f'a[href*="EventGameId={event_game_id}"]',
        f'[data-eventgameid="{event_game_id}"]',
        f'[data-event-game-id="{event_game_id}"]',
        f'[data-game-id="{event_game_id}"]',
        f'[id*="{event_game_id}"]',
    ]
    seen: set[int] = set()

    for selector in selectors:
        for node in soup.select(selector):
            chain = [node]
            chain.extend(list(node.parents)[:6])
            for parent in chain:
                if not isinstance(parent, Tag):
                    continue
                if parent.name in {"body", "html", "[document]"}:
                    continue
                node_id = id(parent)
                if node_id in seen:
                    continue
                seen.add(node_id)
                candidates.append(parent)

    text_pattern = re.compile(rf"EventGameId\s*=\s*{re.escape(event_game_id)}", re.IGNORECASE)
    for text_node in soup.find_all(string=text_pattern):
        if not isinstance(text_node, NavigableString):
            continue
        parent = text_node.parent
        if isinstance(parent, Tag):
            chain = [parent]
            chain.extend(list(parent.parents)[:6])
            for ancestor in chain:
                if isinstance(ancestor, Tag) and id(ancestor) not in seen:
                    if ancestor.name in {"body", "html", "[document]"}:
                        continue
                    seen.add(id(ancestor))
                    candidates.append(ancestor)

    return candidates


def _score_container(container: Tag) -> tuple[int, dict[str, object]]:
    strings = [_normalize_whitespace(s) for s in container.stripped_strings if s.strip()]
    text_blob = " | ".join(strings)
    teams = [s for s in strings if _is_probable_team_name(s)]

    score_pair = None
    for chunk in strings:
        score_pair = _extract_score_pair(chunk)
        if score_pair:
            break

    status = _find_status(text_blob)
    date_value, time_value, field_value = _extract_datetime_field(strings)

    score = 0
    if len(teams) >= 2:
        score += 3
    if score_pair is not None:
        score += 2
    if status is not None:
        score += 1
    if date_value is not None:
        score += 1
    if time_value is not None:
        score += 1

    return score, {
        "teams": teams,
        "score_pair": score_pair,
        "status": status,
        "date": date_value,
        "time": time_value,
        "field": field_value,
        "text_blob": text_blob,
    }


def parse_game_v1(context: ParseContext, event_game_id: str) -> dict[str, object]:
    candidates = _candidate_containers_for_id(context, event_game_id)
    if not candidates:
        raise ScheduleScrapeError(f"EventGameId={event_game_id}: unable to identify a container node.")

    best_payload = None
    best_score = -1
    best_node_name = ""
    for candidate in candidates:
        score, payload = _score_container(candidate)
        if score > best_score:
            best_score = score
            best_payload = payload
            best_node_name = candidate.name

    if best_payload is None:
        raise ScheduleScrapeError(f"EventGameId={event_game_id}: no parseable candidate container.")

    teams = _normalize_team_candidates(best_payload["teams"])
    status = best_payload["status"]
    score_pair = best_payload["score_pair"]

    if len(teams) < 2:
        raise ScheduleScrapeError(f"EventGameId={event_game_id}: team names could not be found in chosen container.")

    if score_pair is None and status is None:
        raise ScheduleScrapeError(
            f"EventGameId={event_game_id}: neither scores nor status were parseable in chosen container."
        )

    score1 = score_pair[0] if score_pair else None
    score2 = score_pair[1] if score_pair else None

    if context.debug:
        print(f"[debug] v1 EventGameId={event_game_id} container=<{best_node_name}> score={best_score}")

    return {
        "EventGameId": event_game_id,
        "team1": teams[0],
        "team2": teams[1],
        "score1": score1,
        "score2": score2,
        "date": best_payload["date"],
        "time": best_payload["time"],
        "field": best_payload["field"],
        "status": status,
        "section": None,
    }


def parse_game_v2(context: ParseContext, event_game_id: str) -> dict[str, object]:
    candidates = _candidate_containers_for_id(context, event_game_id)
    if not candidates:
        raise ScheduleScrapeError(f"EventGameId={event_game_id}: no DOM candidates found referencing this id.")

    scored: list[tuple[int, Tag, dict[str, object]]] = []
    for candidate in candidates:
        score, payload = _score_container(candidate)
        if payload["teams"]:
            score += min(3, len(payload["teams"]))
        if payload["field"]:
            score += 1
        scored.append((score, candidate, payload))

    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best_node, best_payload = scored[0]

    if best_score < 4:
        preview = _normalize_whitespace(best_payload["text_blob"][:240])
        raise ScheduleScrapeError(
            f"EventGameId={event_game_id}: best candidate score too low ({best_score}); preview='{preview}'"
        )

    teams = _normalize_team_candidates([t for t in best_payload["teams"] if _is_probable_team_name(t)])
    if len(teams) < 2:
        raise ScheduleScrapeError(
            f"EventGameId={event_game_id}: expected at least 2 team names, found {len(teams)} in <{best_node.name}>."
        )

    status = best_payload["status"]
    score_pair = best_payload["score_pair"]
    if score_pair is None and status is None:
        raise ScheduleScrapeError(
            f"EventGameId={event_game_id}: missing both score pair and game status in <{best_node.name}>."
        )

    section = None
    for parent in best_node.parents:
        if not isinstance(parent, Tag):
            continue
        data_label = parent.get("data-tab") or parent.get("data-title")
        if isinstance(data_label, str) and data_label.strip():
            section = _normalize_whitespace(data_label)
            break

    if context.debug:
        print(f"[debug] v2 EventGameId={event_game_id} container=<{best_node.name}> score={best_score}")

    return {
        "EventGameId": event_game_id,
        "team1": teams[0],
        "team2": teams[1],
        "score1": score_pair[0] if score_pair else None,
        "score2": score_pair[1] if score_pair else None,
        "date": best_payload["date"],
        "time": best_payload["time"],
        "field": best_payload["field"],
        "status": status,
        "section": section,
    }


def parse_game_v3(context: ParseContext, event_game_id: str) -> dict[str, object]:
    """Production-oriented parser with deterministic validation."""

    game = parse_game_v2(context, event_game_id)

    for key in ("team1", "team2"):
        value = game.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ScheduleScrapeError(f"EventGameId={event_game_id}: invalid {key} extracted ({value!r}).")
        game[key] = _normalize_whitespace(value)

    status = game.get("status")
    score1 = game.get("score1")
    score2 = game.get("score2")
    if score1 is None or score2 is None:
        if not isinstance(status, str):
            raise ScheduleScrapeError(
                f"EventGameId={event_game_id}: missing scores without a status indicating non-played game."
            )
    else:
        if not isinstance(score1, int) or not isinstance(score2, int):
            raise ScheduleScrapeError(f"EventGameId={event_game_id}: parsed scores are not integers.")

    return game


def parse_games(context: ParseContext, event_game_ids: Iterable[str], version: int) -> list[dict[str, object]]:
    parser = {1: parse_game_v1, 2: parse_game_v2, 3: parse_game_v3}[version]
    parsed = []
    for event_game_id in event_game_ids:
        parsed.append(parser(context, event_game_id))
    return parsed


def write_outputs(rows: list[dict[str, object]], json_path: Path, csv_path: Path) -> None:
    json_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")

    fieldnames = [
        "EventGameId",
        "team1",
        "team2",
        "score1",
        "score2",
        "date",
        "time",
        "field",
        "status",
        "section",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def build_summary(rows: list[dict[str, object]], duplicates: Counter) -> dict[str, object]:
    missing_scores = [
        row
        for row in rows
        if row.get("score1") is None or row.get("score2") is None
    ]
    duplicate_ids = {game_id: count for game_id, count in duplicates.items() if count > 1}

    return {
        "total_unique_event_game_ids": len(rows),
        "total_parsed_games": len(rows),
        "parsing_errors": 0,
        "games_missing_scores": len(missing_scores),
        "duplicate_event_game_ids": duplicate_ids,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape USAU schedule HTML in a single request.")
    parser.add_argument("url", help="USAU schedule URL")
    parser.add_argument("--expect-count", type=int, default=None, help="Assert unique EventGameId count")
    parser.add_argument("--version", type=int, default=3, choices=[1, 2, 3], help="Parser iteration")
    parser.add_argument("--json-out", default="schedule_games.json", help="JSON output path")
    parser.add_argument("--csv-out", default="schedule_games.csv", help="CSV output path")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    html, request_count = fetch_schedule_html(args.url)
    if request_count != 1:
        raise ScheduleScrapeError(f"Expected exactly one HTTP request, observed {request_count}.")

    event_game_ids, occurrence_counts = extract_event_game_ids(html, debug=args.debug)
    if args.expect_count is not None and len(event_game_ids) != args.expect_count:
        raise ScheduleScrapeError(
            f"Expected {args.expect_count} unique EventGameIds but found {len(event_game_ids)}."
        )

    context = ParseContext(soup=BeautifulSoup(html, "html.parser"), html=html, debug=args.debug)
    rows = parse_games(context, event_game_ids, version=args.version)

    write_outputs(rows, Path(args.json_out), Path(args.csv_out))

    summary = build_summary(rows, occurrence_counts)
    print(json.dumps(summary, indent=2))

    if args.debug:
        print("[debug] first extracted games:")
        for row in rows[:5]:
            print(json.dumps(row, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
