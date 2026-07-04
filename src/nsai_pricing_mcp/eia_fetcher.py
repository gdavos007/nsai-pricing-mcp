"""
eia_fetcher.py — Pull pricing data directly from the U.S. Energy
Information Administration (EIA) Open Data API.

NSAI does not produce this data. They republish EIA/Platts spot prices
with NSAI formatting. By going to EIA directly we eliminate every
manual step: no spreadsheet download, no scraping, no caching of Excel.

Free API key (takes 90 seconds):
  https://www.eia.gov/opendata/register.php

Set it once in your shell profile:
  export EIA_API_KEY="your_key_here"

Or pass it explicitly:
  NSAIPricingClient(eia_api_key="your_key_here")
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from typing import Optional

import httpx
import pandas as pd

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EIA_BASE = "https://api.eia.gov/v2"
KEY_ENV  = "EIA_API_KEY"

# EIA v2 routes and facets for each commodity.
# Verify / explore at: https://www.eia.gov/opendata/browser/
_SERIES = {
    "wti": {
        "route"      : "petroleum/pri/spt/data/",
        "facets"     : {"product": ["EPCWTI"], "duoarea": ["YCUOK"]},
        "label"      : "WTI Crude Oil ($/Bbl)",
        "description": "Cushing, OK WTI Spot Price FOB — daily (series RWTC)",
    },
    "henry_hub": {
        "route"      : "natural-gas/pri/fut/data/",
        "facets"     : {"series": ["RNGWHHD"]},   # Henry Hub daily spot
        "label"      : "Henry Hub ($/MMBtu)",
        "description": "Henry Hub Natural Gas Spot Price — daily (series RNGWHHD)",
    },
    "mb_propane": {
        "route"      : "petroleum/pri/spt/data/",
        "facets"     : {"product": ["EPLLPA"], "duoarea": ["Y44MB"]},
        "label"      : "Mont Belvieu Propane ($/Gal)",
        "description": "Mont Belvieu LPG/Propane Spot Price — daily",
    },
}

# SEC pricing has been required since Jan 1, 2010
SEC_START_YEAR = 2010


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def fetch_all(
    api_key: Optional[str] = None,
    start_year: int = SEC_START_YEAR,
) -> dict[str, pd.DataFrame]:
    """
    Pull WTI, Henry Hub, and Mont Belvieu propane daily spot prices
    from EIA, then derive:
      - Monthly index prices (calendar-month averages + 12-mo rolling avg)
      - First-day-of-month prices (SEC input)
      - 12-month rolling average of first-day prices (SEC benchmark)

    Returns
    -------
    {
      "monthly_index": DataFrame,    # mirrors NSAI 'Monthly Index Prices'
      "first_day"    : DataFrame,    # mirrors NSAI 'First-Day-of-Month Prices'
    }
    """
    key = _resolve_key(api_key)
    start = f"{start_year}-01-01"

    raw: dict[str, pd.Series] = {}
    for name, spec in _SERIES.items():
        try:
            raw[name] = _fetch_daily_series(key, spec, start)
        except Exception as exc:
            print(f"  Warning: could not fetch {name}: {exc}")

    if not raw:
        raise RuntimeError(
            "No data retrieved from EIA. Check your API key and network."
        )

    return {
        "monthly_index": _build_monthly_index(raw),
        "first_day"    : _build_first_day(raw),
    }


# ---------------------------------------------------------------------------
# EIA API call
# ---------------------------------------------------------------------------

def _fetch_daily_series(
    api_key: str,
    spec: dict,
    start: str,
) -> pd.Series:
    """Fetch a daily price series from EIA v2 and return as a pd.Series."""
    params: dict = {
        "api_key"             : api_key,
        "frequency"           : "daily",
        "data[0]"             : "value",
        "sort[0][column]"     : "period",
        "sort[0][direction]"  : "asc",
        "start"               : start,
        "length"              : 5000,        # EIA max per page
    }
    for k, values in spec.get("facets", {}).items():
        for i, v in enumerate(values):
            params[f"facets[{k}][]"] = v    # EIA bracket notation

    url = f"{EIA_BASE}/{spec['route']}"
    records: list[dict] = []
    offset = 0

    with httpx.Client(timeout=30) as client:
        while True:
            params["offset"] = offset
            resp = client.get(url, params=params)
            resp.raise_for_status()
            body = resp.json()

            page = body.get("response", {}).get("data", [])
            if not page:
                break
            records.extend(page)

            # Paginate if EIA returns exactly 5000 records
            if len(page) < 5000:
                break
            offset += 5000

    if not records:
        raise ValueError(f"EIA returned no records for {spec['label']}")

    df = pd.DataFrame(records)
    df["period"] = pd.to_datetime(df["period"], errors="coerce")
    df = df.dropna(subset=["period", "value"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["value"])
    df = df.set_index("period").sort_index()

    return df["value"].rename(spec["label"])


# ---------------------------------------------------------------------------
# Derived datasets
# ---------------------------------------------------------------------------

def _build_monthly_index(raw: dict[str, pd.Series]) -> pd.DataFrame:
    """
    Build the 'Monthly Index Prices' equivalent.

    For each commodity:
      - Monthly average of daily spot prices
      - 12-month rolling average of those monthly averages
    """
    frames: dict[str, pd.Series] = {}

    for name, series in raw.items():
        label   = _SERIES[name]["label"]
        monthly = series.resample("MS").mean().round(4)   # MS = month start
        rolling = monthly.rolling(window=12, min_periods=12).mean().round(4)

        frames[label]                         = monthly
        frames[f"{label} — 12-Mo Rolling Avg"] = rolling

    df = pd.DataFrame(frames)
    df.index.name = "Date"
    return df.dropna(how="all")


def _build_first_day(raw: dict[str, pd.Series]) -> pd.DataFrame:
    """
    Build the 'First-Day-of-Month Prices' equivalent.

    For each commodity:
      - Price on the first trading day of each month
        (first available price on or after the 1st calendar day)
      - 12-month arithmetic average of those first-day prices
        = the SEC benchmark price per Rule 4-10(a)
    """
    frames: dict[str, pd.Series] = {}

    for name, series in raw.items():
        label   = _SERIES[name]["label"]
        fdm     = _extract_first_day_of_month(series)
        sec_avg = fdm.rolling(window=12, min_periods=12).mean().round(4)

        frames[f"{label} — FDM"]          = fdm
        frames[f"{label} — SEC 12-Mo Avg"] = sec_avg

    df = pd.DataFrame(frames)
    df.index.name = "Date"

    # Add a 'Reporting Period' label for December rows (fiscal year-end)
    df["Reporting Period"] = df.index.map(
        lambda d: f"Dec {d.year}" if d.month == 12 else ""
    )

    return df.dropna(how="all")


def _extract_first_day_of_month(series: pd.Series) -> pd.Series:
    """
    For each calendar month, return the price on the first available
    trading day (i.e., the first day EIA reports a price on or after
    the 1st of the month).

    This matches the SEC's definition: "the price that the registrant
    could have received on the first day of each month."
    """
    result: dict[date, float] = {}
    idx = series.index

    # Generate a monthly range covering the series
    months = pd.date_range(
        start=idx.min().replace(day=1),
        end=idx.max().replace(day=1),
        freq="MS",
    )

    for month_start in months:
        month_end = (month_start + pd.offsets.MonthEnd(0))
        window = series.loc[
            (idx >= month_start) & (idx <= month_end)
        ]
        if not window.empty:
            result[month_start] = round(float(window.iloc[0]), 4)

    s = pd.Series(result, name=series.name)
    s.index = pd.DatetimeIndex(s.index)
    s.index.name = "Date"
    return s


# ---------------------------------------------------------------------------
# Key resolution
# ---------------------------------------------------------------------------

def _resolve_key(api_key: Optional[str]) -> str:
    key = api_key or os.environ.get(KEY_ENV, "")
    # Defensively strip whitespace and surrounding quotes — a common mistake is
    # pasting the value with the quotes from a .env line (EIA_API_KEY="...") or a
    # stray space into a hosting dashboard, which EIA then rejects.
    key = key.strip().strip("\"'").strip()
    if not key:
        raise EnvironmentError(
            "EIA API key not found.\n\n"
            "Get your free key (90 seconds):\n"
            "  https://www.eia.gov/opendata/register.php\n\n"
            "Then set it once:\n"
            "  export EIA_API_KEY='your_key_here'   # add to ~/.zshrc\n\n"
            "Or pass it directly:\n"
            "  NSAIPricingClient(eia_api_key='your_key_here')\n"
            "  fetch_latest_pricing_data(eia_api_key='your_key_here')"
        )
    return key
