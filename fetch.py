import json
import datetime
import yfinance as yf

FUNDS = {
    "野村台灣運籌": "F0HKG05WZB.TW",
    "野村優質(累積)": "F0HKG05X20.TW",
    "施羅德台灣樂活A": "F000000LO1.TW"
}

BENCHMARKS = {
    "加權指數": "^TWII",
    "台積電(2330)": "2330.TW",
    "00935": "00935.TW"
}

START_DATE = "2010-01-01"

def fetch_yf_history(ticker, decimals=2):
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

def main():
    output = {
        "updated_at": (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S CST"),
        "funds": {},
        "benchmarks": {}
    }

    for name, ticker in FUNDS.items():
        print(f"Fetching fund: {name} ({ticker})...")
        output["funds"][name] = fetch_yf_history(ticker, decimals=4)
        print(f"  -> {name} fetched {len(output['funds'][name])} records.")

    for name, ticker in BENCHMARKS.items():
        print(f"Fetching benchmark: {name} ({ticker})...")
        output["benchmarks"][name] = fetch_yf_history(ticker, decimals=2)
        print(f"  -> {name} fetched {len(output['benchmarks'][name])} records.")

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("Successfully updated data.json!")

if __name__ == "__main__":
    main()
