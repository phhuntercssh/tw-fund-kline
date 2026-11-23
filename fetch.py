import os
import re
import json
import time
import datetime
import requests
import yfinance as yf

FUNDS = {
    "野村台灣運籌": {"query": "野村台灣運籌", "must_have": ["野村", "運籌"], "exclude": ["外幣", "美元", "人民幣"]},
    "野村優質(累積)": {"query": "野村優質", "must_have": ["野村", "優質"], "exclude": ["外幣", "美元", "人民幣", "S類型", "R類型"]},
    "施羅德台灣樂活A": {"query": "施羅德台灣樂活", "must_have": ["施羅德", "樂活"], "exclude": ["C", "I", "外幣", "美元"]}
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
    "Referer": "https://cnyes.com",
}


def load_existing_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"funds": {}, "benchmarks": {}}


def find_cnyes_id(session, rule):
    """透過鉅亨網搜尋 API 精準取得該基金的 cnyesId"""
    url = "https://cnyes.com"
    params = {"q": rule["query"], "page": 1, "perPage": 30}
    try:
        resp = session.get(url, params=params, headers=HEADERS, timeout=20)
        print(f"    [搜尋API] HTTP {resp.status_code}")
        if resp.status_code != 200:
            return None

        data = resp.json()
        items = data.get("items", {}).get("data", [])
        for item in items:
            name = item.get("displayNameLocal") or item.get("name") or ""
            cid = item.get("cnyesId")
            if not cid:
                continue
            if all(k in name for k in rule["must_have"]) and not any(ex in name for ex in rule["exclude"]):
                print(f"    [配對成功] {name} -> cnyesId = {cid}")
                return cid
        # 若無完全符合，印出搜尋到的前 3 筆協助排查
        sample = [(x.get("displayNameLocal"), x.get("cnyesId")) for x in items[:3]]
        print(f"    [配對失敗] 候選清單: {sample}")
    except Exception as e:
        print(f"    [搜尋異常] {e}")
    return None


def fetch_cnyes_nav_history(session, cnyes_id, start_date_str):
    """從鉅亨網歷史淨值 API 拉取 2010-01-04 至今全部每日淨值"""
    start_ts = int(datetime.datetime.strptime(start_date_str, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc).timestamp())
    end_ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp()) + 86400

    result = {}
    page = 1
    while True:
        url = f"https://cnyes.com{cnyes_id}/nav"
        params = {
            "format": "table",
            "startAt": start_ts,
            "endAt": end_ts,
            "page": page,
            "perPage": 2500
        }
        try:
            resp = session.get(url, params=params, headers=HEADERS, timeout=25)
            if resp.status_code != 200:
                print(f"    [淨值API] HTTP {resp.status_code} (page {page}): {resp.text[:120]}")
                break

            payload = resp.json()
            items_block = payload.get("items", {})
            rows = items_block.get("data", [])
            if not rows:
                break

            for row in rows:
                ts = row.get("tradeDate")
                nav = row.get("nav")
                if ts is not None and nav is not None:
                    dt_str = datetime.datetime.fromtimestamp(
                        int(ts), tz=datetime.timezone(datetime.timedelta(hours=8))
                    ).strftime("%Y-%m-%d")
                    val = float(nav)
                    if val > 0 and dt_str >= start_date_str:
                        result[dt_str] = round(val, 4)

            last_page = items_block.get("last_page", 1)
            if page >= last_page:
                break
            page += 1
            time.sleep(0.3)
        except Exception as e:
            print(f"    [淨值API異常] {e}")
            break

    return result


def update_fund(session, fund_name, rule, existing_records):
    merged = dict(existing_records)
    earliest_date = min(merged.keys()) if merged else "9999-12-31"

    cnyes_id = find_cnyes_id(session, rule)
    if not cnyes_id:
        print(f"  -> 無法取得 {fund_name} 的 cnyesId，保留原有資料")
        return dict(sorted(merged.items()))

    if earliest_date > "2010-02-01" or len(merged) < 1000:
        print(f"  -> 開始拉取 {START_DATE} 至今完整歷史淨值...")
        fetched = fetch_cnyes_nav_history(session, cnyes_id, START_DATE)
    else:
        recent_start = (datetime.date.today() - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
        print(f"  -> 增量更新近 30 日淨值 ({recent_start} 至今)...")
        fetched = fetch_cnyes_nav_history(session, cnyes_id, recent_start)

    print(f"  -> 本次成功抓取 {len(fetched)} 筆新資料")
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

    for name, rule in FUNDS.items():
        print(f"Fetching fund: {name}...")
        prev_records = existing_funds.get(name, {})
        output["funds"][name] = update_fund(session, name, rule, prev_records)
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
