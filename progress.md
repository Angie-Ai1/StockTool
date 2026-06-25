# 開發進度總覽 — LINE 股市零股記帳小工具

> 最後更新:2026-06-25 18:14

## 整體狀態快照

| 維度 | 比例 | 進度條 | 說明 |
|---|---|---|---|
| 規格設計(MVP / Phase 1) | 100% | `██████████` | 已逐項討論定案,含核心邏輯、隱私架構、可靠性機制 |
| 規格設計(Phase 2~8) | 約 70% | `███████░░░` | 功能範圍已定,部分細節(手續費折數規則等)待展開 |
| Phase 0 基礎設施建置 | 95%(18/19) | `█████████░` | 本機開發環境 100% 完成;雲端帳號設定僅缺 `ADMIN_LINE_USER_ID`(不含選用的帳單保險項目) |
| Phase 1 MVP 程式碼 | 29%(14/48) | `███░░░░░░░` | 已完成 1.1 部分骨架、1.4 `parser.py`、1.5 `pnl_engine.py`;1.2、1.3、1.6~1.12 尚未開始 |
| 測試覆蓋 | 67%(2/3) | `███████░░░` | `test_parser.py`、`test_pnl_engine.py` 共 22 案例全數通過;`test_oauth_service.py` 待 `oauth_service.py` 寫完才能寫 |
| 部署上線:基礎設施 | 100% | `██████████` | Cloud Run 服務運作中(僅 `/health`) |
| 部署上線:功能 | 0% | `░░░░░░░░░░` | webhook/OAuth/tick 等功能路由尚未實作上線 |

**目前卡在**:Phase 0 雲端帳號設定,除 `ADMIN_LINE_USER_ID`(需先加自己的官方帳號好友才能取得)外,其餘項目使用者回報皆已完成。本機 `.env`/`secrets/` 已用檔案證據比對確認 LINE Channel Secret/Token、Google OAuth Client、Firestore Service Account、加密金鑰皆已就位;Sheets/Drive API 啟用、OAuth Audience 發布狀態、LINE Rich Menu、Cloud Scheduler 屬純雲端主控台設定,本機環境無法直接驗證,採信使用者回報。⚠️ 待辦:Firestore 金鑰存放方式已改用 **Secret Manager 掛載為磁碟區**(取代直接貼 JSON 進環境變數,理由與步驟見 `Instruction/cloud_setting.md` 4.2 節、`openspecs/DE.md`),部署 Phase 1.1 `firestore_client.py` 前需在 Cloud Run 主控台完成這個設定:建立 Secret、把執行身分加上 Secret Accessor 角色、掛載到 `/secrets/firestore-service-account.json`,不需要也不應該再有 `FIRESTORE_SERVICE_ACCOUNT_JSON` 環境變數。

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

## Phase 1 — MVP 實作(🔄 29%,14/48 子項)

### 1.1 專案骨架(✅ 3/4)

| 狀態 | 項目 | 備註 |
|---|---|---|
| ⬜ | `app/main.py`:FastAPI 進入點 | 掛載 webhook / LIFF / tick 路由;目前仍是 Phase 0 最小骨架,只有 `/health` |
| ✅ | `app/config.py`:環境變數讀取 | `pydantic-settings`,已用本機 `.env` 驗證可正確讀到所有欄位 |
| ✅ | `app/models/schemas.py`:Pydantic 資料模型 | `FriendRecord`、`SchedulerState`、`TransactionRow`、`ParsedTransaction`、`ParseResult`、`Position` |
| ✅ | `app/db/firestore_client.py` | 改用 Secret Manager 掛載磁碟區提供憑證後,只需 `GOOGLE_APPLICATION_CREDENTIALS` 檔案路徑,本機/雲端共用同一套邏輯;未實際接 Cloud Run 測試 |

### 1.2 LINE Webhook 與事件處理(⬜ 0/5,`app/routers/line_webhook.py`)

| 狀態 | 項目 | 備註 |
|---|---|---|
| ⬜ | LINE Channel Secret 簽章驗證 | |
| ⬜ | 僅處理 1:1 私訊 | 群組/聊天室(group/room)訊息一律忽略不回應 |
| ⬜ | webhook 事件去重 | 短時間窗口 5~10 分鐘,依事件 ID,避免重複投遞造成重複記帳 |
| ⬜ | `follow` 事件處理 | 查 Firestore 判斷是否為回鍋舊親友,是則狀態改回「啟用」 |
| ⬜ | `unfollow` 事件處理 | 標記 Firestore 該親友為「已停用」,不刪除資料;後續主動推播邏輯先檢查此狀態,停用則跳過 |

### 1.3 OAuth 與試算表建立(⬜ 0/5,`app/services/oauth_service.py`、`sheets_client.py`)

| 狀態 | 項目 | 備註 |
|---|---|---|
| ⬜ | Google OAuth 2.0 授權流程 | `/oauth/callback`,state 參數攜帶 LINE user ID 防 CSRF |
| ⬜ | 自動複製範本進親友個人 Drive | 含分頁結構、欄位標題、資料驗證規則、版本號標記 |
| ⬜ | Firestore 寫入對照表 | `spreadsheet_id`、`encrypted_refresh_token`(加密儲存)、`account_tabs_cache`、`status` |
| ⬜ | OAuth 失效偵測(`invalid_grant`) | 標記 Firestore 狀態 `needs_reauth` → 主動推播通知親友重新連結 |
| ⬜ | 親友刪除試算表(Drive API 404) | 與 OAuth 失效採同一套復原流程 |

### 1.4 記帳文字解析(✅ 5/7,`app/services/parser.py`)

| 狀態 | 項目 | 備註 |
|---|---|---|
| ✅ | 固定欄位順序解析 | `買/賣 個股 數量 金額` |
| ✅ | 三種輸入形式統一規則 | 數量+金額反推單價 / 僅金額用收盤價反推估算股數,反推單價統一取小數 2 位、`ROUND_HALF_UP`,成本計算永遠以原始輸入總金額為準。⚠️「僅金額」需注入 `closing_price_lookup` callable 才能運作,目前無收盤價來源(TWSE/TPEx 串接屬 1.9),沒有注入時回報該行解析失敗,不會猜測 |
| ✅ | 股息/配股格式解析 | `股息 個股 金額`、`配股 個股 股數` |
| ✅ | 換行批次記帳 | 一次解析多筆,部分行解析失敗只回該行錯誤(含行號),不整批作廢(實際 batch 寫入 Sheets 屬 1.6) |
| ⬜ | 股票代碼/名稱模糊比對 | `app/services/fuzzy_match.py` 尚未建立;代碼精確比對(O(1) 字典)優先,查不到才 fallback `thefuzz`。parser 目前只回傳原始 `stock_query` 字串,留介面給這支 |
| ⬜ | 多帳戶記帳前選擇 | 單帳戶不問;多帳戶且訊息無標籤 → Quick Reply 選單(批次只問一次);已標籤則跳過詢問。parser 已能解析 `個人/買入...` 標籤,Quick Reply 互動邏輯屬 `line_webhook.py` |
| ✅ | 解析失敗時引導重新輸入 | 不做猜測性寫入,parser 對每種格式錯誤都回報明確中文原因,供之後 webhook 直接組訊息回覆 |

### 1.5 損益引擎(✅ 4/4,`app/services/pnl_engine.py`)

| 狀態 | 項目 | 備註 |
|---|---|---|
| ✅ | 移動加權平均成本法 | 買進/賣出/配股/股息的均價重新計算公式,金額計算全面使用 `Decimal` |
| ✅ | 已實現損益 | 每次賣出立即結算(不等股數歸零);股數歸零後的歷史封存標記屬 1.6 resync 呈現邏輯,尚未實作 |
| ✅ | 未實現損益 | 依每日收盤價動態計算當前庫存浮動損益(`compute_unrealized_pnl`),收盤價來源仍待 1.9 |
| ✅ | 賣超防呆 | 以單一帳戶為準檢查庫存,超賣擋下不寫入並提示(`InsufficientPositionError`);試算表手動輸入路徑由 resync 檢查屬 1.6,尚未實作 |

### 1.6 試算表 resync(⬜ 0/3,`app/services/sheets_client.py`)

| 狀態 | 項目 | 備註 |
|---|---|---|
| ⬜ | resync 核心邏輯 | 重新讀取 Sheet → 用模糊比對解析股票 → 重算損益 → 寫回快取(先寫 Sheet 成功才更新快取) |
| ⬜ | 三種觸發來源串接同一段邏輯 | LIFF 開啟時、14:30 排程、試算表操作面板「立即同步」按鈕 |
| ⬜ | 自動掃描分頁註冊帳戶 | 依欄位標題列結構辨認帳戶分頁,`account_tabs_cache` 每次整批覆寫,非局部增修 |

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

### 1.9 排程(⬜ 0/3,`app/routers/tick.py`)

| 狀態 | 項目 | 備註 |
|---|---|---|
| ⬜ | 單一外部 Cron 進入點 `/tick` | 收到請求立即回應 200 OK,實際重任務丟 `BackgroundTasks` 背景執行 |
| ⬜ | 內部判斷是否已過今日 14:30 | Asia/Taipei,且 Firestore 是否已記錄「今日已執行」 |
| ⬜ | 14:30 收盤任務 | 抓 TWSE/TPEx 收盤價與代碼清單 → 逐位親友依序 resync(留間隔避免撞 Sheets API 共用配額)→ 標記今日已執行 |

### 1.10 共用通知元件(⬜ 0/4,`app/services/notify_service.py`)

| 狀態 | 項目 | 備註 |
|---|---|---|
| ⬜ | 共用 LINE 推播元件 | 同時服務:親友 OAuth 失效通知、管理員系統通知(`ADMIN_LINE_USER_ID`) |
| ⬜ | 排程任務失敗通知管理員 | 收盤價/代碼同步出錯、API 大量報錯 |
| ⬜ | 新親友首次 OAuth 連結通知管理員 | 僅一次 |
| ⬜ | 親友日常操作不通知管理員 | 記帳/查詢/開啟 LIFF 皆不通知,避免變相監看 |

### 1.11 LIFF 網頁(⬜ 0/2,`app/routers/liff.py`)

| 狀態 | 項目 | 備註 |
|---|---|---|
| ⬜ | LIFF SDK `id_token` 驗證身分 | 硬性安全要求,不可用 URL 參數判斷身分 |
| ⬜ | MVP 範圍頁面 | 登入連結狀態、目前庫存列表、簡單損益顯示 |

### 1.12 溫度感文案(⬜ 0/2,Phase 1 即落實)

| 狀態 | 項目 | 備註 |
|---|---|---|
| ⬜ | 機器人回覆採朋友語氣 | 例如「好的,幫你記下囉 📝」取代「已記錄」 |
| ⬜ | 個人化稱呼 | 取得親友 LINE 顯示名稱,訊息中個人化帶入名字 |

### 1.13 測試(✅ 2/3)

| 狀態 | 項目 | 備註 |
|---|---|---|
| ✅ | `tests/test_parser.py` | 12 案例 |
| ✅ | `tests/test_pnl_engine.py` | 10 案例 |
| ⬜ | `tests/test_oauth_service.py` | 待 `oauth_service.py` 完成 |

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
2. **本次完成(Phase 1)**:1.1 骨架(`app/config.py`、`app/models/schemas.py`、`app/db/firestore_client.py`)、1.4 `app/services/parser.py`(完整 4.2~4.7 規則,股票模糊比對留介面給尚未建立的 `fuzzy_match.py`)、1.5 `app/services/pnl_engine.py`(移動加權平均成本法、已實現/未實現損益、賣超防呆)、對應測試 `tests/test_parser.py` + `tests/test_pnl_engine.py`(22 案例全數通過)。`firestore_client.py` 改用 Secret Manager 後只靠 `GOOGLE_APPLICATION_CREDENTIALS` 檔案路徑取得憑證(Application Default Credentials 自動讀取),本機/Cloud Run 共用同一套邏輯,不需要分支判斷。順手修了 `docker-compose.yml` 缺少的 Firestore 金鑰 volume mount,以及 `technical_spec.md` 跟 `cloud_setting.md` 之間 LINE webhook 路徑(`/line/webhook`)的文件不一致。這幾項變更(含 `app/main.py`、`progress.md` 既有未提交的修改)尚未 commit。
3. **下一步建議順序**:`app/services/fuzzy_match.py`(股票代碼/名稱比對,parser 已留好介面)→ `app/services/oauth_service.py` + `app/services/sheets_client.py`(需要實際 Google API 串接,會用到第 1 點確認過的雲端憑證)→ `app/routers/line_webhook.py` 把 parser/pnl_engine/fuzzy_match 串成完整訊息處理流程 → `app/routers/tick.py` → `app/routers/liff.py`。`app/main.py` 要等 webhook/liff/tick 路由都掛上後才會從 Phase 0 骨架升級成正式進入點。
