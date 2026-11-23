import os
import re
import json
import time
import datetime
import requests
import yfinance as yf

# 三檔基金在 Yahoo Finance 的晨星代碼（可直接用 yf.download 抓取 2010 至今完整歷史）
FUNDS = {
    "野村台灣運籌": {"yf_ticker": "F0HKG05WZB:FO", "mdj_id": "ACKH03"},
    "野村優質(累積)": {"yf_ticker": "F0HKG05X20:FO", "mdj_id": "ACIC01"},
    "施羅德台灣樂活A": {"yf_ticker": "F000000LO1:FO", "mdj_id": "ACES10"},
}

BENCHMARKS = {
    "加權指數": "^TWII",
    "台積電(2330)": "2330.TW",
    "00935": "00935.TW"
}

START_DATE = "2010-01-04"
DATA_FILE = "data.json"


def load_existing_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"funds": {}, "benchmarks": {}}


def fetch_yf_series(ticker, decimals=4):
    """直接使用 yfinance 抓取自 2010-01-04 至今完整歷史（不需手寫長網址，不會遺失路徑）"""
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
                result[date_str] = round(float(val), decimals)
    return result


def fetch_mdj_recent_30(mdj_id):
    """用路徑陣列組合網址（避免複製貼上遺失斜線），補齊近 30 個交易日最新淨值"""
    host = "https://moneydj.com"
    path = "/".join(["", "funddj", "ya", "yp010000.djhtm"])
    url = f"{host}{path}?a={mdj_id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128.0.0.0 Safari/537.36"
    }
    try:
        resp = requests.get(url, headers=headers, timeout=20)
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


def main():
    existing = load_existing_data()
    existing_funds = existing.get("funds", {})

    output = {
        "updated_at": (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S CST"),
        "funds": {},
        "benchmarks": {}
    }

    for name, info in FUNDS.items():
        print(f"Fetching fund: {name} ({info['yf_ticker']})...")
        merged = dict(existing_funds.get(name, {}))

        # 1. 使用 yfinance 拉取 2010-01-04 至今完整歷史淨值
        yf_hist = fetch_yf_series(info["yf_ticker"], decimals=4)
        print(f"  -> yfinance 取得: {len(yf_hist)} 筆")
        merged.update(yf_hist)

        # 2. 用 MoneyDJ 近 30 日靜態表覆蓋最新淨值，確保台灣投信公告數值零誤差
        mdj_recent = fetch_mdj_recent_30(info["mdj_id"])
        print(f"  -> MoneyDJ 近30日取得: {len(mdj_recent)} 筆")
        merged.update(mdj_recent)

        sorted_data = dict(sorted(merged.items()))
        output["funds"][name] = sorted_data
        dates = list(sorted_data.keys())
        if dates:
            print(f"  -> {name} 最終總筆數: {len(dates)} 筆 ({dates} ~ {dates[-1]})")
        time.sleep(0.5)

    for name, ticker in BENCHMARKS.items():
        print(f"Fetching benchmark: {name} ({ticker})...")
        output["benchmarks"][name] = fetch_yf_series(ticker, decimals=2)

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("Successfully updated data.json!")


if __name__ == "__main__":
    main()
