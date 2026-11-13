# Site Scout

[![CI](https://github.com/Ezio-06/site-scout/actions/workflows/ci.yml/badge.svg)](https://github.com/Ezio-06/site-scout/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Give it a link. It visits the site politely and writes a scout card: how to get all the data, how hard it is, how fast you can go. Every claim says how it was checked.**

## Why it exists

Most recon tools answer "how protected is this site?" Site Scout also answers "how do I get ALL the data?"
It does this by *testing*: it fetches page 2 and the last page to see whether paging really reaches everything,
and it replays hidden data feeds outside the browser to see whether they work on their own.
Every claim on the card carries an evidence level: **verified** (tested by fetching it), **broken** (tested, does not work),
**seen** (the site says so) or **unknown**. Only verified claims belong in a quote.

## Example card

A real run of v1.3.0 against [books.toscrape.com](https://books.toscrape.com/), a site built for scraping practice
(16 requests, default polite settings). Excerpt:

```markdown
## Verdict

| | |
|---|---|
| **Method** | **B - Plain fetch + parse HTML** (easy) |
| Why | the content is in the raw page |
| Safe pace | 1 request per 6 s (tested) |
| Effort | **S** (2-5 h, starting estimate) |
| Size | ~1,000 items over ~50 list pages |

## Claims and evidence

| Claim | Evidence | Detail |
|---|---|---|
| Plain requests get the content | **verified** | found in the raw page |
| Site accepts an honest bot identity | **verified** | SiteScout/1.3.0 user agent was served normally |
| robots.txt allows the start page | **verified** |  |
| About 1,000 items exist | **seen** | the site's own count |
| Page 2 gives different items | **verified** |  |
| Every item is reachable by paging | **verified** | the last page served real results |
| 1 request per 6 s is safe | **verified** | short test only - hourly/daily limits unknown |
```

Full card: [examples/books.toscrape.com/scout_card.md](examples/books.toscrape.com/scout_card.md)
(with [report.json](examples/books.toscrape.com/report.json) and [requests.log.json](examples/books.toscrape.com/requests.log.json)).

## Quick start

```bash
pipx install git+https://github.com/Ezio-06/site-scout    # or: pip install .
pip install ".[browser]"      # optional: real-browser checks
pip install ".[tls]"          # optional: Chrome-fingerprint check (curl_cffi)
playwright install chromium   # only with the browser extra
```

Then run `site-scout https://example.com/shop`. On Windows, double-click `setup.bat` once, then `scout.bat`
(it asks for the address). Without the extras everything still runs; the card lists the browser checks as "not tested".

## Usage

```bash
site-scout https://example.com/shop --expect "Blue Widget"
```

Point it at a **listing or search-results page** (the page you would scrape), not the home page.

| Flag | What it does |
|---|---|
| `-h`, `--help` | Show the help text and exit. |
| `--expect TEXT` | Text you can see on the page (a name, a price). The scout reports where it lives. Repeatable. Most useful flag. |
| `--browser {auto,always,never}` | `auto` opens the browser only when the raw page looks incomplete (default). `always` also hunts for hidden feeds. `never` is fastest. |
| `--no-pace` | Skip the pace check. |
| `--pace-fast` | After the gentle pace check, step up carefully until the first warning sign. |
| `--pace-interval PACE_INTERVAL` | Seconds between gentle pace requests. |
| `--pace-requests PACE_REQUESTS` | Requests in the gentle pace check. |
| `--min-delay MIN_DELAY` | Minimum seconds between any two requests. |
| `--max-requests MAX_REQUESTS` | Hard cap on requests for the whole run. |
| `--identity {honest,browser}` | `honest` says "SiteScout" (default) and falls back to Chrome with ONE labelled request if refused. `browser` uses Chrome from the start. |
| `--proxy PROXY` | Send traffic through a proxy, e.g. `http://user:pass@host:port`. |
| `--out OUT` | Output folder (default `scouts/<site>_<time>`). |
| `--output -` | Write `report.json` to stdout and nothing else there; summary and log go to stderr. The card files are still written. |
| `--only ONLY` | Comma-separated check names to run. |
| `--skip SKIP` | Comma-separated check names to skip. |
| `--list-checks` | Show the available checks and exit. |
| `-v`, `--verbose` | Show DEBUG messages too (every request). |
| `--quiet` | Console shows warnings only (`scout.log` keeps everything). |
| `--version` | Show the version and exit. |

Output folder contents: `scout_card.md` (read this), `report.json` (every finding), `requests.log.json`
(every request made), `scout.log` (DEBUG log) and `snapshots/` (saved pages and feeds, reusable as scraper test material).

### Exit codes

| Code | Meaning |
|---|---|
| 0 | The run finished (the card says how hard the site is). |
| 2 | Usage error (bad flag, no URL). |
| 3 | The start page could not be reached. |
| 4 | The site blocked every identity that was tried. |
| 5 | robots.txt disallows the start page (nothing was requested from it). |
| 130 | Stopped with Ctrl+C (the card is still written, with what was found so far). |

`site-scout URL --output - > report.json` prints the report as JSON on stdout. Layout:
[docs/report.schema.json](docs/report.schema.json) (JSON Schema, `schema_version` 1).

## What it checks

13 checks, run in this order:

| # | Check | Answers |
|---|---|---|
| 10 | `robots` | What the site's bot rules allow, and whether it asks for a delay (obeyed, up to 30 s). |
| 15 | `sitemap` | Whether a free list of all pages exists, how many, which sections, "last modified" dates. |
| 20 | `fingerprint` | Response, redirects, bot guard (Cloudflare, DataDome, Akamai and others), technology, login wall. |
| 22 | `tls` | Only when plain requests are blocked: retries with curl_cffi, which copies Chrome's connection fingerprint. |
| 25 | `platform` | Shopify, WooCommerce, WordPress and Next.js have built-in feeds. Tries the feed if the site runs on one. |
| 30 | `embedded` | Data already packed in the page (JSON-LD, app-state blobs). Picks the real result list, reads the site's own count, names the search engine and its paging limit. |
| 40 | `location` | Is the data in the raw page, or only after the browser runs the page's code? Do browser cookies make plain requests work? |
| 50 | `network` | Hidden feeds the page calls, and whether they work outside the browser. Judges each as main list or side list. Skips analytics and never replays them. |
| 60 | `structure` | The repeating item list, how fragile it is, how paging works, the total count. Presses "load more" if the browser ran. |
| 62 | `reach` | **Tested paging.** Fetches page 2 and the last page; if that fails, narrows down the last working page (at most 10 fetches). Catches repeats, wrong pages and "200 OK but empty" pages. |
| 65 | `caching` | Whether the site answers "not changed" to re-checks, which makes frequent polling cheap. |
| 90 | `pace` | Gentle, spaced-out visits. Watches for blocks, captchas, shrinking pages, slowdowns and rate-limit headers; stops at the first sign. |
| 95 | `browser_room` | Runs last, in its own room: the browser work that must not disturb the plain-request checks. |

### Reading the verdict

| Method | Meaning | Typical effort |
|---|---|---|
| **A** - Platform / hidden data feed | Clean JSON straight from the site. | S |
| **B** - Plain fetch | Data is in the raw HTML or a packed data block. | S |
| **C** - Real browser | Content only appears after JavaScript runs. | M |
| **D** - Browser + bot protection | Guarded site; may need a proxy and cookie refresh. | L |
| **?** - Unclear | Something could not be tested. See "unknowns". | - |

Effort bands (S 2-5 h, M 5-12 h, L 12-25 h, XL 25 h+) are starting guesses; tune `EFFORT_BANDS` in `scout/config.py`.

## How it's tested

| Layer | What |
|---|---|
| Landmine fake sites | 29 cases in `tests/test_landmines.py`, each a small local fake site built to trip one past bug (soft blocks, fake last pages, repeating pages, honest-identity refusal and more). Every fix adds a case that fails on the old code. |
| Hidden acceptance checks | Written from the spec before the code, kept out of the builder's sight, run after each phase. |
| Holdout | Labelled real sites, scored blind, with a lockbox rule. Latest: **95% out-of-sample** (109.5/115, 22 Oct 2026, v1.1). Method, per-check table and caveats: [docs/holdout.md](docs/holdout.md). |
| CI | ruff and pytest on every push: Ubuntu / Python 3.10, Ubuntu / Python 3.13, Windows / Python 3.12 (Chromium installed). |

Design notes: [docs/design.md](docs/design.md).
## Responsible use

- **Honest identity.** Default user agent is `SiteScout/<version> (+repo URL)`.
- **robots.txt** is obeyed for the `SiteScout` token and for `*`. If it disallows the start page, nothing is requested from it (exit code 5).
- **Caps and gaps.** At most 60 requests per run, at least 2 s apart, a 10-minute runtime cap, 8 MB per download. Stops at the first real pushback (429, block page, challenge).
- **One labelled comparison.** If a site refuses the honest identity, the scout makes ONE request with a Chrome identity, says so on the card, and logs it.
- **No CAPTCHA solving** and **no attack payloads.** Bot-guard detection is passive: signatures are matched against responses the scout already received.
- Scrape public data only, and read the site's terms. A scout card is not legal advice.

## Comparison

Facts below come from the projects' READMEs and source files as read in November 2026. Projects change; check them.
"Not found" means: not found in the tools we checked (November 2026).

| | Site Scout | caniscrape | scraperecon | ScrapeProbe | wafw00f |
|---|---|---|---|---|---|
| Names the bot guard | yes | yes (uses wafw00f) | yes | yes (uses wafw00f) | yes (its purpose) |
| Tests paging (page 2, last page, repeats) | yes | not found | not found | not found (detects paging markers only) | not found |
| Replays hidden feeds outside the browser | yes | not found | not found | not found | not found |
| Compares raw page with rendered page | yes | yes (text-length diff) | not found | not found | not found |
| Reads embedded app-state JSON | yes | not found | yes | not found | not found |
| Rate / pace test | yes (gentle, stops at first sign) | yes (burst and sustained) | yes (opt-in) | not found | not found |
| Grade of evidence per claim | yes (on every claim) | not found | partly (vendor guess only) | partly (evidence records, no grade) | not found |
| Honest self-identifying user agent | yes | not found (random browser identity) | not found | yes | not found |
| Fake-site test suite | yes (29 cases) | not found | not found | not found | not found |
| Sends attack-style probes | no | not found | not found | not found | yes, when the normal request is inconclusive (per its README) |

Different goals, not a ranking: ScrapeProbe also covers terms of service and a time projection, caniscrape gives a 0-10
difficulty score, and wafw00f is the mature standard for guard identification. Site Scout uses wafw00f's signatures.

## Architecture

```mermaid
flowchart LR
    CLI[cli.py] --> R[runner]
    R --> C1[checks]
    C1 --> F[fetcher: the one door to the network]
    F --> S[(site)]
    C1 --> REP[report: card + report.json]
```

```
scout/
  cli.py              command line
  config.py           every knob and limit
  fetcher.py          the only door to the network (politeness rules live here)
  browser.py          lazy shared Chromium + network capture
  context.py          shared state passed to checks
  signals.py          bot-guard / challenge / rate-limit signatures
  guards.py           matches the 171 guard signatures (scout/data) against responses already made
  logsetup.py         logging: console + scout.log
  listing.py          item-list finder + fragility scoring
  jsontools.py        finds item lists inside JSON
  datatools.py        page data blobs, result totals, search engines, item IDs, telemetry filter
  paging.py           page-N address from page 1 + page 2, "x to y of N" reader
  report.py           verdict + scout card
  runner.py           runs checks; one failing never stops the run
  checks/             one file per check
```

### Adding a check
Drop one file into `scout/checks/`. It is discovered automatically:

```python
from .base import Check

class MyCheck(Check):
    name = "my_check"            # used by --only / --skip
    title = "What it answers"    # heading on the card
    order = 55                   # runs after `network` (50), before `structure` (60)

    def run(self, ctx):
        r = ctx.main_page()      # shared, already-fetched start page
        # ctx.get(url)           -> polite request (budget, pacing, block-stop built in)
        # ctx.rendered()         -> browser result if one was loaded
        # ctx.facts[...]         -> what earlier checks found / what later ones will read
        self.note("plain-language finding")
        return {"anything": "machine-readable"}
```

Never call `requests` directly: always go through `ctx.get` so the safety rules apply.

## Limitations and roadmap

- One connection cannot see hourly or daily quotas. The card says so every time.
- Results hold for the network the scout ran from (shown on the card). Datacenter IPs are often treated differently; re-run from a server before moving a collector there.
- The holdout result was measured on v1.1, and those sites are no longer a true holdout ([docs/holdout.md](docs/holdout.md)).
- Effort bands are starting guesses, not measured quotes.

Roadmap:
- Reach test for feed paging and browser-only sites (today it covers HTML and embedded paging).
- Batch mode (many URLs in one run).
- Starter-scraper output generated from the card.
- A fresh labelled holdout batch, scored on the current version.
- Watch mode: measure how often data changes.

## Credits and licence

MIT, see `LICENSE`. Bot-guard signatures come from [wafw00f](https://github.com/EnableSecurity/wafw00f)
(BSD-3-Clause); see `NOTICE` and `scout/data/WAFW00F_LICENSE`. Built on requests, BeautifulSoup, lxml, Playwright,
protego (robots.txt), curl_cffi and chompjs.

Built with help from AI coding tools.
