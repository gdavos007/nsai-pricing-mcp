# Product Requirements Document: nsai-pricing-mcp

**Version:** 0.1.0  
**Status:** Implemented (v0.1)  
**Last updated:** July 2026

---

## Problem Statement

Reservoir engineers, reserve analysts, and petroleum finance teams need oil and gas benchmark pricing data that matches what Netherland, Sewell & Associates (NSAI) publishes monthly. This data underpins critical workflows:

- **SEC reserves reporting** — E&P companies must use SEC Rule 4-10(a) benchmark prices (12-month arithmetic average of first-day-of-month prices) when disclosing proved reserves and computing PV-10 / Standardized Measure in annual 10-K and 20-F filings. This requirement has been in effect since January 1, 2010.
- **Price differential analysis** — Monthly index prices (spot and 12-month rolling averages) are used alongside lease operating statements to compute the gap between benchmark indices and actual wellhead/lease prices received.
- **Engineering and economic modeling** — Reserve evaluations, decline-curve analysis, and acquisition screening all depend on consistent, auditable price histories for WTI crude, Henry Hub natural gas, and NGL benchmarks.

Today, practitioners typically:

1. Manually download NSAI's monthly Excel spreadsheet from a JavaScript-rendered web page.
2. Copy-paste or import data into spreadsheets, Python notebooks, or Snowflake tables.
3. Repeat this process every month to stay current.

This workflow is slow, error-prone, and difficult to automate. AI coding assistants (Claude Code, agents) have no native access to this domain-specific data. Teams that want programmatic access must either maintain brittle scrapers or manually curate files.

NSAI itself republishes data sourced from the U.S. Energy Information Administration (EIA). By going directly to EIA — the authoritative primary source — the manual download step can be eliminated entirely while producing NSAI-equivalent derived datasets.

---

## Solution

**nsai-pricing-mcp** is a Python package that provides NSAI-equivalent oil and gas pricing data through two interfaces:

1. **Pandas client** — A programmatic API for reservoir engineers and analysts writing Python analysis code.
2. **MCP server** — A Model Context Protocol server that exposes pricing data as tools consumable by Claude Code and other MCP-compatible AI agents.

The primary data path pulls live daily spot prices from the **EIA Open Data API**, then derives the same monthly index prices, first-day-of-month prices, and SEC benchmark averages that NSAI publishes. A secondary fallback path loads locally downloaded NSAI Excel files when EIA is unavailable or when users need to reconcile against NSAI's exact spreadsheet.

The MCP server can run locally (stdio transport) or be deployed to Railway (SSE transport) so teams can share a hosted endpoint without each user managing an EIA API key.

---

## Target Users

| Persona | Primary interface | Key need |
|---------|-------------------|----------|
| Reservoir Engineer (RE) | Pandas client | Drop-in price series for decline-curve and reserve models |
| Reserve / SEC reporting analyst | Pandas client or MCP | Current and historical SEC benchmark prices by fiscal year |
| AI-assisted developer / analyst | MCP server | Natural-language queries ("What's the current SEC price for oil and gas?") |
| Data engineer | MCP `export_to_json` or client `to_csv` | Pipe pricing data into Snowflake, dbt, or other pipelines |

---

## User Stories

### Data access — primary (EIA)

1. As a reservoir engineer, I want to fetch live WTI, Henry Hub, and Mont Belvieu propane prices without downloading a spreadsheet, so that my analysis is always current with minimal manual effort.
2. As a reserve analyst, I want monthly calendar-average spot prices with 12-month rolling averages, so that I can compute price differentials against lease operating statements.
3. As an SEC reporting analyst, I want first-day-of-month prices for each commodity, so that I can verify inputs to the SEC benchmark calculation.
4. As an SEC reporting analyst, I want the 12-month arithmetic average of first-day-of-month prices, so that I can report proved reserves under SEC Rule 4-10(a).
5. As an analyst, I want to filter SEC benchmark data by fiscal year (e.g., 2023), so that I can pull the exact prices used in a specific 10-K filing.
6. As an analyst, I want a convenience method that returns only the latest SEC benchmark row, so that I can quickly answer "what is the current SEC price?" without scanning a full history.
7. As an engineer, I want datetime-indexed pandas Series for WTI, Henry Hub, and NGL at spot, first-day-of-month, or SEC-average granularity, so that I can plug prices directly into existing models.
8. As a user, I want historical data back to 2010 (when SEC pricing rules took effect), so that my backtests and audits cover the full regulatory period.
9. As a user, I want in-process caching of EIA responses (default 6 hours), so that repeated calls within a session do not hammer the API or slow down agent workflows.
10. As a user, I want to force-refresh cached data when needed, so that I can pull the latest EIA update on demand.
11. As a user, I want clear error messages when my EIA API key is missing or invalid, so that I know exactly how to fix setup in under two minutes.
12. As a user, I want partial-fetch warnings when one commodity series fails but others succeed, so that I am not left with silently incomplete data.

### Data access — fallback (local Excel)

13. As a user without network access to EIA, I want to load a locally saved NSAI Excel file, so that I get the same DataFrame interface as the live fetch path.
14. As an MCP user, I want to load a spreadsheet from an absolute file path, so that I can use NSAI's official file when automated download is not possible.
15. As a user, I want the Excel parser to auto-detect sheet types (monthly index vs. first-day-of-month) even when NSAI renames sheets, so that the tool remains robust across NSAI format changes.

### Pandas client

16. As a reservoir engineer, I want a single `NSAIPricingClient` class with a chainable `fetch()` method, so that my workflow is one import and one call.
17. As an analyst, I want to export all datasets to CSV or Excel, so that colleagues without Python can use the same numbers.
18. As an analyst, I want `monthly_index_prices()` and `sec_benchmark_prices()` to return proper pandas DataFrames, so that I can join them with production data on date index.

### MCP server — tools

19. As an AI agent user, I want a `fetch_latest_pricing_data` tool, so that the agent can load current prices at the start of a session.
20. As an AI agent user, I want a `list_sheets` tool, so that the agent can inspect available datasets and column names before querying.
21. As an AI agent user, I want a `get_pricing_summary` tool, so that the agent can report date coverage and latest values in one call.
22. As an AI agent user, I want a `get_sec_benchmark_prices` tool with optional year filter, so that the agent can answer SEC pricing questions accurately.
23. As an AI agent user, I want a `get_monthly_index_prices` tool with a configurable month window, so that the agent can retrieve recent spot and rolling-average history.
24. As an AI agent user, I want a `search_prices_by_date` tool with flexible ISO date formats (`2022`, `2022-07`, `2022-07-01`), so that the agent can answer range queries without brittle parsing.
25. As an AI agent user, I want a `get_current_sec_price` shortcut tool, so that common "what is the SEC price today?" questions resolve in one step.
26. As a data engineer, I want an `export_to_json` tool, so that the agent can write pricing data to a file for Snowflake COPY or downstream ingestion.
27. As an MCP client, I want all tool responses as structured JSON with `status: ok` or `status: error`, so that agents can parse results reliably.

### MCP server — deployment

28. As a team lead, I want to deploy the MCP server to Railway with SSE transport, so that my team connects via URL without running a local process.
29. As a hosted-server operator, I want the Railway deployment to hold the `EIA_API_KEY`, so that end users do not need their own keys to use the shared endpoint.
30. As a platform operator, I want a `/health` endpoint separate from `/sse`, so that Railway health checks do not target the long-lived event stream.
31. As a local developer, I want to run the server over stdio with `claude mcp add`, so that I can use my own EIA key without hosting infrastructure.
32. As a security-conscious operator, I want optional `MCP_ALLOWED_HOSTS` configuration for DNS-rebinding protection when locking down a public endpoint.

### Domain correctness

33. As a reserve analyst, I want first-day-of-month prices defined as the first EIA-reported price on or after the 1st calendar day of each month, so that the calculation matches SEC guidance.
34. As a reserve analyst, I want December rows labeled with a reporting period (e.g., "Dec 2024"), so that I can identify the SEC benchmark for a given fiscal year-end.
35. As an engineer, I want Mont Belvieu propane included alongside WTI and Henry Hub, so that NGL reserve evaluations have a standard benchmark.
36. As a practitioner, I want documentation noting that price differentials are applied at lease/field level separately, so that I do not misuse index prices as wellhead prices.

---

## Functional Requirements

### FR-1: EIA data ingestion

| ID | Requirement |
|----|-------------|
| FR-1.1 | Fetch daily spot prices for WTI (Cushing, OK), Henry Hub natural gas, and Mont Belvieu propane from EIA API v2. |
| FR-1.2 | Support pagination for series exceeding 5,000 records per page. |
| FR-1.3 | Retry transient failures (HTTP 429, 5xx, network errors) with bounded exponential backoff (up to 4 attempts). |
| FR-1.4 | Honor `Retry-After` headers when EIA sends them. |
| FR-1.5 | Require a valid `EIA_API_KEY` (environment variable or explicit parameter); fail with actionable setup instructions if missing. |
| FR-1.6 | Default historical start year: 2010 (SEC pricing effective date). |

### FR-2: Derived datasets

| ID | Requirement |
|----|-------------|
| FR-2.1 | **Monthly Index Prices** — calendar-month average of daily spot prices; 12-month rolling average of monthly averages; rounded to 4 decimal places. |
| FR-2.2 | **First-Day-of-Month Prices** — first available daily price on or after the 1st of each calendar month; 12-month arithmetic rolling average (= SEC benchmark). |
| FR-2.3 | Add a "Reporting Period" column on first-day sheet for December rows (fiscal year-end marker). |
| FR-2.4 | Output column labels consistent with NSAI naming conventions (e.g., `WTI Crude Oil ($/Bbl)`, `Henry Hub ($/MMBtu)`). |

### FR-3: Caching

| ID | Requirement |
|----|-------------|
| FR-3.1 | In-process TTL cache per `start_year` (default 6 hours via `EIA_CACHE_TTL_SECONDS`). |
| FR-3.2 | Cache bypass via `force_refresh=True` or `EIA_CACHE_TTL_SECONDS=0`. |
| FR-3.3 | Return cache metadata: `served_from_cache`, `fetched_at`, `cache_age_seconds`, `cache_ttl_seconds`. |
| FR-3.4 | Optional filesystem cache directory (`NSAI_CACHE_DIR`, default `~/.nsai_pricing_cache`) for legacy Excel downloads. |

### FR-4: Excel fallback

| ID | Requirement |
|----|-------------|
| FR-4.1 | Parse `.xlsx` and `.xls` NSAI spreadsheets into the same internal structure as EIA-derived data. |
| FR-4.2 | Auto-detect header rows and classify sheets by name/header heuristics. |
| FR-4.3 | Normalize cell types (dates → ISO strings, floats → 4-decimal rounding) for JSON-safe output. |
| FR-4.4 | Legacy scraper may attempt HTML link extraction and WordPress upload URL probing; manual download remains the documented fallback when JavaScript rendering blocks automation. |

### FR-5: MCP server

| ID | Requirement |
|----|-------------|
| FR-5.1 | Expose nine tools: `fetch_latest_pricing_data`, `load_spreadsheet_from_path`, `list_sheets`, `get_pricing_summary`, `get_sec_benchmark_prices`, `get_monthly_index_prices`, `search_prices_by_date`, `get_current_sec_price`, `export_to_json`. |
| FR-5.2 | Maintain in-memory loaded data across tool calls within a session; require explicit fetch or load before query tools. |
| FR-5.3 | Auto-load from cached Excel file on disk if present and no in-memory data exists. |
| FR-5.4 | Serve stdio transport locally; serve SSE on `0.0.0.0:$PORT` when `PORT` is set (Railway). |
| FR-5.5 | Attach `/health` route returning `ok` for platform health checks. |
| FR-5.6 | Ship agent instructions describing NSAI data semantics, SEC Rule 4-10(a), and recommended tool workflow. |

### FR-6: Pandas client

| ID | Requirement |
|----|-------------|
| FR-6.1 | Unified `NSAIPricingClient` API for both `fetch()` (EIA) and `load()` (Excel) paths. |
| FR-6.2 | Methods: `monthly_index_prices()`, `sec_benchmark_prices(year=)`, `wti_series()`, `henry_hub_series()`, `ngl_series()`, `current_sec_price()`, `all_data()`, `to_csv()`, `to_excel()`. |
| FR-6.3 | Series `price_type` options: `spot`, `fdm` (first-day-of-month), `sec_avg`. |
| FR-6.4 | Raise clear `RuntimeError` if data methods called before `fetch()` or `load()`. |

---

## Non-Functional Requirements

| ID | Category | Requirement |
|----|----------|-------------|
| NFR-1 | Reliability | EIA fetch must succeed if at least one commodity series returns data; partial failures surface as warnings, not silent drops. |
| NFR-2 | Performance | Cached responses served without network round-trip; suitable for agent sessions with many tool calls. |
| NFR-3 | Compatibility | Python ≥ 3.10; pinned MCP SDK version for SSE transport security API stability. |
| NFR-4 | Operability | Single environment variable (`EIA_API_KEY`) for primary setup; optional tuning via `EIA_CACHE_TTL_SECONDS`, `NSAI_CACHE_DIR`, `MCP_ALLOWED_HOSTS`. |
| NFR-5 | Licensing | MIT for code; EIA data is U.S. government public domain. |
| NFR-6 | Security | API keys read from environment (never hardcoded); optional host allowlist for public SSE deployments. |

---

## Implementation Decisions

### Architecture

- **Dual-interface, single data model** — Both the pandas client and MCP server normalize all inputs (EIA or Excel) into two logical datasets: `monthly_index` and `first_day`. Query logic is shared; only the loading layer differs.
- **EIA as source of truth** — Primary path eliminates scraping. NSAI Excel is a compatibility fallback, not the main workflow.
- **Thin MCP layer over domain logic** — MCP tools serialize in-memory dicts (headers + records) to JSON. The EIA fetcher and Excel parser own data transformation; the server owns session state and tool orchestration.

### Data derivation

- Daily EIA spot → monthly resample (`MS` = month start) → rolling 12-month mean for index averages.
- First-day-of-month: for each calendar month, take the first non-empty daily observation within that month.
- SEC benchmark: 12-month rolling arithmetic mean of first-day-of-month prices (`min_periods=12`).

### EIA series mapping

| Commodity | EIA route | Facets |
|-----------|-----------|--------|
| WTI | `petroleum/pri/spt/data/` | product=EPCWTI, duoarea=YCUOK |
| Henry Hub | `natural-gas/pri/fut/data/` | series=RNGWHHD |
| Mont Belvieu Propane | `petroleum/pri/spt/data/` | product=EPLLPA, duoarea=Y44MB |

### MCP transport

- **Local:** stdio via `mcp.run()` when `PORT` is unset.
- **Hosted:** custom SSE Starlette app with `/health` appended (SDK's `/sse` is unsuitable for health checks because it is a long-lived stream).
- **DNS rebinding:** disabled by default on public deploys (low risk for non-localhost); opt-in lockdown via `MCP_ALLOWED_HOSTS`.

### Excel parser resilience

- Header row detected by scanning first 20 rows for ≥2 text cells.
- Sheet type classified by name keywords (`first`, `fdm`, `sec`, `monthly`, `index`) with header-text fallback.
- Duplicate column names de-duplicated with numeric suffixes.

### Dependencies

- `mcp` (FastMCP), `httpx`, `pandas`, `openpyxl`, `uvicorn`, `starlette`, `python-dotenv` (optional, dev convenience), `beautifulsoup4` + `lxml` (legacy scraper only).

---

## Testing Decisions

### Philosophy

- Test **external behavior**, not implementation internals — e.g., given a mocked EIA response, verify derived monthly averages and SEC benchmarks match expected values; do not assert on private helper function call order.
- Prioritize **domain correctness** tests (first-day-of-month edge cases around weekends/holidays, rolling average window boundaries, year filtering) over coverage of HTTP retry logic.

### Recommended test modules

| Module | What to test |
|--------|--------------|
| EIA fetcher | Daily → monthly resample; first-day extraction; SEC 12-month average; cache hit/miss; partial series failure warnings |
| Excel parser | Sheet classification; header detection; date normalization; filter_by_year / filter_by_date_range |
| Pandas client | `fetch()` and `load()` produce identical DataFrame shapes; `current_sec_price()` dict keys; series `price_type` resolution |
| MCP server | Tool JSON response shape; error when data not loaded; year filter on SEC tool |

### Prior art

No automated test suite exists in v0.1. New tests should live alongside the package (e.g., `tests/`) using `pytest` with `httpx` mock transport for EIA calls and fixture Excel files for parser tests.

---

## Out of Scope (v0.1)

- Additional commodities beyond WTI, Henry Hub, and Mont Belvieu propane (Brent, Waha, butane, ethane, Conway, etc.) — noted as future EIA series extensions.
- Price differential calculation at lease/field level — users apply differentials separately per NSAI guidance.
- Proved reserve or PV-10 computation — this product supplies price inputs only.
- Real-time intraday or futures pricing — EIA daily spot only.
- Persistent database or warehouse hosting — export to JSON/CSV/Excel is provided; ingestion is the user's responsibility.
- Authentication/authorization on the hosted MCP endpoint — operators are expected to use Railway/network-level access control if needed.
- Automated NSAI spreadsheet download as primary path — deprecated in favor of EIA; scraper retained only as legacy fallback.
- GUI or web dashboard — CLI, Python API, and MCP tools only.
- Guaranteed bit-for-bit match with every NSAI Excel revision — EIA-primary path is authoritative; Excel path is for reconciliation.

---

## Success Metrics

| Metric | Target |
|--------|--------|
| Time to first SEC price query (new user) | < 5 minutes (including EIA key registration) |
| EIA fetch reliability | ≥ 99% success when API key valid and EIA available |
| Agent workflow | User can ask "current SEC price" and get correct WTI + HH values in one MCP session |
| Data freshness | Within one EIA business-day of official publication |
| Setup friction (hosted) | Zero per-user API key when using team Railway deployment |

---

## Environment Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `EIA_API_KEY` | Yes (unless hosted server provides it) | — | Free EIA Open Data API key |
| `EIA_CACHE_TTL_SECONDS` | No | `21600` (6h) | In-process EIA response TTL; `0` disables |
| `NSAI_CACHE_DIR` | No | `~/.nsai_pricing_cache` | Filesystem cache for legacy Excel downloads |
| `MCP_ALLOWED_HOSTS` | No | (protection disabled) | Comma-separated allowed Host headers for SSE |
| `PORT` | No | — | When set, enables SSE server on `0.0.0.0:PORT` |

---

## Deployment Model

```
┌─────────────────┐     stdio      ┌──────────────────┐
│  Claude Code    │◄──────────────►│  MCP Server      │
│  (local)        │                │  (local process) │
└─────────────────┘                └────────┬─────────┘
                                            │
┌─────────────────┐     SSE        ┌────────▼─────────┐
│  Claude Code    │◄──────────────►│  Railway deploy  │
│  (any machine)  │   /sse         │  (shared team)   │
└─────────────────┘                └────────┬─────────┘
                                            │
                                   ┌────────▼─────────┐
                                   │  EIA Open Data   │
                                   │  API (v2)        │
                                   └──────────────────┘

┌─────────────────┐
│  Python RE      │──► NSAIPricingClient.fetch() ──► EIA API
│  (notebook/CI)  │──► NSAIPricingClient.load()  ──► Local Excel
└─────────────────┘
```

---

## Further Notes

### Regulatory context

SEC Rule 4-10(a) requires the 12-month unweighted arithmetic average of the first-day-of-month price for each of the 12 months prior to the fiscal year-end. Prices are held flat over the proved reserve life. The December row of each year in the first-day dataset represents the benchmark for that fiscal year-end (e.g., FY2024 → December 2024 row).

### Relationship to NSAI

NSAI is a leading independent petroleum engineering firm whose monthly pricing spreadsheet is an industry reference. This product produces **equivalent derived datasets** from the same underlying EIA source. It is not affiliated with or endorsed by NSAI. Users requiring NSAI's exact file format or additional indices should use the Excel fallback or extend EIA series mappings.

### Future considerations (not committed)

- Add Brent, Waha, and additional NGL EIA series behind the same derivation pipeline.
- Ship a published test suite with golden-file fixtures for SEC benchmark values by year.
- Optional API key auth middleware for hosted MCP deployments.
- Snowflake native connector or dbt source package.
- Scheduled Railway cron to warm the EIA cache daily.

---

## Appendix: MCP Tool Reference

| Tool | Purpose |
|------|---------|
| `fetch_latest_pricing_data` | Pull live data from EIA; populate session cache |
| `load_spreadsheet_from_path` | Load local NSAI Excel file |
| `list_sheets` | Inspect loaded datasets, types, headers, row counts |
| `get_pricing_summary` | High-level overview with date coverage and latest values |
| `get_current_sec_price` | Latest SEC benchmark row (WTI + HH shortcut) |
| `get_sec_benchmark_prices` | Full SEC history; optional `year` filter |
| `get_monthly_index_prices` | Last N months of spot + rolling averages |
| `search_prices_by_date` | Date-range query across one or all sheets |
| `export_to_json` | Write loaded data to JSON file |

## Appendix: Pandas Client API Reference

| Method | Returns |
|--------|---------|
| `fetch(start_year=2010)` | `self` (chainable) |
| `load(path)` | `self` (chainable) |
| `monthly_index_prices()` | `DataFrame` |
| `sec_benchmark_prices(year=None)` | `DataFrame` |
| `wti_series(price_type="spot")` | `Series` |
| `henry_hub_series(price_type="spot")` | `Series` |
| `ngl_series(price_type="spot")` | `Series` |
| `current_sec_price()` | `dict` |
| `all_data()` | `dict[str, DataFrame]` |
| `to_csv(directory)` | `dict[str, Path]` |
| `to_excel(path)` | `Path` |
