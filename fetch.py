import os
import re
import json
import time
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

START_DATE = "2010-01-04"
DATA_FILE = "data.json"

BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive",
}


def load_existing_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Warning: 無法讀取現有 {DATA_FILE} ({e})")
    return {"funds": {}, "benchmarks": {}}


def parse_recent_30_days(html):
    """解析 yp010000.djhtm（MM/DD 格式）"""
    clean_text = re.sub(r"<[^>]+>", " ", html)
    pairs = re.findall(r"(?<!\d)(\d{2}/\d{2})(?!\d)\s+([\d,]+\.\d+)", clean_text)

    today = datetime.date.today()
    current_year = today.year
    parsed = {}

    for md_str, nav_raw in pairs:
        month, day = map(int, md_str.split("/"))
        year = current_year - 1 if (month - today.month) > 2 else current_year
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


def fetch_recent_fund_nav(session, fund_code):
    """抓取近 30 日最新淨值（供 GitHub Actions 每日增量更新使用）"""
    urls = [
        f"https://moneydj.com{fund_code}",
        f"https://hncb.com.tw_{fund_code}.djhtm",
    ]
    headers = {**BASE_HEADERS, "Referer": f"https://moneydj.com{fund_code}"}

    for url in urls:
        try:
            resp = session.get(url, headers=headers, timeout=25)
            if resp.status_code == 200:
                resp.encoding = "big5"
                # 嘗試解析完整 YYYY/MM/DD 格式
                clean_text = re.sub(r"<[^>]+>", " ", resp.text)
                full_pairs = re.findall(r"(\d{4}/\d{2}/\d{2})\s+([\d,]+\.\d+)", clean_text)
                if full_pairs:
                    res = {}
                    for d_raw, nav_raw in full_pairs:
                        d_iso = d_raw.replace("/", "-")
                        val = float(nav_raw.replace(",", ""))
                        if val > 0 and d_iso >= START_DATE:
                            res[d_iso] = round(val, 4)
                    if res:
                        return res

                # 若為 MM/DD 格式則用近 30 日解析器
                recent = parse_recent_30_days(resp.text)
                if recent:
                    return recent
        except Exception:
            continue
    return {}


def fetch_full_history_by_year(session, fund_code, start_year):
    """逐年向支援日期區間參數(b, c)的鏡像站點抓取 2010-01-04 至今完整歷史淨值"""
    current_year = datetime.date.today().year
    all_records = {}

    # 支援 b=YYYY-MM-DD & c=YYYY-MM-DD 區間查詢的多個 MoneyDJ 體系靜態接口
    mirror_templates = [
        "https://moneydj.com{code}&b={b}&c={c}",
        "https://hncb.com.tw.djhtm?a={code}&b={b}&c={c}",
        "https://moneydj.com{code}&b={b}&c={c}",
        "https://moneydj.com{code}&b={b}&c={c}",
    ]

    for yr in range(start_year, current_year + 1):
        b_date = START_DATE if yr == start_year else f"{yr}-01-01"
        c_date = f"{yr}-12-31"
        yr_count = 0

        for tmpl in mirror_templates:
            url = tmpl.format(code=fund_code, b=b_date, c=c_date)
            headers = {**BASE_HEADERS, "Referer": url}
            try:
                resp = session.get(url, headers=headers, timeout=25)
                if resp.status_code == 200:
                    resp.encoding = "big5"
                    clean_text = re.sub(r"<[^>]+>", " ", resp.text)
                    pairs = re.findall(r"(\d{4}/\d{2}/\d{2})\s+([\d,]+\.\d+)", clean_text)
                    for d_raw, nav_raw in pairs:
                        d_iso = d_raw.replace("/", "-")
                        val = float(nav_raw.replace(",", ""))
                        if val > 0 and d_iso >= START_DATE:
                            all_records[d_iso] = round(val, 4)
                            yr_count += 1
                    if yr_count > 0:
                        break  # 該年份成功抓到資料就跳出鏡像迴圈，進入下一年
            except Exception:
                continue

        print(f"    - {yr} 年: 取得 {yr_count} 筆")
        time.sleep(0.5)

    return all_records


def update_fund(session, fund_code, existing_records):
    merged = dict(existing_records)
    # 修正原本缺少 [0] 的 bug，正確取得起始年份 2010
    start_year = int(START_DATE.split("-")[0])

    # 檢查現有資料最早日期是否已涵蓋 2010 年；若無或總筆數不足 1000 筆，強制執行完整歷史回溯
    earliest_date = min(merged.keys()) if merged else "9999-12-31"
    if earliest_date > "2010-02-01" or len(merged) < 1000:
        print(f"  -> 現有資料最早僅至 {earliest_date}，開始回溯 {START_DATE} 至今完整歷史...")
        full_data = fetch_full_history_by_year(session, fund_code, start_year)
        merged.update(full_data)

    # 補上最近 30 日最新淨值
    recent_data = fetch_recent_fund_nav(session, fund_code)
    merged.update(recent_data)

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

    for name, code in FUNDS.items():
        print(f"Fetching fund: {name} ({code})...")
        prev_records = existing_funds.get(name, {})
        output["funds"][name] = update_fund(session, code, prev_records)
        dates = list(output["funds"][name].keys())
        if dates:
            print(f"  -> {name} 總筆數: {len(dates)} 筆 ({dates[0]} ~ {dates[-1]})")
        else:
            print(f"  -> {name} 總筆數: 0 筆")
        time.sleep(1.0)

    for name, ticker in BENCHMARKS.items():
        print(f"Fetching benchmark: {name} ({ticker})...")
        output["benchmarks"][name] = fetch_benchmark_history(ticker)

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("Successfully updated data.json!")


if __name__ == "__main__":
    main()
