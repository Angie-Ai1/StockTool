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
    → 下載 JSON → 存到 .personal/secrets/desktop_client.json
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

SECRETS_DIR = Path(__file__).parent.parent / ".personal" / "secrets"
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


GUIDE_TAB_NAME = "使用說明"
LEGACY_PANEL_TAB_NAME = "操作面板"  # 舊版分頁，建立說明頁時一併刪除

# 說明頁文字（A 欄為標題/標籤，B 欄為說明/範例），RAW 寫入。
# 內容只教使用者「怎麼用」，不放任何按鈕或公式——同步改由 LINE 傳「立即同步」觸發。
GUIDE_TEXT = [
    ["📖 使用說明", ""],                                                            # 0  title
    ["", ""],
    ["🟢 第一次使用", ""],                                                          # 2  heading
    ["1. 加入官方帳號好友後，點歡迎訊息裡的連結授權 Google", ""],
    ["2. 系統會自動在你的雲端硬碟建立這份試算表", ""],
    ["3. 之後在 LINE 記帳，資料就會自動寫進這裡", ""],
    ["", ""],
    ["✍️ 怎麼記帳（直接在 LINE 輸入文字）", ""],                                     # 7  heading
    ["買 / 賣", "買 股票 股數 總金額　例：買 台積電 100 85000"],
    ["配息", "配息 股票 金額　例：配息 0050 3000"],
    ["配股", "配股 股票 股數　例：配股 0056 100"],
    ["小提醒", "股票代碼或名稱皆可；單位是「股」，1 張會自動算成 1000 股；多筆請分行一次傳"],
    ["", ""],
    ["🔁 其他指令", ""],                                                            # 13 heading
    ["查詢", "看目前各帳戶的持股與損益"],
    ["撤銷", "撤回剛剛記錯的上一筆"],
    ["立即同步", "重新計算並把最新損益寫回這份試算表"],
    ["新增帳戶 <名稱>", "建立新的帳戶分頁，例如：新增帳戶 海外股"],
    ["", ""],
    ["🔄 資料怎麼流動", ""],                                                        # 19 heading
    ["① 你在 LINE 輸入記帳文字", "↓"],
    ["② 系統辨識股票、解析買賣／配息／配股", "↓"],
    ["③ 寫進對應帳戶分頁左側的流水帳（A–G 欄）", "↓"],
    ["④ 右側統計摘要（I–Q 欄）自動算出持股數、買進平均價、今日收盤價、未實現／已實現損益", ""],
    ["", ""],
    ["📊 怎麼查看損益", ""],                                                        # 25 heading
    ["現在", "在 LINE 傳「查詢」或點選單「查詢」，即可看到持股損益摘要"],
    ["即將推出", "🚧 網頁圖表版（LIFF）：美化圖表、篩選與搜尋，規劃中"],
]

GUIDE_TITLE_ROW = 0
GUIDE_HEADING_ROWS = [2, 7, 13, 19, 25]


def setup_guide_tab(service) -> None:
    """建立／覆寫「使用說明」分頁：純文字操作指南，並刪除舊版「操作面板」分頁。"""
    meta = service.spreadsheets().get(spreadsheetId=TEMPLATE_ID).execute()
    title_to_id = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}

    # 移除舊版操作面板（若存在）
    if LEGACY_PANEL_TAB_NAME in title_to_id:
        service.spreadsheets().batchUpdate(
            spreadsheetId=TEMPLATE_ID,
            body={"requests": [{"deleteSheet": {"sheetId": title_to_id[LEGACY_PANEL_TAB_NAME]}}]},
        ).execute()
        print(f"已刪除舊版「{LEGACY_PANEL_TAB_NAME}」分頁。")

    if GUIDE_TAB_NAME in title_to_id:
        print(f"「{GUIDE_TAB_NAME}」分頁已存在，覆寫內容。")
        guide_id = title_to_id[GUIDE_TAB_NAME]
    else:
        resp = service.spreadsheets().batchUpdate(
            spreadsheetId=TEMPLATE_ID,
            body={"requests": [{"addSheet": {"properties": {"title": GUIDE_TAB_NAME, "index": 0}}}]},
        ).execute()
        guide_id = resp["replies"][0]["addSheet"]["properties"]["sheetId"]
        print(f"「{GUIDE_TAB_NAME}」分頁已建立（sheetId: {guide_id}）")

    service.spreadsheets().values().update(
        spreadsheetId=TEMPLATE_ID,
        range=f"'{GUIDE_TAB_NAME}'!A1",
        valueInputOption="RAW",
        body={"values": GUIDE_TEXT},
    ).execute()

    gray = {"red": 239/255, "green": 239/255, "blue": 239/255}

    def _row(r0, r1, c0=0, c1=2):
        return {"sheetId": guide_id, "startRowIndex": r0, "endRowIndex": r1,
                "startColumnIndex": c0, "endColumnIndex": c1}

    def _col(start, end, px):
        return {"updateDimensionProperties": {
            "range": {"sheetId": guide_id, "dimension": "COLUMNS", "startIndex": start, "endIndex": end},
            "properties": {"pixelSize": px}, "fields": "pixelSize",
        }}

    format_requests = [
        # 主標題：粗體大字
        {"repeatCell": {
            "range": _row(GUIDE_TITLE_ROW, GUIDE_TITLE_ROW + 1),
            "cell": {"userEnteredFormat": {"textFormat": {"bold": True, "fontSize": 14}}},
            "fields": "userEnteredFormat.textFormat",
        }},
        # 內文自動換行
        {"repeatCell": {
            "range": _row(0, len(GUIDE_TEXT)),
            "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP", "verticalAlignment": "MIDDLE"}},
            "fields": "userEnteredFormat(wrapStrategy,verticalAlignment)",
        }},
        # 欄寬：A=260（標籤）、B=560（說明）
        _col(0, 1, 260),
        _col(1, 2, 560),
    ]
    # 各區段標題：粗體灰底
    for heading_row in GUIDE_HEADING_ROWS:
        format_requests.append({"repeatCell": {
            "range": _row(heading_row, heading_row + 1),
            "cell": {"userEnteredFormat": {"backgroundColor": gray, "textFormat": {"bold": True}}},
            "fields": "userEnteredFormat(backgroundColor,textFormat.bold)",
        }})

    service.spreadsheets().batchUpdate(
        spreadsheetId=TEMPLATE_ID,
        body={"requests": format_requests},
    ).execute()
    print("「使用說明」分頁文字與樣式設定完成。")


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

    print("\n建立「使用說明」分頁...")
    setup_guide_tab(service)

    print("\n全部完成！請開啟試算表確認：")
    print(f"  https://docs.google.com/spreadsheets/d/{TEMPLATE_ID}/edit")
    print("\n驗收清單：")
    print("  ☐ 標題列粗體 + 灰底，捲動時固定在頂部")
    print("  ☐ C 欄點任一儲存格，出現下拉選單（買進/賣出/股息/配股）")
    print("  ☐ A 欄（row_uuid）已隱藏（右鍵 → 取消隱藏 可確認）")
    print("  ☐ Z1 顯示 TEMPLATE_VERSION=1")
    print("  ☐ 「使用說明」分頁已建立，內容/排版正確，舊版「操作面板」已移除")
    print("  ☐ 試算表共用設定仍為「任何知道連結的人可以檢視」")


if __name__ == "__main__":
    main()
