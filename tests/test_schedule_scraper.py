from __future__ import annotations

import json

import pytest

import usau_rankings.schedule_scraper as schedule_scraper


HTML = """
<html>
  <body>
    <div class="tab" data-tab="Pool Play">
      <div class="game-row" id="game-1001">
        <a href="/events/match_report/?EventGameId=1001">Match Report</a>
        <span class="date">2026-01-31</span>
        <span class="time">9:00 AM</span>
        <span class="field">Field 2</span>
        <span class="teams">Alpha College vs Beta College</span>
        <span class="score">15-12</span>
        <span class="status">Final</span>
      </div>
    </div>
    <div class="tab" data-tab="Bracket">
      <div class="game-row" id="game-1002">
        <a href="/events/match_report/?EventGameId=1002">Match Report</a>
        <span>Gamma University</span>
        <span>Delta State</span>
        <span class="status">Cancelled</span>
        <span class="date">2026-02-01</span>
        <span class="time">11:30 AM</span>
      </div>
    </div>
    <script>const game = {"EventGameId": 1002};</script>
  </body>
</html>
"""


def test_extract_event_game_ids_with_duplicates():
    ids, counts = schedule_scraper.extract_event_game_ids(HTML)
    assert ids == ["1001", "1002"]
    assert counts["1002"] == 2


def test_parse_games_v3_handles_played_and_unplayed_games():
    context = schedule_scraper.ParseContext(
        soup=schedule_scraper.BeautifulSoup(HTML, "html.parser"),
        html=HTML,
        debug=False,
    )
    rows = schedule_scraper.parse_games(context, ["1001", "1002"], version=3)

    assert rows[0]["team1"] == "Alpha College"
    assert rows[0]["score1"] == 15
    assert rows[0]["score2"] == 12

    assert rows[1]["team1"] == "Gamma University"
    assert rows[1]["team2"] == "Delta State"
    assert rows[1]["status"] == "Cancelled"
    assert rows[1]["score1"] is None


def test_main_fails_on_expect_count_mismatch(monkeypatch, tmp_path):
    def fake_fetch(url: str, timeout: int = 30):
        return HTML, 1

    monkeypatch.setattr(schedule_scraper, "fetch_schedule_html", fake_fetch)

    with pytest.raises(schedule_scraper.ScheduleScrapeError):
        schedule_scraper.main(
            [
                "https://example/schedule",
                "--expect-count",
                "99",
                "--json-out",
                str(tmp_path / "out.json"),
                "--csv-out",
                str(tmp_path / "out.csv"),
            ]
        )


def test_main_writes_outputs(monkeypatch, tmp_path, capsys):
    def fake_fetch(url: str, timeout: int = 30):
        return HTML, 1

    monkeypatch.setattr(schedule_scraper, "fetch_schedule_html", fake_fetch)

    exit_code = schedule_scraper.main(
        [
            "https://example/schedule",
            "--expect-count",
            "2",
            "--json-out",
            str(tmp_path / "out.json"),
            "--csv-out",
            str(tmp_path / "out.csv"),
        ]
    )

    assert exit_code == 0
    payload = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert len(payload) == 2

    summary = capsys.readouterr().out
    assert "total_unique_event_game_ids" in summary
