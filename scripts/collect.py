#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Active ETF CHECK - daily data collector (v4: Naver Finance + WiseReport)
========================================================================
All data comes from public, login-free endpoints reachable from GitHub Actions:

  Naver Finance   finance.naver.com/api/sise/etfItemList.nhn          all listed ETFs (tab code, price, NAV, AUM)
                  m.stock.naver.com/api/stock/{code}/etfAnalysis      issuer, base index, fee, tracking error,
                                                                       period returns (price & NAV), sector weights
  WiseReport      navercomp.wisereport.co.kr/v2/ETF/index.aspx?cmp_cd={code}
                                                                       FULL creation-unit holdings (CU_data JSON)
                  navercomp.wisereport.co.kr/ETF/GetNAVData.aspx      daily NAV / close history

Computes day-over-day holding changes, active weight vs benchmark (benchmark = holdings of the largest
passive ETF tracking the same index), performance and excess return vs the index proxy, sector grouping.
Writes data/latest.json (used by index.html), data/pdf/<date>.json snapshots, data/status.json, data/run_log.txt
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from collections import defaultdict

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
PDF_DIR = os.path.join(DATA_DIR, "pdf")
LATEST_PATH = os.path.join(DATA_DIR, "latest.json")
STATUS_PATH = os.path.join(DATA_DIR, "status.json")
LOG_PATH = os.path.join(DATA_DIR, "run_log.txt")
KST = dt.timezone(dt.timedelta(hours=9))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
NV_HEADERS = {"User-Agent": UA, "Accept": "application/json, text/plain, */*", "Accept-Language": "ko-KR,ko;q=0.9",
              "Referer": "https://m.stock.naver.com/"}
WR_HEADERS = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8", "Accept-Language": "ko-KR,ko;q=0.9",
              "Referer": "https://finance.naver.com/"}
WR_PAGE = "https://navercomp.wisereport.co.kr/v2/ETF/index.aspx"
WR_NAV = "https://navercomp.wisereport.co.kr/ETF/GetNAVData.aspx"

PDF_KEEP_DAYS = 70
SLEEP = 0.5
DEADLINE_SEC = 33 * 60
STARTED = time.time()
_LOG = []

CASH_RE = re.compile(r"현금|예금|설정현금|원화|예치금|외화|달러(?!\s*트리)|\bCASH\b|\bUSD\b|\bEUR\b|\bJPY\b|\bCNY\b|\bHKD\b|\bMMF\b", re.I)
DERIV_RE = re.compile(r"선물|옵션|콜|풋|스왑|선물환|FUTURE|OPTION|SWAP|\d{4}년\s?\d{1,2}월물|F\d{6}|C\d{6}|P\d{6}", re.I)
# active ETFs whose name matches this are NOT equity funds (bonds, money market, commodities, FX, mixed...)
EXCLUDE_RE = re.compile(r"채권|국공채|국채|회사채|은행채|금융채|CD금리|KOFR|SOFR|머니마켓|MMF|단기채|단기자금|혼합|TDF|TRF|금리|"
                        r"달러선물|엔선물|위안선물|엔화|원자재|골드|금현물|은현물|원유|비트코인|이더리움|리츠부동산|리츠|부동산|채\(|"
                        r"하이일드|크레딧|코인|합성|국제금|금커버드콜|은커버드콜", re.I)
# Naver ETF tab: 1=국내시장지수 2=국내업종/테마 4=해외주식 (5=원자재 6=채권 7=기타)
REGION_BY_TAB = {1: "domestic", 2: "domestic", 4: "global"}
# benchmark index -> representative passive ETF (holdings + price used as the index proxy)
STATIC_PROXY = {"KOSPI200": "069500", "KOSDAQ150": "229200", "KOSDAQ": "229200", "KRX반도체": "091160", "KRX300": "292190",
                "KOSPI": "226490", "KRX바이오K뉴딜": "364970", "KRX2차전지K뉴딜": "364980", "KRXBBIGK뉴딜": "364960",
                "KOSPI200커버드콜5OTM": "069500", "KOSPI200커버드콜": "069500", "KOSDAQ150커버드콜": "229200",
                "KOSPI200고배당": "069500", "KOSPI200중소형주": "069500", "KOSPI중형주": "226490", "KRX정보기술": "266370",
                "KRX헬스케어": "266420", "KRX기술이전바이오": None,
                # overseas index proxies
                "SP500": "360750", "S&P500": "360750", "NASDAQ100": "133690", "나스닥100": "133690", "DOWJONES30": "245340",
                "RUSSELL2000": "280930", "PHLXSEMICONDUCTOR": "381180", "필라델피아반도체": "381180", "MSCIWORLD": "251350",
                "MSCIACWI": "251350", "CSI300": "192090", "NIKKEI225": "241180", "NIFTY50": "453810", "EUROSTOXX50": "195930",
                "HANGSENG": "245360", "TOPIX": "195920", "NASDAQ100커버드콜": "133690", "SP500커버드콜": "360750"}


def time_left():
    return DEADLINE_SEC - (time.time() - STARTED)


def log(*a):
    line = dt.datetime.now(KST).strftime("%H:%M:%S") + " " + " ".join(str(x) for x in a)
    print(line, flush=True)
    _LOG.append(line)


def flush_log():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(_LOG[-400:]))
    except Exception:  # noqa
        pass


def to_num(x, default=None):
    if x is None:
        return default
    s = str(x).replace(",", "").replace("%", "").strip()
    if s in ("", "-", "–", "null", "None"):
        return default
    try:
        return float(s)
    except ValueError:
        return default


def load_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, obj, compact=True):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        if compact:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(obj, f, ensure_ascii=False, indent=1)


def krw_text_to_won(t):
    """'2조 4,500억' -> 2450000000000"""
    if not t:
        return None
    t = str(t).replace(",", "")
    won = 0.0
    m = re.search(r"([\d.]+)\s*조", t)
    if m:
        won += float(m.group(1)) * 1e12
    m = re.search(r"([\d.]+)\s*억", t)
    if m:
        won += float(m.group(1)) * 1e8
    return won or None


class Http:
    """requests wrapper with per-host fail-fast: after 4 consecutive failures on a host (slow/blocking server)
    later calls to that host use 1 try x 12s so the run can still finish and fall back to cached data."""

    def __init__(self):
        self.calls = 0
        self.fails = {}

    def get(self, url, headers, params=None, tries=3, as_json=True, timeout=25):
        host = url.split("/")[2] if "//" in url else url
        streak = self.fails.get(host, 0)
        if streak >= 4 and streak % 8 != 0:      # degraded host: fail fast (every 8th call probes with full timeout)
            tries, timeout = 1, 12
        last = None
        for i in range(tries):
            try:
                self.calls += 1
                r = requests.get(url, headers=headers, params=params, timeout=timeout)
                if r.status_code != 200:
                    raise RuntimeError("HTTP %s" % r.status_code)
                out = r.json() if as_json else r.text
                self.fails[host] = 0
                time.sleep(SLEEP)
                return out
            except Exception as e:  # noqa
                last = e
                time.sleep(2 * (i + 1))
        self.fails[host] = streak + 1
        if self.fails[host] == 4:
            log("host degraded (fail-fast mode):", host)
        raise RuntimeError("GET failed %s %s: %s" % (url, params, last))


# ---------------------------------------------------------------- collectors

def fetch_naver_list(h):
    js = h.get("https://finance.naver.com/api/sise/etfItemList.nhn", NV_HEADERS)
    items = js.get("result", {}).get("etfItemList", [])
    return {i["itemcode"]: i for i in items}


def fetch_naver_analysis(h, code):
    return h.get("https://m.stock.naver.com/api/stock/%s/etfAnalysis" % code, NV_HEADERS)


_SUFFIX_RE = re.compile(r"\b(INC|INCORPORATED|CORP|CORPORATION|LTD|LIMITED|PLC|CO|COMPANY|HOLDINGS?|HLDGS?|SA|NV|AG|SE|ADR|ADS|SHS|ORD|NPV|THE|REG|REGISTERED|SPONSORED|NEW)\b", re.I)


def norm_stock_name(name):
    """Key used to match the same security across ETFs (managers spell foreign names differently)."""
    n = (name or "").upper().strip()
    if re.search(r"[가-힣]", n) and not re.search(r"[A-Z]{3,}", n):
        return "N:" + re.sub(r"\s+", "", n)           # Korean names are already canonical
    n = re.sub(r"\(.*?\)", " ", n)
    n = n.replace("&", " AND ").replace("-", " ").replace(".", " ").replace(",", " ").replace("'", "")
    n = re.sub(r"\bCLASS\s+([A-C])\b|\bCL\s+([A-C])\b", lambda m: " " + (m.group(1) or m.group(2)), n)
    n = _SUFFIX_RE.sub(" ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return "N:" + n.replace(" ", "")


def fetch_naver_pc_pdf(h, code):
    """Holdings table ('ETF 주요 구성자산') on the Naver PC ETF page - carries weights for overseas ETFs."""
    html = h.get("https://finance.naver.com/item/main.naver", {"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"},
                 params={"code": code}, as_json=False)
    i = html.find("ETF 주요 구성자산")
    if i < 0:
        return [], None
    seg = html[i:]
    j = seg.find("<h4", 10)
    if j > 0:
        seg = seg[:j]
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", seg, flags=re.S):
        cells = [re.sub(r"<[^>]+>", " ", c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, flags=re.S)]
        cells = [re.sub(r"\s+", " ", c).strip() for c in cells]
        if len(cells) < 3 or not cells[0] or cells[0].startswith("구성종목"):
            continue
        name = cells[0]
        w = to_num(cells[2].replace("%", "")) if "%" in cells[2] else None
        shares = to_num(cells[1])
        px = to_num(cells[3]) if len(cells) > 3 else None
        rows.append([norm_stock_name(name), name, round(w or 0.0, 4), shares, px])
    return rows, None


def fetch_wise_pdf(h, code):
    """Full CU holdings from the WiseReport ETF page. Returns (rows, date). rows: [key, name, weight, shares, None]"""
    html = h.get(WR_PAGE, WR_HEADERS, params={"cmp_cd": code}, as_json=False)
    m = re.search(r"var\s+CU_data\s*=\s*(\{.*?\});", html, flags=re.S)
    if not m:
        raise RuntimeError("CU_data not found for %s" % code)
    grid = json.loads(m.group(1)).get("grid_data", [])
    rows, date = [], None
    for g in grid:
        name = (g.get("STK_NM_KOR") or "").strip()
        if not name:
            continue
        w = to_num(g.get("ETF_WEIGHT"), 0.0) or 0.0
        date = date or re.sub(r"[^0-9]", "", g.get("TRD_DT") or "")
        rows.append([norm_stock_name(name), name, round(w, 4), to_num(g.get("AGMT_STK_CNT")), None])
    return rows, (date or None)


CACHE_DIR = os.path.join(DATA_DIR, "cache")
TICKER_CACHE = os.path.join(CACHE_DIR, "tickers.json")
PRICE_CACHE = os.path.join(CACHE_DIR, "prices.json")
BM_CACHE = os.path.join(CACHE_DIR, "benchmarks.json")
EST_CACHE = os.path.join(CACHE_DIR, "est_flags.json")
YH = {"User-Agent": UA, "Accept": "application/json"}
_FX = {}


def _yq(name):
    q = re.sub(r"\s*-\s*(CL|CLASS)\s*([A-C])\b", " CLASS \\2", name, flags=re.I)
    q = re.sub(r"\bADR\b|\bADS\b|\bSPONSORED\b|\bREG\b|\bSHS\b|\bNPV\b|\bORD\b|\bLTD\b|\bPLC\b|\bSA\b|\bNV\b|\bAG\b|\bSE\b", " ", q, flags=re.I)
    return re.sub(r"\s+", " ", q).strip()


def yahoo_search(h, name):
    """-> ("ok", info|None) when Yahoo answered, ("err", None) when the request failed (never cached as a miss)."""
    q = _yq(name)
    tries = [q]
    words = re.sub(r"\bCLASS [A-C]\b", "", q).split()
    if len(words) > 2:
        tries.append(" ".join(words[:2]))
    err = False
    for qq in tries:
        try:
            js = h.get("https://query2.finance.yahoo.com/v1/finance/search", YH,
                       params={"q": qq, "quotesCount": 6, "newsCount": 0, "listsCount": 0}, tries=2)
        except Exception:  # noqa
            err = True
            continue
        quotes = [x for x in js.get("quotes", []) if x.get("quoteType") in ("EQUITY", "ETF")]
        if not quotes:
            continue
        quotes.sort(key=lambda x: 0 if x.get("exchange") in ("NMS", "NYQ", "NGM", "NCM", "ASE", "PCX") else 1)
        x = quotes[0]
        return "ok", {"symbol": x.get("symbol"), "exch": x.get("exchange"), "yname": x.get("shortname") or x.get("longname")}
    return ("err", None) if err else ("ok", None)


def yahoo_price(h, symbol):
    js = h.get("https://query1.finance.yahoo.com/v8/finance/chart/%s" % symbol, YH,
               params={"range": "5d", "interval": "1d"}, tries=2)
    meta = (js.get("chart", {}).get("result") or [{}])[0].get("meta", {})
    px = meta.get("regularMarketPrice") or meta.get("previousClose")
    return (float(px) if px else None), (meta.get("currency") or "USD").upper()


def yahoo_prices_batch(h, symbols):
    """spark endpoint: many symbols per call. Returns {sym: (px, cur)}; {} on failure (caller falls back to per-symbol)."""
    out = {}
    try:
        js = h.get("https://query1.finance.yahoo.com/v8/finance/spark", YH,
                   params={"symbols": ",".join(symbols), "range": "5d", "interval": "1d"}, tries=1)
    except Exception:  # noqa
        return out
    if not isinstance(js, dict):
        return out
    items = []
    if isinstance(js.get("spark"), dict):
        items = [(r.get("symbol"), (r.get("response") or [{}])[0]) for r in js["spark"].get("result") or []]
    else:
        items = [(k, v) for k, v in js.items() if isinstance(v, dict)]
    for sym, resp in items:
        try:
            meta = resp.get("meta", {})
            px = meta.get("regularMarketPrice") or meta.get("previousClose")
            if not px:
                closes = [c for c in ((resp.get("indicators", {}).get("quote") or [{}])[0].get("close") or []) if c]
                px = closes[-1] if closes else None
            if sym and px:
                out[sym] = (float(px), (meta.get("currency") or "USD").upper())
        except Exception:  # noqa
            pass
    return out


def fx_to_usd(h, cur):
    """multiplier converting 1 unit of `cur` into USD (cached per run)."""
    if cur in ("USD", None, ""):
        return 1.0
    if cur not in _FX:
        try:
            px, _ = yahoo_price(h, "%s=X" % cur)   # e.g. JPY=X = JPY per USD
            _FX[cur] = (1.0 / px) if px else None
        except Exception:  # noqa
            _FX[cur] = None
    return _FX.get(cur)


PRICE_MAX_AGE_DAYS = 3


def _price_fresh(pr, today):
    if not pr or not pr.get("px") or not pr.get("date"):
        return False
    try:
        d0 = dt.datetime.strptime(pr["date"], "%Y%m%d").date()
        d1 = dt.datetime.strptime(today, "%Y%m%d").date()
        return (d1 - d0).days <= PRICE_MAX_AGE_DAYS
    except ValueError:
        return False


def estimate_weights(h, rows, tickers, prices, budget_sec=None, aum_krw=None):
    """Fill missing weights for overseas holdings from shares x Yahoo price. Mutates rows.
    Weights are shares x price / fund AUM when AUM is known (unresolved names simply stay blank);
    otherwise the resolved names are normalised to 100. Returns (n_ok, n_total, method)."""
    today = dt.datetime.now(KST).strftime("%Y%m%d")
    secs = [r for r in rows if is_security(r[1]) and (r[3] or 0) > 0]
    # 1) tickers (search) - misses are retried on later runs up to 3 times
    for r in secs:
        if budget_sec is not None and time_left() < budget_sec:
            break
        t = tickers.get(r[0])
        if t and t.get("symbol"):
            continue
        if t and not t.get("symbol") and (t.get("miss", 0) >= 3 or t.get("last") == today):
            continue
        status, info = yahoo_search(h, r[1])
        if status == "err":
            continue
        tickers[r[0]] = info or {"symbol": None, "miss": (t or {}).get("miss", 0) + 1, "last": today}
    # 2) prices - batch first, per-symbol fallback
    need = []
    for r in secs:
        sym = (tickers.get(r[0]) or {}).get("symbol")
        if sym and not _price_fresh(prices.get(sym), today) and sym not in need:
            need.append(sym)
    for i in range(0, len(need), 20):
        if budget_sec is not None and time_left() < budget_sec:
            break
        chunk = need[i:i + 20]
        got = yahoo_prices_batch(h, chunk)
        for sym, (px, cur) in got.items():
            prices[sym] = {"px": px, "cur": cur, "date": today}
        for sym in chunk:
            if sym in got:
                continue
            if budget_sec is not None and time_left() < budget_sec:
                break
            try:
                px, cur = yahoo_price(h, sym)
                if px:
                    prices[sym] = {"px": px, "cur": cur, "date": today}
            except Exception:  # noqa
                pass
    # 3) values
    vals = {}
    for r in secs:
        sym = (tickers.get(r[0]) or {}).get("symbol")
        pr = prices.get(sym) if sym else None
        if not pr or not pr.get("px"):
            continue
        cur = pr.get("cur", "USD")
        mult = fx_to_usd(h, "GBP" if cur == "GBp" else cur)
        if mult is None:
            continue
        vals[r[0]] = (r[3] or 0) * pr["px"] * mult * (0.01 if cur == "GBp" else 1.0)
    tot = sum(vals.values())
    method = "norm"
    usdkrw = None
    if aum_krw and tot > 0:
        m = fx_to_usd(h, "KRW")
        usdkrw = (1.0 / m) if m else None
    if usdkrw and aum_krw:
        cover = tot * usdkrw / aum_krw * 100
        if 50 <= cover <= 115:                 # plausible: use AUM as denominator (unresolved names stay blank)
            method = "aum"
            for r in rows:
                if r[0] in vals:
                    r[2] = round(vals[r[0]] * usdkrw / aum_krw * 100, 4)
    if method == "norm" and tot > 0:
        for r in rows:
            if r[0] in vals:
                r[2] = round(vals[r[0]] / tot * 100, 4)
    return len(vals), len(secs), method


def fetch_holdings(h, code):
    """WiseReport CU (full list, weights for domestic); when weights are missing (overseas ETFs) use Naver PC table."""
    rows, date = fetch_wise_pdf(h, code)
    if rows and not any(r[2] > 0 for r in rows if is_security(r[1])):
        try:
            nrows, _ = fetch_naver_pc_pdf(h, code)
            if nrows and any(r[2] > 0 for r in nrows if is_security(r[1])):
                return nrows, date, "naver"
        except Exception as ex:  # noqa
            log("naver pc holdings failed", code, str(ex)[:80])
        return rows, date, "wise-noweight"
    return rows, date, "wise"


def fetch_wise_hist(h, code, days=400):
    end = dt.datetime.now(KST).date()
    start = end - dt.timedelta(days=days)
    js = h.get(WR_NAV, WR_HEADERS, params={"startDT": start.isoformat(), "endDT": end.isoformat(), "dataType": "D",
                                          "cmp_cd": code, "cmp_typ": "5"})
    close, nav = {}, {}
    for g in js.get("grid_data", []):
        d = re.sub(r"[^0-9]", "", g.get("TRD_DT") or "")
        if len(d) != 8:
            continue
        c, n = to_num(g.get("CLOSE_PRC")), to_num(g.get("NAV"))
        if c:
            close[d] = c
        if n:
            nav[d] = n
    return close, nav


# ---------------------------------------------------------------- grouping

GROUP_RULES = [
    ("kosdaq", "코스닥", r"코스닥|KOSDAQ"),
    ("semicon", "반도체", r"반도체|SEMICON|파운드리|HBM|메모리|SK하이닉스"),
    ("ai_tech", "AI·테크·소프트웨어", r"(?<![A-Za-z])AI(?![A-Za-z])|인공지능|테크|소프트웨어|SW|플랫폼|인터넷|디지털|데이터센터|클라우드|메타버스|이노베이션|혁신기술|R&D|광통신|위성"),
    ("robot", "로봇·자동화·모빌리티", r"로봇|자동화|우주|항공|드론|휴머노이드|피지컬|자율주행|모빌리티|자동차"),
    ("battery", "2차전지·에너지·소재", r"2차전지|이차전지|배터리|전기차|에너지|태양광|수소|풍력|원자력|원전|전력|송전|신재생|ESS|소재"),
    ("bio", "바이오·헬스케어", r"바이오|헬스케어|제약|의료|헬스|시밀러|CDMO|신약"),
    ("defense_ship", "조선·방산·기계·제조", r"조선|해운|방산|방위|기계|중공업|건설|인프라|제조업|수출"),
    ("coveredcall", "커버드콜(액티브)", r"커버드콜"),
    ("dividend_value", "배당·밸류업·주주가치", r"배당|밸류업|가치|저변동|퀄리티|고배당|인컴|주주환원|주주가치|저PBR|밸류|ESG|성장주|가치주"),
    ("consumer", "소비·미디어·엔터", r"소비|미디어|엔터|콘텐츠|게임|K-?팝|뷰티|화장품|음식료|유통|여행|레저|컬처"),
    ("smallmid", "중소형·성장·퀀트", r"중소형|스몰캡|성장|그로스|퀀트|모멘텀|포스트IPO|강소기업|메가트렌드|미래전략|대장장이|포커스|일레븐"),
    ("broad", "코스피·전체시장", r"코스피|KOSPI|200|KRX\s?300|코리아|KOREA|대형|TOP|전체|종합|메가테크"),
]
GLOBAL_RULES = [
    ("g_income", "미국·글로벌 배당·커버드콜", r"배당|커버드콜|인컴|프리미엄|위클리|데일리|월배당|타겟|버퍼"),
    ("g_semi", "글로벌 반도체", r"반도체|SEMICON|필라델피아|SOX|엔비디아|브로드컴|TSMC|ASML"),
    ("g_asia_tech", "중국·아시아 테크", r"(차이나|중국|일본|아시아|샤오미|BYD|알리바바|텐센트|홍콩|대만).*(AI|테크|밸류체인|빅테크|플랫폼|기업)|샤오미|BYD"),
    ("g_robot", "글로벌 로봇·모빌리티·우주·방산", r"로봇|휴머노이드|피지컬|자율주행|모빌리티|우주|방산|드론|테슬라|전기차|EV\b"),
    ("g_energy", "글로벌 에너지·전력인프라", r"전력|에너지|천연가스|원자력|원전|인프라|친환경|유틸리티"),
    ("g_bio", "글로벌 바이오·헬스케어", r"바이오|헬스케어|제약|비만|메디컬|치료제|질환|의료"),
    ("g_consumer", "글로벌 소비·컨슈머·IP", r"소비|컨슈머|트렌드|럭셔리|저작권|여행|레저|엔터|미디어|영에이지|시니어"),
    ("g_asia", "중국·일본·인도·신흥국", r"차이나|중국|항셍|홍콩|일본|니케이|TOPIX|인도|베트남|신흥국|이머징|아시아|대만"),
    ("g_ai", "글로벌 AI·빅테크·소프트웨어", r"(?<![A-Za-z])AI(?![A-Za-z])|인공지능|테크|소프트웨어|클라우드|빅테크|데이터센터|양자|사이버|인터넷|플랫폼|생성형|밸류체인|넥스트|이노베이션|매그니피센트|FANG"),
    ("g_us_broad", "미국 대표지수·대형주", r"S&P|나스닥|NASDAQ|다우|러셀|미국대형|미국500|미국주식|미국성장|미국가치|성장기업|대형성장|대형가치"),
    ("g_world", "글로벌·선진국 전체시장", r"글로벌|월드|WORLD|ACWI|선진국|유럽|유로|해외|탑픽|분산|일등기업|대장장이"),
]
GROUP_ORDER = [g[0] for g in GROUP_RULES] + ["other"] + [g[0] for g in GLOBAL_RULES] + ["g_other"]
GROUP_NAMES = {g[0]: g[1] for g in GROUP_RULES}
GROUP_NAMES.update({g[0]: g[1] for g in GLOBAL_RULES})
GROUP_NAMES["other"] = "기타"
GROUP_NAMES["g_other"] = "해외 기타"
GROUP_REGION = {g[0]: "domestic" for g in GROUP_RULES}
GROUP_REGION.update({g[0]: "global" for g in GLOBAL_RULES})
GROUP_REGION.update({"other": "domestic", "g_other": "global"})


def classify_global(name, index_name):
    for key, _, pat in GLOBAL_RULES:
        if re.search(pat, name, flags=re.I):
            return key
    text = "%s | %s" % (name, index_name or "")
    for key, _, pat in GLOBAL_RULES:
        if re.search(pat, text, flags=re.I):
            return key
    return "g_other"


def classify(name, index_name):
    # product-name themes first (covered call & kosdaq are very specific), then benchmark name
    if re.search(r"커버드콜", name):
        return "coveredcall"
    for key, _, pat in GROUP_RULES:
        if key == "broad":
            continue
        if re.search(pat, name, flags=re.I):
            return key
    text = "%s | %s" % (name, index_name or "")
    for key, _, pat in GROUP_RULES:
        if re.search(pat, text, flags=re.I):
            return key
    return "other"


def norm_index(s):
    s = (s or "").upper().replace("지수", "").replace("INDEX", "").replace("TOTAL RETURN", "").replace("PRICE RETURN", "")
    s = s.replace("S&P 500", "SP500").replace("S&P500", "SP500")
    s = re.sub(r"\(.*?\)", "", s)
    s = s.replace("코스피", "KOSPI").replace("코스닥", "KOSDAQ")
    return re.sub(r"[^0-9A-Z가-힣]", "", s)


# ---------------------------------------------------------------- analytics

def ret_between(series, end_date, back_days=None, ytd=False):
    dates = sorted(series)
    if not dates:
        return None
    end = end_date if end_date in series else dates[-1]
    end_val = series[end]
    if ytd:
        prev = [d for d in dates if d < end[:4] + "0101"]
        if not prev:
            return None
        base = series[prev[-1]]
    else:
        target = (dt.datetime.strptime(end, "%Y%m%d") - dt.timedelta(days=back_days)).strftime("%Y%m%d")
        cands = [d for d in dates if d <= target]
        if not cands:
            return None
        base = series[cands[-1]]
    if not base:
        return None
    return round((end_val / base - 1) * 100, 2)


PERIODS = {"1D": 1, "1W": 7, "1M": 30, "3M": 91, "6M": 182, "1Y": 365}
NAVER_KEY = {"1D": "D1", "1W": "W1", "1M": "M1", "3M": "M3", "6M": "M6", "1Y": "Y1", "YTD": "YTD"}


def perf_block(price_series, idx_series, asof, naver):
    out = {}
    nv_price = {x["periodTypeCode"]: x["value"] for x in (naver or {}).get("returnPerformanceList", []) or []}
    nv_nav = {x["periodTypeCode"]: x["value"] for x in (naver or {}).get("navPerformanceList", []) or []}
    for label in list(PERIODS) + ["YTD"]:
        kw = {"ytd": True} if label == "YTD" else {"back_days": PERIODS[label]}
        own = ret_between(price_series, asof, **kw) if price_series else None
        nvp = to_num(nv_price.get(NAVER_KEY[label]))
        out[label] = nvp if nvp is not None else own
        out[label + "_nav"] = to_num(nv_nav.get(NAVER_KEY[label]))
        out[label + "_idx"] = ret_between(idx_series, asof, **kw) if idx_series else None
    return out


def is_security(name):
    return not (CASH_RE.search(name or "") or DERIV_RE.search(name or ""))


def compare_holdings(today, prev, bm_weights, bm_names):
    """today/prev rows: [key, name, w, shares, _]. Keys are 'N:<name>' (WiseReport has no codes)."""
    t = {x[0]: x for x in today}
    p = {x[0]: x for x in prev} if prev else {}
    rows = []
    n_new = n_out = n_up = n_down = 0
    for k, x in t.items():
        name, w = x[1], x[2]
        is_cash = not is_security(name)
        pw = p[k][2] if k in p else None
        status = "same"
        if prev and k not in p:
            status = "new"
            if not is_cash:
                n_new += 1
        chg = None if pw is None else round(w - pw, 4)
        if status == "same" and chg is not None and not is_cash:
            if chg >= 0.2:
                n_up += 1
            elif chg <= -0.2:
                n_down += 1
        bw = bm_weights.get(k) if bm_weights else None
        active = None if not bm_weights or is_cash else round(w - (bw or 0.0), 4)
        rows.append({"code": k, "name": name, "w": w, "pw": pw, "chg": chg, "status": status,
                     "bw": bw, "active": active, "cash": is_cash, "shares": x[3]})
    for k, x in p.items():
        if k in t or not is_security(x[1]):
            continue
        n_out += 1
        bw = bm_weights.get(k) if bm_weights else None
        rows.append({"code": k, "name": x[1], "w": 0.0, "pw": x[2], "chg": round(-x[2], 4), "status": "out",
                     "bw": bw, "active": (None if not bm_weights else round(-(bw or 0.0), 4)), "cash": False, "shares": 0})
    rows.sort(key=lambda r: (-r["w"], r["name"]))
    stocks = [r for r in rows if not r["cash"] and r["status"] != "out"]
    top10 = round(sum(r["w"] for r in stocks[:10]), 2)
    cash_w = round(sum(r["w"] for r in rows if r["cash"]), 2)
    turnover = round(sum(abs(r["chg"] or 0) for r in rows if not r["cash"]) / 2, 2) if prev else None
    missing = []
    if bm_weights:
        held = {r["code"] for r in stocks}
        for k, bw in sorted(bm_weights.items(), key=lambda kv: -kv[1]):
            if k not in held and bw >= 0.5:
                missing.append({"code": k, "name": bm_names.get(k, k), "bw": bw})
            if len(missing) >= 15:
                break
    summary = {"n_holdings": len(stocks), "top10": top10, "cash": cash_w, "new": n_new, "out": n_out,
               "up": n_up, "down": n_down, "turnover": turnover}
    return rows, summary, missing


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-etfs", type=int, default=0)
    ap.add_argument("--date", default="", help="ignored (kept for workflow compatibility)")
    args = ap.parse_args()
    h = Http()

    # 1. universe: Naver ETF list -> domestic equity ACTIVE ---------------------------------
    naver_list = fetch_naver_list(h)
    log("Naver ETF list:", len(naver_list))
    active = {}
    for code, i in naver_list.items():
        name = (i.get("itemname") or "").strip()
        if "액티브" not in name or EXCLUDE_RE.search(name):
            continue
        region = REGION_BY_TAB.get(i.get("etfTabCode"))
        if not region:
            continue
        aum = to_num(i.get("marketSum"))
        active[code] = {"code": code, "name": name, "region": region, "close": to_num(i.get("nowVal")), "nav": to_num(i.get("nav")),
                        "aum": aum * 1e8 if aum is not None else None, "manager": "", "index": "", "listed": ""}
    log("equity ACTIVE ETFs:", len(active), "| domestic:", sum(1 for e in active.values() if e["region"] == "domestic"),
        "| global:", sum(1 for e in active.values() if e["region"] == "global"))
    if not active:
        raise RuntimeError("no active ETFs matched")
    if args.max_etfs:
        active = dict(sorted(active.items(), key=lambda kv: -(kv[1]["aum"] or 0))[: args.max_etfs])

    # 2. Naver analysis (meta + period returns) ------------------------------------------------
    naver = {}
    for code, e in active.items():
        try:
            nv = fetch_naver_analysis(h, code)
        except Exception as ex:  # noqa
            log("naver analysis failed", code, str(ex)[:100])
            nv = {}
        naver[code] = nv
        e["index"] = re.sub(r"\((?:PR|TR|NTR|Price Return|Total Return)[^)]*\)", "", nv.get("etfBaseIndex") or "").replace("지수", "").strip()
        e["manager"] = nv.get("issuerName") or ""
        e["listed"] = nv.get("listedDate") or ""
        if e["aum"] is None:
            e["aum"] = krw_text_to_won(nv.get("totalNav"))
    flush_log()

    # 3. full holdings (WiseReport) -------------------------------------------------------------
    prev_latest = load_json(LATEST_PATH, {})           # last successful output -> fallback for anything we cannot refresh today
    prev_etfs = prev_latest.get("etfs", {}) if isinstance(prev_latest, dict) else {}
    os.makedirs(PDF_DIR, exist_ok=True)
    os.makedirs(CACHE_DIR, exist_ok=True)
    pdf_today, pdf_dates, src_count = {}, defaultdict(int), defaultdict(int)
    for code, e in active.items():
        if time_left() < 300:
            log("deadline near - skipping remaining holdings")
            break
        try:
            rows, d, src = fetch_holdings(h, code)
            pdf_today[code] = rows
            src_count[src] += 1
            if d:
                pdf_dates[d] += 1
        except Exception as ex:  # noqa
            log("holdings failed", code, e["name"], str(ex)[:100])
    for code in active:
        pdf_today.setdefault(code, [])
    asof = max(pdf_dates.items(), key=lambda kv: kv[1])[0] if pdf_dates else (prev_latest.get("asof") or dt.datetime.now(KST).strftime("%Y%m%d"))
    n_fetched = sum(1 for v in pdf_today.values() if v)
    log("holdings fetched today: %d/%d  asof=%s  sources=%s" % (n_fetched, len(active), asof, dict(src_count)))
    flush_log()

    # same-day snapshot written by an earlier run today (e.g. 08:50) fills what failed now
    path_today = os.path.join(PDF_DIR, asof + ".json")
    existing = load_json(path_today, {})
    for code, rows in pdf_today.items():
        if not rows and existing.get(code):
            pdf_today[code] = existing[code]

    # 4. price / NAV history (cheap: one call per ETF) ------------------------------------------
    hist, navh = {}, {}
    for code, e in active.items():
        if time_left() < 240:
            hist[code], navh[code] = {}, {}
            continue
        try:
            hist[code], navh[code] = fetch_wise_hist(h, code)
        except Exception as ex:  # noqa
            log("hist failed", code, str(ex)[:100])
            hist[code], navh[code] = {}, {}
    log("history ok: %d/%d" % (sum(1 for v in hist.values() if v), len(active)))
    flush_log()

    # overseas ETFs: WiseReport/Naver carry share counts only -> estimate weights from shares x price (Yahoo)
    tickers = load_json(TICKER_CACHE, {})
    prices = load_json(PRICE_CACHE, {})
    est_cache = load_json(EST_CACHE, {})
    est_flags, n_est = {}, 0

    # 5. benchmark proxies (cached across runs; refreshed when time permits) ------------------------
    bm_cache = load_json(BM_CACHE, {})
    benchmarks = {}
    for code, e in active.items():
        key = norm_index(e["index"])
        if not key or key in benchmarks:
            continue
        entry = {"name": e["index"], "source": None, "weights": {}, "names": {}, "proxy": None, "series": {}}
        proxy = STATIC_PROXY.get(key) or next((v for k, v in STATIC_PROXY.items() if v and k in key), None)
        if proxy and time_left() > 200:
            try:
                rows, _, _src = fetch_holdings(h, proxy)
                _pa = to_num((naver_list.get(proxy) or {}).get("marketSum"))
                _proxy_aum = _pa * 1e8 if _pa else None
                if rows and not any(r[2] > 0 for r in rows if is_security(r[1])) and time_left() > 240:
                    estimate_weights(h, rows, tickers, prices, budget_sec=200, aum_krw=_proxy_aum)
                    save_json(TICKER_CACHE, tickers); save_json(PRICE_CACHE, prices)
                stock_rows = [r for r in rows if is_security(r[1]) and r[2] > 0]
                tot = sum(r[2] for r in stock_rows)
                if tot > 0:
                    entry["weights"] = {r[0]: round(r[2] / tot * 100, 4) for r in stock_rows}
                    entry["names"] = {r[0]: r[1] for r in stock_rows}
                    pname = (naver_list.get(proxy) or {}).get("itemname", proxy)
                    entry["source"] = "%s 구성종목 기준 (지수 대용)" % pname
                    entry["proxy"] = proxy
                    entry["series"], _nav = fetch_wise_hist(h, proxy)
                    entry["asof"] = asof
            except Exception as ex:  # noqa
                log("benchmark proxy failed", e["index"], str(ex)[:100])
        if proxy and not entry["weights"] and bm_cache.get(key, {}).get("weights"):
            entry = bm_cache[key]
            entry["stale"] = True
            log("benchmark", e["index"], "-> cached", entry.get("asof"))
        elif entry["weights"]:
            bm_cache[key] = {k: v for k, v in entry.items() if k != "stale"}
        benchmarks[key] = entry
        log("benchmark", e["index"], "->", entry["source"], len(entry["weights"]))
    save_json(BM_CACHE, bm_cache)
    flush_log()

    # 6. weight estimation for overseas ETFs (whatever time is left; cache makes later runs fast) ----
    for code, e in active.items():
        rows = pdf_today.get(code) or []
        if not rows or any(r[2] > 0 for r in rows if is_security(r[1])):
            continue
        if time_left() < 150:
            log("deadline near - skipping remaining weight estimation")
            break
        ok, tot, method = estimate_weights(h, rows, tickers, prices, budget_sec=120, aum_krw=e.get("aum"))
        est_flags[code] = {"estimated": True, "resolved": ok, "total": tot, "method": method}
        n_est += 1
        if n_est % 10 == 0:
            save_json(TICKER_CACHE, tickers); save_json(PRICE_CACHE, prices); flush_log()
    for code, e in active.items():   # weights reused from an earlier snapshot today keep their "estimated" flag
        if code not in est_flags and code in est_cache and (pdf_today.get(code) or []) and e["region"] == "global":
            est_flags[code] = est_cache[code]
    save_json(TICKER_CACHE, tickers)
    save_json(PRICE_CACHE, prices)
    log("weights estimated for %d ETFs (tickers cached: %d, prices cached: %d, http calls total: %d)" % (n_est, len(tickers), len(prices), h.calls))
    flush_log()

    save_json(path_today, pdf_today)
    snaps = sorted(f[:-5] for f in os.listdir(PDF_DIR) if re.fullmatch(r"\d{8}\.json", f))
    prev_dates = [d for d in snaps if d < asof]
    prev_date = prev_dates[-1] if prev_dates else None
    pdf_prev = load_json(os.path.join(PDF_DIR, prev_date + ".json"), {}) if prev_date else {}
    pdf_prev = {c: [[norm_stock_name(r[1])] + list(r[1:]) for r in rows if len(r) >= 3] for c, rows in pdf_prev.items()}
    # snapshots written by earlier versions used stock codes as keys -> not comparable; require name keys
    if pdf_prev and not any(str(r[0]).startswith("N:") for rows in pdf_prev.values() for r in rows[:1]):
        log("previous snapshot uses old key format - skipping day-over-day diff for today")
        pdf_prev = {}
    log("previous snapshot:", prev_date)
    for f in snaps[:-PDF_KEEP_DAYS]:
        try:
            os.remove(os.path.join(PDF_DIR, f + ".json"))
        except OSError:
            pass

    # carry-forward: ETFs whose holdings could not be fetched today keep the previous snapshot (flagged stale)
    stale_holdings = {}
    for code in active:
        if not pdf_today.get(code) and pdf_prev.get(code):
            pdf_today[code] = pdf_prev[code]
            stale_holdings[code] = prev_date
            if code in est_cache and code not in est_flags:
                est_flags[code] = est_cache[code]
    n_ok = sum(1 for v in pdf_today.values() if v)
    if stale_holdings:
        log("holdings carried forward from %s for %d ETFs" % (prev_date, len(stale_holdings)))
    for code, fl in est_flags.items():
        est_cache[code] = fl
    save_json(EST_CACHE, est_cache)

    # 6. assemble --------------------------------------------------------------------------------
    etf_out, groups = {}, defaultdict(list)
    for code, e in active.items():
        bm = benchmarks.get(norm_index(e["index"]), {})
        rows, summary, missing = compare_holdings(pdf_today.get(code, []), pdf_prev.get(code, []),
                                                  bm.get("weights", {}), bm.get("names", {}))
        nv = naver.get(code) or {}
        ps, ns, idx = hist.get(code, {}), navh.get(code, {}), bm.get("series", {})
        perf = perf_block(ps, idx, asof, nv)
        # NAV-based returns computed from our own series when Naver did not provide them
        for label in list(PERIODS) + ["YTD"]:
            if perf.get(label + "_nav") is None and ns:
                kw = {"ytd": True} if label == "YTD" else {"back_days": PERIODS[label]}
                perf[label + "_nav"] = ret_between(ns, asof, **kw)
        g = classify_global(e["name"], e["index"]) if e["region"] == "global" else classify(e["name"], e["index"])
        groups[g].append(code)
        dates = sorted(ps)
        series = [[d, ps[d], idx.get(d, 0)] for d in dates][-260:]
        stale_series = False
        if not series and (prev_etfs.get(code) or {}).get("series"):
            series, stale_series = prev_etfs[code]["series"], True
        etf_out[code] = {
            "code": code, "name": e["name"], "manager": e["manager"], "index": e["index"], "region": e["region"],
            "fee": to_num(nv.get("totalFee")), "ter": None, "total_cost": None,
            "listed": e["listed"], "group": g,
            "close": e["close"], "nav": e["nav"], "aum": e["aum"], "val": None,
            "premium": (round((e["close"] / e["nav"] - 1) * 100, 2) if e["close"] and e["nav"] else None),
            "tracking_err": to_num(nv.get("chaseErrorRate")),
            "inflow": nv.get("cumulativeNetInflowList") or {},
            "sectors": [{"k": s.get("detailTypeCode"), "w": s.get("weight")} for s in (nv.get("sectorPortfolioList") or []) if s.get("weight")],
            "perf": perf, "summary": summary, "holdings": rows, "bm_missing": missing,
            "bm_source": bm.get("source"), "bm_proxy": bm.get("proxy"), "series": series,
            "weights_estimated": est_flags.get(code),
            "stale": {"holdings": stale_holdings.get(code), "series": stale_series, "bm": bool(bm.get("stale"))},
        }

    group_list = []
    for g in GROUP_ORDER:
        if groups.get(g):
            codes = sorted(groups[g], key=lambda c: -(etf_out[c]["aum"] or 0))
            group_list.append({"key": g, "name": GROUP_NAMES[g], "region": GROUP_REGION.get(g, "domestic"), "etfs": codes})

    events = []
    snaps = sorted(f[:-5] for f in os.listdir(PDF_DIR) if re.fullmatch(r"\d{8}\.json", f))
    for a, b in zip(snaps[:-1], snaps[1:]):
        pa = load_json(os.path.join(PDF_DIR, a + ".json"), {})
        pb = load_json(os.path.join(PDF_DIR, b + ".json"), {})
        for code in active:
            ta = {r[0]: r for r in pa.get(code, []) if str(r[0]).startswith("N:") and is_security(r[1])}
            tb = {r[0]: r for r in pb.get(code, []) if str(r[0]).startswith("N:") and is_security(r[1])}
            if not ta or not tb:
                continue
            for k in tb.keys() - ta.keys():
                events.append({"date": b, "etf": code, "type": "new", "code": k, "name": tb[k][1], "w": tb[k][2]})
            for k in ta.keys() - tb.keys():
                events.append({"date": b, "etf": code, "type": "out", "code": k, "name": ta[k][1], "w": ta[k][2]})
    events.sort(key=lambda x: (x["date"], x["etf"]), reverse=True)

    latest = {
        "asof": asof, "prev": prev_date if pdf_prev else None,
        "generated_at": dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "sample": False, "groups": group_list, "etfs": etf_out,
        "benchmarks": {k: {"name": v["name"], "source": v["source"], "n": len(v["weights"])} for k, v in benchmarks.items()},
        "events": events[:600],
        "stats": {"n_active": len(active), "n_pdf_ok": n_ok, "n_fetched": n_fetched, "n_stale": len(stale_holdings),
                  "calls": h.calls, "seconds": round(time.time() - STARTED),
                  "n_domestic": sum(1 for e in active.values() if e["region"] == "domestic"),
                  "n_global": sum(1 for e in active.values() if e["region"] == "global")},
        "sources": "네이버 금융 · WiseReport(FnGuide)",
    }
    save_json(LATEST_PATH, latest)
    save_json(STATUS_PATH, {"ok": True, "asof": asof, "generated_at": latest["generated_at"],
                            "n_active": len(active), "n_pdf_ok": n_ok, "n_fetched": n_fetched, "n_stale": len(stale_holdings)}, compact=False)
    log("DONE asof=%s active=%d holdings_ok=%d fetched=%d stale=%d calls=%d %.0fs" % (asof, len(active), n_ok, n_fetched, len(stale_holdings), h.calls, time.time() - STARTED))
    flush_log()


if __name__ == "__main__":
    try:
        main()
    except BaseException as e:  # noqa
        log("FATAL:", repr(e))
        import traceback
        traceback.print_exc()
        save_json(STATUS_PATH, {"ok": False, "error": repr(e),
                                "generated_at": dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")}, compact=False)
        flush_log()
        sys.exit(1)
