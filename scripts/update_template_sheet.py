"""Admin script: 套用試算表格式、資料驗證與版本號標記。

**一次性憑證設定（擇一即可）：**

  方法 A ─ 在現有 Web app OAuth client 新增固定 localhost URI
    GCP Console → APIs & Services → Credentials
    → 編輯現有的 "Web application" OAuth 2.0 Client
    → 在 "Authorized redirect URIs" 加入  http://localhost:8080
    → 儲存（注意：Web app client 需要精確比對，含 port）
    然後執行：poetry run python scripts/update_template_sheet.py

  方法 B ─ 建立新的 Desktop app OAuth client（任何 port 都不需設定）
    GCP Console → Credentials → 建立 OAuth 2.0 Client ID → 類型選 "Desktop app"
    → 下載 JSON → 存到 secrets/desktop_client.json
    然後執行：poetry run python scripts/update_template_sheet.py

**WSL2 注意事項：**
  執行後會開啟瀏覽器進行授權（或印出 URL 供手動開啟）。
  若瀏覽器沒有自動開啟，請複製終端機印出的 URL 貼到 Windows 瀏覽器。
  授權後瀏覽器會跳轉到 localhost:8080，腳本自動擷取授權碼。

執行後「個人帳」分頁將完成：
  - 凍結第一列（標題列）
  - 標題列粗體 + 灰底 #EFEFEF
  - C 欄（動作）下拉選單：買進 / 賣出 / 股息 / 配股
  - G 欄（狀態）淡灰背景（提示系統自動填寫）
  - A 欄（row_uuid）隱藏
  - 各欄欄寬調整
  - Z1 版本號標記 TEMPLATE_VERSION=1
"""

import glob
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

TEMPLATE_ID = "1Z6oFyaSeHz02bYXh4p1q-ckcoGd4VlUeF900WfkYVL8"
TAB_NAME = "個人帳戶"

SECRETS_DIR = Path(__file__).parent.parent / "secrets"
DESKTOP_CLIENT = SECRETS_DIR / "desktop_client.json"
TOKEN_CACHE = SECRETS_DIR / "admin_token.json"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]


def _find_client_secret() -> Path:
    if DESKTOP_CLIENT.exists():
        print(f"使用 Desktop app client：{DESKTOP_CLIENT.name}")
        return DESKTOP_CLIENT

    matches = glob.glob(str(SECRETS_DIR / "client_secret_*.json"))
    # 排除 Zone.Identifier 之類的 Windows 附屬檔案
    matches = [m for m in matches if not m.endswith(".json:Zone.Identifier")]
    if not matches:
        raise FileNotFoundError(
            "找不到 OAuth client secret 檔案。\n"
            "請依腳本頂部的說明建立憑證後再執行。"
        )
    path = Path(matches[0])
    print(f"使用 Web app client：{path.name}")
    print("（需在 GCP Console 將 http://localhost:8080 加入 redirect URIs）")
    return path


def get_credentials() -> Credentials:
    creds = None

    if TOKEN_CACHE.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_CACHE), SCOPES)
        except ValueError:
            print("快取 token 格式異常，刪除後重新授權...")
            TOKEN_CACHE.unlink()
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            client_secret = _find_client_secret()
            flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), SCOPES)
            creds = flow.run_local_server(port=8080, access_type="offline", prompt="consent")

        TOKEN_CACHE.write_text(creds.to_json())
        print("授權成功，token 已快取。")

    return creds


def get_sheet_id(service, spreadsheet_id: str, tab_name: str) -> int:
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    available = [s["properties"]["title"] for s in meta["sheets"]]
    print(f"  試算表現有分頁：{available}")
    for sheet in meta["sheets"]:
        if sheet["properties"]["title"] == tab_name:
            return sheet["properties"]["sheetId"]
    raise ValueError(f"找不到分頁：{tab_name}（現有：{available}）")


def _color(hex_str: str) -> dict:
    h = hex_str.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return {"red": r / 255, "green": g / 255, "blue": b / 255}


def _grid_range(sheet_id: int, *, start_row=None, end_row=None, start_col=None, end_col=None) -> dict:
    r: dict = {"sheetId": sheet_id}
    if start_row is not None:
        r["startRowIndex"] = start_row
    if end_row is not None:
        r["endRowIndex"] = end_row
    if start_col is not None:
        r["startColumnIndex"] = start_col
    if end_col is not None:
        r["endColumnIndex"] = end_col
    return r


def build_requests(sheet_id: int) -> list[dict]:
    requests = []

    # 1. 凍結第一列
    requests.append({
        "updateSheetProperties": {
            "properties": {
                "sheetId": sheet_id,
                "gridProperties": {"frozenRowCount": 1},
            },
            "fields": "gridProperties.frozenRowCount",
        }
    })

    # 2. 標題列格式：粗體 + 灰底 #EFEFEF（A1:G1，col 0–6）
    requests.append({
        "repeatCell": {
            "range": _grid_range(sheet_id, start_row=0, end_row=1, start_col=0, end_col=7),
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": _color("EFEFEF"),
                    "textFormat": {"bold": True},
                }
            },
            "fields": "userEnteredFormat(backgroundColor,textFormat.bold)",
        }
    })

    # 3. C 欄資料驗證（C2:C1000）：下拉選單，col index 2
    requests.append({
        "setDataValidation": {
            "range": _grid_range(sheet_id, start_row=1, end_row=1000, start_col=2, end_col=3),
            "rule": {
                "condition": {
                    "type": "ONE_OF_LIST",
                    "values": [
                        {"userEnteredValue": "買進"},
                        {"userEnteredValue": "賣出"},
                        {"userEnteredValue": "股息"},
                        {"userEnteredValue": "配股"},
                    ],
                },
                "showCustomUi": True,
                "strict": True,
            },
        }
    })

    # 4. G 欄淡灰背景（G2:G1000）：col index 6，提示系統欄
    requests.append({
        "repeatCell": {
            "range": _grid_range(sheet_id, start_row=1, end_row=1000, start_col=6, end_col=7),
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": _color("F5F5F5"),
                }
            },
            "fields": "userEnteredFormat.backgroundColor",
        }
    })

    # 5. 隱藏 A 欄（col index 0）
    requests.append({
        "updateDimensionProperties": {
            "range": {
                "sheetId": sheet_id,
                "dimension": "COLUMNS",
                "startIndex": 0,
                "endIndex": 1,
            },
            "properties": {"hiddenByUser": True},
            "fields": "hiddenByUser",
        }
    })

    # 6. 日期欄（B2:B1000）：日期格式 yyyy/mm/dd
    requests.append({
        "repeatCell": {
            "range": _grid_range(sheet_id, start_row=1, end_row=1000, start_col=1, end_col=2),
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "DATE", "pattern": "yyyy/mm/dd"}}},
            "fields": "userEnteredFormat.numberFormat",
        }
    })

    # 7. 欄寬調整（單位 px）
    # col index: 1=日期, 2=動作, 3=股票代碼/名稱, 4=數量, 5=金額
    for col_index, width_px in [(1, 90), (2, 70), (3, 150), (4, 90), (5, 90)]:
        requests.append({
            "updateDimensionProperties": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": col_index,
                    "endIndex": col_index + 1,
                },
                "properties": {"pixelSize": width_px},
                "fields": "pixelSize",
            }
        })

    return requests


PANEL_TAB_NAME = "操作面板"
SOURCE_TAB = "個人帳戶"

# 靜態文字區塊（RAW 寫入）
PANEL_TEXT = [
    ["📋 操作面板", "", ""],        # row 1
    ["", "", ""],                    # row 2
    ["📊 統計摘要", "", ""],         # row 3  ← stats header
    ["動作", "總金額（元）", "總股數"],  # row 4  ← table header
    ["買進", "", ""],                # row 5
    ["賣出", "", ""],                # row 6
    ["股息", "", ""],                # row 7
    ["配股", "", ""],                # row 8
    ["", "", ""],                    # row 9
    ["⚙️ LINE 指令", "", ""],       # row 10  ← commands header
    ["立即同步", "→ LINE 傳「立即同步」", ""],
    ["新增帳戶分頁", "→ LINE 傳「新增帳戶 <名稱>」，例如：新增帳戶 海外股", ""],
]

# SUMIF 公式區塊（USER_ENTERED 寫入），對應 B5:C8
_S = SOURCE_TAB
PANEL_FORMULAS = [
    [f"=SUMIF('{_S}'!C:C,\"買進\",'{_S}'!F:F)", f"=SUMIF('{_S}'!C:C,\"買進\",'{_S}'!E:E)"],
    [f"=SUMIF('{_S}'!C:C,\"賣出\",'{_S}'!F:F)", f"=SUMIF('{_S}'!C:C,\"賣出\",'{_S}'!E:E)"],
    [f"=SUMIF('{_S}'!C:C,\"股息\",'{_S}'!F:F)", "—"],
    ["—",                                         f"=SUMIF('{_S}'!C:C,\"配股\",'{_S}'!E:E)"],
]


def setup_panel_tab(service) -> None:
    """建立「操作面板」分頁：統計摘要（SUMIF 公式）+ LINE 指令對照。"""
    meta = service.spreadsheets().get(spreadsheetId=TEMPLATE_ID).execute()
    existing = [s["properties"]["title"] for s in meta["sheets"]]
    if PANEL_TAB_NAME in existing:
        print(f"「{PANEL_TAB_NAME}」分頁已存在，覆寫內容。")
        panel_id = next(
            s["properties"]["sheetId"] for s in meta["sheets"]
            if s["properties"]["title"] == PANEL_TAB_NAME
        )
    else:
        resp = service.spreadsheets().batchUpdate(
            spreadsheetId=TEMPLATE_ID,
            body={"requests": [{"addSheet": {"properties": {"title": PANEL_TAB_NAME, "index": 0}}}]},
        ).execute()
        panel_id = resp["replies"][0]["addSheet"]["properties"]["sheetId"]
        print(f"「{PANEL_TAB_NAME}」分頁已建立（sheetId: {panel_id}）")

    # 靜態文字（A 欄動作名稱 + 說明文字）
    service.spreadsheets().values().update(
        spreadsheetId=TEMPLATE_ID,
        range=f"'{PANEL_TAB_NAME}'!A1",
        valueInputOption="RAW",
        body={"values": PANEL_TEXT},
    ).execute()

    # SUMIF 公式（B5:C8）
    service.spreadsheets().values().update(
        spreadsheetId=TEMPLATE_ID,
        range=f"'{PANEL_TAB_NAME}'!B5",
        valueInputOption="USER_ENTERED",
        body={"values": PANEL_FORMULAS},
    ).execute()

    gray = {"red": 239/255, "green": 239/255, "blue": 239/255}

    def _row(r0, r1, c0=0, c1=3):
        return {"sheetId": panel_id, "startRowIndex": r0, "endRowIndex": r1,
                "startColumnIndex": c0, "endColumnIndex": c1}

    format_requests = [
        # 主標題（row 1）：粗體大字
        {"repeatCell": {
            "range": _row(0, 1),
            "cell": {"userEnteredFormat": {"textFormat": {"bold": True, "fontSize": 14}}},
            "fields": "userEnteredFormat.textFormat",
        }},
        # 統計摘要區段標題（row 3）：粗體灰底
        {"repeatCell": {
            "range": _row(2, 3),
            "cell": {"userEnteredFormat": {"backgroundColor": gray, "textFormat": {"bold": True}}},
            "fields": "userEnteredFormat(backgroundColor,textFormat.bold)",
        }},
        # 統計表格欄位標題（row 4）：粗體灰底
        {"repeatCell": {
            "range": _row(3, 4),
            "cell": {"userEnteredFormat": {"backgroundColor": gray, "textFormat": {"bold": True}}},
            "fields": "userEnteredFormat(backgroundColor,textFormat.bold)",
        }},
        # LINE 指令區段標題（row 10）：粗體灰底
        {"repeatCell": {
            "range": _row(9, 10),
            "cell": {"userEnteredFormat": {"backgroundColor": gray, "textFormat": {"bold": True}}},
            "fields": "userEnteredFormat(backgroundColor,textFormat.bold)",
        }},
        # 金額欄（B5:B8）：數字格式，千分位
        {"repeatCell": {
            "range": _row(4, 8, 1, 2),
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}},
            "fields": "userEnteredFormat.numberFormat",
        }},
        # 欄寬：A=140, B=160, C=120
        {"updateDimensionProperties": {
            "range": {"sheetId": panel_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1},
            "properties": {"pixelSize": 140}, "fields": "pixelSize",
        }},
        {"updateDimensionProperties": {
            "range": {"sheetId": panel_id, "dimension": "COLUMNS", "startIndex": 1, "endIndex": 2},
            "properties": {"pixelSize": 160}, "fields": "pixelSize",
        }},
        {"updateDimensionProperties": {
            "range": {"sheetId": panel_id, "dimension": "COLUMNS", "startIndex": 2, "endIndex": 3},
            "properties": {"pixelSize": 380}, "fields": "pixelSize",
        }},
    ]
    service.spreadsheets().batchUpdate(
        spreadsheetId=TEMPLATE_ID,
        body={"requests": format_requests},
    ).execute()
    print("「操作面板」統計公式與樣式設定完成。")


def main() -> None:
    print("取得 Google 授權...")
    creds = get_credentials()
    service = build("sheets", "v4", credentials=creds, cache_discovery=False)

    print(f"讀取試算表分頁資訊（ID: {TEMPLATE_ID}）...")
    sheet_id = get_sheet_id(service, TEMPLATE_ID, TAB_NAME)
    print(f"「{TAB_NAME}」分頁 sheetId：{sheet_id}")

    requests = build_requests(sheet_id)
    print(f"執行 batchUpdate（{len(requests)} 個操作）...")
    service.spreadsheets().batchUpdate(
        spreadsheetId=TEMPLATE_ID,
        body={"requests": requests},
    ).execute()
    print("格式設定完成！")

    print("寫入版本號標記 Z1...")
    service.spreadsheets().values().update(
        spreadsheetId=TEMPLATE_ID,
        range=f"{TAB_NAME}!Z1",
        valueInputOption="RAW",
        body={"values": [["TEMPLATE_VERSION=1"]]},
    ).execute()

    print("\n建立「操作面板」分頁...")
    setup_panel_tab(service)

    print("\n全部完成！請開啟試算表確認：")
    print(f"  https://docs.google.com/spreadsheets/d/{TEMPLATE_ID}/edit")
    print("\n驗收清單：")
    print("  ☐ 標題列粗體 + 灰底，捲動時固定在頂部")
    print("  ☐ C 欄點任一儲存格，出現下拉選單（買進/賣出/股息/配股）")
    print("  ☐ A 欄（row_uuid）已隱藏（右鍵 → 取消隱藏 可確認）")
    print("  ☐ Z1 顯示 TEMPLATE_VERSION=1")
    print("  ☐ 「操作面板」分頁已建立，說明文字正確")
    print("  ☐ 試算表共用設定仍為「任何知道連結的人可以檢視」")
    print("\n【手動步驟】在「操作面板」分頁插入按鈕：")
    print("  1. 點選「插入 → 繪圖」，畫一個圓角矩形，輸入文字「立即同步」→ 儲存並關閉")
    print("  2. 點選剛建立的圖形右上角 ⋮ → 指定指令碼 → 輸入 syncNow → 確認")
    print("  3. 重複步驟 1-2，文字改「新增帳戶分頁」，指令碼改 addAccountTab")


if __name__ == "__main__":
    main()
