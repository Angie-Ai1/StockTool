# LINE 股市記帳小工具

> 最後更新：2026-06-26 00:41

## 專案開發進度

| 維度 | 比例 | 進度條 | 說明 |
|---|---|---|---|
| 規格設計(MVP / Phase 1) | 100% | `██████████` | 已逐項討論定案,含核心邏輯、隱私架構、可靠性機制 |
| 規格設計(Phase 2~8) | 約 70% | `███████░░░` | 功能範圍已定,部分細節(手續費折數規則等)待展開 |
| Phase 0 基礎設施建置 | 95%(18/19) | `█████████░` | 本機開發環境 100% 完成;雲端帳號設定僅缺 `ADMIN_LINE_USER_ID`(不含選用的帳單保險項目) |
| Phase 1 MVP 程式碼 | 77%(37/48) | `████████░░` | 已完成 1.1 骨架(含 `app/main.py` 掛載三個 router)、1.2 `line_webhook.py`(5/5+記帳寫入流程)、1.3 OAuth(5/5)、1.4 `parser.py`+`fuzzy_match.py`(7/7 含多帳戶 Quick Reply)、1.5 `pnl_engine.py`、1.9 `tick.py`(3/3)、1.11 `liff.py`(2/2);1.6 `sheets_client.resync()+append` 2/3(僅剩操作面板按鈕);1.7、1.8、1.10、1.12 尚未開始 |
| 測試覆蓋 | 100%(10/10) | `██████████` | `test_parser.py`、`test_pnl_engine.py`、`test_fuzzy_match.py`、`test_market_data_client.py`、`test_oauth_service.py`、`test_friend_repository.py`、`test_sheets_client.py`、`test_line_webhook.py`、`test_tick.py`、`test_liff.py` 共 115 案例全數通過,皆不依賴本機真實 `.env`/打真實外部 API |
| 部署上線:基礎設施 | 100% | `██████████` | Cloud Run 服務運作中(`/health`) |
| 部署上線:功能 | 0% | `░░░░░░░░░░` | `app/main.py` 已掛上三個 router,但程式碼尚未 commit/push,Cloud Run 仍是舊版本 |

## Phase 0 — 基礎設施建置（🔄 95%，18/19）

### 本機開發環境（✅ 9/9）

| 狀態 | 項目 |
|---|---|
| ✅ | Poetry 初始化，`pyproject.toml` 鎖定 Python `^3.10`，`poetry.lock` |
| ✅ | 鎖定生產依賴（fastapi、uvicorn、line-bot-sdk、google-api-python-client、google-auth-oauthlib、google-cloud-firestore、cryptography、thefuzz、httpx） |
| ✅ | 新增 dev 依賴（pytest、pytest-asyncio） |
| ✅ | 建立 `.env.example`（所有必要欄位） |
| ✅ | 建立 `Dockerfile` + `docker-compose.yml`（python:3.12-slim + Poetry） |
| ✅ | `app/main.py` Phase 0 最小骨架（`/health` 驗證基礎設施） |
| ✅ | 本機驗證（uvicorn + pytest 通過） |
| ✅ | Docker Desktop WSL integration 驗證（`docker compose build` + `up -d` 實測） |
| ✅ | `.gitignore` 補規則（`.venv/`、`__pycache__/`、`.pytest_cache/`、`.env`） |

### 雲端帳號設定（🔄 9/10，僅缺 `ADMIN_LINE_USER_ID`）

| 狀態 | 項目 | 備註 |
|---|---|---|
| ✅ | 建立 Google Cloud 專案 | `stocktool-500502` |
| ✅ | 啟用 Sheets API + Drive API | |
| ✅ | 設定 OAuth 同意畫面 | In production（非測試模式，避免 refresh token 每 7 天失效） |
| ✅ | 產生加密金鑰（`cryptography.fernet`） | `.env` 已有值 |
| ✅ | 申請 LINE 官方帳號 + Messaging API Channel | Channel Secret / Access Token 已就位 |
| ✅ | 設定 LINE Rich Menu 雛形 | |
| ✅ | 建立 Firestore（Native mode，`asia-southeast1`）+ Service Account 金鑰 | `secrets/firestore-service-account.json` 已存在 |
| ✅ | 建立 Cloud Run 服務 | `https://stocktool-22843182344.asia-southeast1.run.app`，`/health` 運作中 |
| ✅ | 設定 Cloud Scheduler | 每 10 分鐘呼叫 `/tick`，Asia/Taipei |
| ⬜ | `ADMIN_LINE_USER_ID` | 需先加自己的官方帳號好友後取得 |

⚠️ **雲端待辦（需手動在 GCP 主控台完成）：**
1. **Firestore 金鑰改用 Secret Manager 掛載**：建立 Secret → 執行身分加 Secret Accessor 角色 → 掛載到 `/secrets/firestore-service-account.json`（詳見 `Instruction/cloud_setting.md` 4.2 節）
2. **`TICK_SHARED_SECRET`**：Cloud Run 環境變數新增 + Cloud Scheduler 改用自訂標頭 `X-Tick-Secret`（詳見 `cloud_setting.md` 第 10 節）
3. **`LINE_LOGIN_CHANNEL_ID`**：建立 LINE Login channel + LIFF app → Cloud Run 環境變數新增（詳見 `cloud_setting.md` 第 12 節）

---

## Phase 1 — MVP 實作（🔄 77%，37/48）

### 1.1 專案骨架（✅ 4/4）

| 狀態 | 項目 |
|---|---|
| ✅ | `app/config.py`：環境變數讀取（pydantic-settings） |
| ✅ | `app/models/schemas.py`：Pydantic 資料模型（`FriendRecord`、`TransactionRow`、`ParsedTransaction`、`ParseResult`、`Position`、`ResyncResult` 等） |
| ✅ | `app/db/firestore_client.py`：Firestore 連線（Secret Manager 掛載磁碟區，本機 / Cloud Run 共用同一套邏輯） |
| ✅ | `app/main.py`：FastAPI 進入點，掛載 `line_webhook` / `liff` / `tick` 三個 router |

### 1.2 LINE Webhook 與事件處理（✅ 5/5）

| 狀態 | 項目 |
|---|---|
| ✅ | LINE Channel Secret 簽章驗證（簽章錯誤回 400） |
| ✅ | 僅處理 1:1 私訊（群組 / 聊天室訊息忽略） |
| ✅ | webhook 事件去重（10 分鐘窗口，process 記憶體） |
| ✅ | `follow` 事件：查 Firestore 判斷回鍋舊親友，是則 `reactivate_friend()` |
| ✅ | `unfollow` 事件：標記「已停用」，不刪除資料 |

> 額外完成：文字訊息完整記帳寫入流程（未連結→OAuth URL；`needs_reauth`→重新連結 URL；parse→fuzzy match→賣超防呆→`append_transaction_row()`→回覆；多帳戶無標籤→Quick Reply 詢問，5 分鐘 `_pending_selections` 暫存；多帳戶全部已標籤→分組各自寫入）

### 1.3 OAuth 與試算表建立（✅ 5/5）

| 狀態 | 項目 |
|---|---|
| ✅ | Google OAuth 2.0 授權流程（`build_authorization_url()` + `exchange_code_for_credentials()`，state 帶 LINE user ID 防 CSRF） |
| ✅ | 自動複製範本到親友 Drive（`copy_template_to_drive()`，用親友自己的 OAuth 授權呼叫） |
| ✅ | Firestore 寫入對照表（`link_friend_account()`：換 token → 複製範本 → 加密 refresh token → 寫入） |
| ✅ | OAuth 失效偵測（`refresh_or_raise()` 捕捉 `RefreshError`，`mark_needs_reauth()` 標記狀態） |
| ✅ | 親友刪除試算表（Sheets API 404）共用同一套 `mark_needs_reauth()` 復原流程 |

### 1.4 記帳文字解析（✅ 7/7）

| 狀態 | 項目 |
|---|---|
| ✅ | 固定欄位順序解析（`買/賣 個股 數量 金額`） |
| ✅ | 三種輸入形式（數量+金額 / 僅金額用收盤價反推估算 / 僅股數+單價），反推單價 `ROUND_HALF_UP` 小數 2 位 |
| ✅ | 股息 / 配股格式解析（`股息 個股 金額`、`配股 個股 股數`） |
| ✅ | 換行批次記帳（部分行解析失敗只回該行錯誤含行號，不整批作廢） |
| ✅ | 股票代碼 / 名稱模糊比對（`fuzzy_match.resolve_stock()`：精確代碼優先，fallback `thefuzz` 門檻 70） |
| ✅ | 多帳戶記帳前選擇（單帳戶不問；多帳戶無標籤→Quick Reply 5 分鐘有效；全部已標籤→分組各自寫入） |
| ✅ | 解析失敗明確回報中文原因，不做猜測性寫入 |

> 額外完成：`app/services/market_data_client.py`（`fetch_stock_list()`，合併 TWSE `STOCK_DAY_ALL` + TPEx `tpex_mainboard_daily_close_quotes`，回傳代碼 / 名稱 / 收盤價）

### 1.5 損益引擎（✅ 4/4）

| 狀態 | 項目 |
|---|---|
| ✅ | 移動加權平均成本法（買進 / 賣出 / 配股 / 股息，全面使用 `Decimal`） |
| ✅ | 已實現損益（每次賣出立即結算） |
| ✅ | 未實現損益（依收盤價動態計算，`compute_unrealized_pnl`） |
| ✅ | 賣超防呆（`InsufficientPositionError`，超賣擋下不寫入並提示） |

### 1.6 試算表 resync（🔄 2/3）

| 狀態 | 項目 |
|---|---|
| ✅ | resync 核心邏輯（`resync_account_tab()` 純函式 + `resync()` I/O 整合 + `read_tab_positions()` 純讀取庫存 + `append_transaction_row()` 追加新列） |
| ✅ | 三種觸發來源中的兩個已接上：14:30 排程（`tick.py`）、LIFF 開啟時（`liff.py GET /liff/summary`） |
| ⬜ | 操作面板「立即同步」按鈕（Apps Script + `UrlFetchApp.fetch()`，屬 1.8） |

### 1.7 防呆撤銷與查詢（⬜ 0/2）

| 狀態 | 項目 |
|---|---|
| ⬜ | 記帳成功後 Quick Reply 刪除上一筆（`[❌ 刪除上一筆]`，5 分鐘有效） |
| ⬜ | LINE 直接查看庫存損益（Rich Menu 觸發，回覆目前庫存 + 未實現 / 已實現損益，多帳戶各自摘要） |

### 1.8 首次使用引導與操作面板（⬜ 0/4）

| 狀態 | 項目 |
|---|---|
| ⬜ | 首次加好友說明訊息（`follow` 事件 `replyToken` 回覆 Flex Message，不用 push） |
| ⬜ | Rich Menu「使用說明」按鈕（隨時可再看一次規則說明） |
| ⬜ | 免責聲明（僅供個人記帳參考，非正式對帳 / 報稅依據） |
| ⬜ | 試算表「操作面板」分頁（Apps Script 按鈕 + `UrlFetchApp.fetch()` 呼叫後端「立即同步」） |

### 1.9 排程（✅ 3/3）

| 狀態 | 項目 |
|---|---|
| ✅ | 單一外部 Cron 進入點 `/tick`（共享密鑰驗證 `X-Tick-Secret`，無設定一律 401 fail closed；立即回 200，重任務丟 `BackgroundTasks`） |
| ✅ | 內部判斷：Asia/Taipei 是否已過 14:30 且 Firestore `system/scheduler` 今日尚未執行 |
| ✅ | 14:30 收盤任務：抓 TWSE/TPEx 收盤價 → 存模組層級快取 → 逐位 active 親友呼叫 `resync()`（間隔 2 秒）→ 標記今日已執行；單一親友 OAuth / Sheets 失敗時跳過繼續 |

### 1.10 共用通知元件（⬜ 0/4）

| 狀態 | 項目 |
|---|---|
| ⬜ | 共用 LINE 推播元件（同時服務親友通知 + 管理員通知） |
| ⬜ | 排程任務失敗通知管理員 |
| ⬜ | 新親友首次 OAuth 連結通知管理員（僅一次） |
| ⬜ | 親友日常操作（記帳 / 查詢 / 開啟 LIFF）不通知管理員 |

### 1.11 LIFF 網頁（✅ 2/2）

| 狀態 | 項目 |
|---|---|
| ✅ | LIFF SDK `id_token` 驗證身分（呼叫 LINE 官方 `oauth2/v2.1/verify`，身分以 `sub` 為準，不從 URL 參數取） |
| ✅ | `GET /liff/summary`：連結狀態 + 即時呼叫 `resync()` 回傳庫存列表 + 損益（用 tick 收盤價快取計算未實現損益） |

### 1.12 溫度感文案（⬜ 0/2）

| 狀態 | 項目 |
|---|---|
| ⬜ | 機器人回覆採朋友語氣（「好的，幫你記下囉 📝」取代「已記錄」） |
| ⬜ | 個人化稱呼（取得親友 LINE 顯示名稱，訊息中帶入名字） |

### 1.13 測試（✅ 10/10，115 案例）

| 狀態 | 檔案 | 案例數 |
|---|---|---|
| ✅ | `tests/test_parser.py` | 12 |
| ✅ | `tests/test_pnl_engine.py` | 10 |
| ✅ | `tests/test_fuzzy_match.py` | 5 |
| ✅ | `tests/test_market_data_client.py` | 3 |
| ✅ | `tests/test_oauth_service.py` | 6 |
| ✅ | `tests/test_friend_repository.py` | 8 |
| ✅ | `tests/test_sheets_client.py` | 23 |
| ✅ | `tests/test_line_webhook.py` | 17 |
| ✅ | `tests/test_tick.py` | 13 |
| ✅ | `tests/test_liff.py` | 12 |

> 全部案例皆不依賴真實 `.env` / 外部 API。

---

## 下次可從哪裡接續

1. **commit 積欠變更**：Phase 1 大批程式碼（`line_webhook.py`、`tick.py`、`liff.py`、`sheets_client.py`、`friend_repository.py` 及所有對應測試）尚未 commit，建議先收
2. **1.7 防呆撤銷與查詢**：記帳成功後 Quick Reply 刪除上一筆 + LINE 直接查看庫存損益（完成後 MVP 記帳迴圈閉合，可給親友試用）
3. **1.8 操作面板「立即同步」按鈕**：Apps Script + `UrlFetchApp.fetch()`，完成後 1.6 三個觸發來源全部到位
4. **雲端設定補完**（使用者手動）：Firestore Secret Manager 掛載、`TICK_SHARED_SECRET`、`LINE_LOGIN_CHANNEL_ID`
