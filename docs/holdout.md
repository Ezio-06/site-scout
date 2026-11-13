# Holdout: how accuracy is measured

## Method

- **Labelled sites.** Before any run, each site gets an expected answer written down by hand: which method fits (A/B/C/D/?),
  whether a browser is needed, whether a bot guard is present, how paging works, whether page 2 works, the total count.
  Each label has a confidence (high, medium, low).
- **Scored blind.** The scout runs on the sites with the labels hidden from the code under test. A script compares the card
  with the labels, gives full or half credit (half where the label lists an acceptable alternative), and writes a score file.
- **Lockbox rule.** A site that the scout was fixed against is retired and counted as in-sample from then on.
  In-sample results are reported separately and are not evidence of accuracy. Each retired site is replaced by a fresh one.
- **Misses are read, not hidden.** Each miss is a scout bug, a wrong label, or a site that changed. The label is checked first.

## Latest scored run

22 October 2026, scout v1.1. 35 out-of-sample sites and 15 in-sample (retired) sites. Every site returned a report and no check crashed.

**Out-of-sample overall: 95% (109.5/115).**

| Check | All out-of-sample | Test sites | Real sites | In-sample (retired) |
|---|---|---|---|---|
| overall | 95% (109.5/115) | 95% (64.5/68) | 96% (45/47) | 82% (42/51) |
| tier (method A/B/C/D/?) | 90% (30.5/34) | 84% (13.5/16) | 94% (17/18) | 67% (10/15) |
| browser needed | 100% (20/20) | 100% (11/11) | 100% (9/9) | 78% (7/9) |
| guard detected | 100% (27/27) | 100% (16/16) | 100% (11/11) | 93% (13/14) |
| robots | 100% (1/1) | 100% (1/1) | - | - |
| platform | 100% (3/3) | 100% (2/2) | 100% (1/1) | 100% (1/1) |
| paging style | 94% (17/18) | 100% (11/11) | 86% (6/7) | 100% (9/9) |
| page 2 works | 88% (7/8) | 86% (6/7) | 100% (1/1) | 67% (2/3) |
| total count | 100% (4/4) | 100% (4/4) | - | - |

"Test sites" are sites built for scraping practice (for example toscrape, scrapethissite, webscraper.io, httpbin).
"Real sites" are live commercial or public sites; they appear here only as counts.

Where it lost points: the method letter on pages that can be scraped more than one way (most of these were half credit),
heavily protected real sites that the scout rated as plain fetch instead of guarded, and a few paging and page-2 cases.
Fifteen sites had at least one miss: 8 out-of-sample and 7 in-sample.

## Caveats

1. **Earlier version.** The run is from v1.1. The current release is v1.3.0 and has not been re-scored.
2. **No longer a true holdout.** These sites have since been seen twice (in the run and while reading its misses), so they cannot serve as an unseen test again.
3. **Fresh batch needed.** A new labelled batch, scored on the current version, is on the roadmap. Until then treat 95% as a measurement of v1.1 on those sites, not a promise.
