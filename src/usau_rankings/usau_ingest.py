"""Data ingestion helpers for USAU game results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from .rating_engine import Game

try:
    import requests
except ImportError:  # pragma: no cover - environment dependent
    requests = None

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover - environment dependent
    BeautifulSoup = None


API_GAMES_URL = "https://play.usaultimate.org/api/v1/games/"
HTML_RESULTS_URL = "https://play.usaultimate.org/events/results/"


@dataclass(frozen=True)
class IngestedGame:
    """Container for a parsed game and optional source URL."""

    game: Game
    source_url: str | None = None


def _extract_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("results", "games", "data", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _team_name(raw: Any) -> str | None:
    if isinstance(raw, str):
        return raw.strip() or None
    if isinstance(raw, dict):
        for key in ("name", "team_name", "display_name"):
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _parse_date(raw: Any) -> date | None:
    if not isinstance(raw, str) or not raw:
        return None
    value = raw.strip()
    if "T" in value:
        value = value.split("T", 1)[0]
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _parse_score(raw: Any) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    return None


def _parse_item(item: dict[str, Any]) -> IngestedGame | None:
    game_date = _parse_date(item.get("date") or item.get("game_date") or item.get("start_date"))

    team_a = _team_name(item.get("team_a") or item.get("team1") or item.get("home_team"))
    team_b = _team_name(item.get("team_b") or item.get("team2") or item.get("away_team"))

    score_a = _parse_score(item.get("score_a") or item.get("team_a_score") or item.get("score1"))
    score_b = _parse_score(item.get("score_b") or item.get("team_b_score") or item.get("score2"))

    if game_date is None or not team_a or not team_b or score_a is None or score_b is None:
        return None
    if score_a == score_b:
        return None

    source_url = item.get("source_url") or item.get("url")
    if isinstance(source_url, str):
        source_url = source_url.strip() or None
    else:
        source_url = None

    return IngestedGame(
        game=Game(game_date, team_a, team_b, score_a, score_b),
        source_url=source_url,
    )


def fetch_games_with_metadata(season_year: int, division: str) -> list[IngestedGame]:
    """Fetch games for a season and division from USAU endpoints."""

    if requests is None:  # pragma: no cover - environment dependent
        raise RuntimeError("requests must be installed to fetch USAU games")

    params = {"season": season_year, "division": division}
    response = requests.get(API_GAMES_URL, params=params, timeout=30)
    response.raise_for_status()

    parsed: list[IngestedGame] = []
    try:
        payload = response.json()
    except ValueError:
        payload = None

    for item in _extract_items(payload):
        game = _parse_item(item)
        if game is not None:
            parsed.append(game)

    if parsed:
        parsed.sort(key=lambda value: value.game.date)
        return parsed

    if BeautifulSoup is None:
        return parsed

    html_response = requests.get(HTML_RESULTS_URL, params=params, timeout=30)
    html_response.raise_for_status()
    soup = BeautifulSoup(html_response.text, "html.parser")
    for tag in soup.select("table tbody tr"):
        cells = [cell.get_text(strip=True) for cell in tag.select("td")]
        if len(cells) < 5:
            continue
        game = _parse_item(
            {
                "date": cells[0],
                "team_a": cells[1],
                "score_a": cells[2],
                "score_b": cells[3],
                "team_b": cells[4],
            }
        )
        if game is not None:
            parsed.append(game)

    parsed.sort(key=lambda value: value.game.date)
    return parsed


def fetch_games(season_year: int, division: str) -> list[Game]:
    """Fetch season games and return only `Game` objects for the solver."""

    return [item.game for item in fetch_games_with_metadata(season_year, division)]
