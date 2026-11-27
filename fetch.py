import os
import re
import json
import time
import datetime
import requests
import yfinance as yf
import requests

if not os.path.exists("lightweight-charts.standalone.production.js"):
    cdn_urls = [
        "https://cloudflare.com",
        "https://unpkg.com",
        "https://jsdelivr.net"
    ]
    for u in cdn_urls:
        try:
            r = requests.get(u, timeout=15)
            if r.status_code == 200 and len(r.text) > 50000:
                with open("lightweight-charts.standalone.production.js", "w", encoding="utf-8") as f:
                    f.write(r.text)
                print(f"成功從 {u} 下載圖表引擎！")
                break
        except Exception as e:
            print(f"嘗試下載 {u} 失敗: {e}")


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
        "max_nav": 900.0
    }
}

BENCHMARKS = {
    "加權指數": "^TWII",
    "台積電(2330)": "2330.TW",
    "00935": "00935.TW",
    "00865B": "00865B.TW"
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


def query_sitca_single_day(session, url, hidden_fields, corp_id, target_dt, code_to_name):
    """嚴格鎖定 SITCA 表格固定欄位位置，完全使用迭代器避免複製貼上遺失索引"""
    d_compact = target_dt.strftime("%Y%m%d")
    d_iso = target_dt.strftime("%Y-%m-%d")

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

                it = iter(cells)
                _col0 = next(it)
                row_corp = next(it)
                _col2 = next(it)
                row_code = next(it)
                _col4 = next(it)
                _col5 = next(it)
                row_curr = next(it)
                row_nav_str = next(it).replace(",", "")

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

    return found_navs, hidden_fields





def fetch_twse_stock_month(session, stock_no, year, month):
    """向證交所 (TWSE) 與 FinMind 台灣股市 API 補齊指定年月個股/ETF收盤價"""
    parsed = {}

    # 1. 嘗試臺灣證券交易所 (TWSE) 官方 API
    url = build_url(
        "https://twse.com.tw",
        ["exchangeReport", "STOCK_DAY"],
        f"response=json&date={year}{month:02d}01&stockNo={stock_no}"
    )
    try:
        resp = session.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            for row in data.get("data", []):
                if not isinstance(row, list) or len(row) < 7:
                    continue
                it = iter(row)
                roc_date_str = str(next(it)).strip()
                _vol = next(it)
                _val = next(it)
                _open = next(it)
                _high = next(it)
                _low = next(it)
                close_str = str(next(it)).replace(",", "").strip()

                parts = roc_date_str.split("/")
                if len(parts) == 3 and close_str and close_str != "--":
                    p_it = iter(parts)
                    y_ad = int(next(p_it)) + 1911
                    m_ad = int(next(p_it))
                    d_ad = int(next(p_it))
                    d_iso = f"{y_ad:04d}-{m_ad:02d}-{d_ad:02d}"
                    val = float(close_str)
                    if val > 0 and d_iso >= START_DATE:
                        parsed[d_iso] = round(val, 2)
    except Exception:
        pass

    # 2. 若 TWSE 對海外機房限流未回傳，自動改用 FinMind 台灣股市公開 API 補齊
    if not parsed:
        fm_url = build_url(
            "https://finmindtrade.com",
            ["api", "v4", "data"],
            f"dataset=TaiwanStockPrice&data_id={stock_no}&start_date={year}-{month:02d}-01"
        )
        try:
            r_fm = session.get(fm_url, headers=HEADERS, timeout=15)
            if r_fm.status_code == 200:
                fm_json = r_fm.json()
                for row in fm_json.get("data", []):
                    d_iso = str(row.get("date", ""))[:10]
                    val = float(row.get("close", 0))
                    if len(d_iso) == 10 and val > 0 and d_iso >= START_DATE:
                        parsed[d_iso] = round(val, 2)
        except Exception:
            pass

    return parsed


    # 2. 若 TWSE 對海外機房限流未回傳，自動改用 FinMind 台灣股市公開 API 補齊
    if not parsed:
        fm_url = build_url(
            "https://finmindtrade.com",
            ["api", "v4", "data"],
            f"dataset=TaiwanStockPrice&data_id={stock_no}&start_date={year}-{month:02d}-01"
        )
        try:
            r_fm = session.get(fm_url, headers=HEADERS, timeout=15)
            if r_fm.status_code == 200:
                fm_json = r_fm.json()
                for row in fm_json.get("data", []):
                    d_iso = str(row.get("date", ""))[:10]
                    val = float(row.get("close", 0))
                    if len(d_iso) == 10 and val > 0 and d_iso >= START_DATE:
                        parsed[d_iso] = round(val, 2)
        except Exception:
            pass

    return parsed



def find_missing_gaps(corp_funds_map):
    """掃描現有資料中所有斷層空洞與最新交易日缺口，優先排入補齊"""
    yesterday_tw = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)).date() - datetime.timedelta(days=1)
    gaps = []
    for f_name, records in corp_funds_map.items():
        if not records:
            gaps.append((datetime.date.fromisoformat(START_DATE), yesterday_tw))
            continue
        dates = sorted(records.keys())
        latest_dt = datetime.date.fromisoformat(dates[-1])
        if (yesterday_tw - latest_dt).days >= 1:
            gaps.append((latest_dt + datetime.timedelta(days=1), yesterday_tw))
        for i in range(1, len(dates)):
            d1 = datetime.date.fromisoformat(dates[i - 1])
            d2 = datetime.date.fromisoformat(dates[i])
            if (d2 - d1).days > 6:
                gaps.append((d1 + datetime.timedelta(days=1), d2 - datetime.timedelta(days=1)))
    gaps.sort(reverse=True)
    return gaps


def backfill_corp_days(session, corp_id, corp_funds_map, max_trading_days=120):
    """優先修補中間斷層空洞與最新淨值，空洞修補完後自動接續往 2010 年推進"""
    try:
        url, hidden_fields = init_sitca_session(session)
    except Exception as e:
        print(f"    [SITCA初始化失敗] {e}", flush=True)
        return

    code_to_name = {FUNDS[f_name]["code"]: f_name for f_name in corp_funds_map}
    queried_count, hit_days = 0, 0

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
                    sample_str = ", ".join(f"{k}={v}" for k, v in found_navs.items())
                    print(f"      -> [{cursor}] 命中 ({sample_str})", flush=True)
            cursor -= datetime.timedelta(days=1)
            time.sleep(0.25)

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
                sample_str = ", ".join(f"{k}={v}" for k, v in found_navs.items())
                print(f"      -> [{cursor_dt}] 命中 ({sample_str})", flush=True)
        else:
            empty_streak += 1
            if empty_streak >= 25:
                print(f"    -> 連續 25 個平日無淨值，停止回補。", flush=True)
                break
        cursor_dt -= datetime.timedelta(days=1)
        time.sleep(0.25)

    print(f"    -> {corp_id} 本輪查詢 {queried_count} 個平日，成功補齊 {hit_days} 個交易日", flush=True)





def fetch_benchmark_history(session, ticker, existing_dict=None):
    """抓取基準指數/ETF完整歷史，並額外透過 period='5d' 與 TWSE 官方 API 補齊最新交易日"""
    result = dict(existing_dict) if existing_dict else {}
    try:
        # 1. 抓完整歷史區間
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

        # 2. 額外抓近 5 日即時行情補齊延遲日
        df_recent = yf.download(ticker, period="5d", progress=False, auto_adjust=True)
        if df_recent is not None and not df_recent.empty:
            recent_close = df_recent["Close"]
            if hasattr(recent_close, "columns"):
                recent_close = recent_close.iloc[:, 0]
            for idx, val in recent_close.items():
                if val == val and float(val) > 0:
                    date_str = idx.strftime("%Y-%m-%d")
                    if date_str >= START_DATE:
                        result[date_str] = round(float(val), 2)

        # 3. 針對台股個股與 ETF (.TW) 額外向臺灣證券交易所 (TWSE) 官方補齊當月與上月收盤價
        if ticker.endswith(".TW"):
            stock_no = ticker.replace(".TW", "")
            now_tw = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)).date()
            prev_month_dt = now_tw.replace(day=1) - datetime.timedelta(days=1)
            for dt_obj in (prev_month_dt, now_tw):
                twse_data = fetch_twse_stock_month(session, stock_no, dt_obj.year, dt_obj.month)
                if twse_data:
                    result.update(twse_data)
                time.sleep(0.3)

    except Exception as e:
        print(f"    [基準指標異常 {ticker}] {e}", flush=True)

    return dict(sorted(result.items()))


import json
from datetime import datetime, timedelta, timezone

# --- 貼在 json.dump 存檔之前 ---
def clean_and_validate_fund_navs(data_obj):
    twii_dict = data_obj.get("benchmarks", {}).get("加權指數", {})
    funds_dict = data_obj.get("funds", {})

    for fund_name, nav_map in funds_dict.items():
        if not nav_map:
            continue
        sorted_dates = sorted(nav_map.keys())
        dates_to_remove = []

        for i in range(1, len(sorted_dates)):
            prev_d = sorted_dates[i - 1]
            curr_d = sorted_dates[i]
            if abs(nav_map[curr_d] - nav_map[prev_d]) < 1e-6:
                if prev_d in twii_dict and curr_d in twii_dict and twii_dict[prev_d] > 0:
                    twii_chg_pct = abs((twii_dict[curr_d] - twii_dict[prev_d]) / twii_dict[prev_d]) * 100
                    if twii_chg_pct > 0.3:
                        print(f"[資料清洗] 清除異常重複淨值：{fund_name} {curr_d}（同前日 {nav_map[curr_d]}）")
                        dates_to_remove.append(curr_d)

        for d in dates_to_remove:
            del nav_map[d]

# 執行清洗（請確認變數名稱與您 json.dump 裡的變數一致，通常是 data）
clean_and_validate_fund_navs(data)

# 原本存檔的那幾行：
with open("data.json", "w", encoding="utf-8") as f:
    json.dump(data_obj, f, ensure_ascii=False, indent=2)





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

    # 1. 載入現有資料並清除所有異常淨值區間
    merged_all = {}
    for name, cfg in FUNDS.items():
        records = dict(existing_funds.get(name, {}))
        if name == "施羅德台灣樂活A":
            records = {
                d: v for d, v in records.items()
                if not (d >= "2026-04-01" and v < 45.0)
            }
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
        backfill_corp_days(session, corp_id, corp_funds_map, max_trading_days=120)
        save_progress(merged_all, existing_benchmarks)
        print(f"  -> 已即時保存 {corp_id} 進度至 data.json", flush=True)

    for name in FUNDS:
        dates = sorted(merged_all[name].keys())
        if dates:
            first_d = next(iter(dates))
            last_d = dates[-1]
            print(f"  -> {name} 目前總筆數: {len(dates)} 筆 ({first_d} ~ {last_d})", flush=True)

    # 3. 更新大盤與 ETF 基準指標（含 TWSE 官方補齊）
    benchmarks_out = dict(existing_benchmarks)
    for name, ticker in BENCHMARKS.items():
        print(f"Fetching benchmark: {name} ({ticker})...", flush=True)
        benchmarks_out[name] = fetch_benchmark_history(session, ticker, existing_benchmarks.get(name, {}))

    save_progress(merged_all, benchmarks_out)
    print("Successfully updated data.json!", flush=True)


if __name__ == "__main__":
    main()

