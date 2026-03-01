from __future__ import annotations

from datetime import date
from urllib.parse import quote_plus

import pandas as pd
import streamlit as st

from utils import load_games_data, normalize_team_name

st.set_page_config(page_title="USAU Match Explorer", layout="wide")
st.title("USAU Match Report Explorer")


@st.cache_data(show_spinner=False)
def _load_data(path: str) -> pd.DataFrame:
    return load_games_data(path)


def _safe_date_bounds(df: pd.DataFrame) -> tuple[date, date]:
    valid_dates = df["game_date"].dropna()
    if valid_dates.empty:
        today = date.today()
        return today, today
    return valid_dates.min(), valid_dates.max()


def _score_str(row: pd.Series) -> str:
    if pd.notna(row["score1"]) and pd.notna(row["score2"]):
        return f"{int(row['score1'])}-{int(row['score2'])}"
    return "N/A"


with st.sidebar:
    st.header("Data")
    data_path = st.text_input("games.json path", value="./games.json")

try:
    games = _load_data(data_path)
except Exception as exc:  # noqa: BLE001
    st.error(f"Failed to load data: {exc}")
    st.stop()

if games.empty:
    st.warning("No games found in file.")
    st.stop()


# Shared filters
with st.sidebar:
    st.header("Filters")
    all_events = sorted(games["event"].dropna().astype(str).unique().tolist())
    selected_events = st.multiselect("Event", options=all_events, default=all_events)

    min_date, max_date = _safe_date_bounds(games)
    selected_dates = st.date_input("Date range", value=(min_date, max_date), min_value=min_date, max_value=max_date)

    all_statuses = sorted(games["status"].dropna().astype(str).unique().tolist())
    final_default = [s for s in all_statuses if s.lower() == "final"] or all_statuses
    selected_statuses = st.multiselect("Status", options=all_statuses, default=final_default)

    team_query = st.text_input("Team search (matches team1 or team2)")

    max_point_diff = int(games["point_diff"].fillna(0).max()) if not games["point_diff"].isna().all() else 0
    point_diff_range = st.slider("Point diff range", min_value=0, max_value=max(1, max_point_diff), value=(0, max(1, max_point_diff)))
    close_games_only = st.checkbox("Close games only")
    close_games_n = st.number_input("Close game threshold (<= N)", min_value=0, max_value=50, value=3, step=1)

filtered = games.copy()
if selected_events:
    filtered = filtered[filtered["event"].isin(selected_events)]
if isinstance(selected_dates, tuple) and len(selected_dates) == 2:
    start_date, end_date = selected_dates
    filtered = filtered[filtered["game_date"].between(start_date, end_date, inclusive="both") | filtered["game_date"].isna()]
if selected_statuses:
    filtered = filtered[filtered["status"].isin(selected_statuses)]
if team_query.strip():
    q = normalize_team_name(team_query)
    filtered = filtered[filtered["team1_key"].str.contains(q, na=False) | filtered["team2_key"].str.contains(q, na=False)]
filtered = filtered[filtered["point_diff"].fillna(0).between(point_diff_range[0], point_diff_range[1])]
if close_games_only:
    filtered = filtered[filtered["point_diff"].fillna(10**6) <= close_games_n]


tab_games, tab_teams, tab_events, tab_rankings = st.tabs(["Games", "Teams", "Events", "Rankings (coming soon)"])

with tab_games:
    st.subheader("Games")
    st.caption(f"Showing {len(filtered):,} / {len(games):,} games")

    table = filtered[["event", "game_date", "status", "team1", "score1", "team2", "score2", "point_diff", "event_game_id"]].copy()
    table.insert(0, "select", False)

    edited = st.data_editor(
        table,
        hide_index=True,
        use_container_width=True,
        disabled=[c for c in table.columns if c != "select"],
        column_config={
            "select": st.column_config.CheckboxColumn("Details", help="Select one row to view details"),
            "game_date": st.column_config.DateColumn("game_date"),
        },
        key="games_editor",
    )

    selected_rows = edited[edited["select"]]
    if not selected_rows.empty:
        picked_id = selected_rows.iloc[0]["event_game_id"]
        row = filtered[filtered["event_game_id"] == picked_id].iloc[0]
        st.markdown("### Game details")
        c1, c2 = st.columns(2)
        with c1:
            st.write(f"**Event:** {row['event']}")
            st.write(f"**Date:** {row['game_date']}")
            st.write(f"**Status:** {row['status']}")
            st.write(f"**Event game ID:** `{row['event_game_id']}`")
        with c2:
            st.write(f"**Teams:** {row['team1']} vs {row['team2']}")
            st.write(f"**Score:** {_score_str(row)}")
            if pd.notna(row["winner"]):
                st.write(f"**Winner:** {row['winner']}")

        link = f"https://play.usaultimate.org/teams/events/match_report/?EventGameId={quote_plus(str(row['event_game_id']))}"
        st.link_button("Open match report", link)

with tab_teams:
    st.subheader("Teams")
    all_teams = sorted(set(games["team1"].dropna().tolist()) | set(games["team2"].dropna().tolist()))
    selected_team = st.selectbox("Select team", options=all_teams)

    team_games = games[(games["team1"] == selected_team) | (games["team2"] == selected_team)].copy()
    team_finals = team_games[team_games["is_final"]].copy()

    is_team1 = team_finals["team1"] == selected_team
    team_finals["result"] = "L"
    team_finals.loc[(is_team1 & (team_finals["score1"] > team_finals["score2"])) | (~is_team1 & (team_finals["score2"] > team_finals["score1"])), "result"] = "W"
    team_finals.loc[team_finals["score1"] == team_finals["score2"], "result"] = "T"

    team_finals["team_point_diff"] = team_finals.apply(
        lambda r: (r["score1"] - r["score2"]) if r["team1"] == selected_team else (r["score2"] - r["score1"]),
        axis=1,
    )

    wins = int((team_finals["result"] == "W").sum())
    losses = int((team_finals["result"] == "L").sum())
    avg_pd = float(team_finals["team_point_diff"].mean()) if not team_finals.empty else 0.0
    total_pd = float(team_finals["team_point_diff"].sum()) if not team_finals.empty else 0.0

    m1, m2, m3 = st.columns(3)
    m1.metric("W / L", f"{wins} / {losses}")
    m2.metric("Avg point diff", f"{avg_pd:.2f}")
    m3.metric("Total point diff", f"{total_pd:.0f}")

    team_games_view = team_games.copy()
    team_games_view["opponent"] = team_games_view.apply(lambda r: r["team2"] if r["team1"] == selected_team else r["team1"], axis=1)
    team_games_view["score"] = team_games_view.apply(_score_str, axis=1)
    team_games_view["result"] = team_games_view.apply(
        lambda r: "W" if r["winner"] == selected_team else ("L" if pd.notna(r["winner"]) and r["winner"] != "Tie" else "-"),
        axis=1,
    )
    st.dataframe(
        team_games_view[["game_date", "event", "opponent", "result", "score", "status", "event_game_id"]].sort_values("game_date", ascending=False),
        hide_index=True,
        use_container_width=True,
    )

with tab_events:
    st.subheader("Events")
    selected_event = st.selectbox("Select event", options=all_events)
    event_games = games[games["event"] == selected_event].copy()
    event_finals = event_games[event_games["is_final"]].copy()

    st.markdown("#### Games")
    event_games["score"] = event_games.apply(_score_str, axis=1)
    st.dataframe(
        event_games[["game_date", "team1", "team2", "score", "status", "event_game_id"]].sort_values("game_date", ascending=False),
        hide_index=True,
        use_container_width=True,
    )

    st.markdown("#### Final-games summary")
    if event_finals.empty:
        st.info("No final games with scores for this event.")
    else:
        rows = []
        for _, r in event_finals.iterrows():
            rows.append({"team": r["team1"], "w": int(r["score1"] > r["score2"]), "l": int(r["score1"] < r["score2"]), "pd": int(r["score1"] - r["score2"])})
            rows.append({"team": r["team2"], "w": int(r["score2"] > r["score1"]), "l": int(r["score2"] < r["score1"]), "pd": int(r["score2"] - r["score1"])})
        standings = pd.DataFrame(rows).groupby("team", as_index=False).sum().sort_values(["w", "pd"], ascending=[False, False])
        st.dataframe(standings, hide_index=True, use_container_width=True)

with tab_rankings:
    st.subheader("Rankings (coming soon)")
    try:
        from rankings import compute_rankings

        teams_df, impacts_df = compute_rankings(games)
        st.success("Ranking outputs loaded.")
        st.dataframe(teams_df, use_container_width=True)
        st.dataframe(impacts_df, use_container_width=True)
    except NotImplementedError as exc:
        st.info(str(exc))
    except Exception as exc:  # noqa: BLE001
        st.warning(f"Rankings hook is wired, but unavailable right now: {exc}")
