import os
import json
import time
import datetime
import requests
import yfinance as yf

# 設定基金名稱與對應代碼（同時保留 MoneyDJ 代碼供近30日雙重備援）
FUNDS = {
    "野村台灣運籌": {"cnyes": "A32002", "mdj": "ACKH03"},
    "野村優質(累積)": {"cnyes": "A32001", "mdj": "ACIC01"},
    "施羅德台灣樂活A": {"cnyes": "A42001", "mdj": "ACES10"},
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
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://fund.cnyes.com/",
}


def load_existing_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"funds": {}, "benchmarks": {}}


def resolve_cnyes_id(session, keyword, fallback_id):
    """透過鉅亨網搜尋 API 確認最新基金代碼（避免代碼變更）"""
    search_keyword = keyword.split("(")[0]
    url = f"https://cnyes.com{search_keyword}"
    try:
        resp = session.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            items = resp.json().get("items", {}).get("data", [])
            for item in items:
                name = item.get("displayNameLocal", "") or item.get("name", "")
                if search_keyword in name:
                    # 區分累積型或 A 級別
                    if "優質" in keyword and "累積" not in name and "A" not in name and "TWD" not in name:
                        continue
                    if "樂活" in keyword and "A" not in name:
                        continue
                    found_id = item.get("cnyesId")
                    if found_id:
                        return found_id
    except Exception:
        pass
    return fallback_id


def fetch_cnyes_full_history(session, cnyes_id, start_date_str):
    """從鉅亨網 API 一次拉取 2010-01-04 至今全部歷史淨值（支援海外 IP 與 GitHub Actions）"""
    start_ts = int(datetime.datetime.strptime(start_date_str, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc).timestamp())
    end_ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp()) + 86400

    result = {}
    page = 1
    while True:
        url = (
            f"https://cnyes.com{cnyes_id}/nav"
            f"?format=table&startAt={start_ts}&endAt={end_ts}&page={page}&perPage=2000"
        )
        try:
            resp = session.get(url, headers=HEADERS, timeout=25)
            if resp.status_code != 200:
                print(f"  -> cnyes API HTTP {resp.status_code} (page {page})")
                break

            payload = resp.json()
            items_block = payload.get("items", {})
            rows = items_block.get("data", [])
            if not rows:
                break

            for row in rows:
                # tradeDate 為 Unix timestamp (秒)
                ts = row.get("tradeDate")
                nav = row.get("nav")
                if ts is not None and nav is not None:
                    dt_str = datetime.datetime.fromtimestamp(int(ts), tz=datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d")
                    val = float(nav)
                    if val > 0 and dt_str >= start_date_str:
                        result[dt_str] = round(val, 4)

            last_page = items_block.get("last_page", 1)
            if page >= last_page:
                break
            page += 1
            time.sleep(0.3)
        except Exception as e:
            print(f"  -> cnyes API 發生錯誤: {e}")
            break

    return result


def update_fund(session, fund_name, codes, existing_records):
    merged = dict(existing_records)
    earliest_date = min(merged.keys()) if merged else "9999-12-31"

    cnyes_id = resolve_cnyes_id(session, fund_name, codes["cnyes"])

    # 若資料尚未涵蓋 2010 年，從 2010-01-04 完整拉取；否則只抓最近 30 天做增量更新
    if earliest_date > "2010-02-01" or len(merged) < 1000:
        print(f"  -> 透過鉅亨網 API ({cnyes_id}) 拉取 {START_DATE} 至今完整歷史...")
        fetched = fetch_cnyes_full_history(session, cnyes_id, START_DATE)
    else:
        recent_start = (datetime.date.today() - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
        print(f"  -> 透過鉅亨網 API ({cnyes_id}) 增量更新近 30 日淨值...")
        fetched = fetch_cnyes_full_history(session, cnyes_id, recent_start)

    merged.update(fetched)
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
            print(f"  -> {name} 總筆數: {len(dates)} 筆 ({dates[0]} ~ {dates[-1]})")
        else:
            print(f"  -> {name} 總筆數: 0 筆")
        time.sleep(0.5)

    for name, ticker in BENCHMARKS.items():
        print(f"Fetching benchmark: {name} ({ticker})...")
        output["benchmarks"][name] = fetch_benchmark_history(ticker)

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("Successfully updated data.json!")


if __name__ == "__main__":
    main()
