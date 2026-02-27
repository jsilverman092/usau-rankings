"""USAU game score-to-rating translation helpers."""

from __future__ import annotations

import math


def _validate_score(name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} must be >= 0")


def _validate_rating(name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")


def game_rating_value(winner_score: int, loser_score: int) -> float:
    """Return the USAU rating value earned by the winning team.

    Uses the published formula from play.usaultimate.org:

    * r = loser_score / (winner_score - 1)
    * t = min(1.0, (1.0 - r) / 0.5)
    * x = 125 + 475 * sin(t * 0.4*pi) / sin(0.4*pi)
    """

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
    """Return USAU game ratings for winner and loser.

    USAU game ratings are calculated by applying the score-based game value
    to the opponent's rating:

    * winner_game_rating = loser_rating + game_rating_value
    * loser_game_rating = winner_rating - game_rating_value
    """

    _validate_rating("winner_rating", winner_rating)
    _validate_rating("loser_rating", loser_rating)

    value = game_rating_value(winner_score, loser_score)
    return float(loser_rating + value), float(winner_rating - value)


# Backward-compatible alias.
winner_rating_value = game_rating_value
