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

import logging
import os
import random
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EIA_BASE = "https://api.eia.gov/v2"
KEY_ENV  = "EIA_API_KEY"

# --- Caching ---------------------------------------------------------------
# Serve previously-fetched data for this long before calling EIA again. EIA
# daily spot data updates at most once per business day, so a multi-hour TTL
# removes nearly all redundant calls. Override with EIA_CACHE_TTL_SECONDS
# (set 0 to disable caching entirely).
CACHE_TTL_ENV = "EIA_CACHE_TTL_SECONDS"
DEFAULT_CACHE_TTL_SECONDS = 6 * 60 * 60  # 6 hours

# start_year -> {"ts": <monotonic>, "fetched_at": <iso>, "frames": {...}, "errors": {...}}
_CACHE: dict[int, dict] = {}

# --- Retry / backoff -------------------------------------------------------
# Transient EIA failures (rate limiting, brief 5xx, network blips) are retried
# with bounded exponential backoff. Non-retryable statuses (404, 401, ...) still
# fail fast via raise_for_status().
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 4
_BACKOFF_BASE_SECONDS = 1.0
_BACKOFF_CAP_SECONDS = 16.0

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

def _resolve_ttl(cache_ttl: Optional[int]) -> int:
    """TTL seconds: explicit arg > EIA_CACHE_TTL_SECONDS env > default. 0 disables."""
    if cache_ttl is not None:
        return max(0, int(cache_ttl))
    env = os.environ.get(CACHE_TTL_ENV, "").strip()
    if env.isdigit():
        return int(env)
    return DEFAULT_CACHE_TTL_SECONDS


def fetch_all_with_meta(
    api_key: Optional[str] = None,
    start_year: int = SEC_START_YEAR,
    force_refresh: bool = False,
    cache_ttl: Optional[int] = None,
) -> tuple[dict[str, pd.DataFrame], dict]:
    """
    Like fetch_all(), but also returns fetch metadata:
      {served_from_cache, fetched_at, cache_age_seconds, cache_ttl_seconds, warnings}

    Results are cached in-process per start_year for `cache_ttl` seconds
    (default from EIA_CACHE_TTL_SECONDS, else 6h). Pass force_refresh=True or
    cache_ttl=0 to bypass the cache and refetch.

    `warnings` maps any series that failed all retries to its error string;
    the fetch still succeeds as long as at least one series was retrieved.
    """
    ttl = _resolve_ttl(cache_ttl)
    now = time.monotonic()

    entry = _CACHE.get(start_year)
    if entry and not force_refresh and ttl > 0 and (now - entry["ts"]) < ttl:
        return entry["frames"], {
            "served_from_cache": True,
            "fetched_at": entry["fetched_at"],
            "cache_age_seconds": round(now - entry["ts"], 1),
            "cache_ttl_seconds": ttl,
            "warnings": entry["errors"],
        }

    key = _resolve_key(api_key)
    start = f"{start_year}-01-01"

    raw: dict[str, pd.Series] = {}
    errors: dict[str, str] = {}
    for name, spec in _SERIES.items():
        try:
            raw[name] = _fetch_daily_series(key, spec, start)
        except Exception as exc:
            errors[name] = str(exc)
            logger.warning("Could not fetch %s: %s", name, exc)

    if not raw:
        detail = "; ".join(f"{k}: {v}" for k, v in errors.items())
        raise RuntimeError(
            "No data retrieved from EIA. Check your API key and network."
            + (f" Details — {detail}" if detail else "")
        )

    frames = {
        "monthly_index": _build_monthly_index(raw),
        "first_day"    : _build_first_day(raw),
    }
    fetched_at = datetime.now(timezone.utc).isoformat()
    _CACHE[start_year] = {
        "ts": now,
        "fetched_at": fetched_at,
        "frames": frames,
        "errors": errors,
    }
    return frames, {
        "served_from_cache": False,
        "fetched_at": fetched_at,
        "cache_age_seconds": 0.0,
        "cache_ttl_seconds": ttl,
        "warnings": errors,
    }


def fetch_all(
    api_key: Optional[str] = None,
    start_year: int = SEC_START_YEAR,
    force_refresh: bool = False,
    cache_ttl: Optional[int] = None,
) -> dict[str, pd.DataFrame]:
    """
    Pull WTI, Henry Hub, and Mont Belvieu propane daily spot prices
    from EIA, then derive:
      - Monthly index prices (calendar-month averages + 12-mo rolling avg)
      - First-day-of-month prices (SEC input)
      - 12-month rolling average of first-day prices (SEC benchmark)

    Cached in-process per start_year (see fetch_all_with_meta / cache_ttl).

    Returns
    -------
    {
      "monthly_index": DataFrame,    # mirrors NSAI 'Monthly Index Prices'
      "first_day"    : DataFrame,    # mirrors NSAI 'First-Day-of-Month Prices'
    }
    """
    frames, _meta = fetch_all_with_meta(
        api_key=api_key,
        start_year=start_year,
        force_refresh=force_refresh,
        cache_ttl=cache_ttl,
    )
    return frames


# ---------------------------------------------------------------------------
# EIA API call
# ---------------------------------------------------------------------------

def _retry_after_seconds(resp: httpx.Response) -> Optional[float]:
    """Parse a numeric Retry-After header (seconds) if EIA sent one."""
    ra = resp.headers.get("Retry-After", "").strip()
    return float(ra) if ra.isdigit() else None


def _backoff_sleep(seconds: float) -> None:
    # Small jitter avoids the three series retrying in lockstep.
    time.sleep(seconds + random.uniform(0, 0.25))


def _get_with_retry(client: httpx.Client, url: str, params: dict) -> httpx.Response:
    """
    GET with bounded exponential backoff on transient failures (HTTP 429/5xx
    and network errors), honoring a numeric Retry-After header when present.

    Non-retryable responses (404, 401, ...) still raise immediately via
    raise_for_status(), preserving fail-fast behavior for those.
    """
    delay = _BACKOFF_BASE_SECONDS
    last_exc: Optional[Exception] = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = client.get(url, params=params)
        except httpx.TransportError as exc:  # timeouts, connection errors
            last_exc = exc
            if attempt == _MAX_ATTEMPTS:
                raise
            logger.warning("EIA request error (%s); retry %d/%d in %.1fs",
                           exc, attempt, _MAX_ATTEMPTS, delay)
            _backoff_sleep(delay)
            delay = min(delay * 2, _BACKOFF_CAP_SECONDS)
            continue

        if resp.status_code in _RETRYABLE_STATUS and attempt < _MAX_ATTEMPTS:
            wait = _retry_after_seconds(resp) or delay
            logger.warning("EIA HTTP %s; retry %d/%d in %.1fs",
                           resp.status_code, attempt, _MAX_ATTEMPTS, wait)
            _backoff_sleep(wait)
            delay = min(delay * 2, _BACKOFF_CAP_SECONDS)
            continue

        resp.raise_for_status()
        return resp

    # Retries exhausted on a transient network error.
    raise last_exc if last_exc else RuntimeError("EIA request failed after retries")


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
            resp = _get_with_retry(client, url, params)
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
