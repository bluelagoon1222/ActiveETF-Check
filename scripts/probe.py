#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Diagnostics round 3: find ETF CHECK (Koscom) internal API for full PDF holdings.
Saves everything under data/probe3/."""
import json, os, re, subprocess, sys
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "probe3")
os.makedirs(OUT, exist_ok=True)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
H = {"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9", "Referer": "https://www.etfcheck.co.kr/"}
X = "0163Y0"   # KoAct 코스닥액티브
X2 = "445290"  # KODEX 로봇액티브
log = []
def L(*a):
    s = " ".join(str(x) for x in a); print(s, flush=True); log.append(s)
def save(name, text):
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
        f.write(text if isinstance(text, str) else json.dumps(text, ensure_ascii=False))

# A. static analysis of ETF CHECK page + scripts -> endpoint strings
endpoints = {}
try:
    s = requests.Session(); s.headers.update(H)
    html = s.get(f"https://www.etfcheck.co.kr/mobile/etpitem/{X}/pdf", timeout=30).text
    save("etfcheck_pdf_page.html", html)
    srcs = re.findall(r'<script[^>]+src="([^"]+)"', html)
    L("script srcs:", len(srcs))
    texts = [("inline", html)]
    for src in srcs:
        if src.startswith("/") or "etfcheck" in src:
            url = src if src.startswith("http") else "https://www.etfcheck.co.kr" + src
            try:
                t = s.get(url, timeout=30).text
                texts.append((url, t))
                L("fetched js", url, len(t))
            except Exception as e:
                L("js fail", url, e)
    for name, t in texts:
        for m in re.finditer(r'["\'`](/(?:stock|user|mobile|api|etp)[A-Za-z0-9_/\-]*?)["\'`]', t):
            ep = m.group(1)
            ctx = t[max(0, m.start()-160): m.end()+220].replace("\n", " ")
            endpoints.setdefault(ep, []).append({"src": name[-40:], "ctx": ctx})
    save("endpoints.json", endpoints)
    L("endpoints found:", len(endpoints))
    for ep in sorted(endpoints):
        L("  EP", ep)
except Exception as e:
    L("static stage failed", repr(e))

# B. blind guesses for PDF endpoint
cands = ["/stock/etp/getEtpPdf", "/stock/etp/getEtpPdfList", "/stock/etp/getPdf", "/stock/etp/getPdfList",
         "/stock/etp/getEtfPdf", "/stock/etp/getEtpItemPdf", "/stock/etp/getEtpPortfolio", "/stock/etp/getEtpConstituent",
         "/stock/etp/getEtpItemInfo", "/stock/etp/getEtpItem", "/stock/etp/getEtpDetail"]
cands += [e for e in endpoints if re.search(r"pdf|Pdf|PDF|portf|Portf|const|Const|compos|Compos|holding", e)]
guess = []
for ep in dict.fromkeys(cands):
    for body in ({"ticker": X}, {"code": X}, {"itemCode": X}, {"etpCode": X}, {"isuCd": X}, {"item_code": X}):
        for method in ("post", "get"):
            try:
                url = "https://www.etfcheck.co.kr" + ep
                hh = {**H, "X-Requested-With": "XMLHttpRequest", "Accept": "application/json, text/plain, */*"}
                if method == "post":
                    r = requests.post(url, json=body, headers=hh, timeout=20)
                    r2 = requests.post(url, data=body, headers=hh, timeout=20)
                    pair = [(r.status_code, r.text[:300]), (r2.status_code, r2.text[:300])]
                else:
                    r = requests.get(url, params=body, headers=hh, timeout=20)
                    pair = [(r.status_code, r.text[:300])]
                for st, tx in pair:
                    if st == 200 and len(tx) > 30 and "success" in tx:
                        guess.append({"ep": ep, "method": method, "body": body, "status": st, "text": tx})
                        L("GUESS HIT", ep, method, body, tx[:120])
            except Exception as e:
                pass
save("guesses.json", guess)

# C. headless browser capture of ALL etfcheck requests on pdf page (no truncation)
try:
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "playwright"], check=True, timeout=300)
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True, timeout=600)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        for tag, url in [("pdf", f"https://www.etfcheck.co.kr/mobile/etpitem/{X}/pdf"),
                         ("pdf2", f"https://www.etfcheck.co.kr/mobile/etpitem/{X2}/pdf"),
                         ("item", f"https://www.etfcheck.co.kr/mobile/etpitem/{X}"),
                         ("pc", f"https://www.etfcheck.co.kr/etpitem/{X}/pdf")]:
            ctx = b.new_context(user_agent=UA, locale="ko-KR", viewport={"width": 1280, "height": 2000})
            pg = ctx.new_page()
            cap = []
            def on_resp(resp, cap=cap):
                try:
                    u = resp.url
                    if "etfcheck.co.kr" not in u or re.search(r"\.(js|css|png|jpg|svg|woff2?|gif|ico)(\?|$)", u):
                        return
                    body = ""
                    try:
                        body = resp.text()[:30000]
                    except Exception:
                        pass
                    cap.append({"url": u, "status": resp.status, "method": resp.request.method,
                                "req_headers": {k: v for k, v in resp.request.headers.items() if k.lower() in ("content-type", "x-requested-with", "authorization", "referer")},
                                "post": (resp.request.post_data or "")[:3000], "ct": resp.headers.get("content-type", ""), "body": body})
                except Exception:
                    pass
            pg.on("response", on_resp)
            try:
                pg.goto(url, timeout=60000, wait_until="domcontentloaded")
                pg.wait_for_timeout(12000)
                # try clicking anything that looks like a PDF / 구성종목 tab
                for txt in ["구성종목", "PDF", "포트폴리오", "전체보기", "더보기"]:
                    try:
                        loc = pg.get_by_text(txt, exact=False)
                        if loc.count() > 0:
                            loc.first.click(timeout=3000)
                            pg.wait_for_timeout(4000)
                            L(tag, "clicked", txt)
                    except Exception:
                        pass
                html = pg.content()
            except Exception as e:
                html = "ERR " + repr(e)
            save(f"pw_{tag}_requests.json", cap)
            save(f"pw_{tag}_html.html", html)
            L("playwright", tag, "etfcheck responses:", len(cap), "html", len(html))
            for c in cap:
                L("   ", c["method"], c["status"], c["url"][:150], "| post:", c["post"][:120], "| body:", c["body"][:100].replace("\n", " "))
            ctx.close()
        b.close()
except Exception as e:
    L("playwright stage failed:", repr(e))

save("log.txt", "\n".join(log))
print("PROBE3 DONE")
