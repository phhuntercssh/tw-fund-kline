import json
import time
import datetime
import requests
import yfinance as yf

FUNDS = {
    "野村台灣運籌": "野村台灣運籌",
    "野村優質(累積)": "野村優質基金-累積類型新臺幣計價",
    "施羅德台灣樂活A": "施羅德台灣樂活中小基金-A類型"
}

BENCHMARKS = {
    "加權指數": "^TWII",
    "台積電(2330)": "2330.TW",
    "00935": "00935.TW"
}

START_DATE = "2010-01-01"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://cnyes.com"
}

def find_cnyes_fund_id(keyword):
    url = "https://cnyes.com"
    params = {"q": keyword, "page": 1, "limit": 5}
    resp = requests.get(url, params=params, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    items = data.get("items", {}).get("data", [])
    if not items:
        raise ValueError(f"Cannot find fund ID on cnyes for keyword: {keyword}")
    return items[0]["cnyesId"], items[0].get("displayNameLocal", keyword)

def fetch_fund_history(keyword):
    cnyes_id, matched_name = find_cnyes_fund_id(keyword)
    print(f"  -> Matched cnyesId: {cnyes_id} ({matched_name})")

    start_ts = int(datetime.datetime.strptime(START_DATE, "%Y-%m-%d").timestamp())
    end_ts = int(time.time()) + 86400

    url = f"https://cnyes.com{cnyes_id}/nav"
    params = {"start": start_ts, "end": end_ts, "format": "json"}
    resp = requests.get(url, params=params, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    items = data.get("items", [])
    result = {}
    for row in items:
        # 支援 dict 或 list 格式
        if isinstance(row, dict):
            ts = row.get("date") or row.get("tradeDate")
            nav = row.get("nav") or row.get("price")
        elif isinstance(row, list) and len(row) >= 2:
            ts, nav = row[0], row[1]
        else:
            continue

        if ts is None or nav is None:
            continue

        # 轉換時間戳（毫秒或秒）為 YYYY-MM-DD（台灣時區 UTC+8）
        if ts > 1e11:
            ts = ts / 1000.0
        dt = datetime.datetime.utcfromtimestamp(ts) + datetime.timedelta(hours=8)
        date_str = dt.strftime("%Y-%m-%d")
        val = float(nav)
        if val > 0 and date_str >= START_DATE:
            result[date_str] = round(val, 4)

    return dict(sorted(result.items()))

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
    output = {
        "updated_at": (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S CST"),
        "funds": {},
        "benchmarks": {}
    }

    for name, keyword in FUNDS.items():
        print(f"Fetching fund: {name}...")
        output["funds"][name] = fetch_fund_history(keyword)
        print(f"  -> {name} fetched {len(output['funds'][name])} records.")

    for name, ticker in BENCHMARKS.items():
        print(f"Fetching benchmark: {name} ({ticker})...")
        output["benchmarks"][name] = fetch_benchmark_history(ticker)

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("Successfully updated data.json!")

if __name__ == "__main__":
    main()
