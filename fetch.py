import json
import datetime
import requests
import yfinance as yf

FUNDS = {
    "野村台灣運籌": "ACKH03",
    "野村優質(累積)": "ACIC01",
    "施羅德台灣樂活A": "ACES10"
}

BENCHMARKS = {
    "加權指數": "^TWII",
    "台積電(2330)": "2330.TW",
    "00935": "00935.TW"
}

START_DATE = "2010-01-01"

def fetch_fund_history(fund_code):
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    url = "https://moneydj.com"
    params = {"a": fund_code, "b": START_DATE, "c": today_str}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://moneydj.com"
    }
    resp = requests.get(url, params=params, headers=headers, timeout=20)
    resp.raise_for_status()

    parts = resp.text.strip().split(" ")
    if len(parts) < 2:
        raise ValueError(f"Unexpected response format for {fund_code}: {resp.text[:100]}")

    dates = parts.pop(0).split(",")
    navs = parts.pop(0).split(",")

    result = {}
    for d, n in zip(dates, navs):
        try:
            clean_date = d.replace("/", "-")
            if len(clean_date) == 8 and "-" not in clean_date:
                clean_date = f"{clean_date[:4]}-{clean_date[4:6]}-{clean_date[6:8]}"
            val = float(n)
            if val > 0:
                result[clean_date] = val
        except ValueError:
            continue
    return result

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

    for name, code in FUNDS.items():
        print(f"Fetching fund: {name} ({code})...")
        output["funds"][name] = fetch_fund_history(code)
        print(f"  -> {name} fetched {len(output['funds'][name])} records.")

    for name, ticker in BENCHMARKS.items():
        print(f"Fetching benchmark: {name} ({ticker})...")
        output["benchmarks"][name] = fetch_benchmark_history(ticker)

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("Successfully updated data.json!")

if __name__ == "__main__":
    main()
