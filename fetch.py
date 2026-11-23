import os
import re
import json
import time
import datetime
import requests
import yfinance as yf

FUNDS = {
    "野村台灣運籌": {
        "isin": "TW000T3218Y2",
        "yahoo_fo": "F0HKG05WZB:FO",
        "anue_id": "A32002",
        "mdj_id": "ACKH03"
    },
    "野村優質(累積)": {
        "isin": "TW000T3201Y8",
        "yahoo_fo": "F0HKG05X20:FO",
        "anue_id": "A32001",
        "mdj_id": "ACIC01"
    },
    "施羅德台灣樂活A": {
        "isin": "TW000T4209Y0",
        "yahoo_fo": "F000000LO1:FO",
        "anue_id": "A42001",
        "mdj_id": "ACES10"
    }
}

BENCHMARKS = {
    "加權指數": "^TWII",
    "台積電(2330)": "2330.TW",
    "00935": "00935.TW"
}

START_DATE = "2010-01-04"
DATA_FILE = "data.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
}


def build_url(scheme_host, path_parts, query=""):
    """用陣列組合網址，100% 避免複製貼上到 GitHub 網頁時遺失斜線"""
    url = scheme_host.rstrip("/") + "/" + "/".join(path_parts)
    if query:
        url += "?" + query
    return url


def load_existing_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"funds": {}, "benchmarks": {}}


def fetch_yahoo_tw_chart(session, yahoo_fo):
    """從 Yahoo 股市台灣版基金走勢頁內嵌 JSON/API 拉取完整歷史（走 Yahoo 全球 CDN，不擋海外 IP）"""
    start_ts = int(datetime.datetime.strptime(START_DATE, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc).timestamp())
    end_ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp()) + 86400

    # 透過 Yahoo 股市台灣版 _td-stock 代理接口抓取晨星基金歷史走勢
    api_url = build_url(
        "https://tw.stock.yahoo.com",
        ["_td-stock", "api", "resource", f"FinanceChartService.ApacLibraCharts;symbols=%5B%22{yahoo_fo}%22%5D;type=null"],
        f"period1={start_ts}&period2={end_ts}&interval=1d"
    )
    referer_url = build_url("https://tw.stock.yahoo.com", ["fund", yahoo_fo, "summary"])

    try:
        resp = session.get(api_url, headers={**HEADERS, "Referer": referer_url}, timeout=25)
        print(f"    [Yahoo股市走勢API] HTTP {resp.status_code}")
        if resp.status_code == 200 and resp.text.strip():
            data = resp.json()
            result = {}
            chart_list = data if isinstance(data, list) else [data]
            for item in chart_list:
                res_arr = item.get("chart", {}).get("result", [])
                if not res_arr:
                    continue
                res_obj = res_arr
                timestamps = res_obj.get("timestamp", [])
                quotes = res_obj.get("indicators", {}).get("quote", [{}])
                closes = quotes.get("close", [])
                for ts, val in zip(timestamps, closes):
                    if ts is not None and val is not None and float(val) > 0:
                        dt_str = datetime.datetime.fromtimestamp(
                            int(ts), tz=datetime.timezone(datetime.timedelta(hours=8))
                        ).strftime("%Y-%m-%d")
                        if dt_str >= START_DATE:
                            result[dt_str] = round(float(val), 4)
            if len(result) > 100:
                print(f"    [Yahoo股市走勢API] 成功取得 {len(result)} 筆歷史淨值")
                return result
    except Exception as e:
        print(f"    [Yahoo股市走勢API異常] {e}")

    return {}


def fetch_anuefund_history(session, anue_id):
    """備援：從新版鉅亨買基金 (www.anuefund.com) 歷史淨值 API 拉取完整歷史"""
    api_url = build_url("https://www.anuefund.com", ["api", "fund", "nav", anue_id])
    referer_url = build_url("https://www.anuefund.com", ["fund", "detail", anue_id])
    params = {"startDate": START_DATE, "endDate": datetime.date.today().strftime("%Y-%m-%d")}

    try:
        resp = session.get(api_url, params=params, headers={**HEADERS, "Referer": referer_url}, timeout=25)
        print(f"    [鉅亨買基金API] HTTP {resp.status_code}")
        if resp.status_code == 200 and resp.text.strip():
            data = resp.json()
            rows = data if isinstance(data, list) else data.get("data", [])
            result = {}
            for r in rows:
                d_raw = str(r.get("date") or r.get("navDate") or "")[:10].replace("/", "-")
                nav = r.get("nav") or r.get("price")
                if len(d_raw) == 10 and nav is not None and float(nav) > 0 and d_raw >= START_DATE:
                    result[d_raw] = round(float(nav), 4)
            if len(result) > 100:
                print(f"    [鉅亨買基金API] 成功取得 {len(result)} 筆歷史淨值")
                return result
    except Exception as e:
        print(f"    [鉅亨買基金API異常] {e}")

    return {}


def fetch_mdj_recent_30(session, mdj_id):
    """增量更新：抓取 MoneyDJ 近 30 日最新淨值（已驗證在 GitHub Actions 100% 穩定）"""
    url = build_url("https://www.moneydj.com", ["funddj", "ya", "yp010000.djhtm"], f"a={mdj_id}")
    try:
        resp = session.get(url, headers=HEADERS, timeout=20)
        if resp.status_code == 200:
            resp.encoding = "big5"
            clean_text = re.sub(r"<[^>]+>", " ", resp.text)
            pairs = re.findall(r"(?<!\d)(\d{2}/\d{2})(?!\d)\s+([\d,]+\.\d+)", clean_text)
            today = datetime.date.today()
            parsed = {}
            for md_str, nav_raw in pairs:
                month, day = map(int, md_str.split("/"))
                year = today.year - 1 if (month - today.month) > 2 else today.year
                try:
                    dt = datetime.date(year, month, day)
                    if dt > today + datetime.timedelta(days=5):
                        dt = datetime.date(year - 1, month, day)
                    d_iso = dt.strftime("%Y-%m-%d")
                    val = float(nav_raw.replace(",", ""))
                    if val > 0 and d_iso >= START_DATE:
                        parsed[d_iso] = round(val, 4)
                except ValueError:
                    continue
            return parsed
    except Exception as e:
        print(f"    [MDJ近30日異常] {e}")
    return {}


def update_fund(session, fund_name, info, existing_records):
    merged = dict(existing_records)
    earliest_date = min(merged.keys()) if merged else "9999-12-31"

    # 若尚未取得 2010 年起的完整歷史，先嘗試 Yahoo 股市台灣版走勢 API，再試新版鉅亨買基金 API
    if earliest_date > "2010-02-01" or len(merged) < 1000:
        print(f"  -> 目前最早僅至 {earliest_date}，拉取 {START_DATE} 至今完整歷史...")
        hist = fetch_yahoo_tw_chart(session, info["yahoo_fo"])
        if len(hist) < 100:
            hist = fetch_anuefund_history(session, info["anue_id"])
        merged.update(hist)

    # 每日增量更新：用已確認穩定的 MoneyDJ 近 30 日表補上最新交易日淨值
    recent = fetch_mdj_recent_30(session, info["mdj_id"])
    print(f"  -> MoneyDJ 近30日取得: {len(recent)} 筆")
    merged.update(recent)

    return dict(sorted(merged.items()))


def fetch_benchmark_history(ticker):
    df = yf.download(ticker, start=START_DATE, progress=False, auto_adjust=True)
    result = {}
    if df.empty:
        return result
    close_series = df["Close"]
    if hasattr(close_series, "columns"):
        close_series = close_series.iloc[:, 0]
    for idx, val in close_series.items():
        if val == val and float(val) > 0:
            date_str = idx.strftime("%Y-%m-%d")
            if date_str >= START_DATE:
                result[date_str] = round(float(val), 2)
    return result


def main():
    existing = load_existing_data()
    existing_funds = existing.get("funds", {})

    output = {
        "updated_at": (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S CST"),
        "funds": {},
        "benchmarks": {}
    }

    session = requests.Session()

    for name, info in FUNDS.items():
        print(f"Fetching fund: {name}...")
        prev_records = existing_funds.get(name, {})
        output["funds"][name] = update_fund(session, name, info, prev_records)
        dates = list(output["funds"][name].keys())
        if dates:
            print(f"  -> {name} 最終總筆數: {len(dates)} 筆 ({dates} ~ {dates[-1]})")
        else:
            print(f"  -> {name} 最終總筆數: 0 筆")
        time.sleep(0.5)

    for name, ticker in BENCHMARKS.items():
        print(f"Fetching benchmark: {name} ({ticker})...")
        output["benchmarks"][name] = fetch_benchmark_history(ticker)

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("Successfully updated data.json!")


if __name__ == "__main__":
    main()
