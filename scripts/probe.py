#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Diagnostics round 2: discover ETF holdings (PDF) data sources reachable from the runner.
Saves everything under data/probe2/ so it can be read from the repository afterwards."""
import json, os, re, subprocess, sys, time
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "probe2")
os.makedirs(OUT, exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept-Language": "ko-KR,ko;q=0.9", "Referer": "https://m.stock.naver.com/"}
CAP = 40000


def save(name, text):
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
        f.write(text[:CAP] if isinstance(text, str) else json.dumps(text, ensure_ascii=False)[:CAP])


def get(url, **kw):
    try:
        r = requests.get(url, timeout=25, headers=UA, **kw)
        return r.status_code, r.text
    except Exception as e:
        return None, repr(e)


log = []
def L(*a):
    s = " ".join(str(x) for x in a); print(s, flush=True); log.append(s)

# 1. Naver ETF list -> find active ETFs
st, body = get("https://finance.naver.com/api/sise/etfItemList.nhn")
active_codes = []
try:
    js = json.loads(body)
    items = js["result"]["etfItemList"]
    save("naver_etf_list.json", {"n": len(items), "sample": items[:3]})
    act = [i for i in items if "액티브" in i["itemname"] and i.get("etfTabCode") in (1, 2)]
    L("naver list ok", len(items), "domestic active-named:", len(act))
    save("naver_active_named.json", [{"code": i["itemcode"], "name": i["itemname"], "tab": i["etfTabCode"], "aum": i.get("marketSum")} for i in act])
    active_codes = [i["itemcode"] for i in sorted(act, key=lambda x: -(x.get("marketSum") or 0))[:3]]
except Exception as e:
    L("naver list parse fail", e, str(body)[:200])
X = active_codes[0] if active_codes else "069500"
L("test code:", X, active_codes)

# 2. Naver per-item endpoints (full bodies)
for path in ["basic", "integration", "etfAnalysis", "etfComposition", "etfPortfolio", "composition", "portfolio",
             "holdings", "etfHoldings", "etfConstituents", "constituents", "etfAnalysis/portfolio", "etfCu"]:
    st, body = get(f"https://m.stock.naver.com/api/stock/{X}/{path}")
    L("naver", path, st, len(body or ""))
    if st == 200:
        save(f"naver_{path.replace('/', '_')}.txt", body)
for url in [f"https://m.stock.naver.com/api/etf/{X}/basic", f"https://m.stock.naver.com/api/etf/{X}/portfolio",
            f"https://api.stock.naver.com/etf/{X}/basic", f"https://api.stock.naver.com/etf/{X}/portfolio",
            f"https://navercomp.wisereport.co.kr/v2/ETF/index.aspx?cmp_cd={X}",
            f"https://navercomp.wisereport.co.kr/v2/ETF/ETFPortfolio.aspx?cmp_cd={X}",
            f"https://finance.naver.com/item/coinfo.naver?code={X}",
            f"https://www.etfcheck.co.kr/mobile/etpitem/{X}/pdf",
            f"https://www.etfcheck.co.kr/mobile/api/etp/{X}/pdf", f"https://www.etfcheck.co.kr/api/etp/{X}/pdf",
            f"https://www.etfcheck.co.kr/mobile/api/etpitem/{X}/pdf"]:
    st, body = get(url)
    L("GET", url, st, len(body or ""))
    if st == 200:
        save("raw_" + re.sub(r"[^0-9A-Za-z]+", "_", url)[8:90] + ".txt", body)

# 3. Headless browser: capture network calls made by data pages (reveals their internal APIs)
try:
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "playwright"], check=True, timeout=300)
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True, timeout=600)
    from playwright.sync_api import sync_playwright
    pages = {
        "etfcheck_pdf": f"https://www.etfcheck.co.kr/mobile/etpitem/{X}/pdf",
        "etfcheck_item": f"https://www.etfcheck.co.kr/mobile/etpitem/{X}",
        "wisereport": f"https://navercomp.wisereport.co.kr/v2/ETF/index.aspx?cmp_cd={X}",
        "naver_m_etf": f"https://m.stock.naver.com/domestic/stock/{X}/etfAnalysis",
        "seibro_etf": "https://seibro.or.kr/websquare/control.jsp?w2xPath=/IPORTAL/user/etf/BIP_CNTS06030V.xml&menuNo=179",
        "kodex_list": "https://www.samsungfund.com/etf/product/list.do",
        "koact_home": "https://www.samsungactive.co.kr/",
        "timefolio_etf": "https://www.timefolio.co.kr/etf/etf_list.php",
    }
    with sync_playwright() as p:
        b = p.chromium.launch()
        for name, url in pages.items():
            ctx = b.new_context(user_agent=UA["User-Agent"], locale="ko-KR")
            pg = ctx.new_page()
            captured = []
            def on_resp(resp, captured=captured):
                try:
                    u = resp.url
                    ct = resp.headers.get("content-type", "")
                    if any(k in u for k in ("api", "json", "ajax", "Ajax", ".do", ".jsp", ".asp", "xml", "Service", "data")) or "json" in ct:
                        body = ""
                        try:
                            if resp.status == 200 and ("json" in ct or "xml" in ct or "text" in ct or "javascript" in ct):
                                body = resp.text()[:6000]
                        except Exception:
                            pass
                        captured.append({"url": u[:400], "status": resp.status, "ct": ct[:60],
                                         "method": resp.request.method, "post": (resp.request.post_data or "")[:800], "body": body})
                except Exception:
                    pass
            pg.on("response", on_resp)
            try:
                pg.goto(url, timeout=45000, wait_until="domcontentloaded")
                pg.wait_for_timeout(9000)
                html = pg.content()
            except Exception as e:
                html = "ERR " + repr(e)
            save(f"pw_{name}_net.json", captured)
            save(f"pw_{name}_html.txt", html)
            L("playwright", name, "responses:", len(captured), "html:", len(html))
            ctx.close()
        b.close()
except Exception as e:
    L("playwright stage failed:", repr(e))

save("log.txt", "\n".join(log))
print("PROBE2 DONE")
