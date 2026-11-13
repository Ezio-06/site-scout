"""Site Scout - answers 'how do we scrape this site, and how hard can we push?'"""
from __future__ import annotations

import re
from importlib import metadata
from pathlib import Path


def _version() -> str:
    """Single source of truth is pyproject.toml. A source checkout reads it directly (so it is
    never stale); an installed copy asks its own package metadata."""
    toml = Path(__file__).resolve().parent.parent / "pyproject.toml"
    if toml.is_file():
        m = re.search(r'^version\s*=\s*"([^"]+)"', toml.read_text(encoding="utf-8"), re.M)
        if m:
            return m.group(1)
    try:
        return metadata.version("site-scout")
    except metadata.PackageNotFoundError:
        return "0+unknown"


__version__ = _version()
