"""All tunable knobs in one place.

Every limit the scout respects lives here, so behaviour can be changed
without hunting through the checks. CLI flags override these defaults.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import __version__

REPO_URL = "https://github.com/Ezio-06/site-scout"
# What the scout says about itself by default: an honest bot name plus where to read about it.
HONEST_UA = f"SiteScout/{__version__} (+{REPO_URL})"

# A normal desktop Chrome identity. Scrapers we build will use one of these,
# so the scout should see the site the way our scraper will.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)

_COMMON_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
}
HONEST_HEADERS = {"User-Agent": HONEST_UA, **_COMMON_HEADERS}
BROWSER_HEADERS = {"User-Agent": BROWSER_UA, **_COMMON_HEADERS}
DEFAULT_HEADERS = HONEST_HEADERS   # kept for old imports


def headers_for(identity: str) -> dict:
    return dict(BROWSER_HEADERS if identity == "browser" else HONEST_HEADERS)


@dataclass
class ScoutConfig:
    url: str

    # --- politeness / safety caps (apply to every request from every check) ---
    min_delay: float = 2.0          # seconds between ANY two requests to the site
    max_requests: int = 60          # hard ceiling for the whole run
    max_body_bytes: int = 8_000_000 # stop downloading a single response past this
    timeout: float = 20.0           # seconds per request
    start_retries: int = 2          # retry the START page this often on a network error
    start_retry_wait: float = 5.0   # seconds before retry 1 (x2 before retry 2)
    max_runtime: float = 600.0      # whole run gives up after this (seconds)
    stop_on_block: bool = True      # first sign of blocking halts further fetching

    # --- browser ---
    browser: str = "auto"           # auto | always | never
    browser_timeout_ms: int = 30_000
    block_heavy_assets: bool = True # skip images/fonts/media in the browser (faster, less data)
    scroll_rounds: int = 2          # scrolls used to detect endless-scroll loading
    click_more: int = 2             # times to press a "load more" button (0 = never)

    # --- pace check ---
    pace: bool = True               # gentle "is everything still fine" sequence
    pace_interval: float = 6.0      # seconds between pace requests (gentle default)
    pace_requests: int = 10         # requests in the gentle sequence
    pace_fast: bool = False         # opt-in: step the pace up until a warning sign
    pace_fast_steps: tuple = (3.0, 1.5, 0.75)  # intervals tried after the gentle run
    pace_fast_per_step: int = 6

    # --- what the user is looking for (optional) ---
    expect: list = field(default_factory=list)  # text snippets the data should contain

    # --- plumbing ---
    identity: str = "honest"        # honest (SiteScout UA, default) | browser (Chrome UA from the start)
    proxy: str | None = None
    out_dir: str | None = None
    only: list = field(default_factory=list)    # run only these checks
    skip: list = field(default_factory=list)    # skip these checks
    verbose: bool = False


# Rough effort bands for quoting. Starting guesses - recalibrate after real jobs.
EFFORT_BANDS = {
    "S":  "2-5 h",
    "M":  "5-12 h",
    "L":  "12-25 h",
    "XL": "25 h+",
}
