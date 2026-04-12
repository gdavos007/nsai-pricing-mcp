"""
Fetcher for the NSAI pricing spreadsheet.

Tries multiple strategies in order:
  1. Scrape raw HTML page for any .xlsx/.xls href links
  2. Try common WordPress upload URL patterns (guessed from current date)
  3. Prompt user to download manually and use load_spreadsheet_from_path()
"""

import json
import re
from datetime import datetime
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

NSAI_PRICING_URL = "https://netherlandsewell.com/resources/pricing-data/"

# Both the live site and the WP Engine host may serve files
UPLOAD_BASES = [
    "https://netherlandsewell.com/wp-content/uploads",
    "https://nsaiprod.wpengine.com/wp-content/uploads",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _scrape_page_for_xlsx(url: str) -> str | None:
    """Fetch raw HTML and look for any .xlsx / .xls links or embedded JS URLs."""
    try:
        resp = httpx.get(url, timeout=20, follow_redirects=True, headers=HEADERS)
        resp.raise_for_status()
        html = resp.text

        # 1. BeautifulSoup link scan
        soup = BeautifulSoup(html, "lxml")
        for tag in soup.find_all("a", href=True):
            href: str = tag["href"]
            if re.search(r"\.(xlsx|xls)(\?.*)?$", href, re.IGNORECASE):
                return href if href.startswith("http") else f"https://netherlandsewell.com{href}"

        # 2. Regex scan of raw HTML (catches links in JS strings)
        matches = re.findall(
            r'https?://[^\s"\'>]+\.xlsx(?:\?[^\s"\'>]*)?',
            html,
            re.IGNORECASE,
        )
        if matches:
            return matches[0]

        return None
    except Exception:
        return None


def _candidate_urls() -> list[str]:
    """
    Generate likely WordPress upload URLs based on the current date.
    NSAI publishes monthly; we probe the last 3 months in case of delays.
    """
    now = datetime.now()
    candidates: list[str] = []

    # Common filename patterns observed for NSAI
    filename_patterns = [
        "NSAI-Pricing-Data.xlsx",
        "NSAI_Pricing_Data.xlsx",
        "Pricing-Data.xlsx",
        "pricing-data.xlsx",
        "NSAIPricingData.xlsx",
        "NSAI-Pricing.xlsx",
    ]

    for base in UPLOAD_BASES:
        year = now.year
        month = now.month
        for _ in range(4):  # probe up to 4 months back
            ym = f"{year}/{month:02d}"
            for fname in filename_patterns:
                candidates.append(f"{base}/{ym}/{fname}")
            month -= 1
            if month == 0:
                month = 12
                year -= 1

    return candidates


def _probe_urls(candidates: list[str]) -> str | None:
    """HEAD-request each candidate URL; return first that responds with 200."""
    with httpx.Client(timeout=8, follow_redirects=True, headers=HEADERS) as client:
        for url in candidates:
            try:
                resp = client.head(url)
                if resp.status_code == 200:
                    return url
            except Exception:
                continue
    return None


def find_spreadsheet_url() -> str:
    """
    Locate the NSAI pricing spreadsheet URL.
    Returns the URL if found, raises ValueError with instructions otherwise.
    """
    # Strategy 1: parse the NSAI page HTML
    url = _scrape_page_for_xlsx(NSAI_PRICING_URL)
    if url:
        return url

    # Strategy 2: probe common WP upload paths
    candidates = _candidate_urls()
    url = _probe_urls(candidates)
    if url:
        return url

    raise ValueError(
        "Could not automatically locate the NSAI pricing spreadsheet.\n\n"
        "The NSAI page appears to require JavaScript rendering to expose the download link.\n\n"
        "Manual steps:\n"
        f"  1. Open {NSAI_PRICING_URL} in your browser\n"
        "  2. Download the spreadsheet (Excel file)\n"
        "  3. Call the MCP tool: load_spreadsheet_from_path(file_path='/path/to/file.xlsx')"
    )


def download_spreadsheet(cache_dir: Path) -> Path:
    """
    Download the NSAI pricing spreadsheet to cache_dir.
    Returns the path to the downloaded file.
    """
    xlsx_url = find_spreadsheet_url()
    cache_file = cache_dir / "nsai_pricing_latest.xlsx"
    metadata_file = cache_dir / "metadata.json"

    with httpx.Client(timeout=60, follow_redirects=True, headers=HEADERS) as client:
        resp = client.get(xlsx_url)
        resp.raise_for_status()
        cache_file.write_bytes(resp.content)

    metadata = {
        "url": xlsx_url,
        "downloaded_at": datetime.now().isoformat(),
        "file_size_bytes": len(resp.content),
    }
    metadata_file.write_text(json.dumps(metadata, indent=2))

    return cache_file


def get_cached_metadata(cache_dir: Path) -> dict | None:
    """Return cached download metadata if it exists."""
    metadata_file = cache_dir / "metadata.json"
    if metadata_file.exists():
        try:
            return json.loads(metadata_file.read_text())
        except Exception:
            return None
    return None
