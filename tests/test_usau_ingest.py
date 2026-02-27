from datetime import date

from usau_rankings.rating_engine import Game
from usau_rankings.usau_ingest import fetch_games


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeRequests:
    def __init__(self, payload):
        self._payload = payload

    def get(self, url, params, timeout):
        return _FakeResponse(self._payload)


def test_fetch_games_builds_solver_games_from_json_endpoint(monkeypatch):
    payload = {
        "results": [
            {
                "date": "2024-03-15",
                "team_a": {"name": "Truck Stop"},
                "team_b": {"name": "PoNY"},
                "score_a": 15,
                "score_b": 12,
                "source_url": "https://play.usaultimate.org/game/1",
            },
            {
                "date": "2024-03-16T10:00:00",
                "team1": "Bravo",
                "team2": "Sockeye",
                "score1": "13",
                "score2": "15",
            },
        ]
    }
    monkeypatch.setattr("usau_rankings.usau_ingest.requests", _FakeRequests(payload))

    games = fetch_games(2024, "club-mens")

    assert games == [
        Game(date(2024, 3, 15), "Truck Stop", "PoNY", 15, 12),
        Game(date(2024, 3, 16), "Bravo", "Sockeye", 13, 15),
    ]
