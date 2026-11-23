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

START_DATE = "2010-01-01"
DATA_FILE = "data.json"

BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive",
}


def load_existing_data():
    """讀取現有的 data.json，實現增量更新以降低海外請求頻率與資料量"""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Warning: 無法讀取現有 {DATA_FILE}，將重新建立 ({e})")
    return {"funds": {}, "benchmarks": {}}


def parse_recent_30_days(html):
    """解析 yp010000.djhtm（格式為 MM/DD 與淨值），自動推算正確年份（含跨年處理）"""
    clean_text = re.sub(r"<[^>]+>", " ", html)
    # 匹配 MM/DD 與後方緊跟的淨值數字
    pairs = re.findall(r"(?<!\d)(\d{2}/\d{2})(?!\d)\s+([\d,]+\.\d+)", clean_text)

    today = datetime.date.today()
    current_year = today.year
    parsed = {}

    for md_str, nav_raw in pairs:
        month, day = map(int, md_str.split("/"))
        # 若抓到的月份比目前月份大超過 2 個月（例如現在是 1 月，抓到 12 月的資料），代表是去年
        year = current_year - 1 if (month - today.month) > 2 else current_year
        try:
            dt = datetime.date(year, month, day)
            # 若推算出的日期在未來（例如跨年邊界），再減一年
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
    """抓取單一基金近 30 個交易日靜態淨值表（對海外 IP 最穩定）"""
    urls = [
        f"https://moneydj.com{fund_code}",
        f"https://moneydj.com{fund_code}",
    ]
    headers = {
        **BASE_HEADERS,
        "Referer": f"https://moneydj.com{fund_code}",
    }

    for url in urls:
        try:
            resp = session.get(url, headers=headers, timeout=25)
            if resp.status_code == 200:
                resp.encoding = "big5"
                data = parse_recent_30_days(resp.text)
                if len(data) > 0:
                    print(f"  -> [近30日靜態表] 成功取得 {len(data)} 筆資料")
                    return data
        except Exception as e:
            print(f"  -> 嘗試 {url} 失敗: {e}")
    return {}


def fetch_full_history_by_year(session, fund_code, start_year):
    """逐年分段抓取完整歷史淨值（避免單次跨度太大被伺服器截斷）"""
    current_year = datetime.date.today().year
    headers = {
        **BASE_HEADERS,
        "Referer": f"https://moneydj.com{fund_code}",
    }
    all_records = {}

    for yr in range(start_year, current_year + 1):
        b_date = f"{yr}-01-01"
        c_date = f"{yr}-12-31"
        url = f"https://moneydj.com{fund_code}&b={b_date}&c={c_date}"
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
            time.sleep(0.8)  # 禮貌性延遲，避免觸發頻率限制
        except Exception as e:
            print(f"  -> {yr} 年歷史抓取失敗: {e}")

    print(f"  -> [完整歷史分段] 共取得 {len(all_records)} 筆資料")
    return all_records


def update_fund(session, fund_code, existing_records):
    merged = dict(existing_records)
    start_year = int(START_DATE.split("-")[0])

    # 若現有資料少於 100 筆（代表初次執行或尚未建立歷史庫），先嘗試抓完整歷史
    if len(merged) < 100:
        print("  -> 本地歷史資料不足，執行完整歷史區間抓取...")
        full_data = fetch_full_history_by_year(session, fund_code, start_year)
        merged.update(full_data)

    # 無論如何都再用最穩定的「近 30 日靜態表」補上最新淨值
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
        print(f"  -> {name} 累計總筆數: {len(output['funds'][name])}")
        time.sleep(1.0)

    for name, ticker in BENCHMARKS.items():
        print(f"Fetching benchmark: {name} ({ticker})...")
        output["benchmarks"][name] = fetch_benchmark_history(ticker)

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("Successfully updated data.json!")


if __name__ == "__main__":
    main()
