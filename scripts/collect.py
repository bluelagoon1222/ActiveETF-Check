#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Active ETF CHECK - daily data collector (v2: ETF CHECK + Naver Finance)
=======================================================================
KRX data portal now requires login, so data comes from public endpoints that
work from GitHub Actions runners:

  ETF CHECK (Koscom)  /user/common/getEtpMast                 all listed ETFs: code, ISIN, name, manager,
                                                               benchmark index name, price, NAV, AUM, list date
                      /stock/etp/getEtfTotalExpenseRatio      TER / total cost per ETF
                      /api/user/etp/getEtfPdfRankListWeightAll?code=X   full PDF (holdings + weights)
                      /user/etp/getEtpTermHist?F16013=X&gubun=1Y        1Y daily price history
  Naver Finance       /api/sise/etfItemList.nhn                ETF list with tab code (domestic equity filter)
                      m.stock.naver.com/api/stock/X/etfAnalysis  price/NAV period returns, sector weights, base index

Computes day-over-day holding changes, active weight vs benchmark (benchmark = PDF of the largest
passive ETF tracking the same index), performance and excess return vs index proxy, sector grouping.
Writes data/latest.json (used by index.html), data/pdf/<date>.json snapshots, data/status.json.

Usage:  python scripts/collect.py [--max-etfs N]
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
KST = dt.timezone(dt.timedelta(hours=9))

EC = "https://www.etfcheck.co.kr"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
EC_HEADERS = {"User-Agent": UA, "Accept": "application/json, text/plain, */*", "Accept-Language": "ko-KR,ko;q=0.9",
              "Referer": EC + "/", "X-Requested-With": "XMLHttpRequest"}
EC_RATE_PER_MIN = 22          # ETF CHECK answers ~30 requests/minute per IP, then returns 403 for a while
EC_BLOCK_WAIT = 75            # seconds to wait after a 403 before trying again
NV_HEADERS = {"User-Agent": UA, "Accept": "application/json, text/plain, */*", "Accept-Language": "ko-KR,ko;q=0.9",
              "Referer": "https://m.stock.naver.com/"}

PDF_KEEP_DAYS = 70
SLEEP = 0.3
DEADLINE_SEC = 30 * 60   # stay well under the 40-minute job timeout; optional steps are skipped past this
STARTED = time.time()


def time_left():
    return DEADLINE_SEC - (time.time() - STARTED)
CASH_WORDS = ("현금", "예금", "설정현금", "원화", "CASH")
# active ETFs whose name matches this are NOT domestic equity (bonds, money market, overseas, commodities...)
EXCLUDE_RE = re.compile(r"채권|국공채|국채|회사채|은행채|금융채|CD금리|KOFR|머니마켓|MMF|단기채|단기자금|혼합|TDF|TRF|금리|달러|"
                        r"엔화|위안|원자재|골드|미국|글로벌|차이나|중국|일본|인도|나스닥|S&P|선진국|신흥국|유로|월드|해외|"
                        r"아시아|베트남|유럽|빅테크|테슬라|엔비디아|팔란티어|리츠부동산|리츠|부동산|비트코인|채\(", re.I)


def log(*a):
    print(dt.datetime.now(KST).strftime("%H:%M:%S"), *a, flush=True)


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


class Http:
    """Plain per-request connections (ETF CHECK drops reused keep-alive sockets) + a per-minute rate limiter."""

    def __init__(self):
        self.calls = 0
        self.ec_times = []
        self.blocked_until = 0.0

    def _throttle(self):
        now = time.time()
        if now < self.blocked_until:
            time.sleep(self.blocked_until - now)
        self.ec_times = [t for t in self.ec_times if time.time() - t < 60]
        if len(self.ec_times) >= EC_RATE_PER_MIN:
            wait = 60 - (time.time() - self.ec_times[0]) + 0.5
            if wait > 0:
                time.sleep(wait)
        self.ec_times.append(time.time())

    def get_json(self, url, headers, params=None, tries=2):
        last = None
        for i in range(tries):
            try:
                self.calls += 1
                r = requests.get(url, headers=headers, params=params, timeout=25)
                if r.status_code == 403:
                    raise PermissionError("HTTP 403")
                if r.status_code != 200:
                    raise RuntimeError("HTTP %s" % r.status_code)
                js = r.json()
                time.sleep(SLEEP)
                return js
            except PermissionError as e:
                last = e
                if i < tries - 1:
                    log("  403 from %s -> cooling down %ds" % (url.split("/")[-1], EC_BLOCK_WAIT))
                    self.blocked_until = time.time() + EC_BLOCK_WAIT
                    time.sleep(EC_BLOCK_WAIT)
                    self.ec_times = []
            except Exception as e:  # noqa
                last = e
                time.sleep(2 * (i + 1))
        raise RuntimeError("GET failed %s %s: %s" % (url, params, last))

    def ec(self, path, **params):
        self._throttle()
        js = self.get_json(EC + path, EC_HEADERS, params or None)
        if not js.get("success", True):
            raise RuntimeError("ETF CHECK error %s: %s" % (path, js.get("message")))
        return js.get("results", [])


# ---------------------------------------------------------------- collectors

def fetch_ec_master(h):
    rows = h.ec("/user/common/getEtpMast")
    out = {}
    for r in rows:
        code = (r.get("F16013") or "").strip()
        if not code:
            continue
        aum_m = to_num(r.get("F15015"))  # 백만원
        out[code] = {
            "code": code, "isin": r.get("F16012"), "name": (r.get("F16002") or "").strip(),
            "manager": r.get("F33961") or "", "index": (r.get("F34777") or "").strip(),
            "listed": re.sub(r"[^0-9]", "", r.get("F16017") or ""),
            "close": to_num(r.get("F15001")), "nav": to_num(r.get("F15301")),
            "aum": (aum_m * 1_000_000) if aum_m is not None else None,
            "val": to_num(r.get("F15023")),  # 거래대금
            "date": r.get("F12506"),
            "ec_ret": {"1W": to_num(r.get("W01001")), "1M": to_num(r.get("W01002")), "3M": to_num(r.get("W01003")),
                       "6M": to_num(r.get("W01004")), "1Y": to_num(r.get("W01005")), "3Y": to_num(r.get("W01006"))},
        }
    return out


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


# benchmark index -> representative passive ETF (used when the ETF CHECK master list is unavailable)
STATIC_PROXY = {"KOSPI200": "069500", "KOSDAQ150": "229200", "KRX반도체": "091160", "KRX300": "292190",
                "KOSPI": "226490", "KRX바이오K뉴딜": "364970", "KRX2차전지K뉴딜": "364980", "KRXBBIGK뉴딜": "364960",
                "KOSPI200커버드콜5OTM": "069500", "KOSPI200커버드콜": "069500", "KOSDAQ150커버드콜": "229200"}


def fetch_naver_master(h, naver_list):
    """Fallback universe built from Naver's ETF list (no benchmark index yet; filled from etfAnalysis later)."""
    out = {}
    for code, i in naver_list.items():
        aum = to_num(i.get("marketSum"))
        out[code] = {"code": code, "isin": None, "name": (i.get("itemname") or "").strip(), "manager": "", "index": "",
                     "listed": "", "close": to_num(i.get("nowVal")), "nav": to_num(i.get("nav")),
                     "aum": aum * 1e8 if aum is not None else None, "val": None, "date": None, "ec_ret": {}}
    return out


def fetch_ec_fees(h):
    out = {}
    for r in h.ec("/stock/etp/getEtfTotalExpenseRatio"):
        code = r.get("F16013")
        if code:
            out[code] = {"fee": to_num(r.get("F35188")), "ter": to_num(r.get("F35190")), "total": to_num(r.get("F35192"))}
    return out


def fetch_naver_list(h):
    js = h.get_json("https://finance.naver.com/api/sise/etfItemList.nhn", NV_HEADERS)
    items = js.get("result", {}).get("etfItemList", [])
    return {i["itemcode"]: i for i in items}


def fetch_naver_analysis(h, code):
    return h.get_json("https://m.stock.naver.com/api/stock/%s/etfAnalysis" % code, NV_HEADERS)


def fetch_pdf(h, code):
    rows = h.ec("/api/user/etp/getEtfPdfRankListWeightAll", code=code, start=0, limit=3000)
    holdings, date = [], None
    for r in rows:
        date = date or r.get("F12506")
        scode = (r.get("F16013_PDF") or "").strip()
        name = (r.get("NAME") or r.get("F16004") or "").strip()
        w = to_num(r.get("WEIGHT"), 0.0)
        px = to_num(r.get("F15001"))
        chg = to_num(r.get("F15004"))
        if not scode and not name:
            continue
        # [code, name, weight, price, day_change_pct]
        holdings.append([scode, name, round(w, 4), px, chg])
    return holdings, date


def fetch_hist(h, code):
    rows = h.ec("/user/etp/getEtpTermHist", F16013=code, gubun="1Y")
    out = {}
    for r in rows:
        d = re.sub(r"[^0-9]", "", r.get("F12506") or "")
        p = to_num(r.get("F15001"))
        if len(d) == 8 and p:
            out[d] = p
    return out


# ---------------------------------------------------------------- grouping

GROUP_RULES = [
    ("kosdaq", "코스닥", r"코스닥|KOSDAQ"),
    ("semicon", "반도체", r"반도체|SEMICON|파운드리|HBM|메모리|SK하이닉스"),
    ("ai_tech", "AI·테크·소프트웨어", r"\bAI\b|인공지능|테크|소프트웨어|SW|플랫폼|인터넷|디지털|데이터센터|클라우드|메타버스|이노베이션|혁신기술|R&D|광통신|위성"),
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
GROUP_ORDER = [g[0] for g in GROUP_RULES] + ["other"]
GROUP_NAMES = {g[0]: g[1] for g in GROUP_RULES}
GROUP_NAMES["other"] = "기타"


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
    s = (s or "").upper().replace("지수", "").replace("INDEX", "")
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


def is_stock_code(c):
    return bool(re.fullmatch(r"[0-9A-Z]{6}", c or ""))


def compare_holdings(today, prev, bm_weights, bm_names):
    """today/prev rows: [code, name, w, price, chg%]"""
    def key(hrow):
        return hrow[0] or ("NAME:" + hrow[1])
    t = {key(x): x for x in today}
    p = {key(x): x for x in prev} if prev else {}
    rows = []
    n_new = n_out = n_up = n_down = 0
    for k, x in t.items():
        code, name, w = x[0], x[1], x[2]
        is_cash = (not is_stock_code(code)) or any(cw in name.upper() for cw in CASH_WORDS)
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
        bw = bm_weights.get(code) if bm_weights else None
        active = None if not bm_weights or is_cash else round(w - (bw or 0.0), 4)
        rows.append({"code": code, "name": name, "w": w, "pw": pw, "chg": chg, "status": status,
                     "bw": bw, "active": active, "cash": is_cash, "px": x[3], "dchg": x[4]})
    for k, x in p.items():
        if k in t:
            continue
        code, name = x[0], x[1]
        if (not is_stock_code(code)) or any(cw in name.upper() for cw in CASH_WORDS):
            continue
        n_out += 1
        bw = bm_weights.get(code) if bm_weights else None
        rows.append({"code": code, "name": name, "w": 0.0, "pw": x[2], "chg": round(-x[2], 4), "status": "out",
                     "bw": bw, "active": (None if not bm_weights else round(-(bw or 0.0), 4)), "cash": False,
                     "px": None, "dchg": None})
    rows.sort(key=lambda r: (-r["w"], r["name"]))
    stocks = [r for r in rows if not r["cash"] and r["status"] != "out"]
    top10 = round(sum(r["w"] for r in stocks[:10]), 2)
    cash_w = round(sum(r["w"] for r in rows if r["cash"]), 2)
    turnover = round(sum(abs(r["chg"] or 0) for r in rows if not r["cash"]) / 2, 2) if prev else None
    missing = []
    if bm_weights:
        for code, bw in sorted(bm_weights.items(), key=lambda kv: -kv[1]):
            if code not in {r["code"] for r in stocks} and bw >= 0.5:
                missing.append({"code": code, "name": bm_names.get(code, code), "bw": bw})
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
    started = time.time()
    h = Http()

    # 1. universe -----------------------------------------------------------
    naver_list = {}
    try:
        naver_list = fetch_naver_list(h)
        log("Naver list:", len(naver_list))
    except Exception as e:  # noqa
        log("naver list failed:", e)
    master_src = "etfcheck"
    try:
        master = fetch_ec_master(h)
        log("ETF CHECK master:", len(master))
    except Exception as e:  # noqa
        log("ETF CHECK master failed -> using Naver list as universe:", str(e)[:160])
        if not naver_list:
            raise
        master = fetch_naver_master(h, naver_list)
        master_src = "naver"
    fees = {}
    try:
        fees = fetch_ec_fees(h)
    except Exception as e:  # noqa
        log("fees failed:", str(e)[:120])

    active = {}
    for code, e in master.items():
        if "액티브" not in e["name"]:
            continue
        if EXCLUDE_RE.search(e["name"]):
            continue
        nv = naver_list.get(code)
        if nv and nv.get("etfTabCode") not in (1, 2):   # 4=해외주식, 5=원자재, 6=채권, 7=기타
            continue
        active[code] = e
    log("domestic equity ACTIVE ETFs:", len(active))
    if not active:
        raise RuntimeError("no active ETFs matched")
    if args.max_etfs:
        active = dict(sorted(active.items(), key=lambda kv: -(kv[1]["aum"] or 0))[: args.max_etfs])

    # 2. PDFs ---------------------------------------------------------------
    os.makedirs(PDF_DIR, exist_ok=True)
    pdf_today, pdf_dates = {}, defaultdict(int)
    for code, e in active.items():
        if time_left() < 300:
            log("deadline near - skipping remaining PDFs")
            break
        try:
            rows, d = fetch_pdf(h, code)
            pdf_today[code] = rows
            if d:
                pdf_dates[d] += 1
        except Exception as ex:  # noqa
            log("PDF failed", code, e["name"], ex)
            pdf_today[code] = []
    for code in active:
        pdf_today.setdefault(code, [])
    missing_codes = [c for c, v in pdf_today.items() if not v]
    if missing_codes and time_left() > 240:
        log("second pass for %d empty PDFs after cool-down" % len(missing_codes))
        time.sleep(15)
        for code in missing_codes:
            try:
                rows, d = fetch_pdf(h, code)
                pdf_today[code] = rows
                if d:
                    pdf_dates[d] += 1
            except Exception as ex:  # noqa
                log("PDF failed again", code, str(ex)[:100])
    asof = max(pdf_dates.items(), key=lambda kv: kv[1])[0] if pdf_dates else dt.datetime.now(KST).strftime("%Y%m%d")
    n_ok = sum(1 for v in pdf_today.values() if v)
    log("PDF ok: %d/%d  asof=%s" % (n_ok, len(active), asof))

    # merge with an existing snapshot of the same date (re-runs), then save
    path_today = os.path.join(PDF_DIR, asof + ".json")
    existing = load_json(path_today, {})
    for code, rows in pdf_today.items():
        if not rows and existing.get(code):
            pdf_today[code] = existing[code]
    save_json(path_today, pdf_today)
    snaps = sorted(f[:-5] for f in os.listdir(PDF_DIR) if f.endswith(".json") and re.fullmatch(r"\d{8}\.json", f))
    prev_dates = [d for d in snaps if d < asof]
    prev_date = prev_dates[-1] if prev_dates else None
    pdf_prev = load_json(os.path.join(PDF_DIR, prev_date + ".json"), {}) if prev_date else {}
    log("previous snapshot:", prev_date)
    for f in snaps[:-PDF_KEEP_DAYS]:
        try:
            os.remove(os.path.join(PDF_DIR, f + ".json"))
        except OSError:
            pass

    # 3a. Naver per-ETF analysis (also fills index/manager when master came from Naver) ----
    naver = {}
    for code, e in active.items():
        if time_left() < 120:
            naver[code] = {}
            continue
        try:
            nv = fetch_naver_analysis(h, code)
        except Exception as ex:  # noqa
            log("naver analysis failed", code, ex)
            nv = {}
        naver[code] = nv
        if not e["index"]:
            e["index"] = re.sub(r"\(.*?지수\)$", "", (nv.get("etfBaseIndex") or "")).strip()
        if not e["manager"]:
            e["manager"] = nv.get("issuerName") or ""
        if not e["listed"]:
            e["listed"] = nv.get("listedDate") or ""
        if e["aum"] is None:
            e["aum"] = krw_text_to_won(nv.get("totalNav"))

    # 3b. benchmark proxies: largest passive ETF with same benchmark index --------
    passive_by_index = defaultdict(list)
    for code, e in master.items():
        if "액티브" in e["name"] or not e["index"]:
            continue
        if re.search(r"레버리지|인버스|2X|선물|합성|커버드콜|채권혼합|TR\b", e["name"]):
            continue
        passive_by_index[norm_index(e["index"])].append(e)
    benchmarks = {}
    for code, e in active.items():
        key = norm_index(e["index"])
        if not key or key in benchmarks:
            continue
        if time_left() < 180:
            log("deadline near - skipping remaining benchmarks")
            break
        entry = {"name": e["index"], "source": None, "weights": {}, "names": {}, "proxy": None, "series": {}}
        cands = sorted(passive_by_index.get(key, []), key=lambda p: -(p["aum"] or 0))
        if not cands:
            sp = STATIC_PROXY.get(key) or next((v for k, v in STATIC_PROXY.items() if k and k in key), None)
            if sp:
                cands = [master.get(sp) or {"code": sp, "name": (naver_list.get(sp) or {}).get("itemname", sp), "aum": 0}]
        for p in cands[:1]:
            try:
                rows, _ = fetch_pdf(h, p["code"])
                stock_rows = [r for r in rows if is_stock_code(r[0]) and not any(cw in r[1].upper() for cw in CASH_WORDS)]
                tot = sum(r[2] for r in stock_rows)
                if tot > 0:
                    entry["weights"] = {r[0]: round(r[2] / tot * 100, 4) for r in stock_rows}
                    entry["names"] = {r[0]: r[1] for r in stock_rows}
                    entry["source"] = "%s PDF 기준 (지수 대용)" % p["name"]
                    entry["proxy"] = p["code"]
                    entry["series"] = fetch_hist(h, p["code"])
            except Exception as ex:  # noqa
                log("benchmark proxy failed", e["index"], ex)
        benchmarks[key] = entry
        log("benchmark", e["index"], "->", entry["source"], len(entry["weights"]))

    # 4. per-ETF price history ------------------------------------------------------
    hist = {}
    for code, e in active.items():
        if time_left() < 60:
            hist[code] = {}
            continue
        try:
            hist[code] = fetch_hist(h, code)
        except Exception as ex:  # noqa
            log("hist failed", code, str(ex)[:100])
            hist[code] = {}

    # 5. assemble -------------------------------------------------------------------
    etf_out, groups = {}, defaultdict(list)
    for code, e in active.items():
        bm = benchmarks.get(norm_index(e["index"]), {})
        rows, summary, missing = compare_holdings(pdf_today.get(code, []), pdf_prev.get(code, []),
                                                  bm.get("weights", {}), bm.get("names", {}))
        nv = naver.get(code) or {}
        ps, idx = hist.get(code, {}), bm.get("series", {})
        perf = perf_block(ps, idx, asof, nv)
        g = classify(e["name"], e["index"])
        groups[g].append(code)
        dates = sorted(ps)
        series = [[d, ps[d], idx.get(d, 0)] for d in dates][-260:]
        fee = fees.get(code, {})
        etf_out[code] = {
            "code": code, "name": e["name"], "manager": e["manager"] or nv.get("issuerName", ""),
            "index": e["index"] or nv.get("etfBaseIndex", ""),
            "fee": fee.get("fee") if fee.get("fee") is not None else to_num(nv.get("totalFee")),
            "ter": fee.get("ter"), "total_cost": fee.get("total"),
            "listed": e["listed"] or nv.get("listedDate", ""), "group": g,
            "close": e["close"], "nav": e["nav"], "aum": e["aum"], "val": e["val"],
            "premium": (round((e["close"] / e["nav"] - 1) * 100, 2) if e["close"] and e["nav"] else None),
            "tracking_err": to_num(nv.get("chaseErrorRate")),
            "inflow": nv.get("cumulativeNetInflowList") or {},
            "sectors": [{"k": s.get("detailTypeCode"), "w": s.get("weight")} for s in (nv.get("sectorPortfolioList") or []) if s.get("weight")],
            "perf": perf, "summary": summary, "holdings": rows, "bm_missing": missing,
            "bm_source": bm.get("source"), "bm_proxy": bm.get("proxy"), "series": series,
        }

    group_list = []
    for g in GROUP_ORDER:
        if groups.get(g):
            codes = sorted(groups[g], key=lambda c: -(etf_out[c]["aum"] or 0))
            group_list.append({"key": g, "name": GROUP_NAMES[g], "etfs": codes})

    # events timeline across kept snapshots
    events = []
    snaps = sorted(f[:-5] for f in os.listdir(PDF_DIR) if re.fullmatch(r"\d{8}\.json", f))
    for a, b in zip(snaps[:-1], snaps[1:]):
        pa = load_json(os.path.join(PDF_DIR, a + ".json"), {})
        pb = load_json(os.path.join(PDF_DIR, b + ".json"), {})
        for code in active:
            ta = {r[0]: r for r in pa.get(code, []) if is_stock_code(r[0])}
            tb = {r[0]: r for r in pb.get(code, []) if is_stock_code(r[0])}
            if not ta or not tb:
                continue
            for k in tb.keys() - ta.keys():
                events.append({"date": b, "etf": code, "type": "new", "code": k, "name": tb[k][1], "w": tb[k][2]})
            for k in ta.keys() - tb.keys():
                events.append({"date": b, "etf": code, "type": "out", "code": k, "name": ta[k][1], "w": ta[k][2]})
    events.sort(key=lambda x: (x["date"], x["etf"]), reverse=True)

    latest = {
        "asof": asof, "prev": prev_date,
        "generated_at": dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "sample": False, "groups": group_list, "etfs": etf_out,
        "benchmarks": {k: {"name": v["name"], "source": v["source"], "n": len(v["weights"])} for k, v in benchmarks.items()},
        "events": events[:600],
        "stats": {"n_active": len(active), "n_pdf_ok": n_ok, "calls": h.calls, "seconds": round(time.time() - started)},
        "sources": "ETF CHECK(코스콤) · 네이버 금융", "master_src": master_src,
    }
    save_json(LATEST_PATH, latest)
    save_json(STATUS_PATH, {"ok": True, "asof": asof, "generated_at": latest["generated_at"],
                            "n_active": len(active), "n_pdf_ok": n_ok}, compact=False)
    log("DONE asof=%s active=%d pdf_ok=%d calls=%d %.0fs" % (asof, len(active), n_ok, h.calls, time.time() - started))


if __name__ == "__main__":
    try:
        main()
    except BaseException as e:  # noqa
        log("FATAL:", repr(e))
        import traceback
        traceback.print_exc()
        save_json(STATUS_PATH, {"ok": False, "error": repr(e),
                                "generated_at": dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")}, compact=False)
        sys.exit(1)
