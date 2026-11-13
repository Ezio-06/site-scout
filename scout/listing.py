"""Find the repeating 'list of items' in a page and judge how fragile it is
to scrape. Pure functions over a parsed page - no network."""
from __future__ import annotations

import re
from collections import Counter

SKIP_TAGS = {"nav", "header", "footer", "head", "script", "style", "select", "svg",
             "noscript", "template", "form", "datalist", "picture"}
# never an "item": code, styling, page plumbing (22 Oct: 12 <script> tags were read as 12 items)
NON_CONTENT = {"script", "style", "noscript", "template", "link", "meta", "svg", "iframe", "br", "hr",
               "input", "base", "object", "embed", "source", "track", "param"}
PRICE = re.compile(r"(?:[$€£₽¥₹]|usd|eur|руб|pkr|rs\.?)\s?\d|\d[\d\s,.]*\s?(?:[$€£₽¥₹]|usd|eur|руб|₽)", re.I)
DATE = re.compile(r"\b\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}\b|\b(ago|hours?|minutes?|days?)\b", re.I)

HASHED = [re.compile(p) for p in (
    r"^(css|sc|jsx|emotion|styled|tw|svelte|chakra|mui|makeStyles)-[A-Za-z0-9_-]{4,}$",
    r"^[A-Za-z]+_[A-Za-z0-9-]+__[A-Za-z0-9_-]{4,}$",     # CSS modules: Card_title__x8F2k
    r"^_[A-Za-z0-9_-]{5,}$",
    r"^(?=[a-z0-9]*\d[a-z0-9]*\d)[a-z0-9]{6,}$",          # random-looking: a8k2f9x
    r"^[A-Za-z]{1,3}-?[0-9][A-Za-z0-9]{4,}$",
)]
UTILITY = re.compile(
    r"^(?:-?[mp][trblxy]?-\S+|flex\S*|grid\S*|block|inline\S*|hidden|relative|absolute|w-\S+|h-\S+|"
    r"min-\S+|max-\S+|text-\S+|bg-\S+|font-\S+|rounded\S*|border\S*|gap-\S+|items-\S+|justify-\S+|"
    r"col-\S+|row-\S+|d-\S+|shadow\S*|overflow-\S+|leading-\S+|tracking-\S+|space-\S+|"
    r"(?:sm|md|lg|xl|2xl|hover|focus):\S+)$")
STABLE_ATTRS = ("data-testid", "data-test", "data-qa", "data-cy", "itemprop", "itemtype",
                "data-id", "data-sku", "data-product-id", "data-item-id", "aria-label")


def _shared_classes(kids) -> dict:
    """tag -> classes that most siblings of that tag carry. Per-item classes
    (post-123, product_cat-x, first/last, is-active) are ignored, so items that
    differ only in those still group together (22 Oct: WooCommerce products)."""
    by_tag, counts = Counter(k.name for k in kids), {}
    for k in kids:
        for c in set(k.get("class") or []):
            counts[(k.name, c)] = counts.get((k.name, c), 0) + 1
    return {tag: {c for (t, c), n in counts.items() if t == tag and n >= max(2, 0.8 * total)}
            for tag, total in by_tag.items()}


def _sig(el, shared=None) -> str:
    classes = el.get("class") or []
    if shared is not None:
        classes = [c for c in classes if c in shared.get(el.name, ())]
    return el.name + "." + ".".join(sorted(set(classes)))


def _css(el) -> str:
    if el.get("id") and not any(rx.match(el["id"]) for rx in HASHED):
        return f"{el.name}#{el['id']}"
    classes = [c for c in (el.get("class") or []) if not UTILITY.match(c)]
    return el.name + "".join("." + c for c in classes[:2])


def _path(el, depth=3) -> str:
    parts = []
    while el is not None and getattr(el, "name", None) not in (None, "[document]", "html", "body") and depth:
        parts.append(_css(el))
        if "#" in parts[-1]:
            break
        el = el.parent
        depth -= 1
    return " > ".join(reversed(parts)) or "body"


def find_listing(soup) -> dict | None:
    best, best_score = None, 0.0
    for parent in soup.find_all(True):
        if parent.name in SKIP_TAGS:
            continue
        kids = [c for c in parent.find_all(True, recursive=False) if c.name not in NON_CONTENT]
        if len(kids) < 4:
            continue
        shared = _shared_classes(kids)
        sig, n = Counter(_sig(k, shared) for k in kids).most_common(1)[0]
        if n < 4:
            continue
        members = [k for k in kids if _sig(k, shared) == sig]
        sample = members[:30]
        lens = [len(_visible(m)) for m in sample]
        avg = sum(lens) / len(lens)
        if avg < 15:
            continue
        links = sum(1 for m in sample if m.name == "a" or m.find("a", href=True)) / len(sample)
        imgs = sum(1 for m in sample if m.find(["img", "picture"])) / len(sample)
        prices = sum(1 for m in sample if PRICE.search(m.get_text(" ", strip=True))) / len(sample)
        score = n * min(avg, 250) * (1 + links) * (1 + 0.5 * imgs) * (1 + prices)
        if parent.name in ("ul", "ol") and avg < 40:
            score *= 0.3          # probably a menu
        if score > best_score:
            if parent.find_parent(["nav", "header", "footer"]):
                continue
            best_score, best = score, (parent, members, avg, links, imgs, prices)
    if not best:
        return None
    parent, members, avg, links, imgs, prices = best
    first = members[0]
    item_sel = _css(first) if (first.get("class") or first.get("id")) else f"{_css(parent)} > {first.name}"
    return {
        "count": len(members),
        "container": _path(parent),
        "item_selector": item_sel,
        "avg_text_chars": round(avg),
        "fields": {"link": round(links, 2), "image": round(imgs, 2), "price": round(prices, 2),
                   "date": round(sum(1 for m in members[:30] if DATE.search(m.get_text(" "))) /
                                 min(len(members), 30), 2)},
        "samples": [re_ws(m.get_text(" ", strip=True))[:140] for m in members[:3]],
        "fragility": fragility(members[:15]),
        "_members": members,
    }


def _visible(el) -> str:
    """Text a person would see in this element (skips code inside it)."""
    return " ".join(s.strip() for s in el.find_all(string=True)
                    if s.parent is not None and s.parent.name not in NON_CONTENT and s.strip())


def re_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s)


def fragility(items) -> dict:
    hashed = utility = semantic = 0
    stable_items = 0
    seen = 0
    for it in items:
        has_stable = False
        for el in [it] + it.find_all(True)[:40]:
            seen += 1
            for c in el.get("class") or []:
                if any(rx.match(c) for rx in HASHED):
                    hashed += 1
                elif UTILITY.match(c):
                    utility += 1
                else:
                    semantic += 1
            if any(el.has_attr(a) for a in STABLE_ATTRS):
                has_stable = True
        stable_items += has_stable
    total = hashed + utility + semantic
    if total == 0:   # no class names at all: selectors must lean on tag order
        level = "medium"
    else:
        share = (hashed + 0.5 * utility) / total
        level = "low" if share < 0.2 else "medium" if share < 0.5 else "high"
    stable = stable_items >= max(1, len(items) // 2)
    if stable and level != "low":
        level = {"high": "medium", "medium": "low"}[level]
    return {"level": level, "random_looking_classes": hashed, "utility_classes": utility,
            "meaningful_classes": semantic, "stable_attributes": stable,
            "no_classes": total == 0}
