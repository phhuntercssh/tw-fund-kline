import os
import re
import json
import time
import datetime
import requests
import yfinance as yf

FUNDS = {
    "野村台灣運籌": {
        "isin": "TW000T3218Y2",
        "yahoo_fo": "F0HKG05WZB:FO",
        "anue_id": "A32002",
        "mdj_id": "ACKH03"
    },
    "野村優質(累積)": {
        "isin": "TW000T3201Y8",
        "yahoo_fo": "F0HKG05X20:FO",
        "anue_id": "A32001",
        "mdj_id": "ACIC01"
    },
    "施羅德台灣樂活A": {
        "isin": "TW000T4209Y0",
        "yahoo_fo": "F000000LO1:FO",
        "anue_id": "A42001",
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
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
}


def build_url(scheme_host, path_parts, query=""):
    url = scheme_host.rstrip("/") + "/" + "/".join(path_parts)
    if query:
        url += "?" + query
    return url


def load_existing_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"funds": {}, "benchmarks": {}}


def fetch_mdj_recent_30(session, mdj_id):
    """增量更新：抓取 MoneyDJ 近 30 日最新淨值"""
    url = build_url("https://moneydj.com", ["funddj", "ya", "yp010000.djhtm"], f"a={mdj_id}")
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
    except Exception as e:
        print(f"    [MDJ近30日異常] {e}")
    return {}


def fetch_cnyes_range(session, anue_id, start_dt, end_dt):
    """使用鉅亨網公開基金歷史淨值 API 抓取指定日期區間"""
    start_ts = int(datetime.datetime(start_dt.year, start_dt.month, start_dt.day, 0, 0, 0, tzinfo=datetime.timezone.utc).timestamp())
    end_ts = int(datetime.datetime(end_dt.year, end_dt.month, end_dt.day, 23, 59, 59, tzinfo=datetime.timezone.utc).timestamp())

    api_url = build_url(
        "https://cnyes.com",
        ["fund", "api", "v1", "funds", anue_id, "nav"],
        f"format=table&startAt={start_ts}&endAt={end_ts}&limit=500"
    )
    referer_url = build_url("https://cnyes.com", ["funds", "detail", anue_id, "historical"])
    parsed = {}

    try:
        resp = session.get(
            api_url,
            headers={**HEADERS, "Referer": referer_url, "Origin": "https://cnyes.com"},
            timeout=25
        )
        if resp.status_code != 200:
            print(f"    [鉅亨API HTTP {resp.status_code}] 無法讀取區間 {start_dt} ~ {end_dt}")
            return parsed

        payload = resp.json()
        items = payload.get("items", {})
        rows = items.get("data", []) if isinstance(items, dict) else (items if isinstance(items, list) else [])

        for r in rows:
            ts = r.get("tradeDate") or r.get("date")
            nav = r.get("nav") or r.get("price")
            if ts is not None and nav is not None:
                try:
                    val = float(nav)
                    if val <= 0:
                        continue
                    if isinstance(ts, (int, float)):
                        d_iso = datetime.datetime.fromtimestamp(
                            int(ts), tz=datetime.timezone(datetime.timedelta(hours=8))
                        ).strftime("%Y-%m-%d")
                    else:
                        d_iso = str(ts)[:10].replace("/", "-")
                    if len(d_iso) == 10 and d_iso >= START_DATE:
                        parsed[d_iso] = round(val, 4)
                except (ValueError, TypeError):
                    continue
    except Exception as e:
        print(f"    [鉅亨API異常 {start_dt}~{end_dt}] {e}")

    return parsed


def backfill_history(session, anue_id, merged_records, window_days=180, max_batches=40):
    """以現有最早日期為基準點，每次往前推 180 天批次抓取，直到 2010-01-04"""
    target_start = datetime.date.fromisoformat(START_DATE)
    earliest_str = min(merged_records.keys()) if merged_records else datetime.date.today().strftime("%Y-%m-%d")
    cursor_end = datetime.date.fromisoformat(earliest_str) - datetime.timedelta(days=1)

    batch_count = 0
    empty_streak = 0

    while cursor_end >= target_start and batch_count < max_batches:
        cursor_start = max(target_start, cursor_end - datetime.timedelta(days=window_days))
        batch_data = fetch_cnyes_range(session, anue_id, cursor_start, cursor_end)
        batch_count += 1

        if batch_data:
            merged_records.update(batch_data)
            empty_streak = 0
            print(f"    [批次 {batch_count}] {cursor_start} ~ {cursor_end}：補齊 {len(batch_data)} 筆")
        else:
            empty_streak += 1
            print(f"    [批次 {batch_count}] {cursor_start} ~ {cursor_end}：無交易資料（連續 {empty_streak} 次）")
            if empty_streak >= 4:
                print(f"    -> 連續 4 個區間無資料，停止往回回補。")
                break

        cursor_end = cursor_start - datetime.timedelta(days=1)
        time.sleep(0.5)

    return merged_records


def update_fund(session, fund_name, info, existing_records):
    merged = dict(existing_records)

    # 1. 抓最新近 30 日淨值
    recent = fetch_mdj_recent_30(session, info["mdj_id"])
    print(f"  -> 最新近30日取得: {len(recent)} 筆")
    merged.update(recent)

    # 2. 若最早日期尚未達 2010-01-04，啟動滑動視窗往回回補
    earliest_date = min(merged.keys()) if merged else "9999-12-31"
    if earliest_date > START_DATE:
        print(f"  -> 目前最早資料為 {earliest_date}，啟動 180 天視窗往回回補...")
        merged = backfill_history(session, info["anue_id"], merged, window_days=180, max_batches=40)

    return dict(sorted(merged.items()))


def fetch_benchmark_history(ticker):
    result = {}
    try:
        df = yf.download(ticker, start=START_DATE, progress=False, auto_adjust=True)
        if df is None or df.empty:
            return result
        close_series = df["Close"]
        if hasattr(close_series, "columns"):
            close_series = close_series.iloc[:, 0]
        for idx, val in close_series.items():
            if val == val and float(val) > 0:
                date_str = idx.strftime("%Y-%m-%d")
                if date_str >= START_DATE:
                    result[date_str] = round(float(val), 2)
    except Exception as e:
        print(f"    [yfinance異常 {ticker}] {e}")
    return result


def main():
    existing = load_existing_data()
    existing_funds = existing.get("funds", {})
    existing_benchmarks = existing.get("benchmarks", {})

    output = {
        "updated_at": (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S CST"),
        "funds": {},
        "benchmarks": {}
    }

    session = requests.Session()

    for name, info in FUNDS.items():
        print(f"Fetching fund: {name}...")
        prev_records = existing_funds.get(name, {})
        output["funds"][name] = update_fund(session, name, info, prev_records)
        dates = list(output["funds"][name].keys())
        if dates:
            first_d = next(iter(dates))
            last_d = dates[-1]
            print(f"  -> {name} 最終總筆數: {len(dates)} 筆 ({first_d} ~ {last_d})")
        else:
            print(f"  -> {name} 最終總筆數: 0 筆")
        time.sleep(0.5)

    for name, ticker in BENCHMARKS.items():
        print(f"Fetching benchmark: {name} ({ticker})...")
        fetched = fetch_benchmark_history(ticker)
        output["benchmarks"][name] = fetched if fetched else existing_benchmarks.get(name, {})

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("Successfully updated data.json!")


if __name__ == "__main__":
    main()
