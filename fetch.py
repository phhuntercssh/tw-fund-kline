import os
import re
import json
import time
import datetime
import requests
import yfinance as yf

FUNDS = {
    "野村台灣運籌": {
        "code": "T3218Y",
        "sitca_corp": "A0032",
        "min_nav": 10.0,
        "max_nav": 900.0
    },
    "野村優質(累積)": {
        "code": "T3201Y",
        "sitca_corp": "A0032",
        "min_nav": 15.0,
        "max_nav": 900.0
    },
    "施羅德台灣樂活A": {
        "code": "T4209Y",
        "sitca_corp": "A0042",
        "min_nav": 10.0,
        "max_nav": 120.0
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


_A0042_DIAG_PRINTED = False

def query_sitca_single_day(session, url, hidden_fields, corp_id, target_dt, code_to_name):
    """
    嚴格鎖定 SITCA 表格固定欄位位置（第2欄=投信代號, 第4欄=基金6碼代號, 第7欄=幣別TWD, 第8欄=當日淨值）
    """
    global _A0042_DIAG_PRINTED
    d_compact = target_dt.strftime("%Y%m%d")
    d_iso = target_dt.strftime("%Y-%m-%d")

    post_data = dict(hidden_fields)
    post_data["ctl00$ContentPlaceHolder1$txtQ_Date"] = d_compact
    post_data["ctl00$ContentPlaceHolder1$ddlQ_Comid"] = corp_id
    post_data["ctl00$ContentPlaceHolder1$BtnQuery"] = "查詢"

    found_navs = {}
    a0042_samples = []

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

                it = iter(cells)
                _col0 = next(it)
                row_corp = next(it)
                _col2 = next(it)
                row_code = next(it)
                _col4 = next(it)
                row_fname = next(it)
                row_curr = next(it)
                row_nav_str = next(it).replace(",", "")

                if row_corp == "A0042" and row_curr == "TWD":
                    a0042_samples.append(f"{row_code} | {row_fname[:15]} | {row_nav_str}")

                if row_corp == corp_id and row_code in code_to_name and row_curr == "TWD":
                    try:
                        val = float(row_nav_str)
                        f_name = code_to_name[row_code]
                        cfg = FUNDS[f_name]
                        if cfg["min_nav"] <= val <= cfg["max_nav"]:
                            found_navs[f_name] = (d_iso, round(val, 4))
                    except ValueError:
                        pass
            break
        except Exception as e:
            if attempt == 0:
                time.sleep(1.5)
            else:
                print(f"    [SITCA異常 {corp_id} {d_iso}] {e}", flush=True)

    if corp_id == "A0042" and not found_navs and a0042_samples and not _A0042_DIAG_PRINTED:
        print(f"    [A0042 施羅德投信 {d_iso} 全部台幣基金清單]：", flush=True)
        for item in a0042_samples:
            print(f"      - {item}", flush=True)
        _A0042_DIAG_PRINTED = True

    return found_navs, hidden_fields










def find_missing_gaps(corp_funds_map):
    """掃描現有資料中所有斷層空洞與最新交易日缺口，優先排入補齊"""
    today_tw = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)).date() - datetime.timedelta(days=1)
    gaps = []
    for f_name, records in corp_funds_map.items():
        if not records:
            gaps.append((datetime.date.fromisoformat(START_DATE), today_tw))
            continue
        dates = sorted(records.keys())
        latest_dt = datetime.date.fromisoformat(dates[-1])
        if (today_tw - latest_dt).days >= 1:
            gaps.append((latest_dt + datetime.timedelta(days=1), today_tw))
        for i in range(1, len(dates)):
            d1 = datetime.date.fromisoformat(dates[i - 1])
            d2 = datetime.date.fromisoformat(dates[i])
            if (d2 - d1).days > 6:
                gaps.append((d1 + datetime.timedelta(days=1), d2 - datetime.timedelta(days=1)))
    gaps.sort(key=lambda x: x[1], reverse=True)
    return gaps


def backfill_corp_days(session, corp_id, corp_funds_map, max_trading_days=18):
    """優先修補中間斷層空洞與最新淨值，空洞修補完後自動接續往 2010 年推進"""
    try:
        url, hidden_fields = init_sitca_session(session)
    except Exception as e:
        print(f"    [SITCA初始化失敗] {e}", flush=True)
        return

    code_to_name = {FUNDS[f_name]["code"]: f_name for f_name in corp_funds_map}
    queried_count, hit_days = 0, 0

    # 步驟 A：優先修補中間斷層空洞與最新淨值
    gaps = find_missing_gaps(corp_funds_map)
    for gap_start, gap_end in gaps:
        if queried_count >= max_trading_days:
            break
        print(f"    [修補空洞] {corp_id} 補齊 {gap_start} ~ {gap_end}...", flush=True)
        cursor = gap_end
        while cursor >= gap_start and queried_count < max_trading_days:
            if cursor.weekday() >= 5:
                cursor -= datetime.timedelta(days=1)
                continue
            found_navs, hidden_fields = query_sitca_single_day(
                session, url, hidden_fields, corp_id, cursor, code_to_name
            )
            queried_count += 1
            if found_navs:
                hit_days += 1
                for f_name, (d_iso, val) in found_navs.items():
                    corp_funds_map[f_name][d_iso] = val
                if hit_days % 10 == 1:
                    sample_str = ", ".join(f"{k}={v[1]}" for k, v in found_navs.items())
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
            session, url, hidden_fields, corp_id, cursor_dt, code_to_name
        )
        queried_count += 1
        if found_navs:
            hit_days += 1
            empty_streak = 0
            for f_name, (d_iso, val) in found_navs.items():
                corp_funds_map[f_name][d_iso] = val
            if hit_days % 10 == 1:
                sample_str = ", ".join(f"{k}={v[1]}" for k, v in found_navs.items())
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
        if df is not None and not df.empty:
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

    # 1. 載入現有資料並清除所有超出合理淨值範圍的舊錯誤數據
    merged_all = {}
    for name, cfg in FUNDS.items():
        records = dict(existing_funds.get(name, {}))
        merged_all[name] = {
            d: v for d, v in records.items()
            if cfg["min_nav"] <= v <= cfg["max_nav"]
        }

    # 2. 依投信分組執行 SITCA 官方修補空洞＋歷史回補
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
            first_d = next(iter(dates))
            last_d = dates[-1]
            print(f"  -> {name} 目前總筆數: {len(dates)} 筆 ({first_d} ~ {last_d})", flush=True)

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

