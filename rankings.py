"""Rankings hooks for future versions.

v1.1 plan:
- ``compute_rankings`` will produce team-level rankings and per-game impact outputs.
- For v1 this is intentionally a no-op placeholder that keeps app wiring stable.
"""

from __future__ import annotations

import pandas as pd


def compute_rankings(games_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Compute rankings and game impacts from normalized games.

    Args:
        games_df: Normalized games dataframe from ``utils.load_games_data``.

    Returns:
        Tuple of ``(teams_df, games_impacts_df)``.

    Raises:
        NotImplementedError: Ranking model not implemented yet.
    """
    raise NotImplementedError("Rankings are coming soon. Hook is in place for v1.1.")
