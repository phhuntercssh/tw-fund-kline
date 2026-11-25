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
        "sitca_corp": "A0032"
    },
    "野村優質(累積)": {
        "isin": "TW000T3201Y8",
        "sitca_corp": "A0032"
    },
    "施羅德台灣樂活A": {
        "isin": "TW000T4209Y0",
        "sitca_corp": "A0042"
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


def parse_aspnet_hidden(html):
    fields = {}
    for m in re.finditer(r'<input[^>]+type=["\']hidden["\'][^>]*>', html, re.I):
        tag = m.group(0)
        name_m = re.search(r'name=["\']([^"\']+)["\']', tag, re.I)
        val_m = re.search(r'value=["\']([^"\']*)["\']', tag, re.I)
        if name_m:
            fields[name_m.group(1)] = val_m.group(1) if val_m else ""
    return fields


def init_sitca_session(session):
    url = build_url("https://www." + "sitca.org.tw", ["ROC", "Industry", "IN2106.aspx"], "pid=IN2213_02")
    r_get = session.get(url, headers=HEADERS, timeout=25)
    r_get.raise_for_status()
    r_get.encoding = "utf-8"
    return url, parse_aspnet_hidden(r_get.text)


def query_sitca_single_day(session, url, hidden_fields, corp_id, target_dt, target_isins):
    """查詢 SITCA 指定投信在單一日期 (YYYYMMDD) 的官方淨值（精準比對 6 碼代號）"""
    d_compact = target_dt.strftime("%Y%m%d")
    d_iso = target_dt.strftime("%Y-%m-%d")

    code_to_isin = {}
    for isin in target_isins:
        code_to_isin[isin] = isin
        if isin.startswith("TW000") and len(isin) >= 11:
            code_to_isin[isin[5:11]] = isin

    post_data = dict(hidden_fields)
    post_data["ctl00$ContentPlaceHolder1$txtQ_Date"] = d_compact
    post_data["ctl00$ContentPlaceHolder1$ddlQ_Comid"] = corp_id
    post_data["ctl00$ContentPlaceHolder1$BtnQuery"] = "查詢"

    found_navs = {}
    for attempt in range(2):
        try:
            r_post = session.post(url, data=post_data, headers={**HEADERS, "Referer": url}, timeout=25)
            if r_post.status_code != 200:
                time.sleep(1)
                continue
            r_post.encoding = "utf-8"

            new_hidden = parse_aspnet_hidden(r_post.text)
            if new_hidden:
                hidden_fields = new_hidden

            rows = re.findall(r"<tr[^>]*>(.*?)</tr>", r_post.text, re.S | re.I)
            for row_html in rows:
                cells = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, re.S | re.I)]
                if len(cells) < 8:
                    continue

                matched_isin = None
                for cell in cells[:6]:
                    if cell in code_to_isin:
                        matched_isin = code_to_isin[cell]
                        break

                if not matched_isin:
                    continue

                for cell in cells[6:]:
                    if re.match(r"^[\d,]+\.\d{2,4}$", cell):
                        try:
                            v = float(cell.replace(",", ""))
                            if v > 0:
                                found_navs[matched_isin] = (d_iso, round(v, 4))
                                break
                        except ValueError:
                            pass
            break
        except Exception as e:
            if attempt == 0:
                time.sleep(1.5)
            else:
                print(f"    [SITCA單日異常 {corp_id} {d_iso}] {e}")

    return found_navs, hidden_fields







def find_missing_gaps(corp_funds_map):
    """掃描現有資料中所有斷層空洞（相隔超過 6 個日曆天）以及最新交易日缺口，優先排入補齊"""
    today_tw = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)).date()
    gaps = []

    for f_name, records in corp_funds_map.items():
        if not records:
            gaps.append((datetime.date.fromisoformat(START_DATE), today_tw))
            continue

        dates = sorted(records.keys())
        # 1. 檢查最新一筆到今天之間是否有缺漏（取代原本錯誤的 MoneyDJ 源）
        latest_dt = datetime.date.fromisoformat(dates[-1])
        if (today_tw - latest_dt).days >= 1:
            gaps.append((latest_dt + datetime.timedelta(days=1), today_tw))

        # 2. 檢查中間所有相鄰日期之間是否有超過 6 天的斷層空洞（連假最長約 5~6 天）
        for i in range(1, len(dates)):
            d1 = datetime.date.fromisoformat(dates[i - 1])
            d2 = datetime.date.fromisoformat(dates[i])
            if (d2 - d1).days > 6:
                gaps.append((d1 + datetime.timedelta(days=1), d2 - datetime.timedelta(days=1)))

    # 依照結束日期由新到舊排序（優先修補最新與中間斷層）
    gaps.sort(key=lambda x: x[1], reverse=True)
    return gaps


def backfill_corp_days(session, corp_id, corp_funds_map, max_trading_days=18):
    """優先修補所有中間斷層空洞與最新淨值，空洞修補完後自動接續往 2010 年推進"""
    try:
        url, hidden_fields = init_sitca_session(session)
    except Exception as e:
        print(f"    [SITCA初始化失敗] {e}")
        return

    isin_to_name = {FUNDS[f_name]["isin"]: f_name for f_name in corp_funds_map}
    target_isins = list(isin_to_name.keys())
    queried_count = 0
    hit_days = 0

    # 步驟 A：優先修補中間斷層空洞與最新淨值
    gaps = find_missing_gaps(corp_funds_map)
    for gap_start, gap_end in gaps:
        if queried_count >= max_trading_days:
            break
        print(f"    [修補空洞區間] {corp_id} 補齊 {gap_start} ~ {gap_end}...", flush=True)
        cursor = gap_end
        while cursor >= gap_start and queried_count < max_trading_days:
            if cursor.weekday() >= 5:
                cursor -= datetime.timedelta(days=1)
                continue

            found_navs, hidden_fields = query_sitca_single_day(
                session, url, hidden_fields, corp_id, cursor, target_isins
            )
            queried_count += 1
            if found_navs:
                hit_days += 1
                for isin, (d_iso, val) in found_navs.items():
                    corp_funds_map[isin_to_name[isin]][d_iso] = val
                if hit_days % 10 == 1:
                    sample_str = ", ".join(f"{isin_to_name[k]}={v}" for k, v in found_navs.items())
                    print(f"      -> [{cursor}] 命中 ({sample_str})", flush=True)
            cursor -= datetime.timedelta(days=1)
            time.sleep(0.25)

    # 步驟 B：若額度還有剩，從最早日期繼續往 2010-01-04 推進
    target_start = datetime.date.fromisoformat(START_DATE)
    earliest_dates = [
        min(records.keys()) if records else datetime.date.today().strftime("%Y-%m-%d")
        for records in corp_funds_map.values()
    ]
    cursor_dt = datetime.date.fromisoformat(max(earliest_dates)) - datetime.timedelta(days=1)
    empty_streak = 0

    while cursor_dt >= target_start and queried_count < max_trading_days:
        if cursor_dt.weekday() >= 5:
            cursor_dt -= datetime.timedelta(days=1)
            continue

        found_navs, hidden_fields = query_sitca_single_day(
            session, url, hidden_fields, corp_id, cursor_dt, target_isins
        )
        queried_count += 1

        if found_navs:
            hit_days += 1
            empty_streak = 0
            for isin, (d_iso, val) in found_navs.items():
                corp_funds_map[isin_to_name[isin]][d_iso] = val
            if hit_days % 10 == 1:
                sample_str = ", ".join(f"{isin_to_name[k]}={v}" for k, v in found_navs.items())
                print(f"      -> [{cursor_dt}] 命中 ({sample_str})", flush=True)
        else:
            empty_streak += 1
            if empty_streak >= 25:
                print(f"    -> 連續 25 個平日無淨值，停止回補。", flush=True)
                break

        cursor_dt -= datetime.timedelta(days=1)
        time.sleep(0.25)

    print(f"    -> {corp_id} 本輪查詢 {queried_count} 個平日，成功補齊 {hit_days} 個交易日", flush=True)


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
        print(f"    [yfinance異常 {ticker}] {e}", flush=True)
    return result


def save_progress(merged_all, benchmarks_data):
    output = {
        "updated_at": (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S CST"),
        "funds": {name: dict(sorted(merged_all[name].items())) for name in FUNDS},
        "benchmarks": benchmarks_data
    }
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)


def main():
    existing = load_existing_data()
    existing_funds = existing.get("funds", {})
    existing_benchmarks = existing.get("benchmarks", {})

    session = requests.Session()

    # 1. 載入現有資料並徹底清除先前 MoneyDJ 誤抓的異物暴衝淨值（>350 或 <20）
    merged_all = {}
    for name in FUNDS:
        records = dict(existing_funds.get(name, {}))
        if name == "野村台灣運籌":
            records = {d: v for d, v in records.items() if 10 < v < 350}
        elif name == "野村優質(累積)":
            records = {d: v for d, v in records.items() if 80 < v < 350}
        elif name == "施羅德台灣樂活A":
            records = {d: v for d, v in records.items() if 20 < v < 120}
        merged_all[name] = records

    # 2. 依投信分組執行 SITCA 官方修補空洞＋歷史回補（每家每次 18 個交易日，確保 2 分鐘內安全存檔）
    corps = {}
    for name, info in FUNDS.items():
        corp_id = info["sitca_corp"]
        corps.setdefault(corp_id, {})[name] = merged_all[name]

    for corp_id, corp_funds_map in corps.items():
        print(f"Processing SITCA corp {corp_id}...", flush=True)
        backfill_corp_days(session, corp_id, corp_funds_map, max_trading_days=18)
        save_progress(merged_all, existing_benchmarks)
        print(f"  -> 已即時保存 {corp_id} 進度至 data.json", flush=True)

    for name in FUNDS:
        dates = sorted(merged_all[name].keys())
        if dates:
            print(f"  -> {name} 目前總筆數: {len(dates)} 筆 ({dates} ~ {dates[-1]})", flush=True)

    # 3. 更新大盤與 ETF 基準指標
    benchmarks_out = dict(existing_benchmarks)
    for name, ticker in BENCHMARKS.items():
        print(f"Fetching benchmark: {name} ({ticker})...", flush=True)
        fetched = fetch_benchmark_history(ticker)
        if fetched:
            benchmarks_out[name] = fetched

    save_progress(merged_all, benchmarks_out)
    print("Successfully updated data.json!", flush=True)


if __name__ == "__main__":
    main()

