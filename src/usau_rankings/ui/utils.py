"""Utilities for loading and normalizing USAU games data."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

import pandas as pd


RE_WHITESPACE = re.compile(r"\s+")


EXPECTED_COLUMNS = [
    "event",
    "event_game_id",
    "game_date",
    "team1",
    "team2",
    "score1",
    "score2",
    "status",
]


def normalize_team_name(team: Optional[str], lowercase: bool = True) -> str:
    """Normalize team names for easier searching."""
    if team is None:
        return ""
    normalized = RE_WHITESPACE.sub(" ", str(team)).strip()
    return normalized.lower() if lowercase else normalized


def load_games_data(path: str | Path, lowercase_team_keys: bool = True) -> pd.DataFrame:
    """Load and normalize games from a JSON list file.

    Args:
        path: Path to the ``games.json`` file.
        lowercase_team_keys: If true, team search keys are lowercased.

    Returns:
        A normalized dataframe ready for UI use.
    """
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        records = json.load(f)

    df = pd.DataFrame(records)

    for col in EXPECTED_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA

    # Ensure unique primary key row behavior for upserted data.
    if "event_game_id" in df.columns:
        df = df.drop_duplicates(subset=["event_game_id"], keep="last")

    df["event"] = df["event"].fillna("Unknown Event")
    df["team1"] = df["team1"].fillna("Unknown Team")
    df["team2"] = df["team2"].fillna("Unknown Team")
    df["status"] = df["status"].fillna("Unknown")

    df["game_date"] = pd.to_datetime(df["game_date"], errors="coerce").dt.date
    df["score1"] = pd.to_numeric(df["score1"], errors="coerce")
    df["score2"] = pd.to_numeric(df["score2"], errors="coerce")

    final_mask = df["status"].astype(str).str.lower().eq("final")
    has_scores = df["score1"].notna() & df["score2"].notna()
    df["is_final"] = final_mask & has_scores

    diff = df["score1"] - df["score2"]
    df["point_diff"] = diff.abs()
    df["total_points"] = df["score1"] + df["score2"]

    team1_won = df["is_final"] & diff.gt(0)
    team2_won = df["is_final"] & diff.lt(0)
    tied = df["is_final"] & diff.eq(0)

    df["winner"] = pd.NA
    df.loc[team1_won, "winner"] = df.loc[team1_won, "team1"]
    df.loc[team2_won, "winner"] = df.loc[team2_won, "team2"]
    df.loc[tied, "winner"] = "Tie"

    df["loser"] = pd.NA
    df.loc[team1_won, "loser"] = df.loc[team1_won, "team2"]
    df.loc[team2_won, "loser"] = df.loc[team2_won, "team1"]
    df.loc[tied, "loser"] = "Tie"

    df["team1_key"] = df["team1"].map(lambda t: normalize_team_name(t, lowercase=lowercase_team_keys))
    df["team2_key"] = df["team2"].map(lambda t: normalize_team_name(t, lowercase=lowercase_team_keys))

    return df.reset_index(drop=True)
