import json
import re
from datetime import datetime
import pandas as pd
import requests
import yfinance as yf

FUNDS = {
    "野村台灣運籌": "ackh03",
    "野村優質(累積)": "acic01",
    "施羅德台灣樂活A": "aces10",
}

def fetch_fund_history(code):
    # 抓取 MoneyDJ 完整歷史淨值 JSON 接口 (2010-01-01 至今)
    url = f"https://moneydj.com{code}&b=2010-01-01&c={datetime.now().strftime('%Y-%m-%d')}"
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(url, headers=headers, timeout=20)
    parts = resp.text.strip().split(" ")
    if len(parts) < 2:
        return {}
    dates = parts[0].split(",")
    navs = parts[1].split(",")
    result = {}
    for d, v in zip(dates, navs):
        try:
            # 轉換日期格式為 YYYY-MM-DD
            dt = datetime.strptime(d, "%Y/%m/%d").strftime("%Y-%m-%d")
            result[dt] = float(v)
        except Exception:
            continue
    return result

def fetch_stock_history(ticker):
    df = yf.download(ticker, start="2010-01-01", progress=False, auto_adjust=True)
    result = {}
    if df.empty:
        return result
    close_series = df["Close"]
    if isinstance(close_series, pd.DataFrame):
        close_series = close_series.iloc[:, 0]
    for dt, val in close_series.items():
        if pd.notna(val):
            result[dt.strftime("%Y-%m-%d")] = round(float(val), 2)
    return result

def main():
    output = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "funds": {},
        "benchmarks": {}
    }
    for name, code in FUNDS.items():
        print(f"Fetching {name}...")
        output["funds"][name] = fetch_fund_history(code)

    benchmarks = {
        "加權指數": "^TWII",
        "台積電(2330)": "2330.TW",
        "00935": "00935.TW"
    }
    for name, ticker in benchmarks.items():
        print(f"Fetching {name}...")
        output["benchmarks"][name] = fetch_stock_history(ticker)

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False)
    print("Done!")

if __name__ == "__main__":
    main()
