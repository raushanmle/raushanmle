#!/usr/bin/env python3
"""Generate reliable, local GitHub stats and inject into README.

- Builds a 52-week contributions heatmap to `assets/github-contributions.png`
- Computes summary metrics (last 12 months total, year total, streaks, best day)
- Injects a Markdown block between README markers:
    <!--START_SECTION:github-stats--> ... <!--END_SECTION:github-stats-->

Primary source: public contributions API (no token):
  https://github-contributions-api.jogruber.de/v4/<username>
Optional fallback (if env GITHUB_TOKEN is set): GitHub GraphQL User.contributionsCollection
"""

from __future__ import annotations

import datetime as dt
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import matplotlib.pyplot as plt
import numpy as np
import requests
from matplotlib.colors import BoundaryNorm, LinearSegmentedColormap

USERNAME = "raushanmle"
ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = ROOT / "assets"
IMAGE_PATH = ASSETS_DIR / "github-contributions.png"
README_PATH = ROOT / "README.md"
MARKER_START = "<!--START_SECTION:github-stats-->"
MARKER_END = "<!--END_SECTION:github-stats-->"

CONTRIB_API = f"https://github-contributions-api.jogruber.de/v4/{USERNAME}"
GQL_ENDPOINT = "https://api.github.com/graphql"


@dataclass
class ContributionMetrics:
    total_last_365: int
    total_year: int
    current_streak: int
    current_streak_range: Tuple[dt.date, dt.date] | None
    longest_streak: int
    longest_streak_range: Tuple[dt.date, dt.date] | None
    best_day_count: int
    best_day: dt.date | None


def fetch_contrib_api() -> Dict:
    r = requests.get(CONTRIB_API, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_graphql(today: dt.date) -> Dict | None:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return None

    since = (today - dt.timedelta(days=365)).isoformat()
    query = {
        "query": (
            "query($login:String!,$from:DateTime!){"
            "user(login:$login){"
            "contributionsCollection(from:$from){"
            "contributionCalendar{weeks{contributionDays{date contributionCount}}}"
            "}"
            "}"
            "}"
        ),
        "variables": {"login": USERNAME, "from": since + "T00:00:00Z"},
    }
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    r = requests.post(GQL_ENDPOINT, json=query, headers=headers, timeout=30)
    if r.status_code != 200:
        return None
    data = r.json()
    if "errors" in data:
        return None
    try:
        weeks = data["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
        entries = []
        for w in weeks:
            entries.extend(w["contributionDays"])
        # Build payload similar to public API shape
        contributions = [{"date": e["date"], "count": e["contributionCount"]} for e in entries]
        totals = {str(today.year): sum(e["contributionCount"] for e in entries if e["date"].startswith(str(today.year)))}
        return {"contributions": contributions, "total": totals}
    except Exception:
        return None


def prepare_dates(today: dt.date) -> Tuple[List[dt.date], dt.date, dt.date]:
    """Return an exact 52-week (364-day) window ending at `today`.

    We start from the last 365 days and crop the earliest days so the grid
    becomes divisible by 7 (no padding or future dates). This ensures the
    heatmap represents the strict last 12 months timeframe.
    """
    start = today - dt.timedelta(days=365 - 1)  # inclusive last 365 days
    total_days = (today - start).days + 1  # should be 365

    remainder = total_days % 7  # 365 % 7 == 1
    if remainder != 0:
        start = start + dt.timedelta(days=remainder)  # crop earliest days

    end = today
    total_days = (end - start).days + 1  # now 364 (52 weeks)
    dates = [start + dt.timedelta(days=i) for i in range(total_days)]
    return dates, start, end


def build_heatmap(contributions: Dict[str, int], today: dt.date, last_365_total: int) -> None:
    dates, start, end = prepare_dates(today)
    total_days = len(dates)

    weeks = total_days // 7
    data = np.zeros((7, weeks))
    for idx, day in enumerate(dates):
        week = idx // 7
        weekday = day.weekday()
        data[weekday, week] = contributions.get(day.isoformat(), 0)

    cmap = LinearSegmentedColormap.from_list(
        "github-green",
        ["#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39"],
    )
    bounds = [0, 1, 4, 8, 12, 100]
    norm = BoundaryNorm(bounds, cmap.N)

    fig_height = 3.2
    fig_width = max(weeks * 0.25, 12)
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))
    # Plot with weeks on X-axis and weekdays on Y-axis (no transpose)
    ax.pcolormesh(data, cmap=cmap, norm=norm, edgecolors="none")
    ax.set_xlim(0, weeks)
    ax.set_ylim(0, 7)

    ax.set_yticks([0.5, 2.5, 4.5, 6.5])
    ax.set_yticklabels(["Mon", "Wed", "Fri", "Sun"], fontsize=8)

    month_positions, month_labels, prev_month = [], [], None
    for week in range(weeks):
        week_start = start + dt.timedelta(days=week * 7)
        if week_start.month != prev_month:
            month_positions.append(week + 0.5)
            month_labels.append(week_start.strftime("%b"))
            prev_month = week_start.month

    ax.set_xticks(month_positions)
    ax.set_xticklabels(month_labels, fontsize=8)
    ax.tick_params(length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)

    ax.set_title(
        f"@{USERNAME} · Contributions past 52 weeks",
        fontsize=12,
        pad=12,
    )
    ax.set_xlabel(f"{start:%d %b %Y} → {end:%d %b %Y}", fontsize=8, labelpad=10)

    # Annotate total contributions in last 12 months on the figure
    fig.text(
        0.995,
        0.02,
        f"{last_365_total} contributions · last 12 months",
        ha="right",
        va="bottom",
        fontsize=9,
        color="#216e39",
    )

    plt.tight_layout()
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(IMAGE_PATH, dpi=160, bbox_inches="tight")
    plt.close(fig)


def compute_metrics(contributions: Dict[str, int], today: dt.date, totals: Dict[str, int]) -> ContributionMetrics:
    date_counts = {dt.date.fromisoformat(k): v for k, v in contributions.items()}
    sorted_dates = sorted(date_counts.keys())

    window_start = today - dt.timedelta(days=365)
    total_last_365 = sum(v for d, v in date_counts.items() if d >= window_start)
    total_year = totals.get(str(today.year), 0)

    longest = current = 0
    longest_range = current_range = None
    streak_start = None

    for day in sorted_dates:
        count = date_counts[day]
        if count > 0:
            if streak_start is None:
                streak_start = day
            current += 1
            if current > longest:
                longest = current
                longest_range = (streak_start, day)
        else:
            current = 0
            streak_start = None

    # Current streak
    current = 0
    current_end = None
    for day in sorted(sorted_dates, reverse=True):
        if day > today:
            continue
        count = date_counts[day]
        if count > 0:
            if current_end is None:
                current_end = day
            current += 1
        else:
            break
    current_range = (
        (current_end - dt.timedelta(days=current - 1), current_end) if current > 0 else None
    )

    best_day = None
    best_day_count = 0
    for day, count in date_counts.items():
        if count > best_day_count:
            best_day_count = count
            best_day = day

    return ContributionMetrics(
        total_last_365=total_last_365,
        total_year=total_year,
        current_streak=current,
        current_streak_range=current_range,
        longest_streak=longest,
        longest_streak_range=longest_range,
        best_day_count=best_day_count,
        best_day=best_day,
    )


def format_range(range_value: Tuple[dt.date, dt.date] | None, today: dt.date) -> str:
    if not range_value:
        return "—"
    start, end = range_value
    if end == today:
        return f"{start:%Y-%m-%d} → today"
    return f"{start:%Y-%m-%d} → {end:%Y-%m-%d}"


def build_markdown(metrics: ContributionMetrics, today: dt.date) -> str:
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    best_day_str = (
        f"{metrics.best_day_count} on {metrics.best_day:%Y-%m-%d}"
        if (metrics.best_day and metrics.best_day_count)
        else "—"
    )

    return (
        f"{MARKER_START}\n"
        f"<p align=\"center\">\n"
        f"  <img src=\"assets/github-contributions.png\" alt=\"GitHub contributions heatmap for {USERNAME}\" width=\"920\" />\n"
        f"</p>\n\n"
        f"<div align=\"center\">\n\n"
        f"| Metric | Value |\n"
        f"| --- | --- |\n"
        f"| Contributions (last 12 months) | {metrics.total_last_365} |\n"
        f"| Contributions ({today.year}) | {metrics.total_year} |\n"
        f"| Current streak | {metrics.current_streak} days ({format_range(metrics.current_streak_range, today)}) |\n"
        f"| Longest streak | {metrics.longest_streak} days ({format_range(metrics.longest_streak_range, today)}) |\n"
        f"| Best day | {best_day_str} |\n\n"
        f"</div>\n\n"
        f"<p align=\"center\"><sub>Last updated {timestamp} · Source: GitHub API</sub></p>\n"
        f"{MARKER_END}\n"
    )


def inject_markdown(snippet: str) -> None:
    text = README_PATH.read_text(encoding="utf-8")
    if MARKER_START not in text or MARKER_END not in text:
        raise RuntimeError("Markers for stats section not found in README.md")
    start_idx = text.index(MARKER_START)
    end_idx = text.index(MARKER_END) + len(MARKER_END)
    updated = text[:start_idx] + snippet + text[end_idx:]
    README_PATH.write_text(updated, encoding="utf-8")


def main() -> None:
    today = dt.date.today()

    payload = None
    try:
        payload = fetch_contrib_api()
    except Exception:
        # Try GraphQL fallback if token available
        payload = fetch_graphql(today)
        if payload is None:
            raise

    contributions = {e["date"]: e["count"] for e in payload["contributions"]}
    metrics = compute_metrics(contributions, today, payload.get("total", {}))
    build_heatmap(contributions, today, metrics.total_last_365)
    snippet = build_markdown(metrics, today)
    inject_markdown(snippet)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
