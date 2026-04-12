---
name: nsai-pricing
description: >
  Get current and historical oil & gas pricing data for SEC reserves
  reporting. Use when asked about SEC oil price, WTI benchmark, Henry Hub
  gas price, NSAI pricing, SEC Rule 4-10(a) benchmark, first-day-of-month
  prices, or proved reserves pricing. No spreadsheet download required.
version: 2.0
author: Ganesh Krishnan
tags: [oil-gas, petroleum, SEC, reserves, pricing, EIA]
---

## Purpose

Retrieve and present WTI crude oil, Henry Hub natural gas, and Mont Belvieu
NGL pricing data for reservoir engineering and SEC reserves reporting work.

Data is pulled live from the U.S. Energy Information Administration (EIA)
Open Data API — the same source NSAI uses. No spreadsheet download needed.

---

## One-time setup (first use only)

Check whether the EIA API key is configured:

```bash
echo $EIA_API_KEY
```

If empty, the user needs a free key:
1. Go to https://www.eia.gov/opendata/register.php
2. Register (90 seconds, free)
3. Add to shell: echo 'export EIA_API_KEY="your_key"' >> ~/.zshrc && source ~/.zshrc

Do this check silently. If the key is missing, walk the user through setup first.

---

## Workflow

### Step 1 — Fetch live data

```python
from nsai_pricing_mcp.nsai_client import NSAIPricingClient
import json, os

client = NSAIPricingClient(eia_api_key=os.environ.get("EIA_API_KEY"))
client.fetch()

sec     = client.current_sec_price()
monthly = client.monthly_index_prices()
fdm     = client.sec_benchmark_prices()

print("SEC:", json.dumps(sec, default=str))
print("\nLast 13 months - Monthly Index:")
print(monthly.tail(13).to_string())
print("\nLast 13 months - First-Day-of-Month:")
print(fdm.tail(13).to_string())
```

### Step 2 — Present results

**NSAI-Equivalent Pricing Data — [current month year]**
Source: U.S. Energy Information Administration (EIA)

Current SEC Benchmark Prices (SEC Rule 4-10(a)):
- WTI Crude Oil: $XX.XX / Bbl  (trailing 12-month average)
- Henry Hub Gas: $X.XXX / MMBtu (trailing 12-month average)

Note: These flat prices are held constant over proved reserve life per SEC
disclosure requirements (effective January 1, 2010).

Then render the last 12 months as a clean table.

### Step 3 — Offer follow-up

Ask: "Would you like me to:
(a) Export to Excel for your economics model?
(b) Export to CSV for ARIES or Merak?
(c) Show SEC benchmark prices for a specific year?
(d) Pull WTI or Henry Hub series for a custom analysis?"

---

## Key petroleum engineering context

SEC benchmark: 12-month unweighted arithmetic average of first-day-of-month
WTI (oil) and Henry Hub (gas) prices for the trailing 12 months prior to
fiscal year-end. Required by SEC Rule 4-10(a) since January 1, 2010.

December row = the SEC benchmark for that fiscal year-end (e.g., Dec 2024
row is the price for FY2024 10-K filings).

---

## Error handling

EIA_API_KEY missing -> Walk user through registration above
Module not found   -> pip install -e /path/to/nsai-pricing-mcp
Network timeout    -> EIA occasionally has outages, retry in a few minutes
