"""Page structure: the item list, how pages continue, how many items exist,
and how fragile a layout-based scraper would be."""
from __future__ import annotations

import math
import re

from .base import Check, SkipCheck
from .. import htmltools, paging as pg

NEXT_WORDS = re.compile(r"^\s*(next|next page|›|»|→|>|>>|далее|следующая|след\.?|suivant|weiter|"
                        r"siguiente|próxima|avanti|التالي|下一页)\s*$", re.I)
# any query parameter whose NAME is about pages (page, page_num, pageNumber, pagina, pg, pn...),
# plus the classic offset names and /page/N, /p2, /N.html path styles
PAGE_HREF = re.compile(r"[?&][\w-]*(?:page|pag|pg)[\w-]*=\d+|[?&](?:p|pn|offset|start|from|skip|o)=\d+|"
                       r"/page/\d+|/p\d+/?$|/pagination/\d+", re.I)
from ..signals import LOAD_MORE
from .. import signals
TOTAL = re.compile(
    r"\d+\s*[-–]\s*\d+\s+(?:of|из|von|de)\s+(\d[\d,.]{0,12}\d|\d)"
    r"|(\d[\d,.\u00a0\u202f]{0,12}\d|\d)\s+(?:results|items|products|listings|offers|ads|jobs|properties|"
    r"homes|cars|vehicles|matches|товар\w*|предложени\w*|лот\w*|объявлени\w*|результат\w*)", re.I)


def text_total(text: str, more_than: int = 1) -> int | None:
    """'1 - 25 of 28,367 Homes' / '28,367 results' in visible text -> 28367."""
    r = pg.range_text(text[:200_000])
    if r and r[2] > more_than:
        return r[2]
    found = []
    for m in TOTAL.finditer(text[:200_000]):
        n = _num(m.group(1) or m.group(2))
        if n and n > more_than:
            found.append(n)
    return max(found[:5]) if found else None


def _num(s: str) -> int | None:
    digits = re.sub(r"[^\d]", "", s or "")
    return int(digits) if digits and len(digits) < 10 else None


class Structure(Check):
    name = "structure"
    title = "Page structure, paging and fragility"
    order = 60

    def run(self, ctx):
        needs = ctx.facts.get("needs_browser")
        rendered = ctx.rendered()
        use_rendered = rendered is not None and not rendered.error and needs in (True, "partly")
        if use_rendered:
            soup, lst, source = ctx.soup("rendered", rendered.html), ctx.facts.get("rendered_listing"), "browser"
        else:
            raw = ctx.main_page()
            if not raw.ok:
                raise SkipCheck("no readable version of the page")
            soup, lst, source = ctx.soup("raw", raw.text), ctx.facts.get("raw_listing"), "raw page"
        out = {"source": source}

        # ---- item list
        if lst:
            pub = {k: v for k, v in lst.items() if not k.startswith("_")}
            out["listing"] = pub
            fr = lst["fragility"]
            f = lst["fields"]
            have = [k for k in ("link", "image", "price", "date") if f.get(k, 0) >= 0.5]
            self.note(f"Item list: {lst['count']} items, selector `{lst['item_selector']}` inside "
                      f"`{lst['container']}`" + (f"; items have {', '.join(have)}" if have else "") + ".")
            self.note(f"Sample item: \"{lst['samples'][0]}\"")
            msg = {"low": "LOW - meaningful class names / stable attributes; a layout scraper should hold up.",
                   "medium": "MEDIUM - some random-looking or styling-only class names; pick hooks carefully.",
                   "high": "HIGH - random-looking class names that change on site updates; expect "
                           "breakage and price in maintenance."}[fr["level"]]
            if fr.get("no_classes"):
                msg = ("MEDIUM - no class names at all; a scraper has to rely on tag order, which "
                       "breaks if the layout shifts.")
            self.note(f"Fragility: {msg}")
            if fr["stable_attributes"]:
                self.note("Items carry stable attributes (data-*, itemprop, aria-label) - use these as hooks.")
            ctx.facts["fragility"] = fr["level"]
            ctx.facts["items_per_page"] = lst["count"]
        else:
            self.note("No repeating item list detected - may be a single-item/detail page, or the list "
                      "is built in an unusual way.")
            ctx.facts["fragility"] = None

        # ---- tables
        tables = [t for t in soup.find_all("table") if len(t.find_all("tr")) >= 5]
        if tables:
            rows = max(len(t.find_all("tr")) for t in tables)
            out["tables"] = {"count": len(tables), "max_rows": rows}
            self.note(f"{len(tables)} data table(s), largest has {rows} rows - tables scrape cleanly.")

        # ---- paging
        paging = []
        page_links = set()
        next_link = None
        rel_next = soup.find(["link", "a"], rel=lambda v: v and "next" in (v if isinstance(v, list) else [v]))
        if rel_next is not None:
            paging.append("rel=next link")
            if rel_next.get("href"):
                next_link = ctx.abs(rel_next["href"])
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if PAGE_HREF.search(href):
                page_links.add(ctx.abs(href))
            if next_link is None and (NEXT_WORDS.match(a.get_text(" ", strip=True) or "") or
                                      "next" in " ".join(a.get("class") or []).lower() or
                                      (a.get("aria-label") or "").lower().startswith("next")):
                next_link = ctx.abs(href)
        if page_links:
            paging.append("numbered page links")
        if next_link:
            paging.append("'next' link")
        # a load-more control: short label about "more", and it does not navigate anywhere
        # (22 Oct: webscraper.io's button just says "More")
        buttons = []
        for b in soup.find_all(["button", "a", "div", "span"]):
            label = b.get_text(" ", strip=True) or ""
            if not label or len(label) > 30 or not LOAD_MORE.search(label):
                continue
            if signals.not_load_more(b):
                continue
            href = (b.get("href") or "").strip()
            clickable = b.name == "button" or b.get("role") == "button" or b.get("onclick") is not None
            if b.name == "a" and (not href or href == "#" or href.startswith("javascript")):
                clickable = True
            if b.name in ("div", "span") and not clickable:
                continue
            if clickable or LOAD_MORE.search(label) and b.name == "button":
                buttons.append(b)
        if buttons:
            paging.append("'load more' button")
        if rendered is not None and rendered.scroll.get("grew") and rendered.scroll.get("new_data_calls"):
            paging.append("endless scroll")
        out["paging"] = paging
        out["page_link_examples"] = sorted(page_links)[:5]
        out["next_link"] = next_link
        # how page N's address is built (tested later by the reach check)
        tpl = None
        for cand in [next_link] + sorted(page_links, key=lambda u: (len(u), u)):
            tpl = pg.template_from(ctx.url, cand, ctx.facts.get("items_per_page")) if cand else None
            if tpl:
                break
        if tpl:
            ctx.facts["page_template"] = tpl
            out["page_template"] = tpl
            self.note(f"Page addresses follow: {tpl['pattern']} ({'page number' if tpl['kind'] == 'page' else 'item offset'}"
                      f", page 2 = {pg.url_for(tpl, 2)}). Not trusted until the reach check fetches it.")
        ctx.facts["paging"] = paging
        ctx.facts["page_links"] = sorted(page_links)[:10]
        ctx.facts["next_link"] = next_link
        feed_paging = [p for f in (ctx.facts.get("feeds_seen") or []) for p in f.get("paging_params", [])]
        # what the "load more" button really does (only known if the browser pressed it)
        click = (rendered.click if rendered is not None and not rendered.error else {}) or {}
        out["load_more_test"] = click
        if click.get("found") and click.get("clicks"):
            if click.get("navigated_to"):
                verdict = f"it is a plain link to another page ({click['navigated_to']}) - normal paging"
                ctx.facts["load_more"] = "link"
            elif click.get("new_data_calls"):
                verdict = (f"it fetches new data ({click['new_data_calls']} data request(s) per "
                           f"{click['clicks']} press(es)) - see the feed under Hidden data feeds")
                ctx.facts["load_more"] = "feed"
            elif click["text_after"] > click["text_before"] * 1.05 and not click.get("new_requests"):
                verdict = ("it only REVEALS items already inside the page - no extra requests. "
                           "One page download already holds the full list")
                ctx.facts["load_more"] = "reveal"
            elif click["text_after"] > click["text_before"] * 1.05:
                verdict = "the page grew after pressing it, with some non-data requests - check report.json"
                ctx.facts["load_more"] = "unclear"
            else:
                verdict = "nothing visible changed after pressing it"
                ctx.facts["load_more"] = "nothing"
            self.note(f"Pressed '{click.get('label', 'load more')}' {click['clicks']}x in the browser: {verdict}.")
        elif click.get("error"):
            self.note(f"Tried to press the 'load more' button but failed: {click['error']}")

        if lst:
            hidden = sum(1 for m in lst.get("_members", []) if any("hidden" in c.lower() for c in (m.get("class") or [])))
            if hidden >= len(lst.get("_members", [])) * 0.5:
                self.note(f"{hidden} items carry a 'hidden' class: they are already downloaded and just not "
                          "shown yet - a scraper reading the raw page gets them all.")
                ctx.facts["hidden_items_in_page"] = hidden

        if paging:
            self.note(f"Paging: {', '.join(paging)}" +
                      (f" (e.g. {sorted(page_links)[0]})" if page_links else "") + ".")
            if ctx.facts.get("load_more") in ("reveal", "link", "feed"):
                pass   # already explained by the button test above
            elif "'load more' button" in paging or "endless scroll" in paging:
                if feed_paging:
                    self.note("Button/scroll paging is driven by a data feed underneath - scrape that "
                              "feed instead of clicking. Feed paging params: "
                              + ", ".join(sorted(set(feed_paging))))
                else:
                    self.note("Button/scroll paging is usually driven by a data feed underneath, but none "
                              "was confirmed - run with --browser always to find it.")
        else:
            self.note("No paging found on this page.")

        # ---- total count -> size of the job
        text = htmltools.visible_text(soup)
        text_n = text_total(text, lst["count"] if lst else 1)
        data_n = (ctx.facts.get("embedded_total") or {}).get("value")
        if text_n:
            out["total_in_text"] = text_n
        if data_n:
            out["total_in_data"] = data_n
            if text_n and abs(text_n - data_n) > max(5, data_n * 0.02):
                self.note(f"Page text says {text_n:,} results, page data says {data_n:,} - "
                          "different counts (cached text, or the text counts something else).")
        total = data_n or text_n
        if total:
            out["total_items_claimed"] = total
            ctx.facts["total_items"] = total
            per = ctx.facts.get("items_per_page")
            line = f"Site claims about {total:,} items"
            if per:
                pages = math.ceil(total / per)
                out["pages_needed"] = pages
                ctx.facts["pages_needed"] = pages
                line += f" -> about {pages:,} list pages at {per} per page"
            self.note(line + ". Use this number later as a coverage check (did we get them all?).")
        return out
