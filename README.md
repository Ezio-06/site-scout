# Site Scout

Give it a link. It visits the site politely and writes a **scout card** that answers:

- **How do we scrape this?** Data feed, plain fetch, or real browser.
- **How hard is it?** Bot protection, fragile layout, paging style.
- **How fast can we go?** Stated rules plus a gentle pace check.
- **How big is the job?** Item counts, pages needed, and an effort band for quoting.
- **Can paging really reach everything?** It *fetches* page 2 and the deepest pages instead of assuming.

Every claim on the card carries an evidence level - **verified** (tested by fetching it), **broken** (tested, doesn't work), **seen** (the site says so), **unknown** - in a "Claims and evidence" table. Only verified claims should go into a quote.

Run it before every scraping job, so the engine is built with full context instead of guesswork.

---

## Setup (once)

```bash
pip install .                       # core: plain-request checks
pip install .[browser,tls]          # optional: real-browser checks + Chrome-fingerprint check
python -m playwright install chromium   # only with the browser extra
```

Then run `site-scout https://example.com/shop` (or `python run_scout.py ...`). On Windows, double-click
`setup.bat` once, then `scout.bat`.

By default the scout identifies itself honestly (`SiteScout/<version> (+repo URL)`) and obeys robots.txt rules
for both `SiteScout` and `*`. If a site refuses that identity, it makes ONE labelled comparison request with a
Chrome identity and says so on the card. `--identity browser` uses Chrome from the start.

Without Playwright everything still runs. The card just lists the browser checks as "not tested".

## Use

```bash
python run_scout.py https://example.com/shop
```

Point it at a **listing / search-results page** (the page you'd scrape), not the home page.

| Flag | What it does |
|---|---|
| `--expect "Blue Widget"` | Text you can see on the page (a name, a price). The scout reports *where* it lives: raw page, packed data, browser only, or a feed. Repeatable. **Most useful flag.** |
| `--browser always` | Always open the real browser, even if the raw page looks complete. Use it to hunt for hidden data feeds. |
| `--browser never` | Never open the browser. Fastest run. |
| `--pace-fast` | After the gentle pace check, step up carefully (3s → 1.5s → 0.75s) until the first warning sign. Opt-in. |
| `--no-pace` | Skip the pace check entirely. |
| `--pace-interval 6` `--pace-requests 10` | Settings for the gentle pace check. |
| `--min-delay 2` | Minimum gap between *any* two requests. |
| `--max-requests 60` | Hard cap for the whole run. |
| `--proxy http://user:pass@host:port` | Send traffic through a proxy (plain requests and browser). |
| `--only robots,fingerprint` / `--skip pace` | Run a subset of checks. |
| `--list-checks` | Show all checks. |
| `-v` | Print every request as it happens. |

Output goes to `scouts/<site>_<time>/`:

- `scout_card.md`: the card. Read this.
- `report.json`: every finding, machine-readable.
- `requests.log.json`: every request the scout made (for accountability).
- `snapshots/`: the saved raw page, browser page, robots.txt and feeds. **Reuse these as test material for the scraper**, so its tests run without hitting the site.

## What it checks (in order)

| # | Check | Answers |
|---|---|---|
| 10 | `robots` | What the site's bot rules allow, and whether it asks for a delay between visits (the scout obeys it, up to 30 s). |
| 15 | `sitemap` | Whether a free list of all pages exists, how many pages, which sections, and whether pages carry "last modified" dates. |
| 20 | `fingerprint` | Response, redirects, bot guard (Cloudflare, DataDome, Akamai…), technology, login wall, and whether the site filters the default Python identity. |
| 22 | `tls` | Only when plain requests are blocked: tries again with **curl_cffi**, which copies Chrome's connection fingerprint. If that gets in, a cheap plain scraper still works and the rest of the run uses it. |
| 25 | `platform` | Shopify / WooCommerce / WordPress / Next.js have built-in data feeds. If the site runs on one, it tries the feed. |
| 30 | `embedded` | Clean data already packed in the page: JSON-LD and **any** app-state blob (`window.state = {...}`, `__NEXT_DATA__`, Redux/Apollo/Nuxt state, `JSON.parse("...")`). Picks the array that is the actual result list (not a menu), reads the site's own result count, and names the search engine behind it (Algolia, Elasticsearch, Solr…) with that engine's known paging limit. |
| 40 | `location` | Is the data in the raw page, or only after the browser runs the page's code? If blocked, does a real browser get in, and do its cookies make plain requests work? |
| 50 | `network` | Hidden data feeds the page calls behind the scenes, and whether they work *outside* the browser (plain / with headers / not at all). Judges each feed as **main list** or **side list** (promoted box) by comparing its own result count with the page's. Skips analytics/tracking calls and never replays them. Checks robots.txt of every host it calls. |
| 60 | `structure` | The repeating item list (selector, count, sample), how fragile it is (random class names = breaks often), how paging works, and the total item count. If the browser ran, it **presses the "load more" button** and reports what it really does: fetches a feed, goes to a link, or only reveals items already in the page. |
| 62 | `reach` | **Tested paging.** Builds page N's address from the site's own next link, fetches page 2 (different items?), the last page, and - if that fails - narrows down the last working page (≤ 10 fetches). Catches repeats, wrong pages and "200 OK but empty/fallback" pages using item IDs, the "26 to 50 of …" text and the page number in the data. If paging stops early, says what share is reachable and how many slices the job needs. |
| 65 | `caching` | Whether the site answers "not changed" to re-checks, which makes frequent polling cheap. |
| 90 | `pace` | Polite, spaced-out visits across a few of the site's pages. It watches for blocks, captchas, shrinking pages, slowdowns and the site's own rate-limit headers, and stops at the first sign. |

### Reading the verdict

| Method | Meaning | Typical effort |
|---|---|---|
| **A** - Platform / hidden data feed | Clean JSON straight from the site. Best route: fast and sturdy. | S |
| **B** - Plain fetch | Data is in the raw HTML or a packed data block. | S |
| **C** - Real browser | Content only appears after JavaScript runs. Slower, and uses more memory. | M |
| **D** - Browser + bot protection | Guarded site. Harder, and may need a proxy and cookie refresh. | L |
| **?** - Unclear | Something couldn't be tested. See "unknowns". | - |

Effort bands (S 2–5 h, M 5–12 h, L 12–25 h, XL 25 h+) are **starting guesses**. Tune `EFFORT_BANDS` in `scout/config.py` after real jobs. The band goes up one step for each complication (fragile layout, login wall, strict pace, scroll paging with no feed, many pages).

## Safety defaults

- At most 60 requests per run, at least 2 s apart, a 10-minute runtime cap, and an 8 MB cap per download.
- **Stops everything at the first real sign of pushback** (429, a block page, a challenge).
- Obeys robots.txt crawl-delay. Skips the pace check when robots.txt disallows the page.
- The browser skips images, fonts and video, opens only when needed, and is reused across checks.
- A short test from one connection can't see hourly or daily quotas. The card always says so.
- Results hold **for the network the scout ran from** (shown on the card). Datacenter IPs are often treated differently - re-run from a server before moving a collector there.
- One check crashing never stops the run (the card names the crash). **Ctrl+C** stops politely and still writes the card with what was found.

## Built on existing tools

Where a solid library exists, the scout uses it instead of home-made code:
**requests** (HTTP), **BeautifulSoup + lxml** (parsing), **Playwright** (browser), **protego** (robots.txt, the parser Scrapy uses), **curl_cffi** (browser-fingerprint requests) and **chompjs** (reads JavaScript data blobs that strict JSON can't).
**extruct** (structured-data extraction) was considered but left out: one of its dependencies fails to install on current Python versions.

## Adding a check

Drop one file into `scout/checks/`. It's discovered automatically:

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

## Layout

```
run_scout.py          command line
scout/
  config.py           every knob and limit
  fetcher.py          the only door to the network (politeness rules live here)
  browser.py          lazy shared Chromium + network capture
  context.py          shared state passed to checks
  signals.py          bot-guard / challenge / rate-limit signatures
  listing.py          item-list finder + fragility scoring
  jsontools.py        finds item lists inside JSON
  datatools.py        page data blobs, result totals, search engines, item IDs, telemetry filter
  paging.py           page-N address from page 1 + page 2, "x to y of N" reader
  report.py           verdict + scout card
  runner.py           runs checks; one failing never stops the run
  checks/             one file per check
```

## Planned (v2)

- **Watch mode:** revisit a sample of pages for hours and measure how often data changes. The result is "checking every X min captures Y% of changes".
- Browser-based pace testing for guarded sites.
- Proxy rotation plug-in.
- Reach test for **feed** paging and **browser-only** sites (v1.1 tests HTML/embedded paging only).
- Find the site's own slicing levers (sub-areas, price filters) automatically when paging stops early.

---

## License and credits

MIT, see `LICENSE`. Bot-guard signatures come from [wafw00f](https://github.com/EnableSecurity/wafw00f)
(BSD-3-Clause); see `NOTICE` and `scout/data/WAFW00F_LICENSE`.
