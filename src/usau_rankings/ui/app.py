from __future__ import annotations

from datetime import date
from urllib.parse import quote_plus

import pandas as pd
import streamlit as st

from usau_rankings.rating_engine import _ignored_blowouts, build_games_from_df, build_team_impact_rows, solve_ratings
from usau_rankings.ui.utils import load_games_data, normalize_team_name

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


def _rgba_impact(val: float, max_abs: float) -> str:
    """Soft, translucent red/green background without matplotlib."""
    if pd.isna(val) or max_abs <= 0:
        return ""
    x = float(val) / max_abs
    x = max(-1.0, min(1.0, x))

    # softness knob: lower base + moderate scaling = less "solid"
    alpha = 0.08 + 0.35 * abs(x)

    # green for positive, red for negative
    if x >= 0:
        return f"background-color: rgba(0, 160, 90, {alpha:.3f});"
    return f"background-color: rgba(220, 60, 60, {alpha:.3f});"


with st.sidebar:
    st.header("Data")
    data_path = st.text_input("games.json path", value="./data/games.json")

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
    selected_dates = st.date_input(
        "Date range",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date,
    )

    all_statuses = sorted(games["status"].dropna().astype(str).unique().tolist())
    final_default = [s for s in all_statuses if s.lower() == "final"] or all_statuses
    selected_statuses = st.multiselect("Status", options=all_statuses, default=final_default)

    team_query = st.text_input("Team search (matches team1 or team2)")

    max_point_diff = int(games["point_diff"].fillna(0).max()) if not games["point_diff"].isna().all() else 0
    point_diff_range = st.slider(
        "Point diff range",
        min_value=0,
        max_value=max(1, max_point_diff),
        value=(0, max(1, max_point_diff)),
    )
    close_games_only = st.checkbox("Close games only")
    close_games_n = st.number_input(
        "Close game threshold (<= N)",
        min_value=0,
        max_value=50,
        value=3,
        step=1,
    )

filtered = games.copy()
if selected_events:
    filtered = filtered[filtered["event"].isin(selected_events)]
if isinstance(selected_dates, tuple) and len(selected_dates) == 2:
    start_date, end_date = selected_dates
    filtered = filtered[
        filtered["game_date"].between(start_date, end_date, inclusive="both") | filtered["game_date"].isna()
    ]
if selected_statuses:
    filtered = filtered[filtered["status"].isin(selected_statuses)]
if team_query.strip():
    q = normalize_team_name(team_query)
    filtered = filtered[
        filtered["team1_key"].str.contains(q, na=False) | filtered["team2_key"].str.contains(q, na=False)
    ]
filtered = filtered[filtered["point_diff"].fillna(0).between(point_diff_range[0], point_diff_range[1])]
if close_games_only:
    filtered = filtered[filtered["point_diff"].fillna(10**6) <= close_games_n]

tab_games, tab_teams, tab_events, tab_rankings = st.tabs(["Games", "Teams", "Events", "Rankings"])

with tab_games:
    st.subheader("Games")
    st.caption(f"Showing {len(filtered):,} / {len(games):,} games")

    table = filtered[
        ["event", "game_date", "status", "team1", "score1", "team2", "score2", "point_diff", "event_game_id"]
    ].copy()
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

        link = (
            "https://play.usaultimate.org/teams/events/match_report/?EventGameId="
            f"{quote_plus(str(row['event_game_id']))}"
        )
        st.link_button("Open match report", link)

with tab_teams:
    st.subheader("Teams")
    all_teams = sorted(set(games["team1"].dropna().tolist()) | set(games["team2"].dropna().tolist()))
    selected_team = st.selectbox("Select team", options=all_teams)

    team_games = games[(games["team1"] == selected_team) | (games["team2"] == selected_team)].copy()
    team_finals = team_games[team_games["is_final"]].copy()

    is_team1 = team_finals["team1"] == selected_team
    team_finals["result"] = "L"
    team_finals.loc[
        (is_team1 & (team_finals["score1"] > team_finals["score2"]))
        | (~is_team1 & (team_finals["score2"] > team_finals["score1"])),
        "result",
    ] = "W"
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
    team_games_view["opponent"] = team_games_view.apply(
        lambda r: r["team2"] if r["team1"] == selected_team else r["team1"], axis=1
    )
    team_games_view["score"] = team_games_view.apply(_score_str, axis=1)
    team_games_view["result"] = team_games_view.apply(
        lambda r: "W"
        if r["winner"] == selected_team
        else ("L" if pd.notna(r["winner"]) and r["winner"] != "Tie" else "-"),
        axis=1,
    )
    st.dataframe(
        team_games_view[["game_date", "event", "opponent", "result", "score", "status", "event_game_id"]].sort_values(
            "game_date", ascending=False
        ),
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
        event_games[["game_date", "team1", "team2", "score", "status", "event_game_id"]].sort_values(
            "game_date", ascending=False
        ),
        hide_index=True,
        use_container_width=True,
    )

    st.markdown("#### Final-games summary")
    if event_finals.empty:
        st.info("No final games with scores for this event.")
    else:
        rows = []
        for _, r in event_finals.iterrows():
            rows.append(
                {
                    "team": r["team1"],
                    "w": int(r["score1"] > r["score2"]),
                    "l": int(r["score1"] < r["score2"]),
                    "pd": int(r["score1"] - r["score2"]),
                }
            )
            rows.append(
                {
                    "team": r["team2"],
                    "w": int(r["score2"] > r["score1"]),
                    "l": int(r["score2"] < r["score1"]),
                    "pd": int(r["score2"] - r["score1"]),
                }
            )
        standings = (
            pd.DataFrame(rows).groupby("team", as_index=False).sum().sort_values(["w", "pd"], ascending=[False, False])
        )
        st.dataframe(standings, hide_index=True, use_container_width=True)

with tab_rankings:
    st.subheader("Rankings")

    final_games = games[games["is_final"]].copy()
    if final_games.empty:
        st.info("No final games available to compute rankings.")
    else:
        game_dates = pd.to_datetime(final_games["game_date"], errors="coerce")
        season_start = game_dates.min()
        season_start = None if pd.isna(season_start) else season_start.date()
        season_end = game_dates.max()
        season_end = None if pd.isna(season_end) else season_end.date()

        games_list = build_games_from_df(final_games)
        if not games_list:
            st.info("No valid final non-tied games available to compute rankings.")
        else:
            ratings = solve_ratings(games_list, season_start=season_start, season_end=season_end)

            w_counts: dict[str, int] = {}
            l_counts: dict[str, int] = {}
            gp_counts: dict[str, int] = {}
            for game in games_list:
                if game.score_a > game.score_b:
                    winner, loser = game.team_a, game.team_b
                else:
                    winner, loser = game.team_b, game.team_a
                w_counts[winner] = w_counts.get(winner, 0) + 1
                l_counts[loser] = l_counts.get(loser, 0) + 1
                gp_counts[game.team_a] = gp_counts.get(game.team_a, 0) + 1
                gp_counts[game.team_b] = gp_counts.get(game.team_b, 0) + 1

            ranking_rows = []
            for team, rating in ratings.items():
                ranking_rows.append(
                    {
                        "team": team,
                        "rating": float(rating),
                        "W": int(w_counts.get(team, 0)),
                        "L": int(l_counts.get(team, 0)),
                        "games_played": int(gp_counts.get(team, 0)),
                    }
                )

            rankings_df = pd.DataFrame(ranking_rows).sort_values("rating", ascending=False).reset_index(drop=True)
            rankings_df.insert(2, "rank", rankings_df.index + 1)

            st.caption(f"Computed from {len(games_list):,} final non-tied games ({season_start} to {season_end}).")

            # Display-only rounding for main table
            rankings_main = rankings_df[["team", "rating", "rank", "W", "L", "games_played"]].copy()
            rankings_main["rating"] = rankings_main["rating"].map(lambda x: round(float(x), 2))
            st.dataframe(rankings_main, hide_index=True, use_container_width=True)

            st.markdown("#### Event Impact")

            team_options = sorted(rankings_df["team"].tolist())
            selected_team = st.selectbox("Select team for impact view", options=team_options)

            event_lookup: dict[tuple[date, str, str, int, int], str] = {}
            for row in final_games.itertuples(index=False):
                key = (row.game_date, str(row.team1), str(row.team2), int(row.score1), int(row.score2))
                event_lookup[key] = str(row.event)

            impact_rows = build_team_impact_rows(
                games_list,
                ratings,
                selected_team=selected_team,
                season_start=season_start,
                season_end=season_end,
                event_lookup=event_lookup,
            )
            impact_df = pd.DataFrame(impact_rows)
            if impact_df.empty:
                st.info("No impact games found for this team.")
            else:
                solver_team_rating = float(ratings.get(selected_team, 0.0))
                impact_df["solver_team_rating"] = solver_team_rating

                # Mark blowout-ignored games using the same rule as solve_ratings()
                ignored_idx = _ignored_blowouts(games_list, ratings, min_other_results=5)
                ignored_keys: set[tuple[date, str, str, str]] = set()
                for i in ignored_idx:
                    g = games_list[i]
                    s1, s2 = int(g.score_a), int(g.score_b)
                    # Include both orientations so we can match regardless of perspective
                    ignored_keys.add((g.date, g.team_a, g.team_b, f"{s1}-{s2}"))
                    ignored_keys.add((g.date, g.team_b, g.team_a, f"{s2}-{s1}"))

                impact_df["ignored_blowout"] = impact_df.apply(
                    lambda r: (r["game_date"], selected_team, r["opponent"], r["score"]) in ignored_keys,
                    axis=1,
                )

                # Zero out weight/contribution for ignored games so they produce no impact
                impact_df.loc[impact_df["ignored_blowout"], ["combined_weight", "weighted_contribution"]] = 0.0

                # Use a consistent baseline for leave-one-out math: weighted average of game_ratings
                total_w = float(impact_df["combined_weight"].sum())
                total_wc = float(impact_df["weighted_contribution"].sum())
                approx_team_rating = (total_wc / total_w) if total_w > 0 else solver_team_rating
                impact_df["team_rating"] = approx_team_rating

                # Approx marginal impact of each game on the team's final (weighted-average) rating:
                # impact_k = R_with_all - R_without_k
                def _loo_impact(row: pd.Series) -> float:
                    w = float(row["combined_weight"])
                    wc = float(row["weighted_contribution"])
                    denom = total_w - w
                    if denom <= 0:
                        return 0.0
                    rating_without = (total_wc - wc) / denom
                    return approx_team_rating - rating_without  # + means this game boosts rating

                impact_df["rating_impact"] = impact_df.apply(_loo_impact, axis=1)

                # Ensure ignored games show no impact
                impact_df.loc[impact_df["ignored_blowout"], ["rating_impact"]] = 0.0

                # rating_diff = game_rating - team_rating
                impact_df["rating_diff"] = impact_df["game_rating"] - impact_df["team_rating"]

                # NEW: blowout-ignored games should NOT count toward avg_game_rating / avg_rating_diff.
                # Set game_rating and rating_diff to missing so groupby mean skips them,
                # while still keeping the game for counts/weights/impact.
                impact_df.loc[impact_df["ignored_blowout"], ["game_rating", "rating_diff"]] = pd.NA
                impact_df["game_rating"] = pd.to_numeric(impact_df["game_rating"], errors="coerce")
                impact_df["rating_diff"] = pd.to_numeric(impact_df["rating_diff"], errors="coerce")

                # Shared scale so Event total_impact shading matches Game rating_impact shading
                max_abs_scale = float(impact_df["rating_impact"].abs().max() or 1.0)

                # ---- Event-level impact table (single table + selectbox filter) ----
                event_impact = (
                    impact_df.groupby("event", dropna=False)
                    .agg(
                        games=("event", "size"),
                        team_rating=("team_rating", "mean"),  # constant for team; mean is fine
                        avg_opp_rating=("opponent_rating", "mean"),
                        avg_game_rating=("game_rating", "mean"),
                        avg_rating_diff=("rating_diff", "mean"),
                        total_impact=("rating_impact", "sum"),
                        avg_impact=("rating_impact", "mean"),
                        sum_weight=("combined_weight", "sum"),
                    )
                    .reset_index()
                    .sort_values("total_impact", ascending=False)
                )

                # Round decimal fields to 2dp for display stability (incl sum_weight)
                for col in [
                    "team_rating",
                    "avg_opp_rating",
                    "avg_game_rating",
                    "avg_rating_diff",
                    "total_impact",
                    "avg_impact",
                    "sum_weight",
                ]:
                    if col in event_impact.columns:
                        event_impact[col] = pd.to_numeric(event_impact[col], errors="coerce").round(2)

                event_styled = (
                    event_impact[
                        [
                            "event",
                            "games",
                            "team_rating",
                            "avg_opp_rating",
                            "avg_game_rating",
                            "avg_rating_diff",
                            "total_impact",
                            "avg_impact",
                            "sum_weight",
                        ]
                    ]
                    .style.format(
                        {
                            "team_rating": "{:.2f}",
                            "avg_opp_rating": "{:.2f}",
                            "avg_game_rating": "{:.2f}",
                            "avg_rating_diff": "{:+.2f}",
                            "total_impact": "{:+.2f}",
                            "avg_impact": "{:+.2f}",
                            "sum_weight": "{:.2f}",
                        }
                    )
                    .applymap(lambda v: _rgba_impact(v, max_abs_scale), subset=["total_impact"])
                )

                st.dataframe(event_styled, hide_index=True, use_container_width=True)

                event_choices = ["(all)"] + event_impact["event"].astype(str).tolist()
                selected_event = st.selectbox("Filter games below to event (optional)", options=event_choices)

                # Filter the per-game table by selected event (if any)
                impact_df_display = impact_df.copy()
                if selected_event != "(all)":
                    impact_df_display = impact_df_display[impact_df_display["event"].astype(str) == selected_event]

                impact_df_display = impact_df_display.sort_values("game_date", ascending=False)

                st.markdown("#### Game Impact")

                display_cols = [
                    "game_date",
                    "event",
                    "opponent",
                    "score",
                    "result",
                    "team_rating",
                    "opponent_rating",
                    "game_rating",
                    "rating_diff",
                    "rating_impact",
                    "combined_weight",
                ]

                fmt = {
                    "team_rating": "{:.2f}",
                    "opponent_rating": "{:.2f}",
                    "game_rating": "{:.2f}",
                    "rating_diff": "{:+.2f}",
                    "rating_impact": "{:+.2f}",
                    "combined_weight": "{:.2f}",
                }

                # Use the shared scale so it matches the middle table
                max_abs = max_abs_scale

                styled = (
                    impact_df_display[display_cols]
                    .style.format(fmt)
                    .applymap(lambda v: _rgba_impact(v, max_abs), subset=["rating_impact"])
                )

                st.dataframe(styled, hide_index=True, use_container_width=True)