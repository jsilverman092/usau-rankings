# tests/test_match_report_parser.py
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from usau_rankings.match_report_parser import (
    MatchReportParseError,
    _clean_team_title,
    parse_match_report_html,
)


_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def test_clean_team_title_strips_parenthetical_seed():
    assert _clean_team_title("Emory (12)") == "Emory"
    assert _clean_team_title("North Carolina-Charlotte (25)") == "North Carolina-Charlotte"
    assert _clean_team_title("  Foo   Bar (XYZ)  ") == "Foo Bar"


def test_parse_match_report_fixture_1():
    html = (_FIXTURES_DIR / "match_report_1.html").read_text(encoding="utf-8")
    row = parse_match_report_html(html, source="match_report_1.html")

    assert row["team1"] == "Emory"
    assert row["team2"] == "North Carolina-Charlotte"
    assert row["score1"] == 13
    assert row["score2"] == 9
    assert row["status"] == "Final"
    assert row["game_date"] == date(2026, 1, 30)

    # expected decoded EventGameId from fixture:
    assert row["event_game_id"] == "VzuAgr7XfBz35MIn0Fk/6553W4KxbNqfNZIwzTJbCbU="
    # expected Event name from breadcrumbs:
    assert row["event"] == "Florida Warm Up 2026"


def test_parse_match_report_raises_if_no_teams():
    bad_html = "<html><body><p>No teams here</p></body></html>"
    with pytest.raises(MatchReportParseError):
        parse_match_report_html(bad_html, source="bad.html")
