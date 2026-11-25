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


def init_sitca_session(session):
    """初始化 SITCA 頁面取得 ASP.NET 狀態欄位，避免每次查詢重複 GET"""
    url = build_url("https://www." + "sitca.org.tw", ["ROC", "Industry", "IN2106.aspx"], "pid=IN2213_02")
    r_get = session.get(url, headers=HEADERS, timeout=25)
    r_get.raise_for_status()
    r_get.encoding = "utf-8"
    return url, parse_aspnet_hidden(r_get.text)



_SITCA_ROW_DEBUG = False

def query_sitca_single_day(session, url, hidden_fields, corp_id, target_dt, target_isins):
    """查詢 SITCA 指定投信在單一日期 (YYYYMMDD) 的淨值，精準比對投信基金代號（避免同名基金混淆）"""
    d_compact = target_dt.strftime("%Y%m%d")
    d_iso = target_dt.strftime("%Y-%m-%d")

    # 從 ISIN (如 TW000T4209Y0) 萃取出 SITCA 表格第4欄的精準代號 (如 T4209Y)
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
    # 內建 2 次重試機制，防止政府主機偶發 Read timed out 漏抓
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

                # 精準比對儲存格中的基金代號 (如 T3218Y, T3201Y, T4209Y)
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
            break  # 成功解析即跳出重試迴圈
        except Exception as e:
            if attempt == 0:
                time.sleep(1.5)
            else:
                print(f"    [SITCA單日異常 {corp_id} {d_iso}] {e}")

    return found_navs, hidden_fields









def backfill_corp_days(session, corp_id, corp_funds_map, max_trading_days=130):
    """
    從現有最早日期往前逐個交易日（自動跳過週六日）向 SITCA 補齊淨值，
    單次執行回補約 130 個交易日（半年），搭配每 5 分鐘排程可全自動補滿至 2010 年。
    """
    target_start = datetime.date.fromisoformat(START_DATE)
    earliest_dates = [
        min(records.keys()) if records else datetime.date.today().strftime("%Y-%m-%d")
        for records in corp_funds_map.values()
    ]
    cursor_dt = datetime.date.fromisoformat(max(earliest_dates)) - datetime.timedelta(days=1)

    try:
        url, hidden_fields = init_sitca_session(session)
    except Exception as e:
        print(f"    [SITCA初始化失敗] {e}")
        return

    isin_to_name = {FUNDS[f_name]["isin"]: f_name for f_name in corp_funds_map}
    target_isins = list(isin_to_name.keys())

    queried_count = 0
    hit_days = 0
    empty_streak = 0  # 連續無交易日（含國定假日，若連續超過 25 個平日才視為已達基金成立日）

    while cursor_dt >= target_start and queried_count < max_trading_days:
        # 跳過週六(5)、週日(6)
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
                f_name = isin_to_name[isin]
                corp_funds_map[f_name][d_iso] = val
            if hit_days % 20 == 1 or hit_days == 1:
                sample_str = ", ".join(f"{isin_to_name[k]}={v[1]}" for k, v in found_navs.items())
                print(f"    [SITCA {corp_id}][{cursor_dt}] 命中 ({sample_str}) | 本輪累計 {hit_days} 交易日")
        else:
            empty_streak += 1
            if empty_streak >= 25:
                print(f"    -> 連續 25 個平日無淨值（已達基金成立日或無資料），停止回補。")
                break

        cursor_dt -= datetime.timedelta(days=1)
        time.sleep(0.25)

    print(f"    -> {corp_id} 本輪查詢 {queried_count} 個平日，成功補齊 {hit_days} 個交易日（目前最早至 {cursor_dt + datetime.timedelta(days=1)}）")


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


def save_progress(merged_all, benchmarks_data):
    """即時存檔函式：每完成一家投信就立刻寫入 data.json，確保進度不遺失"""
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

    # 1. 更新最新近 30 日淨值
    merged_all = {}
    for name, info in FUNDS.items():
        print(f"Updating recent 30d: {name}...", flush=True)
        # 加在 main() 讀取 existing_funds 之後，自動清除先前誤抓的配息/低淨值級別斷層
        records = dict(existing_funds.get(name, {}))
        if name == "野村優質(累積)":
            records = {d: v for d, v in records.items() if v > 80}
        elif name == "施羅德台灣樂活A":
            records = {d: v for d, v in records.items() if v > 25}


        recent = fetch_mdj_recent_30(session, info["mdj_id"])
        print(f"  -> MoneyDJ 近30日取得: {len(recent)} 筆", flush=True)
        records.update(recent)
        merged_all[name] = records

    # 2. 依投信分組往回逐日回補（單輪每家抓 35 個交易日，確保 1 分鐘內安全存檔）
    corps = {}
    for name, info in FUNDS.items():
        corp_id = info["sitca_corp"]
        corps.setdefault(corp_id, {})[name] = merged_all[name]

    for corp_id, corp_funds_map in corps.items():
        earliest_in_corp = max(min(r.keys()) if r else "9999-12-31" for r in corp_funds_map.values())
        if earliest_in_corp > START_DATE:
            print(f"Backfilling SITCA corp {corp_id} (目前最早至 {earliest_in_corp})...", flush=True)
            backfill_corp_days(session, corp_id, corp_funds_map, max_trading_days=18)
            # 每跑完一家投信就立刻存檔一次！
            save_progress(merged_all, existing_benchmarks)
            print(f"  -> 已即時保存 {corp_id} 回補進度至 data.json", flush=True)

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
