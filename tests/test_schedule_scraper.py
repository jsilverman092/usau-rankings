from __future__ import annotations

import json

import usau_rankings.schedule_scraper as schedule_scraper


def test_schedule_scraper_offline_wrapper_parses_fixture(tmp_path, monkeypatch, capsys):
    # Arrange: write a tiny HTML file
    html_file = tmp_path / "mr.html"
    html_file.write_text("<html><body><div>stub</div></body></html>", encoding="utf-8")

    # And monkeypatch the parser to prove wiring works
    def fake_parse(html: str, *, source: str | None = None):
        return {"team1": "A", "team2": "B", "score1": 1, "score2": 0, "status": "Final"}

    monkeypatch.setattr(schedule_scraper, "parse_match_report_html", fake_parse)

    # Act
    exit_code = schedule_scraper.main(["--html-file", str(html_file)])

    # Assert
    assert exit_code == 0
    out = capsys.readouterr().out.strip()
    parsed = json.loads(out)
    assert parsed["team1"] == "A"
    assert parsed["team2"] == "B"
    assert parsed["score1"] == 1
    assert parsed["score2"] == 0
    assert parsed["status"] == "Final"
