import json
import datetime
import yfinance as yf

FUNDS = {
    "野村台灣運籌": ["0P00006B0B.TW", "野村台灣運籌"],
    "野村優質(累積)": ["0P00006AYX.TW", "野村優質"],
    "施羅德台灣樂活A": ["0P0000J0Y4.TW", "施羅德台灣樂活"]
}

BENCHMARKS = {
    "加權指數": "^TWII",
    "台積電(2330)": "2330.TW",
    "00935": "00935.TW"
}

START_DATE = "2010-01-01"

def download_series(ticker, decimals=4):
    df = yf.download(ticker, start=START_DATE, progress=False, auto_adjust=True)
    result = {}
    if df.empty:
        return result
    close_series = df["Close"]
    if hasattr(close_series, "columns"):
        close_series = close_series.iloc[:, 0]
    for idx, val in close_series.items():
        if val == val:  # 排除 NaN
            date_str = idx.strftime("%Y-%m-%d")
            result[date_str] = round(float(val), decimals)
    return result

def fetch_fund_smart(primary_ticker, keyword):
    # 1. 優先嘗試精確的 0P0000...TW 代號
    data = download_series(primary_ticker, decimals=4)
    if len(data) > 0:
        return primary_ticker, data

    # 2. 若無資料則自動向 Yahoo Finance 搜尋該基金代號
    search = yf.Search(keyword, max_results=5)
    for q in getattr(search, "quotes", []):
        sym = q.get("symbol", "")
        if sym.endswith(".TW"):
            data = download_series(sym, decimals=4)
            if len(data) > 0:
                return sym, data
    return primary_ticker, {}

def main():
    output = {
        "updated_at": (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S CST"),
        "funds": {},
        "benchmarks": {}
    }

    for name, (ticker, keyword) in FUNDS.items():
        print(f"Fetching fund: {name} ({ticker})...")
        used_ticker, records = fetch_fund_smart(ticker, keyword)
        output["funds"][name] = records
        print(f"  -> {name} [{used_ticker}] fetched {len(records)} records.")

    for name, ticker in BENCHMARKS.items():
        print(f"Fetching benchmark: {name} ({ticker})...")
        output["benchmarks"][name] = download_series(ticker, decimals=2)
        print(f"  -> {name} fetched {len(output['benchmarks'][name])} records.")

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("Successfully updated data.json!")

if __name__ == "__main__":
    main()
