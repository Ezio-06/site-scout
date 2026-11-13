"""Fake websites that reproduce real scraping landmines - regression tests for the scout.

  /Homes/Town-1-N.html  Zameen-like: data in window.state, deep pages fail past page 40
  /catalog              Daraz-like: ?page= ignored, page 2 = page 1
  /cars                 deep pages quietly serve page 1 again (200 OK)
  /blog/                healthy: /blog/page/N/, partial last page
  /jobs                 offset paging (?start=), JSON.parse state, "no results" past 600
  /spa                  browser-only list + main feed + promoted side feed + analytics ping
  --- added 22 Oct from the holdout run (each is a SHAPE, not a copy of one site) ---
  /shop-menu            rich list page whose menu/scripts mention Cloudflare Turnstile -> NOT blocked
  /cf-everywhere        rich page with Cloudflare's per-page challenge script -> NOT blocked
  /challenge200         thin "Just a moment..." page served with HTTP 200 -> blocked
  /gate202              thin 202 page that loads a bot-check script -> blocked
  /app                  empty app shell (own bundle + "enable JavaScript") that fetches /api/crates -> app, not a block
  /junkfeed             items in the HTML + browser fetches an unrelated config list and an ad sync -> NOT a feed route
  /scriptlist           body = 12 inline <script> tags, items come from /api/rows -> scripts are not items
  /packed               quotes only in `var data = [...]` (text/author fields), painted by JS -> packed data (B)
  /hn                   page 2 answers 419 'too fast' -> paging UNTESTED, not broken
  /forms                pager links ?page_num=1 and ?page_num=2 -> page_num paging found and works
  /more                 'More' button (href="#") that loads more via a feed -> load-more detected
  /flaky                the first request is dropped, later ones work -> retried, not unreachable
  /tarpit               plain requests hang, a real browser loads it -> guarded (tarpit), browser route
  /woo/page/N/          products whose class lists differ per item (post-N, instock/outofstock, first/last) -> one list; page 2 works
  /facets               filter panel with "+ More" expanders (aria-expanded) and no load-more -> NOT load-more
  --- added 28 Oct from the Daraz product-page run (shapes, not copies) ---
  /redirect-gate        HTTP 200, no content, script clears a cookie and clicks a hidden link to another host -> blocked
  /goes-sour            rich page for the first request, then the redirect gate for good -> pace must NOT pass
  /goes-blank           rich page for the first request, then an empty 200 page (no guard words at all) -> soft block
  /browser-trips        plain requests fine until a real browser visits; from then on everything gets the gate
                        -> plain tests run first, the browser's damage is caught and named
  /price-format         price only in the page data as "24990"; you typed 24,990 from the screen -> found

Run everything:  python tests/run_tests.py      (starts this server itself)
"""
import json, math, re, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

def items(n0, n1, prefix):
    return [{"externalID": str(100000 + i), "title": f"{prefix} listing {i}", "price": 1000 + i * 7,
             "area": 125.4, "slug": f"/item-{100000+i}"} for i in range(n0, n1)]

FLAKY = {"dropped": False}
HITS = {}                      # path -> plain requests served (28 Oct shapes)
TRIPPED = {"browser": False}
GATE = ('<a id="go" href="https://guard.cdn-example.test/deny?origin=%2Fp"></a><script>'
        'var c = "sec=;max-age=-100;path=/"; document.cookie = c; document.getElementById("go").click();</script>')
TURNSTILE = '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async></script>'
CF_PAGE_SCRIPT = '<script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script>'
QUOTES = [{"text": f"Quote number {i}: the world as we have created it is a process of our thinking.",
           "author": {"name": f"Author {i}", "slug": f"author-{i}"}, "tags": ["change", "world"]} for i in range(10)]


def shop(title, n=12, extra=""):
    cards = "".join(f'<div class="product"><a href="/product/{i}"><h3>Hoodie model {i}</h3></a>'
                    f'<span class="price">$ {30 + i}.00</span></div>' for i in range(n))
    menu = ('<nav><a href="/">Home</a> <a href="/login/cf-turnstile">Cloudflare Turnstile Demo</a> '
            '<a href="/cloudflare-challenge">Cloudflare Challenge</a></nav>')
    return f"<html><head><title>{title}</title>{extra}</head><body>{menu}<main><div class='grid'>{cards}</div></main></body></html>"


def page_html(title, its, total, page, per, rel_next=None, extra_head="", state=None, range_txt=True, links=True):
    head = f"<title>{title}</title>" + (f'<link rel="next" href="{rel_next}"/>' if rel_next else "") + extra_head
    cards = "".join(f'<li class="card"><a href="{it["slug"]}"><h3>{it["title"]}</h3></a>'
                    f'<span class="price">PKR {it["price"]:,}</span><span>5 Marla, posted 2 days ago</span></li>' for it in its)
    rng = f"<p>{(page-1)*per+1:,} to {(page-1)*per+len(its):,} of {total:,} Homes</p>" if range_txt and its else ""
    st = f"<script>window.state = {json.dumps(state)};</script>" if state else ""
    return f"<html><head>{head}</head><body><nav><a href='/'>Home</a></nav><main>{rng}<ul class='results'>{cards}</ul></main>{st}<footer>x</footer></body></html>"

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, code, body, ctype="text/html; charset=utf-8"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_POST(self):
        u = urlsplit(self.path)
        ln = int(self.headers.get("Content-Length", 0)); self.rfile.read(ln)
        if u.path == "/_msearch":
            hits = [{"_source": it} for it in items(900, 925, "promo")]
            return self.send(200, json.dumps({"responses": [{"hits": {"total": {"value": 28, "relation": "eq"}, "hits": hits}}]}), "application/json")
        if u.path.startswith("/ingest/"):
            return self.send(201, json.dumps([{"client_ip": "1.2.3.4", "client_session_id": "s", "timestamp": 1, "metric_action": "view", "x": "y"*200}]*5), "application/json")
        return self.send(404, "{}", "application/json")

    def do_GET(self):
        u = urlsplit(self.path); q = parse_qs(u.query); p = u.path
        r = self.landmines_22oct(p, q)
        if r is not None:
            return r
        if p == "/spa":
            js = """<script>
fetch('/_msearch?side=1',{method:'POST',body:JSON.stringify({query:{bool:{}}})}).then(r=>r.json());
fetch('/ingest/adMetric/',{method:'POST',body:JSON.stringify([{client_ip:1}])});
fetch('/api/search?page=1').then(r=>r.json()).then(d=>{document.getElementById('r').innerHTML=d.results.map(x=>'<li class=c><a href=\"'+x.slug+'\">'+x.title+'</a> PKR '+x.price+'</li>').join('')});
</script>"""
            return self.send(200, "<html><body><h1>Search</h1><p>1 to 25 of 3,000 Homes</p><ul id=r></ul>" + js + "</body></html>")
        if p == "/api/search":
            n = int(q.get("page", ["1"])[0])
            return self.send(200, json.dumps({"results": items((n-1)*25, n*25, "spa"), "total": 3000, "page": n}), "application/json")
        if p == "/robots.txt":
            return self.send(200, "User-agent: *\nDisallow: /private/\n", "text/plain")
        # 1. zameen-like: state blob (algolia format over elastic), deep pages fail past page 40
        m = re.match(r"^/Homes/Town-1-(\d+)\.html$", p)
        if m:
            n = int(m.group(1)); per, total = 25, 2000
            if n > 40:
                return self.send(500, "<html><body><h1>Something went wrong</h1></body></html>")
            its = items((n-1)*per, n*per, "zm")
            # like Zameen: the list has no page field of its own, and a map widget's page is always 0
            state = {"idle": True, "map": {"page": 0}, "algolia": {"activeBackendName": "elastic", "content": {"hits": its, "nbHits": total,
                     "nbPages": 80, "hitsPerPage": per}}, "cities": {"data": [{"name": f"c{i}", "slug": f"/c{i}"} for i in range(200)]}}
            return self.send(200, page_html("Town", its, total, n, per, rel_next=f"/Homes/Town-1-{n+1}.html", state=state))
        # 1b. page number next to the list is the only evidence; deep pages (>20) report page 1 but new items
        m = re.match(r"^/state/(\d+)$", p)
        if m:
            n = int(m.group(1)); per, total = 25, 1000
            its = items((n-1)*per, n*per, "st")
            state = {"map": {"page": 0}, "results": {"hits": its, "nbHits": total, "page": n if n <= 20 else 1}}
            return self.send(200, page_html("State", its, total, n, per, rel_next=f"/state/{n+1}", state=state, range_txt=False))
        # 2. daraz-like: ?page= ignored, always page 1
        if p == "/catalog":
            per, total = 40, 1200
            its = items(0, per, "dz")
            return self.send(200, page_html("Catalog", its, total, 1, per, rel_next="/catalog?page=2", range_txt=False)
                             .replace("<main>", f"<main><p>{total:,} items found</p>"))
        # 3. fallback: deep pages (>30) quietly return page 1 content with 200
        if p == "/cars":
            n = int(q.get("page", ["1"])[0]); per, total = 20, 1000
            real = n if n <= 30 else 1
            its = items((real-1)*per, real*per, "car")
            return self.send(200, page_html("Cars", its, total, n, per, rel_next=f"/cars?page={n+1}", range_txt=False)
                             .replace("<main>", f"<main><p>{total:,} results</p>"))
        # 4. healthy blog: /blog/, /blog/page/N/, 95 posts, 10 per page, last page partial
        m = re.match(r"^/blog/(?:page/(\d+)/)?$", p)
        if m:
            n = int(m.group(1) or 1); per, total = 10, 95
            last = math.ceil(total/per)
            if n > last:
                return self.send(404, "<html><body>Not found</body></html>")
            its = items((n-1)*per, min(n*per, total), "post")
            return self.send(200, page_html("Blog", its, total, n, per, rel_next=f"/blog/page/{n+1}/" if n < last else None))
        # 5. offset style ?start=, JSON.parse state, soft-empty past 600 results
        if p == "/jobs":
            start = int(q.get("start", ["0"])[0]); per, total = 24, 1500
            its = items(start, min(start+per, total), "job") if start < 600 else []
            n = start//per + 1
            body = page_html("Jobs", its, total, n, per, rel_next=f"/jobs?start={start+per}", range_txt=bool(its))
            if not its:
                body = body.replace("<main>", "<main><p>No results found</p>")
            data = json.dumps({"results": its, "totalCount": total, "pad": "x"*500})
            body = body.replace("</body>", f"<script>window.__DATA__ = JSON.parse({json.dumps(data)});</script></body>")
            return self.send(200, body)
        return self.send(404, "<html><body>nope</body></html>")

    def landmines_22oct(self, p, q):
        J = "application/json"
        if p == "/shop-menu":
            return self.send(200, shop("Shop", extra=TURNSTILE))
        if p == "/cf-everywhere":
            return self.send(200, shop("Shop", extra=CF_PAGE_SCRIPT))
        if p == "/challenge200":
            return self.send(200, "<html><head><title>Just a moment...</title></head><body><p>Checking your browser "
                                  "before accessing the site.</p>" + CF_PAGE_SCRIPT + "</body></html>")
        if p == "/gate202":
            return self.send(202, '<html><body><noscript>Please enable JavaScript to continue.</noscript>'
                                  '<script src="https://x1.token.awswaf.com/challenge.js"></script></body></html>')
        if p == "/app":
            return self.send(200, '<html><head><title>crates</title><script src="/assets/app.js"></script></head>'
                                  '<body class="ember-application"><noscript>You need to enable JavaScript to run '
                                  'this app.</noscript><ul id="list"></ul></body></html>')
        if p == "/assets/app.js":
            return self.send(200, "fetch('/api/crates?page=1').then(r=>r.json()).then(d=>{document.getElementById('list')"
                                  ".innerHTML=d.crates.map(c=>'<li class=crate><a href=\"/crates/'+c.id+'\">'+c.name+"
                                  "'</a> <span>'+c.description+'</span></li>').join('')});", "application/javascript")
        if p == "/api/crates":
            return self.send(200, json.dumps({"crates": [{"id": f"crate-{i}", "name": f"http-client-{i}",
                                  "description": f"A fast HTTP client library number {i}"} for i in range(10)],
                                  "meta": {"total": 340}}), J)
        if p == "/junkfeed":
            js = ("<script>fetch('/v1/1c5f94f4-config').then(r=>r.json());"
                  "fetch('/pbs/cookie_sync?x=1').then(r=>r.json());</script>")
            return self.send(200, shop("Laptops").replace("</body>", js + "</body>"))
        if p == "/v1/1c5f94f4-config":
            return self.send(200, json.dumps({"purposes": [{"id": i, "name": f"Store and access information purpose {i}",
                                  "description": "Cookies are used for measurement " * 3} for i in range(10)]}), J)
        if p == "/pbs/cookie_sync":
            return self.send(200, json.dumps({"bidder_status": [{"bidder": f"bidder{i}", "usersync": {"url": "https://x"}}
                                  for i in range(8)], "status": "ok", "pad": "x" * 300}), J)
        if p == "/scriptlist":
            scripts = "".join(f"<script>var cfg{i} = {{a: {i}, b: 'some configuration value for widget {i} here'}};</script>"
                              for i in range(12))
            js = ("<script>fetch('/api/rows').then(r=>r.json()).then(d=>{document.getElementById('t').innerHTML="
                  "d.rows.map(x=>'<tr><td>'+x.team+'</td><td>'+x.wins+'</td></tr>').join('')});</script>")
            return self.send(200, f"<html><body>{scripts}<h1>Teams</h1><table id='t'></table>{js}</body></html>")
        if p == "/api/rows":
            return self.send(200, json.dumps({"rows": [{"team": f"Boston Team {i}", "wins": 40 + i, "year": 1990}
                                                       for i in range(25)]}), J)
        if p == "/packed":
            js = ("<script>var data = " + json.dumps(QUOTES) + ";\n"
                  "document.getElementById('q').innerHTML = data.map(d => '<div class=\"quote\"><span>' + d.text + "
                  "'</span><small>' + d.author.name + '</small></div>').join('');</script>")
            return self.send(200, f"<html><body><h1>Quotes</h1><div id='q'></div>{js}"
                                  "<nav><a href='/packed/page/2/'>Next</a></nav></body></html>")
        if p == "/hn":
            n = int(q.get("p", ["1"])[0])
            if n > 1:
                return self.send(419, "Sorry.", "text/plain")
            return self.send(200, page_html("News", items(0, 30, "story"), 300, 1, 30, range_txt=False)
                             .replace("</main>", "<a href='/hn?p=2' rel='next'>More</a></main>"))
        if p == "/forms":
            n = int(q.get("page_num", ["1"])[0]); per = 25
            rows = "".join(f"<tr class='team'><td class='name'>Team {i} of the league</td><td>{i}</td></tr>"
                           for i in range((n - 1) * per, n * per))
            pager = ("<ul class='pagination'><li><a href='/forms?page_num=1'>1</a></li>"
                     "<li><a href='/forms?page_num=2'>2</a></li><li><a href='/forms?page_num=3'>3</a></li>"
                     f"<li><a href='/forms?page_num={max(1, n - 1)}' aria-label='Previous'>&laquo;</a></li></ul>")
            return self.send(200, f"<html><body><table>{rows}</table>{pager}</body></html>")
        if p == "/more":
            js = ("<script>document.getElementById('m').onclick=e=>{e.preventDefault();fetch('/api/more?offset=6')"
                  ".then(r=>r.json()).then(d=>{document.getElementById('g').innerHTML+=d.items.map(x=>"
                  "'<div class=product><a href=/product/'+x.id+'><h3>'+x.name+'</h3></a> $'+x.price+'</div>').join('')})};</script>")
            return self.send(200, shop("Laptops", n=6).replace("<div class='grid'>", "<div class='grid' id='g'>")
                             .replace("</main>", "<a class='btn' id='m' href='#'>More</a></main>").replace("</body>", js + "</body>"))
        if p == "/api/more":
            return self.send(200, json.dumps({"items": [{"id": i, "name": f"Hoodie model {i}", "price": 30 + i}
                                                        for i in range(6, 12)]}), J)
        m = re.match(r"^/woo/(?:page/(\d+)/)?$", p)
        if m:
            n = int(m.group(1) or 1); per, total = 16, 755
            lis = "".join(
                f'<li class="product type-product post-{700 + i} status-publish {("first" if i % 4 == 0 else "last" if i % 4 == 3 else "")} '
                f'{"outofstock" if i % 5 == 0 else "instock"} product_cat-pokemon-{i % 3} has-post-thumbnail">'
                f'<a href="/shop/item-{i}/"><img src="/i{i}.png"><h2 class="woocommerce-loop-product__title">Pokemon {i}</h2>'
                f'<span class="price">£ {50 + i}.00</span></a><a href="?add-to-cart={i}" class="button">Add to basket</a></li>'
                for i in range((n - 1) * per, n * per))
            body = (f'<html><body class="archive woocommerce">' + "".join(f"<script>var w{i} = {{x: 'cfg {i}'}};</script>" for i in range(20))
                    + f'<main id="main"><p class="woocommerce-result-count">Showing {(n-1)*per+1}&ndash;{n*per} of {total} results</p>'
                    f'<ul class="products columns-4">{lis}</ul><nav class="woocommerce-pagination"><a href="/woo/page/{n+1}/" class="next">&rarr;</a>'
                    f'<a href="/woo/page/2/">2</a><a href="/woo/page/3/">3</a></nav></main></body></html>')
            return self.send(200, body)
        if p == "/facets":
            facets = "".join(f'<div class="filter-group"><h4>Group {g}</h4>' + "".join(f'<label><input type="checkbox"> Option {g}-{k}</label>' for k in range(4))
                             + f'<button class="show-more__btn" aria-expanded="false" aria-controls="c{g}">+ More</button></div>' for g in range(4))
            return self.send(200, shop("Shoes").replace("<main>", f"<main><div class='filters'>{facets}</div>"))
        r = self.landmines_28oct(p)
        if r is not None:
            return r
        if p == "/flaky":
            if not FLAKY["dropped"]:
                FLAKY["dropped"] = True
                self.close_connection = True
                self.connection.close()
                return True
            return self.send(200, shop("Flaky shop"))
        if p == "/tarpit":
            if "image/" not in (self.headers.get("Accept") or ""):   # a script, not a browser: hang
                import time
                time.sleep(6)
                return True
            return self.send(200, shop("Tarpit shop"))
        return None


    def landmines_28oct(self, p):
        browser = "image/" in (self.headers.get("Accept") or "")
        product = shop("Stand Mixer Pro 9000").replace(
            "<main>", '<main><h1>Stand Mixer Pro 9000</h1><script type="application/ld+json">'
            '{"@type": "Product", "name": "Stand Mixer Pro 9000", "offers": {"price": "24990", "priceCurrency": "PKR"}}'
            "</script><p>" + "Seven speeds, steel bowl, dough hook and whisk included. " * 30 + "</p>")
        if p == "/redirect-gate":
            return self.send(200, GATE)
        if p in ("/goes-sour", "/goes-blank"):
            HITS[p] = HITS.get(p, 0) + 1
            if HITS[p] <= 1:
                return self.send(200, product)
            return self.send(200, GATE if p == "/goes-sour" else "<html><head></head><body></body></html>")
        if p == "/browser-trips":
            if browser:
                TRIPPED["browser"] = True
            return self.send(200, GATE if TRIPPED["browser"] else product)
        if p == "/price-format":
            return self.send(200, product)
        return None


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
