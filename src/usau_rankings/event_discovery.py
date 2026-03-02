"""
Discover USA Ultimate event URLs from the PlayUSAU Tournament Calendar (no Selenium).

Highlights
- Handles ASP.NET-style pagination (javascript:__doPostBack)
- Can checkpoint pager state and resume later WITHOUT refetching from page 1
- Supports collecting ALL divisions in one crawl or filtering by a specific division text
- Can target only the Past Events section (default), excluding Current/Upcoming events

Section targeting
- --section past     (default): extract/paginate only the "Past Events" grid
- --section upcoming            : extract/paginate only the "Current/Upcoming Events" grid
- --section both                : extract from whole page; pager selection not grid-specific

Paging semantics
- --pages 4        => page 4 ONLY
- --pages 1-3      => pages 1..3
- --pages 51+      => pages 51..end
- (empty --pages)  => pages 1..end
- --first 4        => pages 1..4 (overrides --pages if provided)

Checkpointing / resume
- --checkpoint PATH (recommended)
- --resume         : load checkpoint and continue from the saved state

Recommended usage (historical index)
  python -m usau_rankings.event_discovery --section past --all-divisions --first 50 --checkpoint data/event_ckpt.json --out data/events.csv
  python -m usau_rankings.event_discovery --section past --all-divisions --pages 51+ --checkpoint data/event_ckpt.json --resume --out data/events.csv
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup, Tag


BASE_URL = "https://play.usaultimate.org/events/tournament/"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class DiscoveredEvent:
    name: str
    url: str
    competition_groups: str
    listing_date_text: str
    listing_year: Optional[int]


# ---------------------------
# Small utilities
# ---------------------------

def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    return s


def _normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _safe_int(x: str) -> Optional[int]:
    try:
        return int(x)
    except Exception:
        return None


def _extract_year_from_text(text: str) -> Optional[int]:
    if not text:
        return None
    m = re.search(r"\b(20\d{2})\b", text)
    if not m:
        return None
    y = _safe_int(m.group(1))
    if y is None:
        return None
    if 2000 <= y <= 2100:
        return y
    return None


def parse_pages_spec(pages_spec: str) -> tuple[int, Optional[int]]:
    """
    Parse pages spec with semantics:
      - ""       -> (1, None)  unlimited starting at 1
      - "4"      -> (4, 4)     ONLY page 4
      - "51-100" -> (51, 100)
      - "51+"    -> (51, None)
    """
    s = (pages_spec or "").strip()
    if not s:
        return 1, None

    if s.endswith("+"):
        start = _safe_int(s[:-1].strip())
        if start is None or start < 1:
            raise ValueError(f"Invalid --pages value: {pages_spec!r}")
        return start, None

    if "-" in s:
        a, b = [x.strip() for x in s.split("-", 1)]
        start = _safe_int(a)
        end = _safe_int(b)
        if start is None or end is None or start < 1 or end < start:
            raise ValueError(f"Invalid --pages range: {pages_spec!r}")
        return start, end

    n = _safe_int(s)
    if n is None or n < 1:
        raise ValueError(f"Invalid --pages value: {pages_spec!r}")
    return n, n


# ---------------------------
# Checkpoint I/O (hardened)
# ---------------------------

def _atomic_write_json(path: str, obj: dict[str, Any]) -> None:
    """
    Write JSON atomically to avoid corrupted/empty checkpoint if process is interrupted.
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


def _save_checkpoint(path: str, ckpt: dict[str, Any], verbose: bool) -> None:
    _atomic_write_json(path, ckpt)
    if verbose:
        print(f"[event_discovery] Saved checkpoint: {path} (page={ckpt.get('current_page')})")


def _load_checkpoint(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Checkpoint not found: {path}")

    # Empty file => treat as invalid
    if os.path.getsize(path) == 0:
        raise ValueError(
            f"Checkpoint file is empty: {path}\n"
            f"Delete it and rerun without --resume to recreate it."
        )

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Checkpoint file is not valid JSON: {path}\n"
            f"Likely an interrupted write. Delete it and rerun without --resume.\n"
            f"Original error: {e}"
        ) from e


def _session_set_cookies(session: requests.Session, cookies: dict[str, str]) -> None:
    for k, v in (cookies or {}).items():
        session.cookies.set(k, v)


def _session_get_cookies(session: requests.Session) -> dict[str, str]:
    return requests.utils.dict_from_cookiejar(session.cookies)


# ---------------------------
# ASP.NET postback pagination helpers
# ---------------------------

def _extract_hidden_inputs(form: Tag) -> dict[str, str]:
    data: dict[str, str] = {}
    for inp in form.select("input"):
        name = inp.get("name")
        if not name:
            continue
        itype = (inp.get("type") or "").lower()
        if itype == "hidden":
            data[name] = inp.get("value") or ""
    return data


def _parse_do_postback(href: str) -> Optional[tuple[str, str]]:
    """
    Parse href like: javascript:__doPostBack('EVENTTARGET','EVENTARGUMENT')
    Returns (eventtarget, eventargument)
    """
    if not href:
        return None
    m = re.search(r"__doPostBack\(\s*'([^']*)'\s*,\s*'([^']*)'\s*\)", href)
    if not m:
        return None
    return m.group(1), m.group(2)


def _find_best_form(soup: BeautifulSoup) -> Optional[Tag]:
    forms = soup.select("form")
    if not forms:
        return None

    def score_form(f: Tag) -> int:
        t = _normalize_ws(f.get_text(" ", strip=True)).lower()
        score = 0
        if "rows:" in t:
            score += 3
        if "page:" in t:
            score += 3
        if "next" in t and "previous" in t:
            score += 2
        score += min(5, len(f.select('input[type="hidden"]')))
        return score

    return sorted(forms, key=score_form, reverse=True)[0]


def _section_grid_preferences(section: str) -> list[str]:
    """
    Tokens we prefer to see in __EVENTTARGET for the correct grid.
    """
    section = (section or "past").lower()
    if section == "past":
        return ["gvPastEvents", "gvPast", "PastEvents"]
    if section == "upcoming":
        return ["gvCurrentUpcomingEvents", "gvUpcoming", "CurrentUpcoming", "UpcomingEvents"]
    return []  # both => no preference


def _find_postback_next(form: Tag, section: str) -> Optional[dict[str, str]]:
    """
    Return the click trigger for "Next" for an ASP.NET pager.

    Prefers LinkButtons (javascript:__doPostBack). Falls back to submit/button.
    If section != both, prefer __EVENTTARGET values that reference that section's grid.
    """
    prefs = _section_grid_preferences(section)

    candidates: list[tuple[int, str, str, str]] = []  # (score, target, arg, text)

    for a in form.select("a"):
        txt = _normalize_ws(a.get_text(" ", strip=True))
        if "next" not in txt.lower():
            continue
        href = a.get("href") or ""
        parsed = _parse_do_postback(href)
        if not parsed:
            continue
        target, arg = parsed

        score = 10 if re.search(r"next\s+\d+", txt, re.I) else 5

        if prefs:
            if any(p.lower() in target.lower() for p in prefs):
                score += 50
            else:
                score -= 20

        candidates.append((score, target, arg, txt))

    if candidates:
        candidates.sort(reverse=True)
        _, target, arg, _txt = candidates[0]
        return {"__EVENTTARGET": target, "__EVENTARGUMENT": arg}

    # Fallback: submit/button style (rare)
    for inp in form.select("input"):
        itype = (inp.get("type") or "").lower()
        if itype not in {"submit", "image", "button"}:
            continue
        val = _normalize_ws(inp.get("value") or "")
        name = inp.get("name")
        if name and "next" in val.lower():
            return {name: inp.get("value") or "Next"}

    for btn in form.select("button"):
        txt = _normalize_ws(btn.get_text(" ", strip=True))
        name = btn.get("name")
        if name and "next" in txt.lower():
            value = btn.get("value") or txt or "Next"
            return {name: value}

    return None


def _build_next_action_from_html(html: str, section: str) -> Optional[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    form = _find_best_form(soup)
    if form is None:
        return None

    hidden = _extract_hidden_inputs(form)
    trigger = _find_postback_next(form, section=section)
    if not trigger:
        return None

    action = form.get("action") or ""
    return {"hidden_inputs": hidden, "next_trigger": trigger, "form_action": action}


def _advance_page_via_postback(
    session: requests.Session,
    current_url: str,
    html: str,
    section: str,
    verbose: bool = True,
    timeout: int = 30,
) -> Optional[tuple[str, str, dict[str, Any]]]:
    next_action = _build_next_action_from_html(html, section=section)
    if not next_action:
        return None

    payload = dict(next_action["hidden_inputs"])
    payload.setdefault("__EVENTTARGET", "")
    payload.setdefault("__EVENTARGUMENT", "")
    payload.update(next_action["next_trigger"])

    post_url = urljoin(current_url, next_action["form_action"] or current_url)

    if verbose:
        trig = next_action["next_trigger"]
        if "__EVENTTARGET" in trig:
            print(f"[event_discovery] Advancing via POSTBACK (__EVENTTARGET={trig['__EVENTTARGET']}) -> POST {post_url}")
        else:
            keys = ", ".join(list(trig.keys()))
            print(f"[event_discovery] Advancing via POSTBACK ({keys}) -> POST {post_url}")

    resp = session.post(post_url, data=payload, timeout=timeout, headers={"Referer": current_url})
    resp.raise_for_status()

    next_html = resp.text
    next_url2 = str(resp.url)
    next_action2 = _build_next_action_from_html(next_html, section=section) or {}
    return next_html, next_url2, next_action2


def _find_next_page_url_anchor(soup: BeautifulSoup, current_url: str) -> Optional[str]:
    for a in soup.select("a"):
        txt = _normalize_ws(a.get_text(" ", strip=True)).lower()
        if "next" not in txt:
            continue
        href = (a.get("href") or "").strip()
        if not href:
            continue
        if href.lower().startswith("javascript:"):
            continue
        if href.startswith("/") or href.startswith("http://") or href.startswith("https://"):
            return urljoin(current_url, href)
    return None


# ---------------------------
# Section targeting (Past vs Upcoming)
# ---------------------------

def _get_section_root(soup: BeautifulSoup, section: str) -> Tag:
    """
    Return a DOM subtree to search for event links.
    Best-effort:
      - past: try element with id containing 'gvPastEvents' first
      - upcoming: try element with id containing 'gvCurrentUpcomingEvents'
      - both: entire soup
    """
    section = (section or "past").lower()
    if section == "both":
        return soup

    id_candidates = _section_grid_preferences(section)
    for token in id_candidates:
        el = soup.find(id=re.compile(re.escape(token), re.I))
        if el is not None:
            return el.parent if isinstance(el.parent, Tag) else el

    heading_text = "Past Events" if section == "past" else "Current/Upcoming Events"
    heading = soup.find(string=re.compile(re.escape(heading_text), re.I))
    if heading and isinstance(getattr(heading, "parent", None), Tag):
        return heading.parent  # type: ignore[return-value]

    return soup


# ---------------------------
# Event extraction
# ---------------------------

def _find_event_row_container(a: Tag) -> Optional[Tag]:
    cur: Optional[Tag] = a
    for _ in range(12):
        if cur is None:
            return None
        if isinstance(cur, Tag) and cur.find("ul") and cur.find("li"):
            return cur
        cur = cur.parent if isinstance(cur, Tag) else None
    return None


def _extract_competition_groups(container: Tag) -> str:
    lis = container.select("ul li")
    if lis:
        return " | ".join(_normalize_ws(li.get_text(" ", strip=True)) for li in lis)
    return ""


def _extract_listing_date_text(container: Tag) -> str:
    raw = container.get_text("\n", strip=True)
    lines = [_normalize_ws(x) for x in raw.split("\n") if _normalize_ws(x)]
    datey = []
    for line in lines:
        if re.search(r"\bJan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec\b", line, re.I):
            datey.append(line)
        elif re.search(r"\b\d{1,2}/\d{1,2}/(20\d{2}|\d{2})\b", line):
            datey.append(line)
        elif re.search(r"\b(20\d{2})\b", line) and re.search(r"[-–]", line):
            datey.append(line)
    return sorted(datey, key=len)[0] if datey else ""


def _extract_events_from_page(soup: BeautifulSoup, page_url: str, section: str) -> list[DiscoveredEvent]:
    root = _get_section_root(soup, section=section)

    events: list[DiscoveredEvent] = []
    anchors = root.select('a[href^="/events/"]')

    seen_urls: set[str] = set()
    for a in anchors:
        href = a.get("href") or ""
        name = _normalize_ws(a.get_text(" ", strip=True))
        if not href.startswith("/events/"):
            continue
        if not name or name.lower() in {"events", "tournament calendar", "event schedule"}:
            continue

        href_root = href
        for suffix in ["/schedule", "/teams", "/register", "/results", "/standings", "/brackets"]:
            if suffix in href_root:
                href_root = href_root.split(suffix)[0]

        abs_url = urljoin(page_url, href_root)
        if abs_url in seen_urls:
            continue
        seen_urls.add(abs_url)

        container = _find_event_row_container(a)
        if container is None:
            events.append(
                DiscoveredEvent(
                    name=name,
                    url=abs_url,
                    competition_groups="",
                    listing_date_text="",
                    listing_year=_extract_year_from_text(name),
                )
            )
            continue

        groups = _extract_competition_groups(container)
        date_text = _extract_listing_date_text(container)
        year = (
            _extract_year_from_text(date_text)
            or _extract_year_from_text(container.get_text(" ", strip=True))
            or _extract_year_from_text(name)
        )

        events.append(
            DiscoveredEvent(
                name=name,
                url=abs_url,
                competition_groups=groups,
                listing_date_text=date_text,
                listing_year=year,
            )
        )

    return events


# ---------------------------
# Core crawl
# ---------------------------

def discover_events(
    *,
    division_filter: Optional[str],
    all_divisions: bool,
    start_year: Optional[int],
    end_year: Optional[int],
    start_page: int,
    end_page: Optional[int],
    sleep_seconds: float,
    verbose: bool,
    checkpoint_path: Optional[str],
    resume: bool,
    section: str,
) -> pd.DataFrame:
    if start_year is not None and end_year is not None and start_year > end_year:
        raise ValueError(f"start_year {start_year} cannot be > end_year {end_year}")
    if start_page < 1:
        raise ValueError("start_page must be >= 1")
    if end_page is not None and end_page < start_page:
        raise ValueError("end_page must be >= start_page")

    section = (section or "past").lower()
    if section not in {"past", "upcoming", "both"}:
        raise ValueError("--section must be one of: past, upcoming, both")

    session = _make_session()

    current_page = 0
    current_url = BASE_URL
    html = ""
    min_year_seen: Optional[int] = None

    if resume:
        if not checkpoint_path:
            raise ValueError("--resume requires --checkpoint PATH")
        ckpt = _load_checkpoint(checkpoint_path)

        current_page = int(ckpt.get("current_page", 0))
        current_url = ckpt.get("current_url", BASE_URL)
        html = ckpt.get("html", "")
        cookies = ckpt.get("cookies", {}) or {}
        _session_set_cookies(session, cookies)

        if not html:
            resp = session.get(current_url, timeout=30)
            resp.raise_for_status()
            html = resp.text
            current_url = str(resp.url)

        if verbose:
            print(f"[event_discovery] Resuming from checkpoint at page {current_page} ({current_url}) section={section}")
    else:
        resp = session.get(current_url, timeout=30)
        resp.raise_for_status()
        html = resp.text
        current_url = str(resp.url)

    collected: list[DiscoveredEvent] = []

    if current_page == 0:
        current_page = 1

    while True:
        if verbose:
            print(f"[event_discovery] Fetching page {current_page}: {current_url}")

        soup = BeautifulSoup(html, "html.parser")
        events = _extract_events_from_page(soup, current_url, section=section)

        for ev in events:
            if ev.listing_year is not None:
                if min_year_seen is None or ev.listing_year < min_year_seen:
                    min_year_seen = ev.listing_year

        in_window = current_page >= start_page and (end_page is None or current_page <= end_page)

        if in_window:
            for ev in events:
                if not all_divisions:
                    if not division_filter:
                        continue
                    if division_filter not in (ev.competition_groups or ""):
                        continue

                if start_year is not None and ev.listing_year is not None and ev.listing_year < start_year:
                    continue
                if end_year is not None and ev.listing_year is not None and ev.listing_year > end_year:
                    continue

                collected.append(ev)

        if checkpoint_path:
            ckpt_out = {
                "current_page": current_page,
                "current_url": current_url,
                "html": html,
                "cookies": _session_get_cookies(session),
                "timestamp": time.time(),
            }
            _save_checkpoint(checkpoint_path, ckpt_out, verbose=verbose)

        if end_page is not None and current_page >= end_page:
            if verbose:
                print(f"[event_discovery] Reached end_page={end_page}; stopping.")
            break

        if start_year is not None and min_year_seen is not None and min_year_seen < start_year:
            if verbose:
                print(
                    f"[event_discovery] Detected events older than start_year "
                    f"(min_year_seen={min_year_seen} < {start_year}); stopping pagination."
                )
            break

        next_url = _find_next_page_url_anchor(soup, current_url)
        if next_url:
            time.sleep(sleep_seconds)
            resp = session.get(next_url, timeout=30)
            resp.raise_for_status()
            html = resp.text
            current_url = str(resp.url)
            current_page += 1
            continue

        advanced = _advance_page_via_postback(session, current_url=current_url, html=html, section=section, verbose=verbose)
        if not advanced:
            if verbose:
                print("[event_discovery] No next page control found (anchor or postback); stopping.")
            break

        next_html, next_url2, _next_action2 = advanced
        time.sleep(sleep_seconds)
        html = next_html
        current_url = next_url2
        current_page += 1

    df = pd.DataFrame(
        [
            {
                "event_name": ev.name,
                "event_url": ev.url,
                "competition_groups": ev.competition_groups,
                "listing_date_text": ev.listing_date_text,
                "listing_year": ev.listing_year,
            }
            for ev in collected
        ]
    )

    if df.empty:
        return df

    df = df.drop_duplicates(subset=["event_url"]).reset_index(drop=True)

    missing = df["listing_year"].isna()
    if missing.any():
        df.loc[missing, "listing_year"] = df.loc[missing, "event_name"].astype(str).apply(_extract_year_from_text)

    if start_year is not None:
        df = df[df["listing_year"].isna() | (df["listing_year"] >= start_year)]
    if end_year is not None:
        df = df[df["listing_year"].isna() | (df["listing_year"] <= end_year)]

    df = df.sort_values(by=["listing_year", "event_name"], ascending=[False, True]).reset_index(drop=True)
    return df


# ---------------------------
# Output (UPSERT)
# ---------------------------

def upsert_to_csv(df_new: pd.DataFrame, out_path: str, verbose: bool = True) -> pd.DataFrame:
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    if os.path.exists(out_path):
        try:
            df_old = pd.read_csv(out_path)
        except Exception:
            df_old = pd.DataFrame()
    else:
        df_old = pd.DataFrame()

    if df_old.empty:
        merged = df_new.copy()
    else:
        all_cols = sorted(set(df_old.columns).union(set(df_new.columns)))
        df_old = df_old.reindex(columns=all_cols)
        df_new = df_new.reindex(columns=all_cols)

        merged = pd.concat([df_old, df_new], ignore_index=True)
        merged = merged.drop_duplicates(subset=["event_url"], keep="last").reset_index(drop=True)

    merged.to_csv(out_path, index=False)
    if verbose:
        print(f"[event_discovery] Upserted {len(df_new)} rows into CSV: {out_path} (total now {len(merged)})")
    return merged


def upsert_to_sqlite(df_new: pd.DataFrame, sqlite_path: str, table: str = "events", verbose: bool = True) -> None:
    import sqlite3

    os.makedirs(os.path.dirname(sqlite_path) or ".", exist_ok=True)
    conn = sqlite3.connect(sqlite_path)

    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table} (
            event_url TEXT PRIMARY KEY,
            event_name TEXT,
            competition_groups TEXT,
            listing_date_text TEXT,
            listing_year INTEGER
        )
        """
    )

    needed = ["event_url", "event_name", "competition_groups", "listing_date_text", "listing_year"]
    for c in needed:
        if c not in df_new.columns:
            df_new[c] = None

    rows = df_new[needed].to_records(index=False)
    conn.executemany(
        f"""
        INSERT OR REPLACE INTO {table}
        (event_url, event_name, competition_groups, listing_date_text, listing_year)
        VALUES (?, ?, ?, ?, ?)
        """,
        list(rows),
    )
    conn.commit()
    conn.close()
    if verbose:
        print(f"[event_discovery] Upserted {len(df_new)} rows into SQLite: {sqlite_path} (table {table})")


# ---------------------------
# CLI
# ---------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Discover PlayUSAU event URLs by crawling tournament calendar pagination.")
    p.add_argument(
        "--section",
        type=str,
        default="past",
        choices=["past", "upcoming", "both"],
        help="Which section to crawl/extract: past (default), upcoming, or both.",
    )
    p.add_argument(
        "--division",
        type=str,
        default="Club - Men",
        help='Division filter text (default: "Club - Men"). Ignored if --all-divisions is set.',
    )
    p.add_argument(
        "--all-divisions",
        action="store_true",
        help="Collect ALL events (recommended). If set, --division is ignored.",
    )
    p.add_argument("--start-year", type=int, default=0, help="Optional earliest year to include (best-effort).")
    p.add_argument("--end-year", type=int, default=0, help="Optional latest year to include (best-effort).")

    p.add_argument(
        "--pages",
        type=str,
        default="",
        help='Pages to crawl: "4" (page 4 only), "1-3", or "51+" (start at 51). Empty means unlimited from page 1.',
    )
    p.add_argument(
        "--first",
        type=int,
        default=0,
        help="Crawl the first N pages (pages 1..N). Overrides --pages if set.",
    )

    p.add_argument("--sleep", type=float, default=0.2, help="Sleep seconds between page requests.")
    p.add_argument("--out", type=str, default="", help="Output file path (CSV or SQLite). If omitted, nothing is written.")
    p.add_argument("--mode", type=str, default="csv", choices=["csv", "sqlite"], help="Write mode (csv or sqlite).")
    p.add_argument("--table", type=str, default="events", help='SQLite table name (default: "events").')

    p.add_argument("--checkpoint", type=str, default="", help="Checkpoint JSON path to save pager state for resume.")
    p.add_argument("--resume", action="store_true", help="Resume from checkpoint (requires --checkpoint).")

    p.add_argument("--quiet", action="store_true", help="Reduce console output.")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    verbose = not args.quiet

    start_year = args.start_year if args.start_year > 0 else None
    end_year = args.end_year if args.end_year > 0 else None

    if args.first and args.first > 0:
        start_page, end_page = 1, args.first
    else:
        start_page, end_page = parse_pages_spec(args.pages)

    checkpoint_path = args.checkpoint.strip() or None

    df_new = discover_events(
        division_filter=args.division,
        all_divisions=bool(args.all_divisions),
        start_year=start_year,
        end_year=end_year,
        start_page=start_page,
        end_page=end_page,
        sleep_seconds=args.sleep,
        verbose=verbose,
        checkpoint_path=checkpoint_path,
        resume=bool(args.resume),
        section=args.section,
    )

    if verbose:
        print(f"[event_discovery] Discovered {len(df_new)} matching events in this run.")
        if len(df_new) > 0:
            print(df_new.head(20).to_string(index=False))

    if args.out:
        if args.mode == "csv":
            upsert_to_csv(df_new, args.out, verbose=verbose)
        else:
            upsert_to_sqlite(df_new, args.out, table=args.table, verbose=verbose)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
