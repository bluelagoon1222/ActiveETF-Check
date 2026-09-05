#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Offline test / sample generator.
Runs collect.py's full pipeline against a FAKE KRX that returns synthetic data,
so the front-end can be checked before the first real GitHub Actions run.
Output is flagged "sample": true and is replaced by the first real run.

    python scripts/make_sample.py
"""
import datetime as dt
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import collect  # noqa: E402

random.seed(7)
TODAY = dt.date(2026, 9, 4)

STOCKS_KOSDAQ = [("247540", "에코프로비엠"), ("086520", "에코프로"), ("028300", "HLB"), ("196170", "알테오젠"),
                 ("328130", "루닛"), ("058470", "리노공업"), ("039030", "이오테크닉스"), ("403870", "HPSP"),
                 ("240810", "원익IPS"), ("035900", "JYP Ent."), ("112040", "위메이드"), ("263750", "펄어비스"),
                 ("357780", "솔브레인"), ("095340", "ISC"), ("214150", "클래시스"), ("145020", "휴젤"),
                 ("068760", "셀트리온제약"), ("141080", "리가켐바이오"), ("277810", "레인보우로보틱스"), ("336370", "솔루스첨단소재"),
                 ("178320", "서진시스템"), ("084370", "유진테크"), ("222080", "씨아이에스"), ("376300", "디어유"),
                 ("041510", "에스엠"), ("122870", "와이지엔터테인먼트"), ("053800", "안랩"), ("036930", "주성엔지니어링")]
STOCKS_SEMI = [("005930", "삼성전자"), ("000660", "SK하이닉스"), ("042700", "한미반도체"), ("403870", "HPSP"),
               ("058470", "리노공업"), ("240810", "원익IPS"), ("039030", "이오테크닉스"), ("357780", "솔브레인"),
               ("036930", "주성엔지니어링"), ("140860", "파크시스템스"), ("000990", "DB하이텍"), ("088980", "맥쿼리인프라"),
               ("064760", "티씨케이"), ("166090", "하나머티리얼즈"), ("084370", "유진테크"), ("102710", "이엔에프테크놀로지")]

ETFS = [
    # code, isin, name, index, manager, fee, rep
    ("A00001", "KR7A00001000", "TIMEFOLIO 코스닥150액티브", "코스닥 150", "타임폴리오", 0.80, "실물(액티브)"),
    ("A00002", "KR7A00002000", "KODEX 코스닥150액티브", "코스닥 150", "삼성자산운용", 0.50, "실물(액티브)"),
    ("A00003", "KR7A00003000", "KoAct 코스닥액티브", "코스닥 150", "삼성액티브자산운용", 0.55, "실물(액티브)"),
    ("A00004", "KR7A00004000", "TIGER 코스닥150액티브", "코스닥 150", "미래에셋자산운용", 0.45, "실물(액티브)"),
    ("A00011", "KR7A00011000", "KoAct 반도체액티브", "KRX 반도체", "삼성액티브자산운용", 0.50, "실물(액티브)"),
    ("A00012", "KR7A00012000", "TIMEFOLIO K반도체액티브", "KRX 반도체", "타임폴리오", 0.80, "실물(액티브)"),
    ("A00013", "KR7A00013000", "ACE 반도체밸류체인액티브", "KRX 반도체", "한국투자신탁운용", 0.39, "실물(액티브)"),
    ("A00021", "KR7A00021000", "KoAct 배당성장액티브", "코스피 200", "삼성액티브자산운용", 0.50, "실물(액티브)"),
    ("P00001", "KR7P00001000", "KODEX 코스닥150", "코스닥 150", "삼성자산운용", 0.15, "실물(패시브)"),
    ("P00002", "KR7P00002000", "KODEX 200", "코스피 200", "삼성자산운용", 0.15, "실물(패시브)"),
    ("B00001", "KR7B00001000", "KODEX 국고채30년액티브", "KIS 국고채 30년", "삼성자산운용", 0.05, "실물(액티브)"),
]


def universe(index):
    return STOCKS_KOSDAQ if "코스닥" in index else STOCKS_SEMI


def portfolio(code, index, seed):
    rnd = random.Random(seed)
    uni = universe(index)
    n = rnd.randint(14, len(uni))
    picks = rnd.sample(uni, n)
    raw = [rnd.random() ** 1.6 + 0.02 for _ in picks]
    tot = sum(raw)
    cash = rnd.uniform(0.5, 3.0)
    rows = [{"COMPST_ISU_CD": c, "COMPST_ISU_NM": nm, "COMPST_RTO": "%.2f" % (w / tot * (100 - cash)),
             "COMPST_ISU_CU1_SHRS": str(rnd.randint(100, 5000)), "VALU_AMT": str(rnd.randint(10 ** 7, 10 ** 9))}
            for (c, nm), w in zip(picks, raw)]
    rows.append({"COMPST_ISU_CD": "KRD010010001", "COMPST_ISU_NM": "원화현금", "COMPST_RTO": "%.2f" % cash,
                 "COMPST_ISU_CU1_SHRS": "0", "VALU_AMT": "1000000"})
    return rows


class FakeKRX:
    calls = 0

    def rows(self, bld, **p):
        self.calls += 1
        if bld == collect.BLD_ETF_LIST:
            return [{"ISU_SRT_CD": c, "ISU_CD": i, "ISU_ABBRV": n, "ETF_OBJ_IDX_NM": ix, "ETF_REPLICA_METHD_TP_CD": rep,
                     "IDX_MKT_CLSS_NM": "국내", "IDX_ASST_CLSS_NM": "채권" if c.startswith("B") else "주식",
                     "COM_ABBRV": m, "ETF_TOT_FEE": str(fee), "LIST_DD": "2023/05/16", "TAX_TP_CD": "배당소득세(보유기간과세)"}
                    for c, i, n, ix, m, fee, rep in ETFS]
        if bld == collect.BLD_ETF_ALL_QUOTE:
            d = dt.datetime.strptime(p["trdDd"], "%Y%m%d").date()
            if d.weekday() >= 5:
                return []
            out = []
            for c, i, n, ix, m, fee, rep in ETFS:
                base = 10000 + (hash(c) % 5000)
                drift = (d - dt.date(2025, 9, 1)).days * 3
                out.append({"ISU_SRT_CD": c, "TDD_CLSPRC": str(base + drift + random.randint(-150, 150)),
                            "NAV": str(base + drift), "INVSTASST_NETASST_TOTAMT": str(random.randint(300, 9000) * 10 ** 8),
                            "MKTCAP": "0", "OBJ_STKPRC_IDX": "%.2f" % (1000 + drift / 5), "ACC_TRDVOL": "1000", "ACC_TRDVAL": "100000000"})
            return out
        if bld == collect.BLD_ETF_HIST:
            rows = []
            d = dt.datetime.strptime(p["strtDd"], "%Y%m%d").date()
            end = dt.datetime.strptime(p["endDd"], "%Y%m%d").date()
            code = [e for e in ETFS if e[1] == p["isuCd"]][0][0]
            base = 10000 + (hash(code) % 5000)
            px, idx = base * 0.8, 800.0
            rnd = random.Random(code)
            while d <= end:
                if d.weekday() < 5:
                    px *= 1 + rnd.gauss(0.0006, 0.015)
                    idx *= 1 + rnd.gauss(0.0004, 0.013)
                    rows.append({"TRD_DD": d.strftime("%Y/%m/%d"), "TDD_CLSPRC": "%.0f" % px, "NAV": "%.0f" % (px * 1.001),
                                 "OBJ_STKPRC_IDX": "%.2f" % idx, "INVSTASST_NETASST_TOTAMT": "100000000000"})
                d += dt.timedelta(days=1)
            return rows
        if bld == collect.BLD_ETF_PDF:
            e = [e for e in ETFS if e[1] == p["isuCd"]][0]
            day = int(p["trdDd"])
            seed = hash(e[0]) % 1000 + (day % 2)   # different portfolio on some days -> diffs
            return portfolio(e[0], e[3], seed)
        if bld == collect.BLD_IDX_FINDER:
            if p["mktsel"] == "2":
                return [{"full_code": "2203", "codeName": "코스닥 150"}]
            if p["mktsel"] == "1":
                return [{"full_code": "1028", "codeName": "코스피 200"}, {"full_code": "5300", "codeName": "KRX 300"}]
            return []
        if bld == collect.BLD_IDX_MEMBERS:
            if p["indIdx2"] == "203":
                return [{"ISU_SRT_CD": c, "ISU_ABBRV": n, "MKTCAP": str(random.randint(5, 200) * 10 ** 11)} for c, n in STOCKS_KOSDAQ]
            if p["indIdx2"] == "028":
                return [{"ISU_SRT_CD": c, "ISU_ABBRV": n, "MKTCAP": str(random.randint(5, 200) * 10 ** 11)} for c, n in STOCKS_SEMI]
            return []
        return []


def run():
    collect.KRX = FakeKRX
    collect.SLEEP = 0
    # two runs: yesterday then today -> so the day-over-day diff exists
    for d in ("20260903", "20260904"):
        sys.argv = ["collect.py", "--date", d]
        collect.main()
    latest = json.load(open(collect.LATEST_PATH, encoding="utf-8"))
    latest["sample"] = True
    collect.save_json(collect.LATEST_PATH, latest)
    print("sample written:", collect.LATEST_PATH, "groups:", [(g["name"], len(g["etfs"])) for g in latest["groups"]])


if __name__ == "__main__":
    run()
