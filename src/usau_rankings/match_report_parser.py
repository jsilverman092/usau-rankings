# usau_rankings/match_report_parser.py
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse, unquote

from bs4 import BeautifulSoup


class MatchReportParseError(RuntimeError):
    pass


_PAREN_TRAIL_RE = re.compile(r"\s*\([^)]*\)\s*$")
_DATE_RE = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{4})\b")


def _clean_team_title(s: str) -> str:
    """
    Examples:
      "Emory (12)" -> "Emory"
      "North Carolina-Charlotte (25)" -> "North Carolina-Charlotte"
    """
    s = (s or "").strip()
    s = _PAREN_TRAIL_RE.sub("", s).strip()
    # collapse internal whitespace
    s = re.sub(r"\s+", " ", s)
    return s


def _find_select(
    soup: BeautifulSoup,
    *,
    id_contains: str | None = None,
    name_contains: str | None = None,
) -> Any | None:
    """Best-effort find for a <select> by id/name substrings."""
    if id_contains:
        el = soup.find("select", id=re.compile(re.escape(id_contains)))
        if el is not None:
            return el
    if name_contains:
        el = soup.find("select", attrs={"name": re.compile(re.escape(name_contains))})
        if el is not None:
            return el
    # last-ditch: scan all selects
    if id_contains or name_contains:
        for sel in soup.find_all("select"):
            sid = (sel.get("id") or "").lower()
            sname = (sel.get("name") or "").lower()
            if id_contains and id_contains.lower() in sid:
                return sel
            if name_contains and name_contains.lower() in sname:
                return sel
    return None


def _selected_option(select_tag: Any | None) -> Any | None:
    if select_tag is None:
        return None
    # HTML might mark selected="selected" or just selected
    opt = select_tag.find("option", selected=True)
    if opt is None:
        # sometimes first non-empty option is effectively the value; but prefer None
        return None
    return opt


def _selected_value_str(select_tag: Any | None) -> str | None:
    opt = _selected_option(select_tag)
    if opt is None:
        return None
    val = opt.get("value")
    if val is None:
        val = opt.get_text(" ", strip=True)
    val = (val or "").strip()
    return val or None


def _selected_value_int(select_tag: Any | None) -> int | None:
    val = _selected_value_str(select_tag)
    if val is None:
        return None
    # Match report sometimes includes W/L/F etc; only parse numeric.
    if re.fullmatch(r"-?\d+", val):
        try:
            return int(val)
        except ValueError:
            return None
    return None


def _parse_match_date(soup: BeautifulSoup) -> Optional[date]:
    """
    Parses date from header like:
      <div class="alt_page_header ...">
        <h1 class="title">Match Report - Pool A 1/30/2026</h1>
      </div>
    """
    h1 = soup.select_one("div.alt_page_header h1.title")
    if not h1:
        return None
    text = " ".join(h1.get_text(" ", strip=True).split())
    m = _DATE_RE.search(text)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%m/%d/%Y").date()
    except ValueError:
        return None


def _extract_event_game_id(soup: BeautifulSoup) -> Optional[str]:
    """
    Look for EventGameId in:
      - form action querystring, e.g. /teams/events/match_report/?EventGameId=... 
      - or hidden input fields
    Returns the URL-decoded value (unquote).
    """
    # 1) check form action (common)
    form = soup.find("form", id=re.compile(r"(^|.*\b)main(\b.*|$)"))
    if form and form.get("action"):
        parsed = urlparse(form.get("action"))
        qs = parse_qs(parsed.query)
        vals = qs.get("EventGameId") or qs.get("EventGameID") or qs.get("eventgameid")
        if vals:
            # take first and unquote once
            return unquote(vals[0])

    # 2) fallback: any form with EventGameId in action
    for f in soup.find_all("form", action=True):
        parsed = urlparse(f["action"])
        qs = parse_qs(parsed.query)
        vals = qs.get("EventGameId") or qs.get("EventGameID") or qs.get("eventgameid")
        if vals:
            return unquote(vals[0])

    # 3) fallback: hidden input fields
    inp = soup.find("input", attrs={"name": re.compile(r"EventGameId", re.I)})
    if inp and inp.get("value"):
        return unquote(inp.get("value"))

    return None


def _extract_event_name(soup: BeautifulSoup) -> Optional[str]:
    """
    Extract the event name from breadcrumbs. Example:
      <div class="breadcrumbs_area">
        <div class="breadcrumbs">
          <a href='/events/league/'>Home</a><span...></span>
          <a href='/events/Florida-Warm-Up-2026/'>Florida Warm Up 2026</a>...
        </div>
      </div>

    Heuristic: look for the first <a> under .breadcrumbs whose href starts with '/events/'
    but is not '/events/league/'.
    """
    crumbs = soup.select_one("div.breadcrumbs_area div.breadcrumbs")
    if not crumbs:
        # try looser selector
        crumbs = soup.find("div", class_=re.compile(r"breadcrumbs", re.I))
    if not crumbs:
        return None

    for a in crumbs.find_all("a", href=True):
        href = a["href"]
        # skip the generic league/home anchor
        if href.rstrip("/") == "/events/league":
            continue
        if href.startswith("/events/"):
            text = " ".join(a.get_text(" ", strip=True).split())
            if text:
                return text
    # fallback: if there are at least two anchors, return the second one (common structure)
    anchors = crumbs.find_all("a")
    if len(anchors) >= 2:
        return " ".join(anchors[1].get_text(" ", strip=True).split())
    return None


def parse_match_report_html(html: str, *, source: str | None = None) -> dict[str, Any]:
    """
    Parse a USAU Match Report page and return a minimal game record.

    Returns:
      {
        "team1": str,
        "team2": str,
        "score1": int | None,
        "score2": int | None,
        "status": str | None,
        "game_date": datetime.date | None,
        "event_game_id": str | None,
        "event": str | None,
      }

    Notes:
    - We treat teams as required. If we can't find both, we raise MatchReportParseError.
    - Scores/status/date/event fields can be None if not present.
    """
    if not html or not isinstance(html, str):
        raise MatchReportParseError(f"Empty/invalid HTML (source={source!r})")

    soup = BeautifulSoup(html, "html.parser")

    # ---- Match date (best-effort) ----
    game_date = _parse_match_date(soup)

    # ---- Teams (primary) ----
    titles = soup.select("p.tab_contents_title")
    team1 = team2 = None
    if len(titles) >= 2:
        team1 = _clean_team_title(titles[0].get_text(" ", strip=True))
        team2 = _clean_team_title(titles[1].get_text(" ", strip=True))

    # ---- Teams (fallback) ----
    if not team1 or not team2:
        # Sometimes class naming differs but contains 'tab_contents_title'
        fallback_titles = []
        for el in soup.find_all(True):
            cls = el.get("class") or []
            if any("tab_contents_title" in c for c in cls):
                fallback_titles.append(el)
        if len(fallback_titles) >= 2:
            team1 = team1 or _clean_team_title(fallback_titles[0].get_text(" ", strip=True))
            team2 = team2 or _clean_team_title(fallback_titles[1].get_text(" ", strip=True))

    if not team1 or not team2:
        preview = soup.get_text(" ", strip=True)[:250]
        raise MatchReportParseError(
            f"Could not locate both team names (source={source!r}); preview={preview!r}"
        )

    # ---- Scores / status ----
    home_sel = _find_select(soup, id_contains="drpTeamScoreHome", name_contains="drpTeamScoreHome")
    away_sel = _find_select(soup, id_contains="drpTeamScoreAway", name_contains="drpTeamScoreAway")
    status_sel = _find_select(soup, id_contains="drpGameStatus", name_contains="drpGameStatus")

    score1 = _selected_value_int(home_sel)
    score2 = _selected_value_int(away_sel)
    status = _selected_value_str(status_sel)

    # ---- EventGameId / Event ----
    event_game_id = _extract_event_game_id(soup)
    event_name = _extract_event_name(soup)

    return {
        "team1": team1,
        "team2": team2,
        "score1": score1,
        "score2": score2,
        "status": status,
        "game_date": game_date,
        "event_game_id": event_game_id,
        "event": event_name,
    }
