import requests, json, time, os, sys
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"); os.makedirs(OUT, exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept-Language": "ko-KR,ko;q=0.9"}
res = []
def rec(name, fn):
    t = time.time()
    try:
        r = fn()
        body = r.text[:600].replace("\n", " ")
        res.append({"name": name, "status": r.status_code, "ms": int((time.time()-t)*1000), "len": len(r.content),
                    "ctype": r.headers.get("content-type"), "server": r.headers.get("server"), "body": body})
    except Exception as e:
        res.append({"name": name, "error": repr(e)[:300]})
    print(res[-1]["name"], res[-1].get("status"), res[-1].get("error", "")[:80], flush=True)

rec("ipinfo", lambda: requests.get("https://ipinfo.io/json", timeout=15))
# --- KRX variants
s = requests.Session(); s.headers.update(UA)
rec("krx_home_get", lambda: s.get("https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201030108", timeout=20))
krx_pay = {"bld": "dbms/MDC/STAT/standard/MDCSTAT04601", "locale": "ko_KR", "share": "1", "csvxls_isNo": "false"}
rec("krx_post_plain", lambda: s.post("https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd", data=krx_pay, timeout=20))
rec("krx_post_referer", lambda: s.post("https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd", data=krx_pay, timeout=20,
    headers={"Referer": "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201030108"}))
rec("krx_post_http", lambda: requests.post("http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd", data=krx_pay, timeout=20,
    headers={**UA, "Referer": "http://data.krx.co.kr/contents/MDC/MDI/mdiLoader"}))
rec("krx_otp_generate", lambda: s.post("https://data.krx.co.kr/comm/fileDn/GenerateOTP/generate.cmd", timeout=20,
    data={"locale": "ko_KR", "mktId": "ALL", "share": "1", "csvxls_isNo": "false", "name": "fileDown", "url": "dbms/MDC/STAT/standard/MDCSTAT04601"},
    headers={"Referer": "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201030108"}))
rec("krx_open_api_home", lambda: requests.get("https://open.krx.co.kr", timeout=20, headers=UA))
rec("krx_kind", lambda: requests.get("https://kind.krx.co.kr/main.do", timeout=20, headers=UA))
# --- Naver
rec("naver_etf_list", lambda: requests.get("https://finance.naver.com/api/sise/etfItemList.nhn", timeout=20, headers=UA))
rec("naver_m_basic", lambda: requests.get("https://m.stock.naver.com/api/stock/069500/basic", timeout=20, headers=UA))
rec("naver_m_integration", lambda: requests.get("https://m.stock.naver.com/api/stock/069500/integration", timeout=20, headers=UA))
rec("naver_m_etfAnalysis", lambda: requests.get("https://m.stock.naver.com/api/stock/069500/etfAnalysis", timeout=20, headers=UA))
rec("naver_pc_etf_item", lambda: requests.get("https://finance.naver.com/item/main.naver?code=069500", timeout=20, headers=UA))
# --- Seibro / KOFIA / others
rec("seibro_home", lambda: requests.get("https://seibro.or.kr/websquare/control.jsp?w2xPath=/IPORTAL/user/etf/BIP_CNTS06030V.xml&menuNo=179", timeout=20, headers=UA))
rec("kofia_freesis", lambda: requests.get("https://freesis.kofia.or.kr/", timeout=20, headers=UA))
rec("etfcheck_home", lambda: requests.get("https://www.etfcheck.co.kr/", timeout=20, headers=UA))
rec("etfcheck_m", lambda: requests.get("https://www.etfcheck.co.kr/mobile/etpitem/069500/pdf", timeout=20, headers=UA))
rec("fnguide_etf", lambda: requests.get("https://comp.fnguide.com/SVO2/ASP/etf_snapshot.asp?pGB=1&gicode=A069500", timeout=20, headers=UA))
rec("funetf", lambda: requests.get("https://www.funetf.co.kr/", timeout=20, headers=UA))
# --- asset managers
for name, url in [
    ("kodex", "https://www.samsungfund.com/etf/product/view.do?id=2ETF01"),
    ("tiger", "https://www.tigeretf.com/ko/product/search/detail/index.do?ksdFund=KR7102110004"),
    ("koact", "https://www.samsungactive.co.kr/"),
    ("timefolio", "https://www.timefolio.co.kr/"),
    ("ace", "https://www.aceetf.co.kr/"),
    ("rise", "https://www.riseetf.co.kr/"),
    ("sol", "https://www.soletf.com/"),
    ("plus", "https://www.plusetf.co.kr/"),
    ("hanaro", "https://www.hanaroetf.com/"),
    ("kiwoom", "https://www.kiwoomam.com/"),
    ("kosef", "https://www.kosef.co.kr/"),
    ("woori_won", "https://www.wooriam.kr/"),
    ("daishin343", "https://www.daishin343.com/"),
    ("bnk", "https://www.bnkasset.com/"),
    ("dbetf", "https://www.db-am.com/"),
    ("hankookmiraemang", "https://www.hi-am.com/"),
]:
    rec("am_" + name, lambda url=url: requests.get(url, timeout=20, headers=UA, allow_redirects=True))
json.dump(res, open(os.path.join(OUT, "probe.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("DONE", len(res))
