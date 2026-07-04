# nsai-pricing-mcp

MCP server and pandas client for oil & gas pricing data
equivalent to what Netherland, Sewell & Associates (NSAI) publishes monthly.

Data is pulled **live from the U.S. Energy Information Administration (EIA)
Open Data API** — the same primary source NSAI uses. No spreadsheet download.
No scraping. Always current.

---

## What data is available?

| Dataset | Description |
|---------|-------------|
| **Monthly Index Prices** | Calendar-month averages + 12-month rolling averages for WTI crude, Henry Hub gas, and Mont Belvieu propane |
| **First-Day-of-Month Prices** | Price on the 1st trading day of each month — the SEC input |
| **SEC Benchmark Price** | 12-month arithmetic average of first-day prices per SEC Rule 4-10(a) |

The **SEC benchmark price** has been required since January 1, 2010.
E&P companies use it to report proved reserves and compute PV-10 in
annual 10-K / 20-F filings.

---

## Project structure

```
nsai-pricing-mcp/
├── pyproject.toml          # package definition and dependencies
├── requirements.txt        # pip install list
├── railway.toml            # Railway deploy config (SSE transport)
├── Procfile                # process definition for the hosted server
├── README.md
│
└── src/
    └── nsai_pricing_mcp/
        ├── __init__.py
        ├── eia_fetcher.py  # pulls live data from EIA API → pandas DataFrames
        ├── fetcher.py      # legacy NSAI page scraper (Excel fallback)
        ├── parser.py       # Excel parser (used when loading a local file)
        ├── nsai_client.py  # pandas client — the RE-facing interface
        └── server.py       # MCP server — the Claude Code / agent interface
```

---

## Installation

```bash
git clone https://github.com/youruser/nsai-pricing-mcp.git
cd nsai-pricing-mcp
pip install -e .
```

---

## EIA API key (one-time, free)

All live data comes from the EIA Open Data API.

1. Register at **https://www.eia.gov/opendata/register.php** (90 seconds)
2. Add your key to your shell profile:
   ```bash
   echo 'export EIA_API_KEY="your_key_here"' >> ~/.zshrc
   source ~/.zshrc
   ```

That's the only setup step. Every data call after this is fully automated.

---

## Two ways to use it

### 1. Python / pandas — for Reservoir Engineers

```python
from nsai_pricing_mcp.nsai_client import NSAIPricingClient

client = NSAIPricingClient()
client.fetch()                            # pulls live from EIA, no downloads

df_monthly = client.monthly_index_prices()    # spot prices + rolling averages
df_sec     = client.sec_benchmark_prices()    # first-day prices + SEC averages
df_2023    = client.sec_benchmark_prices(year=2023)

wti  = client.wti_series()               # pd.Series, datetime-indexed
hh   = client.henry_hub_series()
sec  = client.current_sec_price()        # {'wti_sec_avg_bbl': 72.34, ...}

client.to_excel("NSAI_pricing.xlsx")     # export for colleagues without Python
client.to_csv("./output/")
```

### 2. MCP server — for Claude Code / AI agents

Register the server with Claude Code using the `claude mcp add` command.
Pick the option that matches how you're running it.

#### A. Connect to a hosted (Railway) deployment over SSE — recommended

If the server is deployed to Railway (the repo includes `railway.toml` and a
`Procfile`), the hosted instance holds its own `EIA_API_KEY`, so clients don't
pass a key.
Register it with one command:

```bash
claude mcp add --transport sse epv-pricing https://<your-railway-domain>/sse
```

- The registration name (`epv-pricing`) is an arbitrary local alias — choose anything.
- **Scope:** by default it's saved to your local, per-project config. Add
  `--scope user` to make it available in every directory, or `--scope project`
  to write a committed `.mcp.json` that collaborators share.

Verify the connection:

```bash
claude mcp list
# epv-pricing: https://<your-railway-domain>/sse (SSE) - ✔ Connected
```

The tools load at the start of a Claude Code session — start a fresh session
in the directory where you registered it, then just ask questions like
*"What's the current SEC price for oil and gas?"*

#### B. Run the server locally over stdio

Have Claude Code launch the server as a subprocess (no hosting needed). This
uses your local `EIA_API_KEY`:

```bash
claude mcp add epv-pricing \
  --env EIA_API_KEY=your_key_here \
  -- python -m nsai_pricing_mcp.server
```

This writes an entry to `~/.claude.json`. Restart Claude Code to pick it up.

To remove a server: `claude mcp remove epv-pricing`.

#### Available tools

| Tool | Description |
|------|-------------|
| `fetch_latest_pricing_data` | Pull live data from EIA |
| `load_spreadsheet_from_path` | Load a local NSAI Excel (fallback) |
| `list_sheets` | Inspect loaded datasets and column names |
| `get_pricing_summary` | Overview with date coverage |
| `get_current_sec_price` | Latest SEC benchmark (WTI + HH) |
| `get_sec_benchmark_prices` | Full history, filterable by year |
| `get_monthly_index_prices` | Last N months of spot + rolling averages |
| `search_prices_by_date` | Date-range query |
| `export_to_json` | Dump to JSON for Snowflake COPY or pandas |

---

## Data sources

| Commodity | EIA Series | Notes |
|-----------|-----------|-------|
| WTI Crude Oil | `petroleum/pri/spt` — EPCWTI, YCUOK | Cushing OK spot (series RWTC), $/Bbl |
| Henry Hub Gas | `natural-gas/pri/fut` — RNGWHHD | Henry Hub spot, $/MMBtu |
| Mont Belvieu Propane | `petroleum/pri/spt` — EPLLPA, Y44MB | Enterprise terminal, $/Gal |

EIA publishes these as daily spot prices. The client derives:
- Monthly averages by calendar-month resampling
- 12-month rolling averages
- First-day-of-month prices (first trading day on or after the 1st)
- 12-month arithmetic average of first-day prices (the SEC benchmark)

---

## Key petroleum engineering notes

- **SEC Rule 4-10(a)**: The December row of each year contains the
  SEC benchmark price for that fiscal year-end (FY2024 → December 2024 row)
- **First-day-of-month**: EIA's first reported price on or after the 1st
  calendar day of each month — not necessarily a settlement price
- **Price differentials**: Applied separately at lease/field level.
  See NSAI's guide: https://netherlandsewell.com/resources/calculating-differentials/
- **NGL pricing**: Mont Belvieu propane shown; butane and ethane available
  via additional EIA series if needed

---

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `EIA_API_KEY` | Yes | Free EIA Open Data API key |
| `NSAI_CACHE_DIR` | No | Cache directory (default: `~/.nsai_pricing_cache`) |

---

## License

MIT. EIA data is public domain (U.S. government work).
