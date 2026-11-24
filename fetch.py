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
        "sitca_corp": "A0032",
        "mdj_id": "ACKH03"
    },
    "野村優質(累積)": {
        "isin": "TW000T3201Y8",
        "sitca_corp": "A0032",
        "mdj_id": "ACIC01"
    },
    "施羅德台灣樂活A": {
        "isin": "TW000T4209Y0",
        "sitca_corp": "A0042",
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
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
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
    url = build_url("https://www.moneydj.com", ["funddj", "ya", "yp010000.djhtm"], f"a={mdj_id}")
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


def parse_aspnet_hidden(html):
    """擷取 ASP.NET 網頁表單必需的 __VIEWSTATE 等隱藏欄位"""
    fields = {}
    for m in re.finditer(r'<input[^>]+type=["\']hidden["\'][^>]*>', html, re.I):
        tag = m.group(0)
        name_m = re.search(r'name=["\']([^"\']+)["\']', tag, re.I)
        val_m = re.search(r'value=["\']([^"\']*)["\']', tag, re.I)
        if name_m:
            fields[name_m.group(1)] = val_m.group(1) if val_m else ""
    return fields


def fetch_sitca_month(session, corp_id, year, month, target_isins):
    """
    向投信投顧公會 (SITCA) IN2106.aspx 查詢指定投信在某年某月的全部基金每日淨值，
    並依照 ISIN 一次提取該投信旗下多檔基金（例如同時提取野村台灣運籌與野村優質）。
    """
    url = build_url("https://www.sitca.org.tw", ["ROC", "Industry", "IN2106.aspx"], "pid=IN2213_02")
    results_by_isin = {isin: {} for isin in target_isins}

    try:
        # 1. 先 GET 取得表單隱藏欄位與下拉選單名稱
        r_get = session.get(url, headers=HEADERS, timeout=25)
        if r_get.status_code != 200:
            print(f"    [SITCA GET HTTP {r_get.status_code}]")
            return results_by_isin
        r_get.encoding = "utf-8"
        hidden = parse_aspnet_hidden(r_get.text)

        # 自動偵測表單內年月與投信下拉選單的 name 屬性
        select_names = re.findall(r'<select[^>]+name=["\']([^"\']+)["\']', r_get.text, re.I)
        btn_match = re.search(r'<input[^>]+type=["\']submit["\'][^>]+name=["\']([^"\']+)["\']', r_get.text, re.I)

        ym_str = f"{year}{month:02d}"
        post_data = dict(hidden)
        for s_name in select_names:
            low = s_name.lower()
            if "year" in low or "yy" in low:
                post_data[s_name] = str(year)
            elif "month" in low or "mm" in low:
                post_data[s_name] = f"{month:02d}"
            elif "ym" in low or "date" in low:
                post_data[s_name] = ym_str
            elif "corp" in low or "com" in low or "ddl" in low:
                post_data[s_name] = corp_id

        if btn_match:
            post_data[btn_match.group(1)] = "查詢"

        r_post = session.post(url, data=post_data, headers={**HEADERS, "Referer": url}, timeout=30)
        if r_post.status_code != 200:
            print(f"    [SITCA POST HTTP {r_post.status_code}]")
            return results_by_isin
        r_post.encoding = "utf-8"

        # 2. 解析回傳表格每一列（找尋包含目標 ISIN 與日期/淨值的列）
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", r_post.text, re.S | re.I)
        for row_html in rows:
            matched_isin = None
            for isin in target_isins:
                if isin in row_html:
                    matched_isin = isin
                    break
            if not matched_isin:
                continue

            cells = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, re.S | re.I)]
            d_iso = None
            nav_val = None
            for cell in cells:
                # 支援西元 2025/05/02 或民國 114/05/02
                m_ad = re.match(r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2})$", cell)
                m_roc = re.match(r"^(\d{2,3})[/-](\d{1,2})[/-](\d{1,2})$", cell)
                if m_ad:
                    d_iso = f"{int(m_ad.group(1)):04d}-{int(m_ad.group(2)):02d}-{int(m_ad.group(3)):02d}"
                elif m_roc:
                    y_ad = int(m_roc.group(1)) + 1911
                    d_iso = f"{y_ad:04d}-{int(m_roc.group(2)):02d}-{int(m_roc.group(3)):02d}"
                elif re.match(r"^[\d,]+\.\d{2,4}$", cell):
                    try:
                        v = float(cell.replace(",", ""))
                        if v > 0 and nav_val is None:
                            nav_val = round(v, 4)
                    except ValueError:
                        pass

            if d_iso and nav_val and d_iso >= START_DATE:
                results_by_isin[matched_isin][d_iso] = nav_val

    except Exception as e:
        print(f"    [SITCA異常 {corp_id} {year}-{month:02d}] {e}")

    return results_by_isin







def backfill_by_corp_month(session, corp_id, corp_funds_map, max_months=24):
    """
    以投信公司為單位，逐月往回查詢投信投顧公會 (SITCA) 歷史淨值，
    單次執行最多往回抓 24 個月（2 年），搭配每 5 分鐘排程可無痛補滿至 2010 年。
    """
    target_ym = (2010, 1)
    earliest_dates = []
    for f_name, records in corp_funds_map.items():
        if records:
            earliest_dates.append(min(records.keys()))
        else:
            earliest_dates.append(datetime.date.today().strftime("%Y-%m-%d"))

    # 從所有同投信基金中「最晚的那個最早日期」開始往回補
    cursor_str = max(earliest_dates)
    cursor_dt = datetime.date.fromisoformat(cursor_str)
    cur_year, cur_month = cursor_dt.year, cursor_dt.month

    # 往回推 1 個月作為起始查詢月
    if cur_month == 1:
        cur_year -= 1
        cur_month = 12
    else:
        cur_month -= 1

    isin_to_name = {FUNDS[f_name]["isin"]: f_name for f_name in corp_funds_map}
    target_isins = list(isin_to_name.keys())

    month_count = 0
    empty_streak = 0

    while (cur_year, cur_month) >= target_ym and month_count < max_months:
        month_data_map = fetch_sitca_month(session, corp_id, cur_year, cur_month, target_isins)
        month_count += 1

        total_added = 0
        for isin, rows in month_data_map.items():
            if rows:
                f_name = isin_to_name[isin]
                corp_funds_map[f_name].update(rows)
                total_added += len(rows)

        if total_added > 0:
            empty_streak = 0
            print(f"    [SITCA {corp_id}][{cur_year}-{cur_month:02d}] 補齊共 {total_added} 筆淨值")
        else:
            empty_streak += 1
            print(f"    [SITCA {corp_id}][{cur_year}-{cur_month:02d}] 無資料（連續 {empty_streak} 個月）")
            if empty_streak >= 4:
                print(f"    -> 連續 4 個月無資料，停止往回回補。")
                break

        if cur_month == 1:
            cur_year -= 1
            cur_month = 12
        else:
            cur_month -= 1

        time.sleep(0.8)


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

    # 1. 先載入舊資料並抓最新近 30 日淨值
    merged_all = {}
    for name, info in FUNDS.items():
        print(f"Updating recent 30d: {name}...")
        records = dict(existing_funds.get(name, {}))
        recent = fetch_mdj_recent_30(session, info["mdj_id"])
        print(f"  -> MoneyDJ 近30日取得: {len(recent)} 筆")
        records.update(recent)
        merged_all[name] = records

    # 2. 依投信公司分組，啟動 SITCA 逐月往回回補至 2010-01
    corps = {}
    for name, info in FUNDS.items():
        corp_id = info["sitca_corp"]
        corps.setdefault(corp_id, {})[name] = merged_all[name]

    for corp_id, corp_funds_map in corps.items():
        earliest_in_corp = max(min(r.keys()) if r else "9999-12-31" for r in corp_funds_map.values())
        if earliest_in_corp > START_DATE:
            print(f"Backfilling SITCA corp {corp_id} (目前最早至 {earliest_in_corp})...")
            backfill_by_corp_month(session, corp_id, corp_funds_map, max_months=24)

    for name in FUNDS:
        sorted_records = dict(sorted(merged_all[name].items()))
        output["funds"][name] = sorted_records
        dates = list(sorted_records.keys())
        if dates:
            first_d = next(iter(dates))
            last_d = dates[-1]
            print(f"  -> {name} 最終總筆數: {len(dates)} 筆 ({first_d} ~ {last_d})")
        else:
            print(f"  -> {name} 最終總筆數: 0 筆")

    # 3. 更新大盤與 ETF 基準指標
    for name, ticker in BENCHMARKS.items():
        print(f"Fetching benchmark: {name} ({ticker})...")
        fetched = fetch_benchmark_history(ticker)
        output["benchmarks"][name] = fetched if fetched else existing_benchmarks.get(name, {})

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print("Successfully updated data.json!")


if __name__ == "__main__":
    main()



if __name__ == "__main__":
    main()
