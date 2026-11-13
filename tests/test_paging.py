"""Page-address builder: every shape must rebuild the site's own links exactly.
    pytest tests/test_paging.py     (or: python tests/test_paging.py)
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scout import paging as pg  # noqa: E402

CASES = [  # page 1, page 2 (as the site links it), per page, page to build, expected
    # the leading zero in 'region-01' was dropped -> false "paging broken"
    ("https://shop.example.com/find-agent/region-01",
     "https://shop.example.com/find-agent/region-01?page=2", None, 3,
     "https://shop.example.com/find-agent/region-01?page=3"),
    ("https://x.com/Homes/Lahore-1-1.html", "https://x.com/Homes/Lahore-1-2.html", None, 5,
     "https://x.com/Homes/Lahore-1-5.html"),
    ("https://x.com/jobs?q=a", "https://x.com/jobs?q=a&start=25", 25, 3, "https://x.com/jobs?q=a&start=50"),
    ("https://x.com/2026/03/news", "https://x.com/2026/03/news/page/2/", None, 4,
     "https://x.com/2026/03/news/page/4/"),
    ("https://x.com/blog/", "https://x.com/blog/page/2/", None, 9, "https://x.com/blog/page/9/"),
    ("https://x.com/c?a=1&page=1", "https://x.com/c?page=2&a=1", None, 3, "https://x.com/c?a=1&page=3"),
]


@pytest.mark.parametrize("u1,u2,per,n,want", CASES)
def test_page_address(u1, u2, per, n, want):
    t = pg.template_from(u1, u2, per)
    assert (pg.url_for(t, n) if t else None) == want


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
