# Design notes

Six decisions that shape the code.

## Evidence levels

Every claim on a card is tagged **verified** (tested by fetching it), **broken** (tested and it failed), **seen**
(the site states it, for example a result count) or **unknown** (could not be tested). The point is the quote: a scraping
estimate should rest on verified claims, and the card shows which ones those are. "Unknown" is a normal, honest
answer. A check that cannot test something says so rather than guessing. The "Not tested / unknowns" section lists
what a short run from one connection cannot see, such as hourly or daily quotas.

## One door to the network

All requests go through `scout/fetcher.py`. Checks call `ctx.get(url)`, never `requests` directly. The fetcher holds
every politeness rule in one place: the request cap (60), the minimum gap (2 s), the runtime cap, the download size cap,
robots.txt, the identity, and the stop at the first real pushback. Every request is written to `requests.log.json`.
Because there is only one door, a new check cannot bypass the rules, and the log is complete by construction.

## Landmine method

Each bug found in a real run becomes a small fake site in `tests/fakesites.py` plus a case in the landmine table
(28 cases, one pytest test each). A case must fail on the old code before the fix goes in. The fake sites model traps such
as a "last page" that returns 200 with fallback content, pages that repeat, a soft block that shrinks the page, or a
site that refuses the honest identity. The aim is to fix the pattern, not the one site that exposed it. The browser
cases skip when Playwright is not installed.

## Honest identity

The scout calls itself `SiteScout/<version> (+repo URL)` and obeys robots.txt for its own token and for `*`.
Sending a Chrome identity by default would make it impossible to tell whether a site blocks bots or only blocks Python.
The rule: try honest first. If the site refuses, make ONE request with a Chrome identity, label it on the card and in
the log, and read robots.txt as that identity would see it before the comparison. The user can ask for Chrome from
the start with `--identity browser`. No request is sent under an identity the card does not name.

## Passive guard matching

Bot-guard names come from 171 signatures derived from wafw00f's plugins (BSD-3, credited in `NOTICE`). The scout never
sends attack-style probes. `scout/guards.py` matches the signatures against responses the scout already
received for its normal checks: headers, cookies, body text. A guard seen this way is reported with its evidence;
a guard that would only show under attack is not claimed.

## The thin-page rule

A block is often a normal-looking 200 reply with a much smaller body. So the pace and reach checks compare each page
with the size and content of the start page. A page that shrinks sharply, loses its items, or repeats earlier items is
treated as a soft block or a fallback page, and the run stops or the claim is downgraded. The rule prefers a false alarm
to silently counting a block page as data. That choice has a cost: a 304 "not changed" reply (empty by design) was once
mistaken for a soft block, which is why the example card has no pace result. It is a known bug, listed in the README.
