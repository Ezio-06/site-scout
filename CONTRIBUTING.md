# Contributing to Site Scout

Thanks for looking. Site Scout is a small, early tool (v1.3), so issues with a real site that fooled it are just as
valuable as code.

## Setup

```bash
git clone https://github.com/Ezio-06/site-scout && cd site-scout
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[browser,tls,dev]"
playwright install chromium
pytest            # all tests run offline against local fake sites
ruff check .
```

## House rules

- **Every bug fix comes with a landmine.** Add a small fake site to `tests/fakesites.py` and a case to `tests/run_tests.py`
  that **fails on the old code** and passes on the fix. Model the *shape* of the problem, never copy a real site.
- **Tests never touch live websites.** CI is offline.
- **One door to the network.** Checks call `ctx.get(...)`, never `requests` directly, so the request cap, gaps,
  robots.txt and stop-on-pushback rules always apply.
- **Never a false "verified".** If a check can't confirm something, it says `unknown` or `not tested`. A wrong
  "verified" is the worst bug this tool can have.
- **Polite and passive.** No CAPTCHA solving, no attack payloads to trigger guards, no logins or paywalls, honest
  identity by default.

## Reporting a site that fooled it

Open an issue with: the URL (a listing page), the scout card (or `report.json`), what the card claimed, and what is
actually true. Don't paste anything personal from the site.

## Future development ideas

Ideas, not promises. Roughly in order of value; effort is a guess (S = an evening, M = a weekend, L = longer).

| Idea | What it adds | Effort |
|---|---|---|
| **Large random-sample benchmark** | Run hundreds of sites from a public ranking (e.g. Tranco), filtered to scrapeable categories. Score with consistency checks that must hold if a card is honest (a "verified" page can't be a thin block page; page 2 can't repeat page 1), plus a blind-labelled random subset for a real accuracy figure. | L |
| **Reach test for feed paging** | Today paging is tested for HTML and embedded data. Extend it to hidden JSON feeds and browser-only sites. | M-L |
| **Find the site's own slicing levers** | When paging stops early, discover sub-areas, categories or price filters that split the data into reachable pieces. | L |
| **Batch mode** | A file of URLs in, one summary table out. | M |
| **Starter scraper output** | Turn a verified route (feed or selector + paging) into a small, runnable scraper skeleton. | M |
| **Watch mode** | Revisit a sample of pages for hours and measure how often data changes ("checking every X min captures Y%"). | M |
| **Browser-based pace test** | Pace testing for sites that only work in a real browser. | M |
| **`--fail-on` for CI** | Exit non-zero when a chosen claim is broken or unknown, for use in pipelines. | S |
| **Generic host label on cards** | Cards print the machine name; use a neutral label by default so cards are safe to share. | S |
| **More "load more" words** | The button finder knows a handful of languages; add more. | S |
| **Keep guard signatures fresh** | Re-run `tools/build_signatures.py` against newer wafw00f releases and add tests for new signature types. | S |

Good first contributions: the S items, or a landmine for a site shape the scout gets wrong.
