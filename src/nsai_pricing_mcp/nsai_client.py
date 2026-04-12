"""
nsai_client.py — Pandas client for NSAI-equivalent oil & gas pricing data.

Data flows
----------
  Primary (automated):
    NSAIPricingClient().fetch()
    → pulls WTI, Henry Hub, Mont Belvieu from EIA Open Data API
    → derives monthly index prices + first-day-of-month SEC prices
    → returns proper pandas DataFrames, no files touched

  Fallback (if user has the NSAI Excel locally):
    NSAIPricingClient().load("/path/to/NSAI-Pricing-Data.xlsx")
    → same DataFrames, same interface

Either way the RE writes identical analysis code.

Workflow
--------
    from nsai_pricing_mcp.nsai_client import NSAIPricingClient

    client = NSAIPricingClient()
    client.fetch()                   # automated — no downloads needed

    df_monthly = client.monthly_index_prices()
    df_sec     = client.sec_benchmark_prices()
    wti        = client.wti_series()
    hh         = client.henry_hub_series()
    sec        = client.current_sec_price()
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from .eia_fetcher import fetch_all as _eia_fetch_all
from .parser import parse_nsai_spreadsheet


class NSAIPricingClient:
    """
    Unified interface to NSAI-equivalent pricing data.
    Identical API whether data comes from EIA (automated) or a local Excel.
    """

    def __init__(self, eia_api_key: Optional[str] = None):
        """
        Parameters
        ----------
        eia_api_key : str, optional
            EIA API key. If None, reads EIA_API_KEY env var.
            Free key: https://www.eia.gov/opendata/register.php
        """
        self._eia_api_key = eia_api_key
        self._frames: dict[str, pd.DataFrame] | None = None
        self._source: str | None = None

    # ------------------------------------------------------------------
    # Loading — two paths, identical output
    # ------------------------------------------------------------------

    def fetch(self, start_year: int = 2010) -> "NSAIPricingClient":
        """
        Pull live data from the EIA Open Data API.
        No files. No downloads. Always current.

        Returns self for chaining.
        """
        print("Fetching from EIA Open Data API...")
        self._frames = _eia_fetch_all(
            api_key=self._eia_api_key,
            start_year=start_year,
        )
        self._source = "EIA Open Data API"
        self._print_load_summary()
        return self

    def load(self, path: str | Path) -> "NSAIPricingClient":
        """
        Load from a locally saved NSAI Excel file.
        Fallback only — prefer fetch() for live data.

        Returns self for chaining.
        """
        path = Path(path).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        raw = parse_nsai_spreadsheet(path)
        self._frames = self._normalize_excel_sheets(raw)
        self._source = str(path)
        self._print_load_summary()
        return self

    def _require_loaded(self):
        if self._frames is None:
            raise RuntimeError(
                "No data loaded.\n"
                "  client.fetch()            # pull live from EIA (recommended)\n"
                "  client.load('file.xlsx')  # load a local NSAI Excel file"
            )

    def _print_load_summary(self):
        print(f"Source: {self._source}")
        for name, df in self._frames.items():
            print(f"  '{name}': {len(df)} rows, {len(df.columns)} columns")

    # ------------------------------------------------------------------
    # Core DataFrames
    # ------------------------------------------------------------------

    def monthly_index_prices(self) -> pd.DataFrame:
        """
        Monthly spot prices and 12-month rolling averages.
        Columns: WTI ($/Bbl), WTI 12-Mo Rolling Avg,
                 Henry Hub ($/MMBtu), HH 12-Mo Rolling Avg,
                 Mont Belvieu Propane ($/Gal), MB 12-Mo Rolling Avg
        """
        self._require_loaded()
        return self._frames["monthly_index"].copy()

    def sec_benchmark_prices(self, year: Optional[int] = None) -> pd.DataFrame:
        """
        First-day-of-month prices and 12-month SEC arithmetic averages.
        The December row of each year = the SEC benchmark for that FY.

        Parameters
        ----------
        year : int, optional  Filter to a single fiscal year (e.g., 2024).
        """
        self._require_loaded()
        df = self._frames["first_day"].copy()
        if year is not None:
            df = df[df.index.year == year]
        return df

    # ------------------------------------------------------------------
    # Convenience Series
    # ------------------------------------------------------------------

    def wti_series(self, price_type: str = "spot") -> pd.Series:
        """WTI crude oil price series.
        price_type: 'spot' | 'fdm' | 'sec_avg'
        """
        return self._get_series("wti", price_type)

    def henry_hub_series(self, price_type: str = "spot") -> pd.Series:
        """Henry Hub natural gas price series.
        price_type: 'spot' | 'fdm' | 'sec_avg'
        """
        return self._get_series("henry_hub", price_type)

    def ngl_series(self, price_type: str = "spot") -> pd.Series:
        """Mont Belvieu propane price series.
        price_type: 'spot' | 'fdm' | 'sec_avg'
        """
        return self._get_series("mb_propane", price_type)

    def current_sec_price(self) -> dict:
        """
        Most recent SEC benchmark prices as a plain dict.
        {'date': Timestamp, 'wti_sec_avg_bbl': float, 'hh_sec_avg_mmbtu': float}
        """
        self._require_loaded()
        df = self._frames["first_day"].dropna(how="all")
        last = df.iloc[-1]
        result = {"date": last.name}
        for col in df.columns:
            c = col.lower()
            if "sec" in c and "avg" in c:
                if "wti" in c or "crude" in c or "oil" in c:
                    result["wti_sec_avg_bbl"] = last[col]
                elif "hh" in c or "henry" in c or "gas" in c:
                    result["hh_sec_avg_mmbtu"] = last[col]
        return result

    def all_data(self) -> dict[str, pd.DataFrame]:
        self._require_loaded()
        return {k: v.copy() for k, v in self._frames.items()}

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def to_csv(self, directory: str | Path = ".") -> dict[str, Path]:
        self._require_loaded()
        out = {}
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        for name, df in self._frames.items():
            path = directory / f"NSAI_{name}.csv"
            df.to_csv(path)
            out[name] = path
            print(f"  Written: {path}")
        return out

    def to_excel(self, path: str | Path = "NSAI_pricing_export.xlsx") -> Path:
        self._require_loaded()
        path = Path(path)
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            for name, df in self._frames.items():
                df.to_excel(writer, sheet_name=name[:31])
        print(f"Written: {path}")
        return path

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _get_series(self, commodity: str, price_type: str) -> pd.Series:
        self._require_loaded()
        label_map  = {
            "wti"       : "WTI Crude Oil ($/Bbl)",
            "henry_hub" : "Henry Hub ($/MMBtu)",
            "mb_propane": "Mont Belvieu Propane ($/Gal)",
        }
        suffix_map = {"spot": "", "fdm": "— FDM", "sec_avg": "— SEC 12-Mo Avg"}

        base  = label_map[commodity]
        sfx   = suffix_map[price_type]
        frame = "monthly_index" if price_type == "spot" else "first_day"
        df    = self._frames[frame]

        for col in df.columns:
            if base in col:
                if sfx == "":
                    if not any(k in col for k in ("Avg", "Rolling", "FDM", "SEC")):
                        return df[col].dropna().rename(col)
                elif sfx in col:
                    return df[col].dropna().rename(col)

        raise KeyError(
            f"Column for {commodity}/{price_type} not found in {frame}.\n"
            f"Available: {list(df.columns)}"
        )

    @staticmethod
    def _normalize_excel_sheets(raw: dict[str, dict]) -> dict[str, pd.DataFrame]:
        """Convert parser output (Excel) to same structure as EIA fetcher."""
        frames: dict[str, pd.DataFrame] = {}
        for sheet_name, content in raw.items():
            records   = content.get("records", [])
            headers   = content.get("headers", [])
            sheet_type = content.get("sheet_type", "unknown")
            if not records:
                continue
            df = pd.DataFrame(records, columns=headers)
            date_col = next((c for c in df.columns if c.lower() == "date"), None)
            if date_col:
                df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
                df = df.dropna(subset=[date_col]).set_index(date_col).sort_index()
            for col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="ignore")
            key = "first_day" if sheet_type == "first_day" else \
                  "monthly_index" if sheet_type == "monthly_index" else sheet_name
            frames[key] = df
        return frames
