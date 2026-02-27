"""USAU rankings engine helpers and iterative solver."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import math
from typing import Iterable


DEFAULT_RATING = 1000.0


@dataclass(frozen=True)
class Game:
    """Single game result used by the iterative solver."""

    date: date
    team_a: str
    team_b: str
    score_a: int
    score_b: int


@dataclass(frozen=True)
class TeamGameRating:
    """Team-specific game rating and combined weight."""

    game_rating: float
    weight: float


def _validate_score(name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} must be >= 0")


def _validate_rating(name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")


def game_rating_value(winner_score: int, loser_score: int) -> float:
    """Return the USAU rating value earned by the winning team."""

    _validate_score("winner_score", winner_score)
    _validate_score("loser_score", loser_score)

    if winner_score <= loser_score:
        raise ValueError("winner_score must be > loser_score")
    if winner_score < 2:
        raise ValueError("winner_score must be >= 2")

    r = loser_score / (winner_score - 1)
    t = min(1.0, (1.0 - r) / 0.5)
    x = 125 + 475 * math.sin(t * 0.4 * math.pi) / math.sin(0.4 * math.pi)
    return float(x)


def calculate_game_rating(
    winner_rating: float,
    loser_rating: float,
    winner_score: int,
    loser_score: int,
) -> tuple[float, float]:
    """Return USAU game ratings for winner and loser."""

    _validate_rating("winner_rating", winner_rating)
    _validate_rating("loser_rating", loser_rating)

    value = game_rating_value(winner_score, loser_score)
    return float(loser_rating + value), float(winner_rating - value)


def score_weight(winner_score: int, loser_score: int) -> float:
    """Return USAU score weight for a single game."""

    _validate_score("winner_score", winner_score)
    _validate_score("loser_score", loser_score)
    if winner_score <= loser_score:
        raise ValueError("winner_score must be > loser_score")

    if winner_score >= 13 or (winner_score + loser_score) >= 19:
        return 1.0

    adjusted_loser = max(loser_score, (winner_score - 1) / 2)
    return float(min(1.0, math.sqrt((winner_score + adjusted_loser) / 19)))


def date_weight(game_date: date, season_start: date, season_end: date) -> float:
    """Return USAU date weight via exponential interpolation from 0.5 to 1.0."""

    if season_end < season_start:
        raise ValueError("season_end must be on or after season_start")

    num_weeks = 1 + ((season_end - season_start).days // 7)
    week_index = (game_date - season_start).days // 7
    week_index = max(0, min(week_index, num_weeks - 1))

    if num_weeks > 1:
        multiplier = (1.0 / 0.5) ** (1.0 / (num_weeks - 1))
    else:
        multiplier = 1.0

    weight = 0.5 * (multiplier**week_index)
    return float(max(0.5, min(weight, 1.0)))


def weighted_team_rating(team_games: Iterable[TeamGameRating]) -> float:
    """Return weighted average of team game ratings.

    Returns 1000.0 when the provided games have zero total weight.
    """

    total_weight = 0.0
    weighted_sum = 0.0
    for game in team_games:
        total_weight += game.weight
        weighted_sum += game.game_rating * game.weight

    if total_weight == 0:
        return DEFAULT_RATING
    return weighted_sum / total_weight


def _iter_teams(games: Iterable[Game]) -> set[str]:
    teams: set[str] = set()
    for game in games:
        teams.add(game.team_a)
        teams.add(game.team_b)
    return teams


def _winner_loser(game: Game) -> tuple[str, str, int, int]:
    if game.score_a == game.score_b:
        raise ValueError("Games cannot end in a tie")
    if game.score_a > game.score_b:
        return game.team_a, game.team_b, game.score_a, game.score_b
    return game.team_b, game.team_a, game.score_b, game.score_a


def _ignored_blowouts(
    games: list[Game],
    ratings: dict[str, float],
    min_other_results: int,
) -> set[int]:
    """Return game indices ignored under the 600-point blowout rule."""

    ignored: set[int] = set()

    while True:
        changed = False
        for idx, game in enumerate(games):
            if idx in ignored:
                continue

            winner, loser, winner_score, loser_score = _winner_loser(game)
            rating_gap = ratings[winner] - ratings[loser]
            is_blowout = winner_score > (2 * loser_score + 1)
            if rating_gap <= 600 or not is_blowout:
                continue

            non_ignored_for_winner = 0
            for other_idx, other_game in enumerate(games):
                if other_idx == idx or other_idx in ignored:
                    continue
                if winner in (other_game.team_a, other_game.team_b):
                    non_ignored_for_winner += 1

            if non_ignored_for_winner >= min_other_results:
                ignored.add(idx)
                changed = True

        if not changed:
            return ignored


def solve_ratings(
    games: list[Game],
    season_start: date,
    season_end: date,
    *,
    convergence_threshold: float = 0.01,
    max_iters: int = 5000,
    blowout_min_other_results: int = 5,
) -> dict[str, float]:
    """Solve ratings by iterating USAU game-rating updates to convergence."""

    if max_iters < 1:
        raise ValueError("max_iters must be >= 1")

    teams = _iter_teams(games)
    ratings = {team: DEFAULT_RATING for team in teams}

    for _ in range(max_iters):
        per_team_games: dict[str, list[TeamGameRating]] = {team: [] for team in teams}
        ignored_games = _ignored_blowouts(games, ratings, blowout_min_other_results)

        for idx, game in enumerate(games):
            if idx in ignored_games:
                continue

            winner, loser, winner_score, loser_score = _winner_loser(game)
            winner_game_rating, loser_game_rating = calculate_game_rating(
                ratings[winner],
                ratings[loser],
                winner_score,
                loser_score,
            )
            combined_weight = date_weight(game.date, season_start, season_end) * score_weight(
                winner_score,
                loser_score,
            )

            per_team_games[winner].append(TeamGameRating(winner_game_rating, combined_weight))
            per_team_games[loser].append(TeamGameRating(loser_game_rating, combined_weight))

        updated_ratings = {
            team: weighted_team_rating(team_games)
            for team, team_games in per_team_games.items()
        }
        max_change = max(
            abs(updated_ratings[team] - ratings[team])
            for team in teams
        ) if teams else 0.0
        ratings = updated_ratings

        if max_change < convergence_threshold:
            break

    return ratings


# Backward-compatible alias.
winner_rating_value = game_rating_value
