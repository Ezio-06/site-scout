# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.3.0]

### Added
- Guard signature file (171 signatures) used passively against responses already received.
- Machine output: `schema_version` 1, `docs/report.schema.json`, and `--output -` (JSON on stdout, summary on stderr).
- Exit codes 0, 2, 3, 4, 5 and 130, documented in `--help`.
- Logging: console INFO, `-v` for DEBUG, `--quiet` for warnings only, and a `scout.log` per run.
- pytest wiring (one test per landmine case; browser cases skip without Playwright) and ruff configuration.
- CI on Ubuntu (Python 3.10, 3.13) and Windows (Python 3.12).
- Real example card for a scraping practice site in `examples/`, a rewritten README, `docs/holdout.md` and `docs/design.md`.

### Changed
- Prints replaced by logging.

### Fixed
- A crash when robots.txt forbids the start page; the card now has a verdict.

## [1.2.0]

### Added
- Honest identity by default (`SiteScout/<version>`), with `--identity browser`.
- One labelled Chrome-identity comparison request when a site refuses the honest identity.
- robots.txt checked for the `SiteScout` token, and as the browser identity sees it before the comparison request.
- Notes on the card when optional extras are missing.
- Packaging (`pyproject.toml`, `site-scout` command), MIT licence, `NOTICE`, `scout.bat`.

### Changed
- The unlabelled python-requests probe is skipped while running honest.

## Earlier

- 1.1 and before: the first set of checks (robots, sitemap, fingerprint, tls, platform, embedded, location, network,
  structure, reach, caching, pace), the scout card with evidence levels, and the landmine fake-site suite.
