import re
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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8"
}

def fetch_fund_history(fund_code):
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    # 嘗試多個 MoneyDJ 鏡像站點的歷史淨值查詢介面（含總站與銀行代銷靜態介面）
    candidate_urls = [
        f"https://moneydj.com{fund_code}&b={START_DATE}&c={today_str}",
        f"https://fund.hncb.com.tw/w/wr/wr02_{fund_code}.djhtm",
        f"https://taishinbankrwd.moneydj.com/w/wr/wr02_{fund_code}.djhtm",
        f"https://bopfund.moneydj.com/w/wr/wr02.djhtm?a={fund_code}"
    ]

    result = {}
    for url in candidate_urls:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            if resp.status_code != 200:
                continue
            # 自動處理 Big5 / UTF-8 編碼避免亂碼干擾標籤解析
            resp.encoding = resp.apparent_encoding or "big5"
            html = resp.text

            # 去除所有 HTML 標籤，只保留純文字與換行
            clean_text = re.sub(r"<[^>]+>", " ", html)
            # 尋找「YYYY/MM/DD」緊跟「淨值數字」的配對
            pairs = re.findall(r"(\d{4}/\d{2}/\d{2})\s+([\d,]+\.\d+)", clean_text)
            for d_raw, nav_raw in pairs:
                d_iso = d_raw.replace("/", "-")
                val = float(nav_raw.replace(",", ""))
                if val > 0 and d_iso >= START_DATE:
                    result[d_iso] = round(val, 4)

            if len(result) > 0:
                print(f"  -> Matched via {url.split('/')[2]} ({len(result)} records)")
                break
        except Exception as e:
            print(f"  -> Mirror failed ({url}): {e}")
            continue

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

    for name, code in FUNDS.items():
        print(f"Fetching fund: {name} ({code})...")
        output["funds"][name] = fetch_fund_history(code)
        print(f"  -> {name} total records: {len(output['funds'][name])}")

    for name, ticker in BENCHMARKS.items():
        print(f"Fetching benchmark: {name} ({ticker})...")
        output["benchmarks"][name] = fetch_benchmark_history(ticker)

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("Successfully updated data.json!")

if __name__ == "__main__":
    main()
