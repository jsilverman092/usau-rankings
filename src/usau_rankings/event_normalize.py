from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd

_GROUP_RE = re.compile(r"^\s*(?P<name>.*?)\s*(?:\[(?P<count>\d+)\])?\s*$")

_SERIES_KEYWORDS = [
    "sectionals",
    "sectional championship",
    "regionals",
    "regional championship",
    "nationals",
    "national championship",
]

# Explicit overrides for app logic
FORCE_SERIES_EVENTS = {
    "2025 South New England Men's Sectional Champonship",
    # add more exact matches here
}

FORCE_NON_SERIES_EVENTS = {
    # if something ever gets incorrectly flagged as series
}


def split_competition_groups(s: str) -> list[str]:
    if not isinstance(s, str) or not s.strip():
        return []
    return [part.strip() for part in s.split("|") if part.strip()]


def parse_group_token(token: str) -> tuple[str, Optional[int], Optional[str], Optional[str]]:
    m = _GROUP_RE.match(token or "")
    if not m:
        cleaned = (token or "").strip()
        return cleaned, None, None, None

    group_name = (m.group("name") or "").strip()
    team_count = int(m.group("count")) if m.group("count") else None

    division = None
    gender_raw = None
    if " - " in group_name:
        division, gender_raw = [p.strip() for p in group_name.split(" - ", 1)]

    return group_name, team_count, division, gender_raw


def normalize_gender(gender_raw: object) -> Optional[str]:
    if not isinstance(gender_raw, str):
        return None

    gl = gender_raw.strip().lower()
    if gl.startswith("men"):
        return "Men"
    if gl.startswith("women"):
        return "Women"
    if gl.startswith("mixed"):
        return "Mixed"
    return None


def parse_listing_dates(text: str) -> tuple[Optional[date], Optional[date]]:
    if not isinstance(text, str):
        return None, None

    t = text.strip()

    if " - " in t:
        left, right = [x.strip() for x in t.split(" - ", 1)]
        try:
            start = pd.to_datetime(left, errors="raise").date()
            end = pd.to_datetime(right, errors="raise").date()
            return start, end
        except Exception:
            return None, None

    try:
        d = pd.to_datetime(t, errors="raise").date()
        return d, d
    except Exception:
        return None, None


def is_cancelled_event_name(event_name: object) -> bool:
    if not isinstance(event_name, str):
        return False
    name = event_name.strip().lower()
    return (
        name.endswith("-cancelled")
        or name.endswith(" -cancelled")
        or name.endswith("- cancelled")
    )


def is_series_event(event_name: object) -> bool:
    if not isinstance(event_name, str):
        return False

    # Hard overrides first
    if event_name in FORCE_SERIES_EVENTS:
        return True
    if event_name in FORCE_NON_SERIES_EVENTS:
        return False

    name_lower = event_name.lower()
    return any(keyword in name_lower for keyword in _SERIES_KEYWORDS)


def build_schedule_url(
    event_url: object,
    *,
    division: Optional[str],
    gender: Optional[str],
    competition_group: Optional[str],
) -> Optional[str]:
    """
    Build schedule URL with division-specific rules:

    College:
        "College - Men"  → CollegeMen

    Club:
        "Club - Men"     → Club-Men

    Final format:
        <event_url>/schedule/{gender_lower}/{group_path}/
    """
    if not isinstance(event_url, str) or not event_url.strip():
        return None
    if division not in {"College", "Club"}:
        return None
    if gender not in {"Men", "Women", "Mixed"}:
        return None
    if not isinstance(competition_group, str):
        return None

    if division == "College":
        group_path = competition_group.replace(" ", "").replace("-", "")
    else:  # Club
        group_path = competition_group.replace(" ", "")

    base = event_url.rstrip("/")
    return f"{base}/schedule/{gender.lower()}/{group_path}/"


def normalize_events_df(df_raw: pd.DataFrame) -> pd.DataFrame:
    required = {
        "competition_groups",
        "event_name",
        "event_url",
        "listing_date_text",
        "listing_year",
    }
    missing = required - set(df_raw.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")

    rows: list[dict] = []

    for _, r in df_raw.iterrows():
        event_name = r.get("event_name")
        event_url = r.get("event_url")

        cancelled = is_cancelled_event_name(event_name)
        series_event = is_series_event(event_name)

        groups = split_competition_groups(r.get("competition_groups", ""))
        if not groups:
            groups = [None]

        start_date, end_date = parse_listing_dates(r.get("listing_date_text", ""))

        listing_year = r.get("listing_year", None)
        try:
            listing_year_int = int(listing_year) if pd.notna(listing_year) else None
        except Exception:
            listing_year_int = None

        for token in groups:
            group_name, team_count, division, gender_raw = parse_group_token(token or "")
            gender = normalize_gender(gender_raw)

            schedule_url = build_schedule_url(
                event_url,
                division=division,
                gender=gender,
                competition_group=group_name,
            )

            rows.append(
                {
                    "event_url": event_url,
                    "event_name": event_name,
                    "cancelled": cancelled,
                    "series_event": series_event,
                    "listing_date_text": r.get("listing_date_text"),
                    "start_date": start_date,
                    "end_date": end_date,
                    "listing_year": listing_year_int,
                    "competition_group_raw": token,
                    "competition_group": group_name if token is not None else None,
                    "team_count": team_count,
                    "division": division,
                    "gender_raw": gender_raw,
                    "gender": gender,
                    "schedule_url": schedule_url,
                }
            )

    out = pd.DataFrame(rows)

    out["listing_year"] = out["listing_year"].astype("Int64")
    out["team_count"] = out["team_count"].astype("Int64")
    out["cancelled"] = out["cancelled"].astype(bool)
    out["series_event"] = out["series_event"].astype(bool)

    return out


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Normalize USAU events CSV.")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/events.csv"),
        help="Path to raw events CSV",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/events_normalized.parquet"),
        help="Path to output normalized file (.parquet or .csv)",
    )

    args = parser.parse_args()

    print(f"[event_normalize] Reading {args.input}")
    df_raw = pd.read_csv(args.input)

    print("[event_normalize] Normalizing…")
    df_norm = normalize_events_df(df_raw)

    missing_sched = int(df_norm["schedule_url"].isna().sum())
    print(f"[event_normalize] schedule_url populated for {len(df_norm) - missing_sched} rows.")

    print(f"[event_normalize] Writing {args.output} ({len(df_norm)} rows)")
    if args.output.suffix.lower() == ".csv":
        df_norm.to_csv(args.output, index=False)
    else:
        df_norm.to_parquet(args.output, index=False)

    print("[event_normalize] Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
