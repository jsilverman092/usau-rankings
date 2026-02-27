import pytest

from usau_rankings.rating_engine import game_rating_value


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
