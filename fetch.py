import os
import re
import json
import time
import datetime
import requests
import yfinance as yf

# 三檔基金的精準跨平台代碼（Yahoo晨星代碼、基富通代碼、MoneyDJ代碼）
FUNDS = {
    "野村台灣運籌": {
        "yahoo_id": "F0HKG05WZB:FO",
        "fundrich_id": "NOM006",
        "mdj_id": "ACKH03"
    },
    "野村優質(累積)": {
        "yahoo_id": "F0HKG05X20:FO",
        "fundrich_id": "NOM002",
        "mdj_id": "ACIC01"
    },
    "施羅德台灣樂活A": {
        "yahoo_id": "F000000LO1:FO",
        "fundrich_id": "SCH004",
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
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
}


def load_existing_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"funds": {}, "benchmarks": {}}


def fetch_yahoo_fund_history(session, yahoo_id):
    """透過 Yahoo 股市基金走勢 API 拉取自 2010-01-04 至今完整歷史淨值（不擋海外 IP）"""
    start_ts = int(datetime.datetime.strptime(START_DATE, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc).timestamp())
    end_ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp()) + 86400

    # Yahoo 股市基金走勢端點（支援晨星代碼 F0HKG05WZB:FO）
    endpoints = [
        f"https://yahoo.com{yahoo_id}%22%5D;type=null?period1={start_ts}&period2={end_ts}&interval=1d",
        f"https://yahoo.com{yahoo_id}?period1={start_ts}&period2={end_ts}&interval=1d",
        f"https://yahoo.com{yahoo_id}?period1={start_ts}&period2={end_ts}&interval=1d",
    ]

    for idx, url in enumerate(endpoints):
        try:
            resp = session.get(url, headers=HEADERS, timeout=25)
            if resp.status_code != 200 or not resp.text.strip():
                continue

            data = resp.json()
            result = {}

            # 格式 A：Yahoo Finance v8 chart 標準格式
            if "chart" in data and data["chart"].get("result"):
                res_obj = data["chart"]["result"]
                timestamps = res_obj.get("timestamp", [])
                quotes = res_obj.get("indicators", {}).get("quote", [{}])
                closes = quotes.get("close", [])
                for ts, val in zip(timestamps, closes):
                    if ts is not None and val is not None:
                        dt_str = datetime.datetime.fromtimestamp(
                            int(ts), tz=datetime.timezone(datetime.timedelta(hours=8))
                        ).strftime("%Y-%m-%d")
                        if val > 0 and dt_str >= START_DATE:
                            result[dt_str] = round(float(val), 4)

            # 格式 B：tw.stock.yahoo.com _td-stock ApacLibraCharts 格式
            elif isinstance(data, list) and len(data) > 0:
                chart_block = data.get("chart", {}).get("result", [{}])
                timestamps = chart_block.get("timestamp", [])
                quotes = chart_block.get("indicators", {}).get("quote", [{}])
                closes = quotes.get("close", [])
                for ts, val in zip(timestamps, closes):
                    if ts is not None and val is not None:
                        dt_str = datetime.datetime.fromtimestamp(
                            int(ts), tz=datetime.timezone(datetime.timedelta(hours=8))
                        ).strftime("%Y-%m-%d")
                        if val > 0 and dt_str >= START_DATE:
                            result[dt_str] = round(float(val), 4)

            if len(result) > 100:
                print(f"    [Yahoo基金API-{idx+1}] 成功取得 {len(result)} 筆歷史淨值")
                return result
        except Exception as e:
            print(f"    [Yahoo基金API-{idx+1}異常] {e}")

    return {}


def fetch_fundrich_history(session, fundrich_id):
    """備援：從基富通 (FundRich) 歷史淨值 API 拉取完整歷史"""
    url = "https://fundrich.com.tw"
    payload = {
        "fundId": fundrich_id,
        "startDate": START_DATE,
        "endDate": datetime.date.today().strftime("%Y-%m-%d")
    }
    headers = {
        **HEADERS,
        "Content-Type": "application/json",
        "Referer": f"https://www.fundrich.com.tw/fundCenter/fundOverview/fundContent/{fundrich_id}"
    }
    try:
        resp = session.post(url, json=payload, headers=headers, timeout=25)
        if resp.status_code == 200 and resp.text.strip():
            data = resp.json()
            rows = data if isinstance(data, list) else data.get("data", [])
            result = {}
            for r in rows:
                d_raw = str(r.get("navDate") or r.get("date") or "")[:10].replace("/", "-")
                nav = r.get("nav") or r.get("netAssetValue")
                if len(d_raw) == 10 and nav is not None:
                    val = float(nav)
                    if val > 0 and d_raw >= START_DATE:
                        result[d_raw] = round(val, 4)
            if len(result) > 100:
                print(f"    [基富通API] 成功取得 {len(result)} 筆歷史淨值")
                return result
    except Exception as e:
        print(f"    [基富通API異常] {e}")
    return {}


def fetch_mdj_recent_30(session, mdj_id):
    """增量更新：抓取 MoneyDJ 近 30 日最新淨值（確保最新一日淨值零時差）"""
    url = f"https://www.moneydj.com/funddj/ya/yp010000.djhtm?a={mdj_id}"
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
    except Exception:
        pass
    return {}


def update_fund(session, fund_name, codes, existing_records):
    merged = dict(existing_records)
    earliest_date = min(merged.keys()) if merged else "9999-12-31"

    # 若尚未擁有 2010 年起的完整歷史（目前只有 30 筆），呼叫 Yahoo 基金 API 與基富通 API 拉回全部歷史
    if earliest_date > "2010-02-01" or len(merged) < 1000:
        print(f"  -> 目前最早僅至 {earliest_date}，嘗試拉取 {START_DATE} 至今完整歷史...")
        hist = fetch_yahoo_fund_history(session, codes["yahoo_id"])
        if len(hist) < 100:
            hist = fetch_fundrich_history(session, codes["fundrich_id"])
        merged.update(hist)

    # 再用 MoneyDJ 近 30 日靜態表補齊最近幾天的最新淨值
    recent = fetch_mdj_recent_30(session, codes["mdj_id"])
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
        if val == val:
            date_str = idx.strftime("%Y-%m-%d")
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

    for name, codes in FUNDS.items():
        print(f"Fetching fund: {name}...")
        prev_records = existing_funds.get(name, {})
        output["funds"][name] = update_fund(session, name, codes, prev_records)
        dates = list(output["funds"][name].keys())
        if dates:
            print(f"  -> {name} 累計總筆數: {len(dates)} 筆 ({dates} ~ {dates[-1]})")
        else:
            print(f"  -> {name} 累計總筆數: 0 筆")
        time.sleep(0.5)

    for name, ticker in BENCHMARKS.items():
        print(f"Fetching benchmark: {name} ({ticker})...")
        output["benchmarks"][name] = fetch_benchmark_history(ticker)

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("Successfully updated data.json!")


if __name__ == "__main__":
    main()
