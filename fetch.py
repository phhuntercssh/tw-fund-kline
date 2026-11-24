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
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
}


def build_url(scheme_host, path_parts, query=""):
    """用陣列組合網址，避免複製貼上到 GitHub 網頁時遺失斜線"""
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


def fetch_yahoo_tw_chart(session, yahoo_fo):
    """從 Yahoo 股市台灣版基金走勢 API 拉取完整歷史"""
    start_ts = int(datetime.datetime.strptime(START_DATE, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc).timestamp())
    end_ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp()) + 86400

    api_url = build_url(
        "https://yahoo.com",
        ["_td-stock", "api", "resource", f"FinanceChartService.ApacLibraCharts;symbols=%5B%22{yahoo_fo}%22%5D;type=null"],
        f"period1={start_ts}&period2={end_ts}&interval=1d"
    )
    referer_url = build_url("https://yahoo.com", ["fund", yahoo_fo, "summary"])

    try:
        resp = session.get(api_url, headers={**HEADERS, "Referer": referer_url}, timeout=25)
        print(f"    [Yahoo股市走勢API] HTTP {resp.status_code}")
        if resp.status_code == 200 and resp.text.strip():
            data = resp.json()
            result = {}
            chart_list = data if isinstance(data, list) else [data]
            for item in chart_list:
                res_arr = item.get("chart", {}).get("result", [])
                res_obj = next(iter(res_arr), None)
                if not isinstance(res_obj, dict):
                    continue
                timestamps = res_obj.get("timestamp", [])
                quotes_list = res_obj.get("indicators", {}).get("quote", [])
                quote_obj = next(iter(quotes_list), {})
                closes = quote_obj.get("close", []) if isinstance(quote_obj, dict) else []
                for ts, val in zip(timestamps, closes):
                    if ts is not None and val is not None and float(val) > 0:
                        dt_str = datetime.datetime.fromtimestamp(
                            int(ts), tz=datetime.timezone(datetime.timedelta(hours=8))
                        ).strftime("%Y-%m-%d")
                        if dt_str >= START_DATE:
                            result[dt_str] = round(float(val), 4)
            if len(result) > 100:
                print(f"    [Yahoo股市走勢API] 成功取得 {len(result)} 筆歷史淨值")
                return result
    except Exception as e:
        print(f"    [Yahoo股市走勢API異常] {e}")

    return {}


def fetch_anuefund_history(session, anue_id):
    """備援：從新版鉅亨買基金歷史淨值 API 拉取完整歷史"""
    api_url = build_url("https://anuefund.com", ["api", "fund", "nav", anue_id])
    referer_url = build_url("https://anuefund.com", ["fund", "detail", anue_id])
    params = {"startDate": START_DATE, "endDate": datetime.date.today().strftime("%Y-%m-%d")}

    try:
        resp = session.get(api_url, params=params, headers={**HEADERS, "Referer": referer_url}, timeout=25)
        print(f"    [鉅亨買基金API] HTTP {resp.status_code}")
        if resp.status_code == 200 and resp.text.strip():
            data = resp.json()
            rows = data if isinstance(data, list) else data.get("data", [])
            result = {}
            for r in rows:
                d_raw = str(r.get("date") or r.get("navDate") or "")[:10].replace("/", "-")
                nav = r.get("nav") or r.get("price")
                if len(d_raw) == 10 and nav is not None and float(nav) > 0 and d_raw >= START_DATE:
                    result[d_raw] = round(float(nav), 4)
            if len(result) > 100:
                print(f"    [鉅亨買基金API] 成功取得 {len(result)} 筆歷史淨值")
                return result
    except Exception as e:
        print(f"    [鉅亨買基金API異常] {e}")

    return {}


def fetch_mdj_range(session, mdj_id, start_dt, end_dt):
    """指定日期區間抓取 MoneyDJ 歷史淨值（支援 30 天視窗滑動回補）"""
    # MoneyDJ 歷史區間查詢頁：yp010001.djhtm，b=起始日(YYYY/MM/DD), e=結束日(YYYY/MM/DD)
    url = build_url(
        "https://www.moneydj.com",
        ["funddj", "ya", "yp010001.djhtm"],
        f"a={mdj_id}&b={start_dt.strftime('%Y/%m/%d')}&e={end_dt.strftime('%Y/%m/%d')}"
    )
    parsed = {}
    try:
        resp = session.get(url, headers=HEADERS, timeout=20)
        if resp.status_code == 200:
            resp.encoding = "big5"
            clean_text = re.sub(r"<[^>]+>", " ", resp.text)
            # 歷史查詢頁面日期格式通常為 YYYY/MM/DD 或 YY/MM/DD
            pairs = re.findall(r"(\d{4}/\d{2}/\d{2})\s+([\d,]+\.\d+)", clean_text)
            for d_raw, nav_raw in pairs:
                d_iso = d_raw.replace("/", "-")
                val = float(nav_raw.replace(",", ""))
                if val > 0 and d_iso >= START_DATE:
                    parsed[d_iso] = round(val, 4)
    except Exception as e:
        print(f"    [MDJ區間查詢異常 {start_dt}~{end_dt}] {e}")
    return parsed


def backfill_by_30_days(session, mdj_id, merged_records, max_batches=30):
    """
    以現有最早日期為基準點，每次往前推 30 天批次抓取，直到 2010-01-01
    max_batches=30 代表單次執行最多往回抓 30 批（約 900 天＝2.5 年），
    避免單次 GitHub Actions 跑太久，多跑幾次就會自動補滿至 2010/01/01。
    """
    target_start = datetime.date.fromisoformat(START_DATE)
    earliest_str = min(merged_records.keys()) if merged_records else datetime.date.today().strftime("%Y-%m-%d")
    cursor_end = datetime.date.fromisoformat(earliest_str) - datetime.timedelta(days=1)

    batch_count = 0
    empty_streak = 0  # 連續無資料次數（防呆：若基金成立晚於 2010 年，避免無效空轉）

    while cursor_end >= target_start and batch_count < max_batches:
        cursor_start = max(target_start, cursor_end - datetime.timedelta(days=30))
        batch_data = fetch_mdj_range(session, mdj_id, cursor_start, cursor_end)
        batch_count += 1

        if batch_data:
            merged_records.update(batch_data)
            empty_streak = 0
            print(f"    [批次 {batch_count}] {cursor_start} ~ {cursor_end}：補齊 {len(batch_data)} 筆")
        else:
            empty_streak += 1
            print(f"    [批次 {batch_count}] {cursor_start} ~ {cursor_end}：無交易資料（連續 {empty_streak} 次）")
            # 若連續 6 個月（6 批）完全沒淨值，代表已觸及該基金成立日，提前結束迴圈
            if empty_streak >= 6:
                print(f"    -> 已達該基金最早成立日附近，停止往回回補。")
                break

        cursor_end = cursor_start - datetime.timedelta(days=1)
        time.sleep(0.8)  # 禮貌性延遲，避免觸發網站防爬蟲封鎖

    return merged_records


def update_fund(session, fund_name, info, existing_records):
    merged = dict(existing_records)

    # 1. 先抓最新近 30 日淨值（確保最新交易日永遠是最新的）
    recent = fetch_mdj_recent_30(session, info["mdj_id"])
    print(f"  -> 最新近30日取得: {len(recent)} 筆")
    merged.update(recent)

    # 2. 檢查最早日期是否已達 2010-01-04，若還沒補滿，就以最早日期為基準往前每次推 30 天批次補齊
    earliest_date = min(merged.keys()) if merged else "9999-12-31"
    if earliest_date > START_DATE:
        print(f"  -> 目前最早資料為 {earliest_date}，啟動 30 天滑動視窗往回回補...")
        merged = backfill_by_30_days(session, info["mdj_id"], merged, max_batches=30)

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
        # 若 yfinance 偶發失敗，保留舊資料不清空
        output["benchmarks"][name] = fetched if fetched else existing_benchmarks.get(name, {})

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("Successfully updated data.json!")


if __name__ == "__main__":
    main()
