# Scout card - books.toscrape.com

`https://books.toscrape.com/catalogue/category/books_1/index.html`  
2026-11-13 23:09 - 16 requests - 73 s - ran from `home-pc`  
identity: honest (SiteScout user agent)  

## Verdict

| | |
|---|---|
| **Method** | **B - Plain fetch + parse HTML** (easy) |
| Why | the content is in the raw page |
| Safe pace | 1 request per 6 s (tested) |
| Effort | **S** (2-5 h, starting estimate) |
| Size | ~1,000 items over ~50 list pages |

## Claims and evidence

Only *verified* claims should go into a quote. Results are for this network only - re-run from a server before moving a collector there.

| Claim | Evidence | Detail |
|---|---|---|
| Plain requests get the content | **verified** | found in the raw page |
| Site accepts an honest bot identity | **verified** | SiteScout/1.3.0 user agent was served normally |
| robots.txt allows the start page | **verified** |  |
| About 1,000 items exist | **seen** | the site's own count |
| Page 2 gives different items | **verified** |  |
| Every item is reachable by paging | **verified** | the last page served real results |
| 1 request per 6 s is safe | **verified** | short test only - hourly/daily limits unknown |

## Findings

### Bot rules (robots.txt)
- No robots.txt rules (missing) - no stated restrictions.

### Sitemap (page list)
- No sitemap found - pages have to be discovered by following links.

### First look: response, guards, technology
- Plain request works: HTTP 200, 49 KB in 645 ms.
- Built with: jQuery.
- Replies carry change markers (ETag/Last-Modified) - see caching check.

### Browser-identity requests (curl_cffi) *(skipped)*
_plain requests already work_

### Platform shortcuts (built-in data feeds) *(skipped)*
_no platform with a known public feed detected_

### Data packed inside the page
- No packed data blocks - data has to come from the page layout or a feed.

### Where the data lives (raw page vs browser)
- Raw page already has the content - no browser needed (pass --browser always to also hunt for hidden data feeds).

### Hidden data feeds *(skipped)*
_browser was not used, no feed hints in source_

### Page structure, paging and fragility
- Item list: 20 items, selector `li` inside `section > div > ol.row`; items have link, image, price.
- Sample item: "A Light in the ... £51.77 In stock Add to basket"
- Fragility: LOW - meaningful class names / stable attributes; a layout scraper should hold up.
- Page addresses follow: https://books.toscrape.com/catalogue/category/books_1/page-{n}.html (page number, page 2 = https://books.toscrape.com/catalogue/category/books_1/page-2.html). Not trusted until the reach check fetches it.
- Paging: 'next' link.
- Site claims about 1,000 items -> about 50 list pages at 20 per page. Use this number later as a coverage check (did we get them all?).

### Paging and reach (tested)
- Page 2 works: 20 items, none repeated (https://books.toscrape.com/catalogue/category/books_1/page-2.html).
- The last page (page 50) still serves real results - every item is reachable by paging.

### Cheap re-checks (change markers)
- Site answers 'not changed' (304, 0 bytes) instead of resending 49 KB - re-checks can be frequent and cheap.

### Pace check (does the site stay normal?)
- Visiting 5 of the site's pages, 1 request every 6 s, 10 requests (~60 s).
- No trouble up to 1 request per 6 s (~10/min) over 10 requests / 60 s. Short test from one connection - treat it as 'fine so far', not a guarantee.
- Faster paces not tested (add --pace-fast to step up carefully).
- Suggested scraper pace: 1 request per 6 s (the tested gentle pace).

### Browser check (run last, in its own room) *(skipped)*
_nothing was waiting for the browser_

## Not tested / unknowns

- Hidden data feeds: browser not used because the raw page looked complete (run with --browser always to hunt for feeds).
- Speeds faster than the gentle pace (use --pace-fast).
- Long-window limits (hourly/daily quotas) - a short test cannot see them.
- How often the data changes (needs watch mode, planned for v2).

## Files

- `report.json` - every finding, machine-readable
- `requests.log.json` - every request the scout made
- `snapshots/` - saved pages and feeds (reuse them as test material for the scraper)
