"""Page-address builder: every shape must rebuild the site's own links exactly.
    python tests/test_paging.py           (from the repo root)
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scout import paging as pg

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

fails = 0
for u1, u2, per, n, want in CASES:
    t = pg.template_from(u1, u2, per)
    got = pg.url_for(t, n) if t else None
    ok = got == want
    fails += not ok
    print(("  ok    " if ok else "  FAIL  ") + f"{u2}  ->  page {n}: {got}")
print("all passed" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
