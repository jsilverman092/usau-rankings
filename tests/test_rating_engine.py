from datetime import date

import pytest

from usau_rankings.rating_engine import (
    DEFAULT_RATING,
    Game,
    TeamGameRating,
    calculate_game_rating,
    date_weight,
    game_rating_value,
    score_weight,
    solve_ratings,
    weighted_team_rating,
)


@pytest.mark.parametrize(
    ("winner_score", "loser_score", "published_value"),
    [
        (15, 14, 125),
        (15, 13, 214),
        (15, 12, 300),
        (15, 11, 381),
        (15, 10, 454),
        (15, 9, 515),
        (15, 8, 565),
        (15, 7, 600),
        (13, 12, 125),
        (13, 11, 229),
        (13, 10, 328),
        (13, 9, 419),
        (13, 8, 496),
        (13, 7, 558),
        (13, 6, 600),
        (11, 10, 125),
        (11, 9, 249),
        (11, 8, 366),
        (11, 7, 467),
        (11, 6, 547),
        (11, 5, 600),
    ],
)
def test_published_table_values(winner_score, loser_score, published_value):
    assert round(game_rating_value(winner_score, loser_score)) == published_value


def test_shutouts_are_max_value():
    assert game_rating_value(15, 0) == pytest.approx(600.0)
    assert game_rating_value(11, 0) == pytest.approx(600.0)


@pytest.mark.parametrize(
    ("winner_score", "loser_score", "error"),
    [
        (-1, 0, ValueError),
        (2, -1, ValueError),
        (1, 0, ValueError),
        (5, 5, ValueError),
        (4, 5, ValueError),
        (10.5, 9, TypeError),
        (10, "9", TypeError),
        (True, 0, TypeError),
    ],
)
def test_input_validation(winner_score, loser_score, error):
    with pytest.raises(error):
        game_rating_value(winner_score, loser_score)


def test_calculate_game_rating_returns_both_team_game_ratings():
    winner_game_rating, loser_game_rating = calculate_game_rating(1000, 900, 15, 10)

    assert winner_game_rating == pytest.approx(900 + game_rating_value(15, 10))
    assert loser_game_rating == pytest.approx(1000 - game_rating_value(15, 10))


@pytest.mark.parametrize(
    ("winner_rating", "loser_rating", "error"),
    [
        ("1000", 900, TypeError),
        (1000, object(), TypeError),
        (True, 900, TypeError),
    ],
)
def test_calculate_game_rating_validates_team_ratings(winner_rating, loser_rating, error):
    with pytest.raises(error):
        calculate_game_rating(winner_rating, loser_rating, 15, 10)


@pytest.mark.parametrize(
    ("winner_score", "loser_score", "expected"),
    [
        (13, 0, 1.0),
        (12, 7, 1.0),
        (10, 8, ((10 + max(8, 4.5)) / 19) ** 0.5),
        (8, 1, ((8 + max(1, 3.5)) / 19) ** 0.5),
    ],
)
def test_score_weight_examples_and_boundaries(winner_score, loser_score, expected):
    assert score_weight(winner_score, loser_score) == pytest.approx(expected)


def test_date_weight_three_week_season():
    season_start = date(2024, 1, 1)
    season_end = date(2024, 1, 21)

    assert date_weight(date(2024, 1, 1), season_start, season_end) == pytest.approx(0.5)
    assert date_weight(date(2024, 1, 8), season_start, season_end) == pytest.approx(2 ** -0.5)
    assert date_weight(date(2024, 1, 20), season_start, season_end) == pytest.approx(1.0)


def test_weighted_team_rating_returns_default_for_zero_total_weight():
    rating = weighted_team_rating([TeamGameRating(1200, 0.0), TeamGameRating(900, 0.0)])
    assert rating == DEFAULT_RATING


def test_iterative_solver_moves_ratings_and_converges():
    games = [
        Game(date(2024, 1, 1), "A", "B", 15, 10),
        Game(date(2024, 1, 2), "A", "C", 15, 9),
        Game(date(2024, 1, 3), "B", "C", 15, 14),
        Game(date(2024, 1, 4), "D", "C", 15, 12),
        Game(date(2024, 1, 5), "A", "D", 15, 13),
    ]

    ratings = solve_ratings(
        games,
        season_start=date(2024, 1, 1),
        season_end=date(2024, 1, 31),
        convergence_threshold=0.01,
        max_iters=5000,
    )

    assert ratings["A"] > ratings["B"] > ratings["C"]
    assert ratings["A"] > ratings["D"]

    rerun = solve_ratings(
        games,
        season_start=date(2024, 1, 1),
        season_end=date(2024, 1, 31),
        convergence_threshold=0.0001,
        max_iters=5000,
    )
    for team in ratings:
        assert abs(ratings[team] - rerun[team]) < 1.0


def test_blowout_rule_ignores_game_when_high_rated_winner_has_other_results():
    games = [
        Game(date(2024, 1, 1), "A", "B", 15, 5),
        Game(date(2024, 1, 2), "A", "C", 15, 12),
        Game(date(2024, 1, 3), "A", "D", 15, 12),
        Game(date(2024, 1, 4), "A", "E", 15, 12),
        Game(date(2024, 1, 5), "A", "F", 15, 12),
        Game(date(2024, 1, 6), "A", "G", 15, 12),
    ]

    ratings_with_ignore = solve_ratings(
        games,
        season_start=date(2024, 1, 1),
        season_end=date(2024, 1, 31),
        max_iters=500,
        blowout_min_other_results=5,
    )
    ratings_without_ignore = solve_ratings(
        games,
        season_start=date(2024, 1, 1),
        season_end=date(2024, 1, 31),
        max_iters=500,
        blowout_min_other_results=99,
    )

    assert ratings_with_ignore["B"] == pytest.approx(DEFAULT_RATING)
    assert ratings_without_ignore["B"] < DEFAULT_RATING
