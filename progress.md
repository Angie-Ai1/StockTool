# 開發進度總覽 — LINE 股市零股記帳小工具

> 最後更新:2026-06-25 20:22

## 整體狀態快照

| 維度 | 比例 | 進度條 | 說明 |
|---|---|---|---|
| 規格設計(MVP / Phase 1) | 100% | `██████████` | 已逐項討論定案,含核心邏輯、隱私架構、可靠性機制 |
| 規格設計(Phase 2~8) | 約 70% | `███████░░░` | 功能範圍已定,部分細節(手續費折數規則等)待展開 |
| Phase 0 基礎設施建置 | 95%(18/19) | `█████████░` | 本機開發環境 100% 完成;雲端帳號設定僅缺 `ADMIN_LINE_USER_ID`(不含選用的帳單保險項目) |
| Phase 1 MVP 程式碼 | 67%(32/48) | `███████░░░` | 已完成 1.1 部分骨架、1.2 `line_webhook.py`(5/5)、1.3 OAuth 連結(5/5)、1.4 `parser.py`+`fuzzy_match.py`、1.5 `pnl_engine.py`、1.9 `tick.py`(3/3)、1.11 `liff.py`(2/2);1.6 `sheets_client.resync()` 2/3(核心邏輯+兩個呼叫端完成,僅剩操作面板按鈕);1.7、1.8、1.10、1.12 尚未開始 |
| 測試覆蓋 | 100%(10/10) | `██████████` | `test_parser.py`、`test_pnl_engine.py`、`test_fuzzy_match.py`、`test_market_data_client.py`、`test_oauth_service.py`、`test_friend_repository.py`、`test_sheets_client.py`、`test_line_webhook.py`、`test_tick.py`、`test_liff.py` 共 101 案例全數通過,皆不依賴本機真實 `.env`/打真實外部 API |
| 部署上線:基礎設施 | 100% | `██████████` | Cloud Run 服務運作中(僅 `/health`) |
| 部署上線:功能 | 0% | `░░░░░░░░░░` | webhook/OAuth/tick/liff 路由程式碼已完成但尚未掛上 `app/main.py` |

**目前卡在**:Phase 0 雲端帳號設定,除 `ADMIN_LINE_USER_ID`(需先加自己的官方帳號好友才能取得)外,其餘項目使用者回報皆已完成。本機 `.env`/`secrets/` 已用檔案證據比對確認 LINE Channel Secret/Token、Google OAuth Client、Firestore Service Account、加密金鑰皆已就位;Sheets/Drive API 啟用、OAuth Audience 發布狀態、LINE Rich Menu、Cloud Scheduler 屬純雲端主控台設定,本機環境無法直接驗證,採信使用者回報。⚠️ 待辦:
1. Firestore 金鑰存放方式已改用 **Secret Manager 掛載為磁碟區**(取代直接貼 JSON 進環境變數,理由與步驟見 `Instruction/cloud_setting.md` 4.2 節、`openspecs/DE.md`),部署 Phase 1.1 `firestore_client.py` 前需在 Cloud Run 主控台完成這個設定:建立 Secret、把執行身分加上 Secret Accessor 角色、掛載到 `/secrets/firestore-service-account.json`,不需要也不應該再有 `FIRESTORE_SERVICE_ACCOUNT_JSON` 環境變數。
2. `/tick` 端點新增共享密鑰驗證(`TICK_SHARED_SECRET`),部署前需同步:① Cloud Run 環境變數新增 `TICK_SHARED_SECRET`;② Cloud Scheduler 既有的「Add OIDC token」改成自訂標頭 `X-Tick-Secret`(步驟已更新進 `Instruction/cloud_setting.md` 第 10 節)。兩者都還沒做。
3. `/liff/summary` 身分驗證需要 `LINE_LOGIN_CHANNEL_ID`(LINE Login channel,不是 Messaging API channel),部署前需:① 建立 LINE Login channel + LIFF app(步驟見 `Instruction/cloud_setting.md` 第 12 節);② Cloud Run 環境變數新增 `LINE_LOGIN_CHANNEL_ID`。都還沒做。

---

## Phase 0 — 基礎設施建置(95%,本機部分已完成,雲端帳號僅缺 `ADMIN_LINE_USER_ID`)

### 本機開發環境(✅ 9/9,2026-06-25 完成)

| 狀態 | 項目 | 備註 |
|---|---|---|
| ✅ | Poetry 初始化專案 | `pyproject.toml` 鎖定 Python `^3.10`、`poetry.lock`,取代 `requirements.txt` |
| ✅ | 鎖定生產依賴 | fastapi、uvicorn、pydantic/pydantic-settings、line-bot-sdk、google-api-python-client、google-auth、google-auth-oauthlib、google-cloud-firestore、cryptography、thefuzz、httpx |
| ✅ | 新增 dev 依賴 | pytest、pytest-asyncio(依 1.13 測試規劃) |
| ✅ | 建立 `.env.example` | LINE channel secret、Google OAuth client、Firestore 服務帳號金鑰路徑、加密金鑰、`ADMIN_LINE_USER_ID` 等欄位 |
| ✅ | 建立 `Dockerfile` + `docker-compose.yml` | `python:3.12-slim` + 容器內安裝 Poetry + `poetry install --only main` |
| ✅ | 建立 `app/main.py` 最小骨架 | 僅 `/health`,供驗證本機環境串接;原命名 `/healthz` 已改名,原因見 `openspecs/debugging_notes.md` |
| ✅ | 本機驗證(`uvicorn` + `pytest`) | 健康檢查路由回 200,`poetry run pytest` 可正常執行 |
| ✅ | Docker Desktop WSL integration 驗證 | `docker compose build` + `up -d` 實測通過,`--reload`、`.env` 注入皆正常,已 `docker compose down` 清理 |
| ✅ | `.gitignore` 補規則 | `.venv/`、`__pycache__/`、`.pytest_cache/`、`.env`(`.env.example` 維持進版控) |

### 雲端帳號設定(✅ 9/10,僅缺 `ADMIN_LINE_USER_ID`;待使用者執行,Claude 無法代為操作)

> 詳細操作步驟、網址、填值對照表已整理成 `Instruction/cloud_setting.md`(本檔不進版控)。

| 狀態 | 項目 | 備註 |
|---|---|---|
| ✅ | 建立 Google Cloud 專案 | `stocktool-500502` |
| ✅ | 啟用 Sheets API + Drive API | 使用者回報完成,本機無法直接驗證 |
| ✅ | 設定 OAuth 同意畫面(Google Auth Platform) | ⚠️ 已更正為 **In production + 不送驗證**(取代測試模式,原因見 `openspecs/DE.md` 09:59 區塊——測試模式會讓 refresh token 每 7 天強制失效)。`.env`/`secrets/` 已有對應 OAuth Client ID/Secret 與 `client_secret_*.json` |
| ✅ | 產生並設定加密金鑰 | `cryptography.fernet` key,本機 `.env` `ENCRYPTION_KEY` 已有值;Cloud Run 環境變數待統一確認 |
| ✅ | 申請 LINE 官方帳號 + Messaging API Channel | 取得 Channel Secret / Access Token,本機 `.env` 已有對應值 |
| ✅ | 設定 LINE Rich Menu 雛形 | 使用者回報完成,本機無法直接驗證 |
| ✅ | 建立 Firestore(Native mode,`asia-southeast1`)+ Service Account 金鑰 | `secrets/firestore-service-account.json` 已存在,`docker-compose.yml` 已補上對應 volume mount |
| ⬜ | (選用)Firestore 帳單硬上限保險 | 預算 → Pub/Sub → Cloud Function 自動關閉 Billing,程式碼已備好於 `Instruction/billing-killswitch/`,步驟見 `cloud_setting.md` 4.3 節 |
| ✅ | 建立 Cloud Run 服務 | asia-southeast1,Continuously deploy,min instances 0、Allow unauthenticated invocations(ADR-021)。網址 `https://stocktool-22843182344.asia-southeast1.run.app`;`/healthz` 被平台保留路徑攔截的踩坑已解決(改名 `/health`,見 `openspecs/debugging_notes.md`) |
| ✅ | 設定 Cloud Scheduler | 每 10 分鐘呼叫 `/tick`,時區 Asia/Taipei;使用者回報完成,本機無法直接驗證。`/tick` 路由要到 1.9 才實作,目前持續 404 屬正常現象 |
| ⬜ | `ADMIN_LINE_USER_ID` | 需先加自己的官方帳號好友後才能取得,本機 `.env` 仍空白 |

---

## Phase 1 — MVP 實作(🔄 40%,19/48 子項)

### 1.1 專案骨架(✅ 3/4)

| 狀態 | 項目 | 備註 |
|---|---|---|
| ⬜ | `app/main.py`:FastAPI 進入點 | 掛載 webhook / LIFF / tick 路由;目前仍是 Phase 0 最小骨架,只有 `/health` |
| ✅ | `app/config.py`:環境變數讀取 | `pydantic-settings`,已用本機 `.env` 驗證可正確讀到所有欄位 |
| ✅ | `app/models/schemas.py`:Pydantic 資料模型 | `FriendRecord`、`SchedulerState`、`TransactionRow`、`ParsedTransaction`、`ParseResult`、`Position` |
| ✅ | `app/db/firestore_client.py` | 改用 Secret Manager 掛載磁碟區提供憑證後,只需 `GOOGLE_APPLICATION_CREDENTIALS` 檔案路徑,本機/雲端共用同一套邏輯;未實際接 Cloud Run 測試 |

### 1.2 LINE Webhook 與事件處理(✅ 5/5,`app/routers/line_webhook.py`)

| 狀態 | 項目 | 備註 |
|---|---|---|
| ✅ | LINE Channel Secret 簽章驗證 | `linebot.v3.webhook.WebhookParser`,簽章錯誤回 400 |
| ✅ | 僅處理 1:1 私訊 | 群組/聊天室(group/room)訊息一律忽略不回應(`isinstance(event.source, UserSource)` 過濾) |
| ✅ | webhook 事件去重 | 短時間窗口(10 分鐘)依 `webhookEventId` 去重,process 記憶體實作,過期自動清除不持續增長 |
| ✅ | `follow` 事件處理 | 查 Firestore 判斷是否為回鍋舊親友(`oauth_service.get_friend_record()`),是則狀態改回「啟用」(`reactivate_friend()`) |
| ✅ | `unfollow` 事件處理 | 存在的親友才標記為「已停用」(`deactivate_friend()`),不刪除資料 |

> 額外完成:1:1 文字訊息且尚未連結試算表時,直接回覆 OAuth 連結引導(重用 1.3 `build_authorization_url()`)。已連結試算表的親友傳訊息目前**沒有任何回應**——完整的解析→模糊比對→損益計算→寫入試算表流程要等 1.4 最後一項(多帳戶詢問)、1.6(resync 寫入)、1.9(收盤價快取消費端)都到位後才整合,刻意不做半套。`app/main.py` 尚未掛上這個 router。

### 1.3 OAuth 與試算表建立(✅ 5/5,`app/services/oauth_service.py`、`sheets_client.py`)

| 狀態 | 項目 | 備註 |
|---|---|---|
| ✅ | Google OAuth 2.0 授權流程 | `oauth_service.build_authorization_url()`(state 帶 LINE user ID 防 CSRF)+ `exchange_code_for_credentials()`。**還沒掛 `/oauth/callback` 路由**,屬之後 `app/routers/` 那輪要做的事,這裡先做完可單獨測試的業務邏輯 |
| ✅ | 自動複製範本進親友個人 Drive | `sheets_client.copy_template_to_drive()`,用親友自己的 OAuth 授權呼叫 Drive API。⚠️ scope 用完整 `drive`(非 `drive.file`),理由見 `openspecs/DE.md` 18:55 區塊;範本本身(分頁結構/資料驗證/版本號標記)需手動在 Drive 建立一次,檔案 ID 填入新增的 `GOOGLE_SHEETS_TEMPLATE_ID`,目前仍空白 |
| ✅ | Firestore 寫入對照表 | `oauth_service.link_friend_account()` 串起換 token → 複製範本 → 加密 refresh token → 寫入 `friends/{line_user_id}`,欄位與 schema 一致 |
| ✅ | OAuth 失效偵測(`invalid_grant`) | `oauth_service.refresh_or_raise()` 捕捉 `RefreshError` 包成 `OAuthInvalidGrantError`;`mark_needs_reauth()`(現搬到 `app/services/friend_repository.py`,理由見下方 1.6)標記 Firestore 狀態。實際呼叫點已在 1.6 `sheets_client.resync()` 串接完成 |
| ✅ | 親友刪除試算表(Sheets API 404) | `resync()` 讀取試算表時捕捉 `HttpError(404)`,跟 `invalid_grant` 共用同一套 `mark_needs_reauth()` 復原流程 |

### 1.4 記帳文字解析(✅ 6/7,`app/services/parser.py`)

| 狀態 | 項目 | 備註 |
|---|---|---|
| ✅ | 固定欄位順序解析 | `買/賣 個股 數量 金額` |
| ✅ | 三種輸入形式統一規則 | 數量+金額反推單價 / 僅金額用收盤價反推估算股數,反推單價統一取小數 2 位、`ROUND_HALF_UP`,成本計算永遠以原始輸入總金額為準。⚠️「僅金額」需注入 `closing_price_lookup` callable 才能運作,目前無收盤價來源(TWSE/TPEx 串接屬 1.9),沒有注入時回報該行解析失敗,不會猜測 |
| ✅ | 股息/配股格式解析 | `股息 個股 金額`、`配股 個股 股數` |
| ✅ | 換行批次記帳 | 一次解析多筆,部分行解析失敗只回該行錯誤(含行號),不整批作廢(實際 batch 寫入 Sheets 屬 1.6) |
| ✅ | 股票代碼/名稱模糊比對 | `app/services/fuzzy_match.py`:`resolve_stock()`,代碼精確比對(O(1) 字典)優先,查不到才 fallback `thefuzz`(`process.extractOne`,門檻分數 70)。股票清單由呼叫端注入(不在這支自己打 API),實際清單來源見下方 `market_data_client.py` |
| ⬜ | 多帳戶記帳前選擇 | 單帳戶不問;多帳戶且訊息無標籤 → Quick Reply 選單(批次只問一次);已標籤則跳過詢問。parser 已能解析 `個人/買入...` 標籤,Quick Reply 互動邏輯屬 `line_webhook.py` |
| ✅ | 解析失敗時引導重新輸入 | 不做猜測性寫入,parser 對每種格式錯誤都回報明確中文原因,供之後 webhook 直接組訊息回覆 |

> ⚠️ 開發本項時提前建好 `app/services/market_data_client.py`(規格 6.1,`fetch_stock_list()` 合併 TWSE `STOCK_DAY_ALL` + TPEx `tpex_mainboard_daily_close_quotes` 兩個 OpenAPI,回傳代碼/名稱/收盤價)。原規劃在 1.9 才做,因為 `fuzzy_match.py` 跟 1.9 的收盤價需求其實是同一份清單,選擇現在一次建好,1.9 屆時只需呼叫 + 補上快取與排程判斷,不需重新設計資料來源。

### 1.5 損益引擎(✅ 4/4,`app/services/pnl_engine.py`)

| 狀態 | 項目 | 備註 |
|---|---|---|
| ✅ | 移動加權平均成本法 | 買進/賣出/配股/股息的均價重新計算公式,金額計算全面使用 `Decimal` |
| ✅ | 已實現損益 | 每次賣出立即結算(不等股數歸零);股數歸零後的歷史封存標記屬 1.6 resync 呈現邏輯,尚未實作 |
| ✅ | 未實現損益 | 依每日收盤價動態計算當前庫存浮動損益(`compute_unrealized_pnl`),收盤價來源仍待 1.9 |
| ✅ | 賣超防呆 | 以單一帳戶為準檢查庫存,超賣擋下不寫入並提示(`InsufficientPositionError`);試算表手動輸入路徑由 resync 檢查屬 1.6,尚未實作 |

### 1.6 試算表 resync(✅ 2/3,`app/services/sheets_client.py`)

| 狀態 | 項目 | 備註 |
|---|---|---|
| ✅ | resync 核心邏輯 | `resync_account_tab()`(純函式,不打外部 API,模糊比對解析股票 + `pnl_engine` 重算損益)+ `resync()`(I/O 整合:讀 Sheet → 寫狀態欄 → 全部分頁寫回成功後才整批覆寫 Firestore `account_tabs_cache`)。OAuth 失效/Sheets 404 都標記 `mark_needs_reauth()` 並往上拋例外 |
| ⬜ | 三種觸發來源串接同一段邏輯 | `resync()` 本身已是單一可重用函式,14:30 排程(`tick.py` 逐位親友迴圈)、LIFF 開啟時(`liff.py` 的 `GET /liff/summary`)都已接上;僅剩操作面板「立即同步」按鈕還沒建(屬 1.8) |
| ✅ | 自動掃描分頁註冊帳戶 | 依欄位標題列結構(`row_uuid`/日期/動作/股票代碼名稱/數量/金額/狀態 七個表頭都要有)辨認帳戶分頁,`account_tabs_cache` 每次整批覆寫(`friend_repository.update_account_tabs_cache()`) |

> 額外完成:把 `oauth_service.py` 裡跟「親友狀態 CRUD」相關的 4 個函式(`get_friend_record`/`reactivate_friend`/`deactivate_friend`/`mark_needs_reauth`)搬到新建的 `app/services/friend_repository.py`——因為 `sheets_client.resync()` 需要 `oauth_service` 的憑證函式,而 `oauth_service.link_friend_account()` 原本在模組層級 import `sheets_client.copy_template_to_drive`,兩邊互相 import 會循環依賴。拆出 `friend_repository.py` 後,`oauth_service.py` 改成在 `link_friend_account()` 函式內延遲 import `sheets_client`,兩個方向都不會在模組載入時互相卡住。`line_webhook.py` 的對應 import 已同步更新,行為不變。新增 `app/models/schemas.py` 的 `AccountResyncResult`/`ResyncResult`(resync 的回傳型別,不額外持久化)。

### 1.7 防呆撤銷與查詢(⬜ 0/2)

| 狀態 | 項目 | 備註 |
|---|---|---|
| ⬜ | 記帳成功後 Quick Reply 刪除上一筆 | `[❌ 刪除上一筆]`,5 分鐘內有效,逾時失效 |
| ⬜ | LINE 直接查看(Rich Menu 觸發) | 回覆目前庫存、未實現損益、已實現損益、總資產;多帳戶預設列出全部帳戶各自摘要(不跨帳戶加總) |

### 1.8 首次使用引導與操作面板(⬜ 0/4)

| 狀態 | 項目 | 備註 |
|---|---|---|
| ⬜ | 首次加好友說明訊息 | 用 `follow` 事件的 `replyToken` 回覆(Flex Message),**不可用 push** 避免占用推播額度 |
| ⬜ | Rich Menu「使用說明」按鈕 | 隨時可再看一次規則說明 |
| ⬜ | 免責聲明 | 僅供個人記帳參考,非正式對帳/報稅依據 |
| ⬜ | 試算表「操作面板」分頁 | Apps Script 按鈕 + `UrlFetchApp.fetch()`,MVP 提供「立即同步」按鈕 |

### 1.9 排程(✅ 3/3,`app/routers/tick.py`)

| 狀態 | 項目 | 備註 |
|---|---|---|
| ✅ | 單一外部 Cron 進入點 `/tick` | 共享密鑰驗證(`X-Tick-Secret` header,沒設定一律 401 fail closed)→ 收到請求立即回應 200 OK,實際重任務丟 `BackgroundTasks` 背景執行 |
| ✅ | 內部判斷是否已過今日 14:30 | Asia/Taipei,且 Firestore `system/scheduler` 是否已記錄「今日已執行」(`SchedulerState.last_run_date`) |
| ✅ | 14:30 收盤任務 | 抓 TWSE/TPEx 收盤價與代碼清單 → 存進模組層級快取 → 逐位 `friend_repository.list_active_friends()` 查出的 active 親友依序呼叫 `sheets_client.resync()`(間隔 `FRIEND_RESYNC_INTERVAL_SECONDS`=2 秒避免撞 Sheets API 共用配額)→ 標記今日已執行。單一親友撞到 OAuth 失效/Sheets 404(`resync()` 內已標記 `needs_reauth`)時跳過繼續下一位;其他未預期例外則直接往上拋,中斷本輪背景任務(今日執行旗標不會被標記,下次 `/tick` 會整批重來,resync 是 cache-aside 設計,重做安全) |

### 1.10 共用通知元件(⬜ 0/4,`app/services/notify_service.py`)

| 狀態 | 項目 | 備註 |
|---|---|---|
| ⬜ | 共用 LINE 推播元件 | 同時服務:親友 OAuth 失效通知、管理員系統通知(`ADMIN_LINE_USER_ID`) |
| ⬜ | 排程任務失敗通知管理員 | 收盤價/代碼同步出錯、API 大量報錯 |
| ⬜ | 新親友首次 OAuth 連結通知管理員 | 僅一次 |
| ⬜ | 親友日常操作不通知管理員 | 記帳/查詢/開啟 LIFF 皆不通知,避免變相監看 |

### 1.11 LIFF 網頁(✅ 2/2,`app/routers/liff.py`)

| 狀態 | 項目 | 備註 |
|---|---|---|
| ✅ | LIFF SDK `id_token` 驗證身分 | `verify_liff_id_token()` 呼叫 LINE 官方 `oauth2/v2.1/verify` 端點,身分一律以驗證後拿到的 `sub`(LINE user ID)為準,絕對不從 URL 參數取——硬性安全要求(ADR-009)。新增 `LINE_LOGIN_CHANNEL_ID` 設定,建立步驟見 `Instruction/cloud_setting.md` 第 12 節(目前仍空白,屬之後雲端設定待辦) |
| ✅ | MVP 範圍頁面 | `GET /liff/summary`:登入連結狀態(`linked`)、`status`(`active`/`needs_reauth`,已連結但 OAuth 失效時不嘗試 resync,直接回報需重新連結)、目前庫存列表 + 簡單損益顯示(即時呼叫 1.6 `resync()`,確保資料最新,不是排程留下的舊快取;`unrealized_pnl` 用 1.9 `tick.py` 的 `get_cached_stock_list()` 收盤價計算)。⚠️ 只做了資料查詢 API,實際的 HTML/Tailwind 前端頁面本身規格只要求「資料查詢 API」(`liff.py` 的職責),前端頁面留待之後決定要不要自己做 |

> 額外完成:`app/models/schemas.py` 新增 `PositionSummary`/`AccountSummary`/`LiffSummaryResponse`(LIFF 回應的展示用型別,補上 `resync()` 沒有的股票名稱/收盤價/未實現損益,跟 `Position`/`ResyncResult` 這兩個內部計算型別分開,避免把展示邏輯混進核心計算)。`resync()` 因此多了第二個真實呼叫端,1.6 第二項「三種觸發來源」升級成 2/3(LIFF + 14:30 排程都已接上,僅剩操作面板「立即同步」按鈕)。

### 1.12 溫度感文案(⬜ 0/2,Phase 1 即落實)

| 狀態 | 項目 | 備註 |
|---|---|---|
| ⬜ | 機器人回覆採朋友語氣 | 例如「好的,幫你記下囉 📝」取代「已記錄」 |
| ⬜ | 個人化稱呼 | 取得親友 LINE 顯示名稱,訊息中個人化帶入名字 |

### 1.13 測試(✅ 10/10)

| 狀態 | 項目 | 備註 |
|---|---|---|
| ✅ | `tests/test_parser.py` | 12 案例 |
| ✅ | `tests/test_pnl_engine.py` | 10 案例 |
| ✅ | `tests/test_fuzzy_match.py` | 5 案例(精確代碼、精確名稱、模糊比對、查無此股票、空白輸入) |
| ✅ | `tests/test_market_data_client.py` | 3 案例(TWSE/TPEx 個別解析、`--` 收盤價轉 `None`、合併清單),用 `httpx.MockTransport` 隔離真實網路請求 |
| ✅ | `tests/test_oauth_service.py` | 6 案例,settings 用 monkeypatch 注入假值,不依賴本機真實 `.env` 內容,也不打真實 Google API |
| ✅ | `tests/test_friend_repository.py` | 8 案例(`get_friend_record`/`reactivate_friend`/`deactivate_friend`/`mark_needs_reauth`/`update_account_tabs_cache`/`list_active_friends`),純 mock Firestore client |
| ✅ | `tests/test_sheets_client.py` | 23 案例:`copy_template_to_drive`(1)、`map_header_columns`(2)、`resync_account_tab` 純函式核心邏輯(9,含買賣/股息/配股/賣超/無法辨識股票/格式錯誤/空白行/多股票)、`_column_letter`(5)、`_write_status_column`(2)、`resync()` 整合(3,含 OAuth 失效與 Sheets 404 兩個復原路徑) |
| ✅ | `tests/test_line_webhook.py` | 9 案例(簽章驗證、群組訊息忽略、事件去重、follow/unfollow、未連結親友回覆 OAuth 連結、已連結親友不回應),monkeypatch 假掉 Firestore 與 LINE Messaging API,不打真實服務 |
| ✅ | `tests/test_tick.py` | 13 案例(共享密鑰驗證、background task 排程、`SchedulerState` 讀取、14:30 時間判斷、今日已執行防呆、收盤任務正常執行並寫快取、逐位親友 resync 含間隔、OAuth 失效/Sheets 404 跳過繼續、未預期例外往上拋且不標記今日已執行),`now`/`firestore_client`/`fetch_stock_list_fn`/`list_friends_fn`/`resync_fn`/`sleep_fn` 皆可注入,不依賴真實系統時間或外部 API |
| ✅ | `tests/test_liff.py` | 12 案例:`verify_liff_id_token` 用 `httpx.MockTransport` 隔離真實 LINE API(2)、`_extract_bearer_token`(2)、`GET /liff/summary` 整合(8,含缺 header/非 Bearer/id_token 失效/未連結/已知需重新連結/正常算出未實現損益/resync 途中撞 OAuth 失效或 Sheets 404 都會回報需重新連結) |

---

## Phase 2 — 多帳戶與互動優化(⬜ 未開始)

| 狀態 | 項目 |
|---|---|
| ⬜ | 多帳戶(多分頁)管理:LIFF/操作面板「新增帳戶」按鈕(後端同一動作) |
| ⬜ | 試算表手動新增分頁辨識(依標題列結構自動註冊) |
| ⬜ | 匯入舊試算表(換綁帳號資料搬移) |
| ⬜ | 常用股票快捷與「重複上一筆」Quick Reply |
| ⬜ | 今日紀錄總表 + 選擇刪除任一筆(依隱藏 UUID 精準刪除) |
| ⬜ | 通用廣播功能(管理員觸發,固定內容) |
| ⬜ | 里程碑通知(首次記帳、週年、累積記帳次數門檻) |
| ⬜ | 主動關懷(長時間未互動低頻友善提醒) |

## Phase 3 — 前端視覺化強化(⬜ 未開始)

| 狀態 | 項目 |
|---|---|
| ⬜ | 複選帳戶動態加總(Checkbox 篩選器 + 即時融合計算) |
| ⬜ | 個股履歷表 Drill-down(跨帳戶持有比例、進出時間軸、勝率統計) |
| ⬜ | 整體資產歷史趨勢圖 |
| ⬜ | 電腦端懸浮記帳 Modal |
| ⬜ | 定期小結推送(月報/年報) |

## Phase 4 — 報表匯出(⬜ 未開始)

| 狀態 | 項目 |
|---|---|
| ⬜ | 一鍵匯出 Excel(`pandas` / `openpyxl` 排版) |

## Phase 5 — 手續費/證交稅精算(⬜ 未開始)

| 狀態 | 項目 |
|---|---|
| ⬜ | 手續費折數來源設計(自訂折數) |
| ⬜ | 低消門檻預設值設計 |
| ⬜ | ⚠️ 細節尚待確認(見下方「尚待確認事項」) |

## Phase 6 — 照片 AI 記帳(⬜ 未開始)

| 狀態 | 項目 |
|---|---|
| ⬜ | Gemini Vision API 整合(唯一會產生費用的外部服務) |
| ⬜ | 照片記帳 OCR 解析流程設計 |

## Phase 7 — 市場情報擴充功能(⬜ 未開始)

| 狀態 | 項目 |
|---|---|
| ⬜ | 除權息資訊(殖利率、股價、除權息時程) |
| ⬜ | 填息天數與填息成功率(需自行設計追蹤邏輯) |
| ⬜ | 警示股與處置股名單 |

## Phase 8 — 發想階段,尚未確定開發範疇

| 狀態 | 項目 |
|---|---|
| ⬜ | 依產業分類資產配置圖 |
| ⬜ | 損益卡片分享功能 |

---

## 明確排除(Out of Scope)

| 項目 | 原因 |
|---|---|
| 親友之間互相比較績效/排行榜 | 與「隱私優先」核心原則直接衝突,刻意不做 |
| 海外股票(美股等)支援 | 未排入任何階段 |
| 開放給 100 人以上的公開服務 | OAuth 測試模式上限與專案定位皆為親友規模工具,非公開 SaaS |

## 尚待確認事項

1. Phase 5 手續費精算細節(折數來源、低消門檻預設值)
2. 多帳戶合併彙總查看(LINE 查詢與 LIFF 網頁)的優化時程,規劃在 Phase 2 之後
3. 退出機制:親友解除連結時,Firestore 對照紀錄是否清除/標記,尚未設計

## 下次可從哪裡接續

0. 部署平台已改為 **Cloud Run + Cloud Scheduler**(ADR-021,取代 ADR-020 的 Render 方案)。GCP 專案(`stocktool-500502`)與 Cloud Run 服務已建立並部署成功,網址 `https://stocktool-22843182344.asia-southeast1.run.app`,健康檢查路由為 `/health`(不是 `/healthz`,原因見 `openspecs/debugging_notes.md`)。
1. Phase 0 雲端帳號設定:除 `ADMIN_LINE_USER_ID`(需先加自己官方帳號好友取得)外,使用者回報其餘項目皆已完成;本機檔案證據(`.env`、`secrets/`)可佐證 LINE/OAuth/Firestore/加密金鑰皆已就位。⚠️ Firestore 金鑰存放方式改用 Secret Manager(見上方「目前卡在」),Cloud Run 主控台的對應設定**尚未確認是否已完成**。
2. **18:01 完成(Phase 1)**:1.1 骨架(`app/config.py`、`app/models/schemas.py`、`app/db/firestore_client.py`)、1.4 `app/services/parser.py`(完整 4.2~4.7 規則,股票模糊比對留介面給尚未建立的 `fuzzy_match.py`)、1.5 `app/services/pnl_engine.py`(移動加權平均成本法、已實現/未實現損益、賣超防呆)、對應測試 `tests/test_parser.py` + `tests/test_pnl_engine.py`(22 案例全數通過)。`firestore_client.py` 改用 Secret Manager 後只靠 `GOOGLE_APPLICATION_CREDENTIALS` 檔案路徑取得憑證(Application Default Credentials 自動讀取),本機/Cloud Run 共用同一套邏輯,不需要分支判斷。順手修了 `docker-compose.yml` 缺少的 Firestore 金鑰 volume mount,以及 `technical_spec.md` 跟 `cloud_setting.md` 之間 LINE webhook 路徑(`/line/webhook`)的文件不一致。這幾項變更已於後續 commit(`0a265ac`、`d8a1e32`、`e40eeee`、`30c5633`、`4297a5c`)收斂完畢。
3. **18:40 完成(Phase 1)**:1.4 剩下的 `app/services/fuzzy_match.py`(`resolve_stock()`,代碼精確比對優先、`thefuzz` fallback,門檻分數 70)。額外提前建好 `app/services/market_data_client.py`(`fetch_stock_list()`,合併 TWSE `STOCK_DAY_ALL` + TPEx `tpex_mainboard_daily_close_quotes` 兩個官方 OpenAPI,回傳代碼/名稱/收盤價),原規劃在 1.9 才做,因為跟 1.4 模糊比對需要同一份清單而提前做掉。`app/models/schemas.py` 新增 `StockQuote` 模型。對應測試 `tests/test_fuzzy_match.py` + `tests/test_market_data_client.py`(後者用 `httpx.MockTransport` 隔離真實網路請求),全專案共 30 案例全數通過。這幾項變更已 commit(`4f68c76`),尚未 push。
4. **18:50 完成(Phase 1)**:1.3 OAuth 與試算表建立 4/5。`app/services/oauth_service.py`:`build_authorization_url()`/`exchange_code_for_credentials()`(OAuth 流程,state 帶 LINE user ID)、`encrypt_refresh_token()`/`decrypt_refresh_token()`(`cryptography.fernet`)、`refresh_or_raise()`(包 `RefreshError` 成 `OAuthInvalidGrantError`)、`mark_needs_reauth()`(Firestore 狀態標記,OAuth 失效跟 Drive 404 共用)、`link_friend_account()`(串起整段流程並寫入 Firestore)。`app/services/sheets_client.py`:`copy_template_to_drive()`(用親友自己的授權複製範本)。`app/config.py` 新增 `google_sheets_template_id` 欄位(目前空白,待手動建立範本試算表後填入)。對應測試 `tests/test_oauth_service.py`(7 案例,monkeypatch 假 settings,不依賴本機 `.env` 真實值)+ `tests/test_sheets_client.py`(1 案例),全專案共 38 案例全數通過。**還沒做**:`/oauth/callback` FastAPI 路由本身(留給之後建 `app/routers/` 那輪)、「親友刪除試算表 Drive 404」的實際偵測呼叫點(要等 1.6 resync 才有程式碼真的去呼叫已存在親友的試算表)。這幾項變更尚未 commit。
5. **19:00 完成(Phase 1)**:`app/routers/line_webhook.py`(1.2,5/5)。簽章驗證(`linebot.v3.webhook.WebhookParser`)、僅處理 1:1 私訊(group/room 過濾)、事件去重(process 記憶體,10 分鐘窗口)、follow/unfollow 事件處理(`oauth_service` 新增 `get_friend_record()`/`reactivate_friend()`/`deactivate_friend()`)。額外完成:未連結試算表的親友傳訊息時回覆 OAuth 連結引導。**刻意不做**:已連結親友的記帳文字解析→寫入流程(留給 1.4 多帳戶詢問/1.6 resync/1.9 收盤價快取都到位後整合),`app/main.py` 尚未掛這個 router。對應測試 `tests/test_line_webhook.py`(9 案例)。
6. **19:32 完成(Phase 1)**:`app/routers/tick.py`(1.9,2/3)。`/tick` 端點(共享密鑰驗證 `TICK_SHARED_SECRET`/`X-Tick-Secret` header,沒設定一律 401)→ 立即回 200,重任務丟 `BackgroundTasks`;`run_daily_close_task()` 判斷是否已過 14:30(Asia/Taipei)且 Firestore `system/scheduler` 今日尚未執行,是則呼叫 `market_data_client.fetch_stock_list()` 存進模組層級快取(`get_cached_stock_list()`)並標記今日已執行。身分驗證選共享密鑰而非 OIDC token 的理由見 `openspecs/DE.md` 19:32 區塊(風險等級評估後用 `AskUserQuestion` 確認,留了之後升級成 OIDC 的彈性)。**刻意不做**:逐位親友 resync(留給 1.6)。`Instruction/cloud_setting.md` 第 10 節同步把 Cloud Scheduler 設定從「Add OIDC token」改成自訂標頭 `X-Tick-Secret`,但**尚未確認使用者是否已在 Cloud Run/Cloud Scheduler 主控台同步更新**。對應測試 `tests/test_tick.py`(9 案例)+ `tests/test_oauth_service.py` 新增 4 案例。全專案累計 60 案例全數通過。這兩輪變更皆尚未 commit。
7. **19:48 完成(Phase 1)**:`app/services/sheets_client.resync()`(1.6,2/3)。拆成 `resync_account_tab()`(純函式核心邏輯:依序套用一個分頁的每列交易,模糊比對解析股票 + `pnl_engine` 重算損益,無法辨識/賣超的列只標記狀態不污染後續計算)+ `resync()`(I/O 整合:讀試算表所有分頁 → 依標題列結構辨認帳戶分頁 → 寫回狀態欄 → 全部分頁寫回成功後才整批覆寫 Firestore `account_tabs_cache`)。OAuth `invalid_grant` 跟 Sheets API 404(親友刪除試算表)都接上 `mark_needs_reauth()`,補完 1.3 當時留下的兩個呼叫點,1.3 因此升級成 5/5。**重構**:發現 `sheets_client.resync()` 需要 `oauth_service` 的憑證函式,但 `oauth_service.link_friend_account()` 原本在模組層級 import `sheets_client.copy_template_to_drive`,兩邊互相 import 會循環依賴——拆出 `app/services/friend_repository.py` 收斂「親友狀態 CRUD」(`get_friend_record`/`reactivate_friend`/`deactivate_friend`/`mark_needs_reauth`/新增的 `update_account_tabs_cache`),`oauth_service.py` 改成在函式內延遲 import `sheets_client`,兩個方向都不會在模組載入時互相卡住;`line_webhook.py` 的對應 import 已同步更新,行為不變。新增 `app/models/schemas.py` 的 `AccountResyncResult`/`ResyncResult`(resync 的回傳型別,不額外持久化,呼叫端用完即丟,下次需要再重新呼叫 resync 算一次)。**刻意不做**:LIFF/14:30 排程/操作面板「立即同步」按鈕這三個觸發來源還沒建,所以 `resync()` 目前還沒有任何程式碼路徑會真的呼叫到它。對應測試 `tests/test_sheets_client.py` 新增 22 案例 + 新建 `tests/test_friend_repository.py`(6 案例),全專案累計 83 案例全數通過。這三輪變更(line_webhook.py、tick.py、sheets_client.resync())皆尚未 commit。
8. **20:07 完成(Phase 1)**:把 1.9 `tick.py` 的 14:30 收盤任務跟 1.6 `resync()` 接上,1.9 因此升級 3/3。`run_daily_close_task()` 新增 `list_friends_fn`/`resync_fn`/`sleep_fn` 三個可注入參數:抓完收盤價清單後,用新增的 `friend_repository.list_active_friends()`(Firestore `where status == active` 查詢)拿出所有啟用中親友,逐位呼叫 `resync()`,中間用 `FRIEND_RESYNC_INTERVAL_SECONDS`(2 秒)隔開避免撞 Sheets API per-project 共用配額。單一親友撞到 `OAuthInvalidGrantError`/`HttpError`(`resync()` 內部已標記 `needs_reauth`)時跳過繼續下一位;其他未預期的例外直接往上拋,中斷本輪背景任務並讓「今日已執行」旗標不被標記(下次 `/tick` 會整批重來,resync 的 cache-aside 設計讓重做是安全的)。對應測試:`tests/test_tick.py` 新增 4 案例(逐位 resync 含間隔、OAuth 失效跳過、Sheets 404 跳過、未預期例外往上拋且不標記執行)、`tests/test_friend_repository.py` 新增 2 案例(`list_active_friends`)。全專案累計 89 案例全數通過。這四輪變更(line_webhook.py、tick.py、sheets_client.resync()、本次的 friend loop 接線)皆尚未 commit。
9. **20:17 完成(Phase 1)**:`app/routers/liff.py`(1.11,2/2)。`verify_liff_id_token()` 呼叫 LINE 官方 `oauth2/v2.1/verify` 端點驗證前端帶來的 `id_token`(`Authorization: Bearer` header),回傳的 `sub` 才是身分依據,絕對不從 URL 參數判斷(ADR-009)。`GET /liff/summary`:查無親友回傳 `linked: false`;已連結但 `status == needs_reauth` 直接回報,不嘗試 resync(省一次必然失敗的網路呼叫);其餘狀態即時呼叫 1.6 `resync()`(用 1.9 `tick.get_cached_stock_list()` 的收盤價計算未實現損益),resync 途中若撞到 OAuth 失效/Sheets 404 一樣回報需重新連結,不讓例外變成 500。新增 `app/models/schemas.py` 的 `PositionSummary`/`AccountSummary`/`LiffSummaryResponse`(展示用型別,跟 `Position`/`ResyncResult` 這兩個計算用型別分開)。`app/config.py` 新增 `line_login_channel_id`(目前空白,待手動建立 LINE Login channel/LIFF app 後填入,步驟見 `Instruction/cloud_setting.md` 第 12 節)。1.6 因此升級到「LIFF + 14:30 排程」兩個觸發來源都接上,僅剩操作面板按鈕。對應測試 `tests/test_liff.py`(12 案例,`verify_liff_id_token` 用 `httpx.MockTransport`,其餘 monkeypatch 假掉 Firestore/resync,不打真實服務),全專案累計 101 案例全數通過。這四輪變更(line_webhook.py、tick.py、sheets_client.resync()、friend loop 接線、liff.py)皆尚未 commit。
10. **下一步建議順序**:1.8 操作面板「立即同步」按鈕(Apps Script 呼叫後端 API,是 1.6 最後一個觸發來源,完成後 1.6 升級 3/3)→ 1.7 LINE 查詢/刪除上一筆。`app/main.py` 要等 webhook/liff/tick 路由都掛上後才會從 Phase 0 骨架升級成正式進入點——三個路由現在都已完成,可以考慮這次順便把 `app/main.py` 升級掉。
