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
    "Accept": "*/*",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
}


def load_existing_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"funds": {}, "benchmarks": {}}


def parse_chart_or_table_data(text):
    """同時支援解析：
    1. 走勢圖純數據格式（日期列表與淨值列表以逗號/分號分隔，如 2010/01/04,2010/01/05...;12.34,12.45...）
    2. 一般 HTML 表格格式（YYYY/MM/DD 緊跟淨值）
    """
    result = {}

    # 模式 A：解析走勢圖專用純數據格式（通常包含兩組等長的日期與數值陣列）
    dates_found = re.findall(r"\b(\d{4}/\d{2}/\d{2})\b", text)
    if len(dates_found) > 100 and ";" in text:
        parts = text.strip().split(";")
        # 尋找日期區塊與緊鄰的淨值數值區塊
        for i in range(len(parts) - 1):
            d_list = re.findall(r"\d{4}/\d{2}/\d{2}", parts[i])
            if len(d_list) > 100:
                v_list = [x.strip() for x in parts[i + 1].split(",") if re.match(r"^[\d.]+$", x.strip())]
                if len(d_list) == len(v_list):
                    for d_raw, v_raw in zip(d_list, v_list):
                        d_iso = d_raw.replace("/", "-")
                        val = float(v_raw)
                        if val > 0 and d_iso >= START_DATE:
                            result[d_iso] = round(val, 4)
                    if result:
                        return result

    # 模式 B：解析 HTML 表格中的 YYYY/MM/DD 與淨值
    clean_text = re.sub(r"<[^>]+>", " ", text)
    pairs = re.findall(r"(\d{4}/\d{2}/\d{2})\s+([\d,]+\.\d+)", clean_text)
    for d_raw, nav_raw in pairs:
        d_iso = d_raw.replace("/", "-")
        val = float(nav_raw.replace(",", ""))
        if val > 0 and d_iso >= START_DATE:
            result[d_iso] = round(val, 4)

    return result


def fetch_full_history_via_chart_api(session, fund_code):
    """透過 MoneyDJ 體系走勢圖歷史資料接口（一次回傳成立至今全部每日淨值）"""
    today_slash = datetime.date.today().strftime("%Y/%m/%d")
    start_slash = START_DATE.replace("-", "/")

    # MoneyDJ 基金走勢圖資料接口（GET 與 POST 雙重嘗試）
    chart_endpoints = [
        # 1. MoneyDJ 主站走勢圖數據接口 (bcd)
        ("GET", f"https://moneydj.com{fund_code}&b={start_slash}&c={today_slash}", None),
        # 2. 銀行代銷鏡像站走勢圖數據接口 (djbcd)
        ("GET", f"https://hncb.com.tw{fund_code}&b={start_slash}&c={today_slash}", None),
        ("GET", f"https://moneydj.com{fund_code}&b={start_slash}&c={today_slash}", None),
        ("GET", f"https://moneydj.com{fund_code}&b={start_slash}&c={today_slash}", None),
        # 3. POST 表單方式送出歷史區間查詢 (wr02.djhtm)
        ("POST", f"https://hncb.com.tw_{fund_code}.djhtm", {"a": fund_code, "b": start_slash, "c": today_slash}),
        ("POST", f"https://moneydj.com{fund_code}", {"b": start_slash, "c": today_slash}),
    ]

    for method, url, payload in chart_endpoints:
        headers = {**BASE_HEADERS, "Referer": f"https://moneydj.com{fund_code}"}
        try:
            if method == "GET":
                resp = session.get(url, headers=headers, timeout=25)
            else:
                resp = session.post(url, data=payload, headers=headers, timeout=25)

            if resp.status_code == 200:
                resp.encoding = "big5"
                parsed = parse_chart_or_table_data(resp.text)
                if len(parsed) > 100:
                    print(f"  -> 成功透過 {url.split('/')[2]} 取得 {len(parsed)} 筆歷史淨值")
                    return parsed
        except Exception:
            continue

    return {}


def fetch_recent_30_days(session, fund_code):
    """抓取近 30 日最新淨值（MM/DD 格式）"""
    url = f"https://moneydj.com{fund_code}"
    headers = {**BASE_HEADERS, "Referer": url}
    try:
        resp = session.get(url, headers=headers, timeout=20)
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
    except Exception:
        pass
    return {}


def update_fund(session, fund_code, existing_records):
    merged = dict(existing_records)
    earliest_date = min(merged.keys()) if merged else "9999-12-31"

    # 若尚未取得 2010 年起的完整歷史，呼叫走勢圖數據接口 (BCDNavList.djbcd) 一次拉回全部歷史
    if earliest_date > "2010-02-01" or len(merged) < 1000:
        print(f"  -> 目前最早僅至 {earliest_date}，嘗試從走勢圖接口拉取 {START_DATE} 至今完整歷史...")
        full_data = fetch_full_history_via_chart_api(session, fund_code)
        merged.update(full_data)

    # 再用近 30 日靜態表補上最新交易日淨值
    recent = fetch_recent_30_days(session, fund_code)
    merged.update(recent)

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
