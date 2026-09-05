#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Active ETF CHECK - daily data collector
=======================================
Collects, from KRX data portal (data.krx.co.kr, no login required):
  1. ETF master list           -> filters domestic equity ACTIVE ETFs
  2. Daily ETF quotes (all)    -> close / NAV / AUM / benchmark index level
  3. PDF (portfolio deposit file) for every active ETF
  4. Benchmark index constituents (market-cap weighted) for every benchmark index
Then computes:
  - day-over-day holding changes (NEW / OUT / weight change)
  - active weight vs benchmark (over / under weight) per holding
  - performance (1D/1W/1M/3M/6M/YTD/1Y) and excess return vs benchmark index
  - automatic sector grouping so ETFs in the same theme can be compared side by side
Writes data/latest.json (consumed by index.html), data/history.json, data/pdf/<date>.json

Usage:  python scripts/collect.py [--date YYYYMMDD] [--max-etfs N]
Environment: none required.
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
HISTORY_PATH = os.path.join(DATA_DIR, "history.json")
LATEST_PATH = os.path.join(DATA_DIR, "latest.json")
STATUS_PATH = os.path.join(DATA_DIR, "status.json")

KST = dt.timezone(dt.timedelta(hours=9))
KRX_URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
KRX_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Referer": "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201030108",
    "Origin": "https://data.krx.co.kr",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}

# KRX "bld" identifiers (same ones used by the KRX data portal web pages)
BLD_ETF_LIST = "dbms/MDC/STAT/standard/MDCSTAT04601"     # ETF 전종목 기본정보
BLD_ETF_ALL_QUOTE = "dbms/MDC/STAT/standard/MDCSTAT04501"  # ETF 전종목 시세 (one day)
BLD_ETF_HIST = "dbms/MDC/STAT/standard/MDCSTAT04301"     # ETF 개별종목 시세 추이
BLD_ETF_PDF = "dbms/MDC/STAT/standard/MDCSTAT05001"      # ETF 구성종목 (PDF)
BLD_IDX_FINDER = "dbms/comm/finder/finder_equidx"         # 지수 검색
BLD_IDX_MEMBERS = "dbms/MDC/STAT/standard/MDCSTAT00601"  # 지수 구성종목

PDF_KEEP_DAYS = 70          # how many daily PDF snapshots to keep in repo
HISTORY_DAYS = 420          # calendar days of price history to backfill for new ETFs
INCLUDE_MARKETS = ("국내",)  # IDX_MKT_CLSS_NM values to include
CASH_CODE_PREFIX = "KRD"    # KRX code prefix used for KRW cash rows in PDF
SLEEP = 0.35                # politeness delay between KRX calls (seconds)

# ---------------------------------------------------------------- utilities

def log(*a):
    print(dt.datetime.now(KST).strftime("%H:%M:%S"), *a, flush=True)


def to_num(x, default=0.0):
    if x is None:
        return default
    s = str(x).replace(",", "").replace("%", "").strip()
    if s in ("", "-", "–"):
        return default
    try:
        return float(s)
    except ValueError:
        return default


def pick(rec, *keys, default=""):
    for k in keys:
        if k in rec and rec[k] not in (None, ""):
            return rec[k]
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


# ---------------------------------------------------------------- KRX client

class KRX:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update(KRX_HEADERS)
        self.calls = 0
        # warm up cookies
        try:
            self.s.get("https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201030108",
                       timeout=20)
        except Exception as e:  # noqa
            log("warm-up failed (continuing):", e)

    def post(self, bld, **params):
        payload = {"bld": bld, "locale": "ko_KR", "share": "1", "csvxls_isNo": "false"}
        payload.update({k: str(v) for k, v in params.items()})
        last_err = None
        for attempt in range(4):
            try:
                self.calls += 1
                r = self.s.post(KRX_URL, data=payload, timeout=40)
                if r.status_code != 200:
                    raise RuntimeError("HTTP %s" % r.status_code)
                js = r.json()
                time.sleep(SLEEP)
                return js
            except Exception as e:  # noqa
                last_err = e
                wait = 2 * (attempt + 1)
                log("KRX call failed (%s) bld=%s attempt=%d, retry in %ds" % (e, bld, attempt + 1, wait))
                time.sleep(wait)
        raise RuntimeError("KRX call failed permanently: bld=%s err=%s" % (bld, last_err))

    def rows(self, bld, **params):
        js = self.post(bld, **params)
        for key in ("output", "OutBlock_1", "block1"):
            if key in js and isinstance(js[key], list):
                return js[key]
        # unknown shape - return first list found
        for v in js.values():
            if isinstance(v, list):
                return v
        return []


# ---------------------------------------------------------------- collectors

def fetch_etf_master(krx):
    rows = krx.rows(BLD_ETF_LIST)
    if not rows:
        raise RuntimeError("ETF master list empty")
    log("ETF master rows:", len(rows), "| sample keys:", sorted(rows[0].keys())[:20])
    etfs = {}
    for r in rows:
        code = pick(r, "ISU_SRT_CD").strip()
        if not code:
            continue
        etfs[code] = {
            "code": code,
            "isin": pick(r, "ISU_CD"),
            "name": pick(r, "ISU_ABBRV", "ISU_NM"),
            "index": pick(r, "ETF_OBJ_IDX_NM", "IDX_IND_NM"),
            "replication": pick(r, "ETF_REPLICA_METHD_TP_CD"),   # e.g. 실물(액티브)
            "market": pick(r, "IDX_MKT_CLSS_NM"),                 # 국내 / 해외 / 국내&해외
            "asset": pick(r, "IDX_ASST_CLSS_NM"),                 # 주식 / 채권 / ...
            "manager": pick(r, "COM_ABBRV"),
            "fee": to_num(pick(r, "ETF_TOT_FEE")),
            "listed": re.sub(r"[^0-9]", "", pick(r, "LIST_DD")),
            "tax": pick(r, "TAX_TP_CD"),
        }
    return etfs


def is_active_domestic_equity(e):
    rep = e["replication"] or ""
    return ("액티브" in rep) and (e["asset"] == "주식") and (e["market"] in INCLUDE_MARKETS)


def fetch_all_quotes(krx, date_str):
    rows = krx.rows(BLD_ETF_ALL_QUOTE, trdDd=date_str)
    out = {}
    for r in rows:
        code = pick(r, "ISU_SRT_CD").strip()
        if not code:
            continue
        out[code] = {
            "close": to_num(pick(r, "TDD_CLSPRC")),
            "nav": to_num(pick(r, "NAV")),
            "aum": to_num(pick(r, "INVSTASST_NETASST_TOTAMT")),   # 순자산총액 (원)
            "mktcap": to_num(pick(r, "MKTCAP")),
            "idx": to_num(pick(r, "OBJ_STKPRC_IDX")),
            "vol": to_num(pick(r, "ACC_TRDVOL")),
            "val": to_num(pick(r, "ACC_TRDVAL")),
        }
    return out


def find_trading_date(krx, start):
    """Walk back from `start` (date) until KRX returns a non-empty ETF quote table."""
    d = start
    for _ in range(10):
        ds = d.strftime("%Y%m%d")
        q = fetch_all_quotes(krx, ds)
        if q and any(v["close"] > 0 for v in q.values()):
            return ds, q
        log("no quotes on", ds, "- stepping back")
        d -= dt.timedelta(days=1)
    raise RuntimeError("could not find a trading date")


def fetch_history(krx, isin, start, end):
    rows = krx.rows(BLD_ETF_HIST, isuCd=isin, strtDd=start, endDd=end)
    hist = {}
    for r in rows:
        d = pick(r, "TRD_DD").replace("/", "").replace("-", "").replace(".", "")
        if len(d) != 8:
            continue
        hist[d] = {
            "close": to_num(pick(r, "TDD_CLSPRC")),
            "nav": to_num(pick(r, "NAV")),
            "idx": to_num(pick(r, "OBJ_STKPRC_IDX")),
            "aum": to_num(pick(r, "INVSTASST_NETASST_TOTAMT")),
        }
    return hist


def fetch_pdf(krx, isin, date_str):
    rows = krx.rows(BLD_ETF_PDF, isuCd=isin, trdDd=date_str)
    holdings = []
    for r in rows:
        code = pick(r, "COMPST_ISU_CD").strip()
        name = pick(r, "COMPST_ISU_NM").strip()
        w = to_num(pick(r, "COMPST_RTO"))
        shares = to_num(pick(r, "COMPST_ISU_CU1_SHRS"))
        amt = to_num(pick(r, "VALU_AMT", "COMPST_AMT"))
        if not code and not name:
            continue
        holdings.append([code, name, round(w, 4), shares, amt])
    return holdings


def normalize_index_name(s):
    s = (s or "").upper()
    s = s.replace("지수", "").replace("INDEX", "")
    s = re.sub(r"\(.*?\)", "", s)
    s = re.sub(r"[^0-9A-Z가-힣]", "", s)
    return s


def build_index_finder(krx):
    """Return list of (normalized_name, raw_name, indIdx, indIdx2)."""
    found = []
    for mktsel in ("1", "2", "3", "4"):
        try:
            rows = krx.rows(BLD_IDX_FINDER, mktsel=mktsel, searchText="")
        except Exception as e:  # noqa
            log("index finder failed mktsel=%s: %s" % (mktsel, e))
            continue
        for r in rows:
            full = pick(r, "full_code").strip()
            name = pick(r, "codeName").strip()
            if len(full) >= 2 and name:
                found.append((normalize_index_name(name), name, full[0], full[1:]))
    log("index finder entries:", len(found))
    return found


def match_index(index_name, finder):
    n = normalize_index_name(index_name)
    if not n:
        return None
    for nn, raw, a, b in finder:
        if nn == n:
            return (raw, a, b)
    # relaxed: containment either way, prefer longest overlap
    best = None
    for nn, raw, a, b in finder:
        if nn and (nn in n or n in nn):
            score = len(nn)
            if best is None or score > best[0]:
                best = (score, raw, a, b)
    return best[1:] if best else None


def fetch_index_weights(krx, ind_idx, ind_idx2, date_str):
    rows = krx.rows(BLD_IDX_MEMBERS, indIdx=ind_idx, indIdx2=ind_idx2, trdDd=date_str)
    caps = {}
    names = {}
    for r in rows:
        code = pick(r, "ISU_SRT_CD").strip()
        cap = to_num(pick(r, "MKTCAP"))
        if code and cap > 0:
            caps[code] = cap
            names[code] = pick(r, "ISU_ABBRV")
    total = sum(caps.values())
    if total <= 0:
        return {}, {}
    return {c: round(v / total * 100, 4) for c, v in caps.items()}, names


# ---------------------------------------------------------------- grouping

GROUP_RULES = [
    # (group key, display name, keyword regex applied to "name | index name")
    ("kosdaq", "코스닥", r"코스닥|KOSDAQ"),
    ("semicon", "반도체", r"반도체|SEMICON|파운드리|HBM|메모리"),
    ("ai_tech", "AI·테크·소프트웨어", r"\bAI\b|인공지능|테크|소프트웨어|SW|IT|플랫폼|인터넷|디지털|데이터센터|클라우드"),
    ("robot", "로봇·자동화·우주", r"로봇|자동화|우주|항공|드론|휴머노이드"),
    ("battery", "2차전지·에너지", r"2차전지|이차전지|배터리|전기차|에너지|태양광|수소|풍력|원자력|원전|전력|송전|신재생"),
    ("bio", "바이오·헬스케어", r"바이오|헬스케어|제약|의료|헬스"),
    ("defense_ship", "조선·방산·기계", r"조선|방산|방위|기계|중공업|건설|인프라"),
    ("dividend_value", "배당·밸류업·저변동", r"배당|밸류업|가치|저변동|퀄리티|고배당|커버드콜|인컴|주주환원|저PBR|밸류"),
    ("consumer", "소비·미디어·엔터", r"소비|미디어|엔터|콘텐츠|게임|K-?팝|뷰티|화장품|음식료|유통|여행|레저"),
    ("finance", "금융·지주", r"금융|은행|보험|증권|지주"),
    ("auto_mobility", "자동차·모빌리티", r"자동차|모빌리티|자율주행"),
    ("growth_theme", "성장·테마 혼합", r"성장|그로스|혁신|테마|메가트렌드|미래|신성장|퀀트|모멘텀|중소형|스몰캡"),
    ("broad", "코스피·전체시장", r"코스피|KOSPI|KRX\s?300|MSCI|전체|종합|코리아|KOREA|대형|TOP|F-?ETF|국내주식"),
]
GROUP_ORDER = [g[0] for g in GROUP_RULES] + ["other"]
GROUP_NAMES = {g[0]: g[1] for g in GROUP_RULES}
GROUP_NAMES["other"] = "기타"


def classify(etf):
    text = "%s | %s" % (etf["name"], etf["index"])
    # a few name-based priority tweaks: theme in product name beats broad index in benchmark
    name_only = etf["name"]
    for key, _, pat in GROUP_RULES:
        if key == "broad":
            continue
        if re.search(pat, name_only, flags=re.I):
            return key
    for key, _, pat in GROUP_RULES:
        if re.search(pat, text, flags=re.I):
            return key
    return "other"


# ---------------------------------------------------------------- analytics

def series_return(hist, dates, end_date, back_days=None, ytd=False, field="close"):
    """Return % change between end_date and the last available date <= target."""
    if end_date not in hist or hist[end_date].get(field, 0) <= 0:
        return None
    end_val = hist[end_date][field]
    if ytd:
        target = end_date[:4] + "0101"
        # last date of previous year
        prev = [d for d in dates if d < target and hist[d].get(field, 0) > 0]
        if not prev:
            return None
        base = hist[prev[-1]][field]
    else:
        end_dt = dt.datetime.strptime(end_date, "%Y%m%d")
        target = (end_dt - dt.timedelta(days=back_days)).strftime("%Y%m%d")
        cands = [d for d in dates if d <= target and hist[d].get(field, 0) > 0]
        if not cands:
            return None
        base = hist[cands[-1]][field]
    if base <= 0:
        return None
    return round((end_val / base - 1) * 100, 2)


def perf_block(hist, end_date):
    dates = sorted(hist.keys())
    out = {}
    periods = {"1D": 1, "1W": 7, "1M": 30, "3M": 91, "6M": 182, "1Y": 365}
    for label, days in periods.items():
        out[label] = series_return(hist, dates, end_date, back_days=days)
        out[label + "_idx"] = series_return(hist, dates, end_date, back_days=days, field="idx")
        out[label + "_nav"] = series_return(hist, dates, end_date, back_days=days, field="nav")
    out["YTD"] = series_return(hist, dates, end_date, ytd=True)
    out["YTD_idx"] = series_return(hist, dates, end_date, ytd=True, field="idx")
    out["YTD_nav"] = series_return(hist, dates, end_date, ytd=True, field="nav")
    # 1D excess using 1D close: prefer NAV for excess if available
    return out


def compare_holdings(today, prev, bm_weights, bm_names):
    """today/prev: list of [code,name,w,shares,amt]. Returns list of dicts + summary."""
    t = {h[0]: h for h in today if h[0]}
    p = {h[0]: h for h in prev if h[0]} if prev else {}
    rows = []
    n_new = n_out = n_up = n_down = 0
    for code, h in t.items():
        is_cash = code.startswith(CASH_CODE_PREFIX) or ("현금" in h[1]) or ("예금" in h[1])
        w = h[2]
        pw = p[code][2] if code in p else None
        status = "same"
        if prev and code not in p:
            status = "new"
            n_new += 1
        chg = None if pw is None else round(w - pw, 4)
        if status == "same" and chg is not None:
            if chg >= 0.2:
                n_up += 1
            elif chg <= -0.2:
                n_down += 1
        bw = bm_weights.get(code) if bm_weights else None
        active = None if (bw is None and not bm_weights) else round(w - (bw or 0.0), 4)
        rows.append({
            "code": code, "name": h[1], "w": w, "pw": pw, "chg": chg,
            "status": status, "bw": bw, "active": active,
            "cash": is_cash, "shares": h[3], "amt": h[4],
        })
    # removed holdings
    for code, h in p.items():
        if code not in t:
            is_cash = code.startswith(CASH_CODE_PREFIX) or ("현금" in h[1])
            if is_cash:
                continue
            n_out += 1
            bw = bm_weights.get(code) if bm_weights else None
            rows.append({
                "code": code, "name": h[1], "w": 0.0, "pw": h[2], "chg": round(-h[2], 4),
                "status": "out", "bw": bw,
                "active": (None if not bm_weights else round(0.0 - (bw or 0.0), 4)),
                "cash": False, "shares": 0, "amt": 0,
            })
    rows.sort(key=lambda r: (-r["w"], r["name"]))
    stocks = [r for r in rows if not r["cash"] and r["status"] != "out"]
    top10 = round(sum(r["w"] for r in stocks[:10]), 2)
    cash_w = round(sum(r["w"] for r in rows if r["cash"]), 2)
    turnover = round(sum(abs(r["chg"] or 0) for r in rows if not r["cash"]) / 2, 2) if prev else None
    # benchmark names not held (biggest underweights) - useful for manager view
    missing = []
    if bm_weights:
        for code, bw in sorted(bm_weights.items(), key=lambda kv: -kv[1]):
            if code not in t and bw >= 0.5:
                missing.append({"code": code, "name": bm_names.get(code, code), "bw": bw})
            if len(missing) >= 15:
                break
    summary = {"n_holdings": len(stocks), "top10": top10, "cash": cash_w,
               "new": n_new, "out": n_out, "up": n_up, "down": n_down, "turnover": turnover}
    return rows, summary, missing


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYYMMDD (default: latest trading date)")
    ap.add_argument("--max-etfs", type=int, default=0, help="limit for testing")
    args = ap.parse_args()

    started = time.time()
    krx = KRX()

    # 1. master list --------------------------------------------------------
    master = fetch_etf_master(krx)
    active = {c: e for c, e in master.items() if is_active_domestic_equity(e)}
    log("total ETFs:", len(master), "| domestic equity ACTIVE:", len(active))
    if not active:
        # dump distinct replication values to help debugging
        vals = defaultdict(int)
        for e in master.values():
            vals[(e["replication"], e["market"], e["asset"])] += 1
        log("replication/market/asset combos:", dict(vals))
        raise RuntimeError("no active ETFs matched - check field names above")
    if args.max_etfs:
        active = dict(list(active.items())[: args.max_etfs])

    # 2. trading date + quotes ---------------------------------------------
    start = dt.datetime.strptime(args.date, "%Y%m%d").date() if args.date else dt.datetime.now(KST).date()
    date_str, quotes = find_trading_date(krx, start)
    log("as-of trading date:", date_str)

    # 3. price history -----------------------------------------------------
    history = load_json(HISTORY_PATH, {})
    history = {c: v for c, v in history.items() if c in active}  # drop delisted / sample codes
    hist_start = (dt.datetime.strptime(date_str, "%Y%m%d") - dt.timedelta(days=HISTORY_DAYS)).strftime("%Y%m%d")
    for code, e in active.items():
        h = history.get(code, {})
        if len(h) < 200:  # new ETF or first run -> backfill ~1 year of history
            try:
                h.update(fetch_history(krx, e["isin"], hist_start, date_str))
                log("history backfilled", code, e["name"], len(h))
            except Exception as ex:  # noqa
                log("history fetch failed", code, ex)
        q = quotes.get(code)
        if q and q["close"] > 0:
            h[date_str] = {"close": q["close"], "nav": q["nav"], "idx": q["idx"], "aum": q["aum"]}
        # trim to ~2 years
        cutoff = (dt.datetime.strptime(date_str, "%Y%m%d") - dt.timedelta(days=760)).strftime("%Y%m%d")
        history[code] = {d: v for d, v in h.items() if d >= cutoff}
    save_json(HISTORY_PATH, history)

    # previous trading date = latest date in history before date_str
    all_dates = sorted({d for h in history.values() for d in h.keys()})
    prev_dates = [d for d in all_dates if d < date_str]
    prev_date = prev_dates[-1] if prev_dates else None
    log("previous trading date:", prev_date)

    # 4. PDFs --------------------------------------------------------------
    os.makedirs(PDF_DIR, exist_ok=True)
    pdf_today_path = os.path.join(PDF_DIR, date_str + ".json")
    pdf_today = load_json(pdf_today_path, {})
    pdf_prev = load_json(os.path.join(PDF_DIR, prev_date + ".json"), {}) if prev_date else {}
    for code, e in active.items():
        if code not in pdf_today or not pdf_today[code]:
            try:
                pdf_today[code] = fetch_pdf(krx, e["isin"], date_str)
            except Exception as ex:  # noqa
                log("PDF fetch failed", code, ex)
                pdf_today[code] = []
        if prev_date and code not in pdf_prev:
            try:
                pdf_prev[code] = fetch_pdf(krx, e["isin"], prev_date)
            except Exception as ex:  # noqa
                log("prev PDF fetch failed", code, ex)
                pdf_prev[code] = []
    save_json(pdf_today_path, pdf_today)
    if prev_date:
        save_json(os.path.join(PDF_DIR, prev_date + ".json"), pdf_prev)
    n_ok = sum(1 for v in pdf_today.values() if v)
    log("PDF fetched: %d/%d" % (n_ok, len(active)))
    # prune old snapshots
    snaps = sorted(f for f in os.listdir(PDF_DIR) if f.endswith(".json"))
    for f in snaps[:-PDF_KEEP_DAYS]:
        os.remove(os.path.join(PDF_DIR, f))

    # 5. benchmark weights -------------------------------------------------
    finder = build_index_finder(krx)
    benchmarks = {}
    passive_by_index = defaultdict(list)
    for c, e in master.items():
        if ("패시브" in (e["replication"] or "")) and e["asset"] == "주식":
            passive_by_index[normalize_index_name(e["index"])].append(e)
    for code, e in active.items():
        idx_name = e["index"]
        if not idx_name or idx_name in benchmarks:
            continue
        entry = {"name": idx_name, "source": None, "weights": {}, "names": {}}
        m = match_index(idx_name, finder)
        if m:
            try:
                w, names = fetch_index_weights(krx, m[1], m[2], date_str)
                if w:
                    entry.update({"source": "KRX 지수구성종목 시총가중 (%s)" % m[0], "weights": w, "names": names})
            except Exception as ex:  # noqa
                log("index members failed", idx_name, ex)
        if not entry["weights"]:
            # fallback: PDF of a passive ETF tracking the same index (largest AUM)
            cands = passive_by_index.get(normalize_index_name(idx_name), [])
            cands.sort(key=lambda p: -quotes.get(p["code"], {}).get("aum", 0))
            for p in cands[:1]:
                try:
                    rows = fetch_pdf(krx, p["isin"], date_str)
                    tot = sum(r[2] for r in rows if r[0] and not r[0].startswith(CASH_CODE_PREFIX))
                    if tot > 0:
                        entry["weights"] = {r[0]: round(r[2] / tot * 100, 4) for r in rows
                                            if r[0] and not r[0].startswith(CASH_CODE_PREFIX)}
                        entry["names"] = {r[0]: r[1] for r in rows if r[0]}
                        entry["source"] = "패시브 ETF PDF 대용 (%s)" % p["name"]
                except Exception as ex:  # noqa
                    log("passive fallback failed", idx_name, ex)
        benchmarks[idx_name] = entry
        log("benchmark", idx_name, "->", entry["source"], len(entry["weights"]))

    # 6. assemble ----------------------------------------------------------
    etf_out = {}
    groups = defaultdict(list)
    for code, e in active.items():
        h = history.get(code, {})
        bm = benchmarks.get(e["index"], {})
        rows, summary, missing = compare_holdings(pdf_today.get(code, []), pdf_prev.get(code, []),
                                                  bm.get("weights", {}), bm.get("names", {}))
        q = quotes.get(code, {})
        perf = perf_block(h, date_str)
        g = classify(e)
        groups[g].append(code)
        # compact 1Y series for sparkline / chart: [date, close, idx]
        dates = sorted(h.keys())
        series = [[d, h[d]["close"], h[d].get("idx", 0)] for d in dates if h[d]["close"] > 0][-260:]
        etf_out[code] = {
            "code": code, "name": e["name"], "manager": e["manager"], "index": e["index"],
            "fee": e["fee"], "listed": e["listed"], "group": g,
            "close": q.get("close"), "nav": q.get("nav"), "aum": q.get("aum"), "val": q.get("val"),
            "premium": (round((q["close"] / q["nav"] - 1) * 100, 2) if q.get("nav") else None),
            "perf": perf, "summary": summary, "holdings": rows, "bm_missing": missing,
            "bm_source": bm.get("source"), "series": series,
        }

    group_list = []
    for g in GROUP_ORDER:
        if groups.get(g):
            codes = sorted(groups[g], key=lambda c: -(etf_out[c]["aum"] or 0))
            group_list.append({"key": g, "name": GROUP_NAMES[g], "etfs": codes})

    # recent NEW/OUT events across kept snapshots (for timeline)
    events = []
    snaps = sorted(f[:-5] for f in os.listdir(PDF_DIR) if f.endswith(".json"))
    for a, b in zip(snaps[:-1], snaps[1:]):
        pa = load_json(os.path.join(PDF_DIR, a + ".json"), {})
        pb = load_json(os.path.join(PDF_DIR, b + ".json"), {})
        for code in active:
            ta = {r[0]: r for r in pa.get(code, []) if r[0] and not r[0].startswith(CASH_CODE_PREFIX)}
            tb = {r[0]: r for r in pb.get(code, []) if r[0] and not r[0].startswith(CASH_CODE_PREFIX)}
            if not ta or not tb:
                continue
            for k in tb.keys() - ta.keys():
                events.append({"date": b, "etf": code, "type": "new", "code": k, "name": tb[k][1], "w": tb[k][2]})
            for k in ta.keys() - tb.keys():
                events.append({"date": b, "etf": code, "type": "out", "code": k, "name": ta[k][1], "w": ta[k][2]})
    events.sort(key=lambda x: (x["date"], x["etf"]), reverse=True)

    latest = {
        "asof": date_str, "prev": prev_date,
        "generated_at": dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "sample": False,
        "groups": group_list, "etfs": etf_out,
        "benchmarks": {k: {"name": v["name"], "source": v["source"], "n": len(v["weights"])}
                       for k, v in benchmarks.items()},
        "events": events[:600],
        "stats": {"n_active": len(active), "n_pdf_ok": n_ok, "krx_calls": krx.calls,
                  "seconds": round(time.time() - started)},
    }
    save_json(LATEST_PATH, latest)
    save_json(STATUS_PATH, {"ok": True, "asof": date_str, "generated_at": latest["generated_at"],
                            "n_active": len(active), "n_pdf_ok": n_ok}, compact=False)
    log("DONE asof=%s active=%d pdf_ok=%d calls=%d %.0fs" %
        (date_str, len(active), n_ok, krx.calls, time.time() - started))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa
        log("FATAL:", repr(e))
        save_json(STATUS_PATH, {"ok": False, "error": repr(e),
                                "generated_at": dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")}, compact=False)
        # diagnostics: which data sources are reachable from this runner? -> data/probe.json
        try:
            import subprocess
            subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe.py")],
                           timeout=600, check=False)
        except Exception as pe:  # noqa
            log("probe failed:", pe)
        sys.exit(1)
