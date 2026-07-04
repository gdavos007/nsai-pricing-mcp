"""
NSAI Pricing Data MCP Server

Exposes Netherland, Sewell & Associates (NSAI) oil & gas pricing data
as MCP tools consumable by Claude and other MCP-compatible agents.

Data published by NSAI:
  - Monthly Index Prices: monthly WTI, Henry Hub, NGL spot prices
    with 12-month rolling averages
  - First-Day-of-Month Prices: SEC-required benchmark prices
    (12-month unweighted arithmetic average of first-day prices)

Usage:
  python -m nsai_pricing_mcp.server
  # or via entry-point:
  nsai-pricing-mcp
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

# Load a local .env for development convenience (e.g. EIA_API_KEY).
# override=False means real environment variables (Railway, CI, shell exports)
# always win — .env only fills in values that aren't already set. No-op if
# python-dotenv isn't installed or no .env file exists.
try:
    from dotenv import load_dotenv

    load_dotenv(override=False)
except ImportError:
    pass

from mcp.server.fastmcp import FastMCP

from .eia_fetcher import fetch_all as _eia_fetch_all, KEY_ENV
from .fetcher import download_spreadsheet, get_cached_metadata, NSAI_PRICING_URL
from .parser import (
    parse_nsai_spreadsheet,
    filter_by_year,
    filter_by_date_range,
    get_latest_record,
    summarise_sheet,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CACHE_DIR = Path(os.environ.get("NSAI_CACHE_DIR", Path.home() / ".nsai_pricing_cache"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# FastMCP server
# ---------------------------------------------------------------------------

def _transport_security() -> "TransportSecuritySettings":
    """
    Configure DNS-rebinding (Host/Origin) protection for the HTTP transports.

    Recent MCP SDKs validate the Host header and reject any request whose host
    isn't in an allowlist (returns 421 "Invalid Host header"). Behind a platform
    proxy like Railway the external host varies by deploy and can't be wildcarded
    (the SDK only supports exact host or `:*` port patterns), so:

      * If MCP_ALLOWED_HOSTS is set (comma-separated exact hosts), lock down to it.
      * Otherwise disable DNS-rebinding protection — it primarily guards localhost
        servers from browser-based attacks and adds little for a public endpoint.
    """
    from mcp.server.transport_security import TransportSecuritySettings

    allowed = [h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    if allowed:
        return TransportSecuritySettings(allowed_hosts=allowed, allowed_origins=allowed)
    return TransportSecuritySettings(enable_dns_rebinding_protection=False)


mcp = FastMCP(
    name="nsai-pricing",
    transport_security=_transport_security(),
    instructions="""
You have access to oil and gas pricing data published by Netherland, Sewell &
Associates, Inc. (NSAI) — a leading independent petroleum engineering firm.

The spreadsheet contains:
  1. Monthly Index Prices — spot prices and 12-month rolling averages for
     WTI crude oil, Henry Hub natural gas, and NGL indices (Mont Belvieu, etc.)
  2. First-Day-of-Month Prices — prices on the first calendar day of each month,
     used to compute SEC benchmark prices for reserves reporting.

SEC Benchmark Price (defined by SEC Rule 4-10(a)):
  The 12-month unweighted arithmetic average of the first-day-of-month price
  for each of the 12 months in the period prior to the end of the fiscal year.
  As of December 31 each year, this becomes the required price for proved
  reserves disclosures in SEC filings.

Typical indices tracked:
  Oil:  West Texas Intermediate (WTI), Brent
  Gas:  Henry Hub (HH), Waha, El Paso San Juan, Southern California Border
  NGL:  Mont Belvieu propane, butane, ethane; Conway propane

Workflow:
  1. Call fetch_latest_pricing_data() or load_spreadsheet_from_path() first.
  2. Use list_sheets() to inspect available sheets and their column names.
  3. Use get_sec_benchmark_prices(), get_monthly_index_prices(), or
     search_prices_by_date() to retrieve the data you need.
""",
)

# ---------------------------------------------------------------------------
# In-process data cache (avoids re-parsing Excel on every tool call)
# ---------------------------------------------------------------------------

_DATA: dict | None = None
_DATA_SOURCE: str | None = None


def _loaded_data() -> dict | None:
    """Return in-memory data, auto-loading from cache file if available."""
    global _DATA, _DATA_SOURCE

    if _DATA is not None:
        return _DATA

    cached_xlsx = CACHE_DIR / "nsai_pricing_latest.xlsx"
    if cached_xlsx.exists():
        _DATA = parse_nsai_spreadsheet(cached_xlsx)
        _DATA_SOURCE = str(cached_xlsx)

    return _DATA


def _require_data() -> dict:
    data = _loaded_data()
    if data is None:
        raise RuntimeError(
            "No pricing data loaded. "
            "Call fetch_latest_pricing_data() or load_spreadsheet_from_path() first."
        )
    return data


def _ok(payload: dict) -> str:
    return json.dumps({"status": "ok", **payload}, indent=2, default=str)


def _err(message: str) -> str:
    return json.dumps({"status": "error", "message": message}, indent=2)


# ---------------------------------------------------------------------------
# Tool: fetch_latest_pricing_data
# ---------------------------------------------------------------------------

@mcp.tool()
def fetch_latest_pricing_data(eia_api_key: Optional[str] = None) -> str:
    """
    Pull the latest WTI, Henry Hub, and Mont Belvieu pricing data
    directly from the EIA (U.S. Energy Information Administration) API.

    No spreadsheet download. No scraping. Data comes from the primary
    source — EIA is where NSAI gets these prices in the first place.

    Requires a free EIA API key (90-second registration):
      https://www.eia.gov/opendata/register.php

    Set once in your shell:
      export EIA_API_KEY="your_key_here"

    Or pass it directly as eia_api_key parameter.

    Returns a summary of datasets loaded and date coverage.
    """
    global _DATA, _DATA_SOURCE

    try:
        frames = _eia_fetch_all(api_key=eia_api_key)

        # Convert EIA DataFrames into the same dict structure the rest
        # of the server expects (list of records + headers + sheet_type)
        _DATA = {}
        for name, df in frames.items():
            records = df.reset_index().to_dict(orient="records")
            _DATA[name] = {
                "headers"   : list(df.reset_index().columns),
                "records"   : [
                    {k: str(v) if hasattr(v, "isoformat") else v
                     for k, v in r.items()}
                    for r in records
                ],
                "sheet_type": name,
            }
        _DATA_SOURCE = "EIA Open Data API"

        summary = {
            name: {
                "row_count": len(frames[name]),
                "date_from": str(frames[name].index.min().date()),
                "date_to"  : str(frames[name].index.max().date()),
                "columns"  : list(frames[name].columns),
            }
            for name in frames
        }

        return _ok({
            "source"  : "U.S. Energy Information Administration (EIA)",
            "api_url" : "https://api.eia.gov/v2/",
            "datasets": summary,
        })

    except EnvironmentError as exc:
        return json.dumps({
            "status" : "api_key_required",
            "message": str(exc),
        }, indent=2)
    except Exception as exc:
        return _err(f"EIA fetch failed: {exc}")


# ---------------------------------------------------------------------------
# Tool: load_spreadsheet_from_path
# ---------------------------------------------------------------------------

@mcp.tool()
def load_spreadsheet_from_path(file_path: str) -> str:
    """
    Load a manually downloaded NSAI pricing spreadsheet from a local file path.

    Use this when fetch_latest_pricing_data() cannot find the file automatically
    (the NSAI page requires JavaScript rendering to expose the download link).

    Steps to get the file manually:
      1. Open https://netherlandsewell.com/resources/pricing-data/ in your browser
      2. Download the spreadsheet
      3. Call this tool with the full path, e.g.:
         load_spreadsheet_from_path('/Users/you/Downloads/NSAI-Pricing-Data.xlsx')

    Args:
        file_path: Absolute path to the downloaded .xlsx or .xls file.
    """
    global _DATA, _DATA_SOURCE

    path = Path(file_path).expanduser()
    if not path.exists():
        return _err(f"File not found: {file_path}")
    if path.suffix.lower() not in {".xlsx", ".xls"}:
        return _err(f"Expected an Excel file (.xlsx or .xls), got: {path.suffix}")

    try:
        _DATA = parse_nsai_spreadsheet(path)
        _DATA_SOURCE = str(path)

        sheet_summary = {
            name: {
                "sheet_type": content["sheet_type"],
                "row_count": len(content["records"]),
                "headers": content["headers"],
            }
            for name, content in _DATA.items()
        }

        return _ok({
            "loaded_from": str(path),
            "sheets": sheet_summary,
        })
    except Exception as exc:
        return _err(f"Failed to parse Excel file: {exc}")


# ---------------------------------------------------------------------------
# Tool: list_sheets
# ---------------------------------------------------------------------------

@mcp.tool()
def list_sheets() -> str:
    """
    List all worksheets in the loaded NSAI pricing spreadsheet.

    Returns each sheet's name, detected type, column headers, and row count.
    Run this after loading data to understand the available structure.
    """
    try:
        data = _require_data()
    except RuntimeError as exc:
        return _err(str(exc))

    sheets = {
        name: {
            "sheet_type": content["sheet_type"],
            "description": _sheet_type_description(content["sheet_type"]),
            "headers": content["headers"],
            "row_count": len(content["records"]),
        }
        for name, content in data.items()
    }

    return _ok({
        "source": _DATA_SOURCE,
        "sheet_count": len(sheets),
        "sheets": sheets,
    })


def _sheet_type_description(sheet_type: str) -> str:
    descriptions = {
        "monthly_index": (
            "Monthly spot prices and 12-month rolling averages. "
            "Used with LOS data to determine differential to index."
        ),
        "first_day": (
            "First-day-of-month prices and their 12-month arithmetic average. "
            "The 12-month average is the SEC benchmark price for reserves reporting."
        ),
        "unknown": "Sheet type could not be determined automatically.",
    }
    return descriptions.get(sheet_type, "Unknown")


# ---------------------------------------------------------------------------
# Tool: get_pricing_summary
# ---------------------------------------------------------------------------

@mcp.tool()
def get_pricing_summary() -> str:
    """
    Return a high-level summary of the loaded NSAI pricing data:
    source information, data coverage dates, and the most recent
    available prices for each sheet.
    """
    try:
        data = _require_data()
    except RuntimeError as exc:
        return _err(str(exc))

    meta = get_cached_metadata(CACHE_DIR)
    sheets_summary = {}

    for name, content in data.items():
        summary = summarise_sheet(content, name)
        sheets_summary[name] = summary

    return _ok({
        "data_source": "Netherland, Sewell & Associates, Inc. (NSAI)",
        "source_url": NSAI_PRICING_URL,
        "cache_metadata": meta,
        "loaded_from": _DATA_SOURCE,
        "sheets": sheets_summary,
    })


# ---------------------------------------------------------------------------
# Tool: get_sec_benchmark_prices
# ---------------------------------------------------------------------------

@mcp.tool()
def get_sec_benchmark_prices(year: Optional[int] = None) -> str:
    """
    Return SEC benchmark prices — the 12-month unweighted arithmetic average
    of first-day-of-month WTI crude and Henry Hub gas prices.

    These are the prices required by the SEC under Rule 4-10(a) for oil and gas
    proved reserves disclosures in annual reports (10-K / 20-F).

    E&P companies must use these flat prices (held constant over property life)
    when reporting proved reserves and computing Standardized Measure / PV-10.

    Args:
        year: Filter results to a specific reporting year (e.g., 2023).
              If None, returns all available historical benchmark prices.
    """
    try:
        data = _require_data()
    except RuntimeError as exc:
        return _err(str(exc))

    # Prefer sheets classified as 'first_day'; fall back to first sheet
    target_sheet = None
    target_name = None
    for name, content in data.items():
        if content["sheet_type"] == "first_day":
            target_sheet = content
            target_name = name
            break

    if target_sheet is None:
        target_name, target_sheet = next(iter(data.items()))

    records = target_sheet["records"]
    if year:
        records = filter_by_year(target_sheet, year)

    if not records:
        return _err(f"No records found for year={year}. "
                    f"Use list_sheets() to verify data coverage.")

    latest = get_latest_record(target_sheet)

    return _ok({
        "sheet_used": target_name,
        "sheet_type": target_sheet["sheet_type"],
        "note": (
            "The SEC benchmark price for a given fiscal year-end is the "
            "12-month arithmetic average of first-day-of-month prices for "
            "the trailing 12 months (SEC Rule 4-10(a))."
        ),
        "headers": target_sheet["headers"],
        "latest_available": latest,
        "filtered_by_year": year,
        "record_count": len(records),
        "records": records,
    })


# ---------------------------------------------------------------------------
# Tool: get_monthly_index_prices
# ---------------------------------------------------------------------------

@mcp.tool()
def get_monthly_index_prices(
    months: int = 12,
    sheet_name: Optional[str] = None,
) -> str:
    """
    Return the most recent N months of monthly index prices from the NSAI spreadsheet.

    Monthly index prices (spot prices and 12-month rolling averages) are used
    alongside monthly lease operating statements (LOS) to determine price
    differentials — the gap between the benchmark index and the actual
    wellhead/lease price received.

    Args:
        months:     Number of recent months to return (default: 12, max: all available).
        sheet_name: Specific sheet to query. Use list_sheets() to see options.
                    If omitted, the 'monthly_index' sheet is used (or first sheet).
    """
    try:
        data = _require_data()
    except RuntimeError as exc:
        return _err(str(exc))

    # Resolve sheet
    if sheet_name:
        if sheet_name not in data:
            available = list(data.keys())
            return _err(
                f"Sheet '{sheet_name}' not found. Available sheets: {available}"
            )
        sheet = data[sheet_name]
        name = sheet_name
    else:
        # Prefer monthly_index, fall back to first sheet
        name, sheet = next(iter(data.items()))
        for n, s in data.items():
            if s["sheet_type"] == "monthly_index":
                name, sheet = n, s
                break

    all_records = sheet["records"]
    recent = all_records[-months:] if len(all_records) >= months else all_records

    return _ok({
        "sheet_used": name,
        "sheet_type": sheet["sheet_type"],
        "headers": sheet["headers"],
        "requested_months": months,
        "returned_months": len(recent),
        "total_months_available": len(all_records),
        "records": recent,
    })


# ---------------------------------------------------------------------------
# Tool: search_prices_by_date
# ---------------------------------------------------------------------------

@mcp.tool()
def search_prices_by_date(
    start_date: str,
    end_date: Optional[str] = None,
    sheet_name: Optional[str] = None,
) -> str:
    """
    Query NSAI pricing data for a specific date range across one or all sheets.

    Args:
        start_date: ISO date string — supports partial dates:
                      '2022'        → all of 2022
                      '2022-07'     → July 2022 onward
                      '2022-07-01'  → July 1, 2022 onward
        end_date:   Optional ISO date string (same format as start_date).
                    If omitted, returns everything from start_date to latest.
        sheet_name: Sheet to query. If omitted, searches all sheets.
    """
    try:
        data = _require_data()
    except RuntimeError as exc:
        return _err(str(exc))

    sheets_to_search = (
        {sheet_name: data[sheet_name]}
        if sheet_name and sheet_name in data
        else data
    )

    if sheet_name and sheet_name not in data:
        return _err(
            f"Sheet '{sheet_name}' not found. Available: {list(data.keys())}"
        )

    results: dict[str, dict] = {}

    for name, content in sheets_to_search.items():
        records = filter_by_date_range(content, start_date, end_date)
        if records:
            results[name] = {
                "sheet_type": content["sheet_type"],
                "headers": content["headers"],
                "record_count": len(records),
                "records": records,
            }

    if not results:
        return json.dumps({
            "status": "ok",
            "message": f"No records found between {start_date} and {end_date or 'latest'}.",
            "searched_sheets": list(sheets_to_search.keys()),
        }, indent=2)

    return _ok({
        "query": {"start_date": start_date, "end_date": end_date},
        "sheets_searched": list(sheets_to_search.keys()),
        "results": results,
    })


# ---------------------------------------------------------------------------
# Tool: get_current_sec_price
# ---------------------------------------------------------------------------

@mcp.tool()
def get_current_sec_price() -> str:
    """
    Return just the single most recent SEC benchmark price row —
    the latest 12-month average WTI and Henry Hub prices from the
    first-day-of-month sheet.

    This is a convenience shortcut for quickly answering:
    'What is the current SEC price for oil and gas?'
    """
    try:
        data = _require_data()
    except RuntimeError as exc:
        return _err(str(exc))

    # Target first-day sheet
    target_sheet = None
    target_name = None
    for name, content in data.items():
        if content["sheet_type"] == "first_day":
            target_sheet = content
            target_name = name
            break

    if target_sheet is None:
        target_name, target_sheet = next(iter(data.items()))

    latest = get_latest_record(target_sheet)
    if latest is None:
        return _err("No records found in the first-day-of-month sheet.")

    return _ok({
        "sheet_used": target_name,
        "headers": target_sheet["headers"],
        "latest_sec_price": latest,
        "note": (
            "SEC benchmark price = 12-month unweighted arithmetic average "
            "of first-day-of-month prices (WTI for oil, Henry Hub for gas). "
            "All prices held flat over proved reserve life per SEC Rule 4-10(a)."
        ),
    })


# ---------------------------------------------------------------------------
# Tool: export_to_json
# ---------------------------------------------------------------------------

@mcp.tool()
def export_to_json(
    output_path: str,
    sheet_name: Optional[str] = None,
) -> str:
    """
    Export all loaded pricing data (or a specific sheet) to a JSON file.

    Useful for piping NSAI data into other tools (Snowflake COPY, pandas, etc.).

    Args:
        output_path: Full path for the output .json file, e.g. '/tmp/nsai_prices.json'
        sheet_name:  Export only this sheet. If omitted, exports all sheets.
    """
    try:
        data = _require_data()
    except RuntimeError as exc:
        return _err(str(exc))

    if sheet_name:
        if sheet_name not in data:
            return _err(f"Sheet '{sheet_name}' not found. Available: {list(data.keys())}")
        export_data = {sheet_name: data[sheet_name]}
    else:
        export_data = data

    out_path = Path(output_path).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "exported_at": datetime.now().isoformat(),
        "source": NSAI_PRICING_URL,
        "loaded_from": _DATA_SOURCE,
        "sheets": export_data,
    }

    out_path.write_text(json.dumps(payload, indent=2, default=str))

    total_records = sum(
        len(s["records"]) for s in export_data.values()
    )

    return _ok({
        "output_path": str(out_path),
        "sheets_exported": list(export_data.keys()),
        "total_records": total_records,
    })


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    port = os.environ.get("PORT")
    if port:
        # Railway (and similar) inject PORT — serve over SSE on 0.0.0.0.
        #
        # We build the SSE Starlette app ourselves (rather than mcp.run(
        # transport="sse")) so we can attach a lightweight /health route.
        # The /sse endpoint is a long-lived event stream that never returns a
        # completed response, so it is unusable as a platform healthcheck
        # target — point railway.toml's healthcheckPath at /health instead.
        import uvicorn
        from starlette.responses import PlainTextResponse
        from starlette.routing import Route

        mcp.settings.host = "0.0.0.0"
        mcp.settings.port = int(port)

        app = mcp.sse_app()

        async def health(_request):
            return PlainTextResponse("ok")

        app.router.routes.append(Route("/health", health, methods=["GET"]))

        uvicorn.run(app, host="0.0.0.0", port=int(port))
    else:
        mcp.run()


if __name__ == "__main__":
    main()
