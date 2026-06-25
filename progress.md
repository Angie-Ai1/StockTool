# 開發進度總覽 — LINE 股市零股記帳小工具

> 最後更新:2026-06-25 10:17

## 整體狀態快照

| 維度 | 完整度 | 說明 |
|---|---|---|
| 規格設計(MVP / Phase 1) | 100% | 已逐項討論定案,含核心邏輯、隱私架構、可靠性機制 |
| 規格設計(Phase 2~8) | 約 70% | 功能範圍已定,部分細節(手續費折數規則等)待展開 |
| 程式碼實作 | 進行中 | Phase 0 本機可建置部分已完成(Poetry / Dockerfile / docker-compose / `.env.example`);應用程式邏輯(`app/services` 等)尚未開始 |
| 測試覆蓋 | 0% | 測試框架(pytest)已就位,尚無實際測試案例 |
| 部署上線 | 0% | 尚未建立任何雲端資源(GCP 專案、LINE Channel、Render 服務) |

**目前卡在**:Phase 0 中需要使用者本人帳號操作的雲端服務申請(GCP / LINE / Render / 外部 Cron)尚待使用者執行,Claude 無法代為登入第三方主控台完成。

---

## Phase 0 — 基礎設施建置(本機部分已完成,雲端帳號部分待使用者執行)

### 本機開發環境(已完成,2026-06-25)
- [x] 用 **Poetry** 初始化專案(`pyproject.toml` 鎖定 Python `^3.10`、`poetry.lock`),取代 `requirements.txt`
- [x] 依架構文件技術棧鎖定生產依賴:fastapi、uvicorn、pydantic/pydantic-settings、line-bot-sdk、google-api-python-client、google-auth、google-auth-oauthlib、google-cloud-firestore、cryptography、thefuzz、httpx
- [x] 依 `1.13 測試` 規劃新增 dev 依賴:pytest、pytest-asyncio
- [x] 建立 `.env.example`(LINE channel secret、Google OAuth client、Firestore 服務帳號金鑰路徑、加密金鑰、`ADMIN_LINE_USER_ID` 等欄位)
- [x] 建立 `Dockerfile`(`python:3.12-slim` + 容器內安裝 Poetry + `poetry install --only main`)與 `docker-compose.yml`
- [x] 建立 `app/main.py` 最小骨架(僅 `/healthz`,供驗證本機環境串接,Phase 1.1 會替換為正式進入點)
- [x] 已用 `poetry run uvicorn` 本機驗證 `/healthz` 回 200、`poetry run pytest` 可正常執行(0 個測試)
- [x] Docker Desktop WSL integration 已開啟,`docker compose build` + `up -d` 實測通過:容器內 `/healthz` 回 200、`--reload` 開發模式與 `.env` 環境變數注入皆正常,驗證後已 `docker compose down` 清理
- [x] `.gitignore` 補上 `.venv/`、`__pycache__/`、`.pytest_cache/`、`.env`(`.env.example` 維持進版控)

### 雲端帳號設定(待使用者執行,需登入個人帳號,Claude 無法代為操作)

> 詳細操作步驟、網址、填值對照表已整理成 `Instruction/cloud_setting.md`(本檔不進版控),照著做即可,下面只列勾選清單。

- [ ] 建立 Google Cloud 專案
- [ ] 啟用 Sheets API + Drive API
- [ ] 設定 OAuth 同意畫面(Google Auth Platform):⚠️ 已更正為 **In production + 不送驗證**(取代原計畫的測試模式,原因見 `openspecs/DE.md` 2026-06-25 09:59 區塊——測試模式會讓 refresh token 每 7 天強制失效)
- [ ] 產生並設定加密金鑰(如 `cryptography.fernet` key),存於 Render 環境變數,**不寫入程式碼/不進版控**
- [ ] 申請 LINE 官方帳號 + 啟用 Messaging API Channel,取得 Channel Secret / Access Token
- [ ] 設定 LINE Rich Menu 雛形(供 Phase 1 查詢/說明按鈕使用)
- [ ] 建立 Google Cloud Firestore(Native mode,`asia-southeast1`)+ 建立 Service Account 金鑰
- [ ] (選用)Firestore 帳單硬上限保險:預算 → Pub/Sub → Cloud Function 自動關閉 Billing,程式碼已備好於 `Instruction/billing-killswitch/`,步驟見 `cloud_setting.md` 第 4.3 節
- [ ] 建立 Render 服務(免費方案,**選 Singapore 機房**降低台灣延遲),確認當下實際免費額度規則(運行時數上限、LINE push 月配額)(部署平台決策見 ADR-020)
- [ ] 設定外部 Cron(如 cron-job.org),每 10 分鐘呼叫 `/tick`,**時區指定 Asia/Taipei**

---

## Phase 1 — MVP 實作(未開始)

### 1.1 專案骨架
- [ ] `app/main.py`:FastAPI 進入點,掛載 webhook / LIFF / tick 路由
- [ ] `app/config.py`:環境變數讀取
- [ ] `app/models/schemas.py`:Pydantic 資料模型
- [ ] `app/db/firestore_client.py`

### 1.2 LINE Webhook 與事件處理(`app/routers/line_webhook.py`)
- [ ] LINE Channel Secret 簽章驗證
- [ ] 僅處理 1:1 私訊(`message` 事件),群組/聊天室(group/room)訊息一律忽略不回應
- [ ] webhook 事件去重(短時間窗口 5~10 分鐘,依事件 ID,避免重複投遞造成重複記帳)
- [ ] `follow` 事件處理:查 Firestore 判斷是否為回鍋舊親友,是則狀態改回「啟用」
- [ ] `unfollow` 事件處理:標記 Firestore 該親友為「已停用」,不刪除資料;後續所有主動推播邏輯先檢查此狀態,停用則跳過

### 1.3 OAuth 與試算表建立(`app/services/oauth_service.py`、`sheets_client.py`)
- [ ] Google OAuth 2.0 授權流程(`/oauth/callback`,state 參數攜帶 LINE user ID 防 CSRF)
- [ ] 親友完成 OAuth 後,自動把範本複製進其個人 Drive(含分頁結構、欄位標題、資料驗證規則、版本號標記)
- [ ] Firestore 寫入對照表:`spreadsheet_id`、`encrypted_refresh_token`(加密儲存)、`account_tabs_cache`、`status`
- [ ] OAuth 失效偵測(`invalid_grant`)→ 標記 Firestore 狀態 `needs_reauth` → 主動推播通知親友重新連結
- [ ] 親友刪除試算表(Drive API 404)→ 與 OAuth 失效採同一套復原流程

### 1.4 記帳文字解析(`app/services/parser.py`)
- [ ] 固定欄位順序解析:`買/賣 個股 數量 金額`
- [ ] 三種輸入形式統一規則(數量+金額反推單價 / 僅金額用收盤價反推估算股數),反推單價統一取小數 2 位、`ROUND_HALF_UP`,但成本計算永遠以原始輸入總金額為準
- [ ] 股息格式解析(`股息 個股 金額`)、配股格式解析(`配股 個股 股數`)
- [ ] 換行批次記帳:一次解析多筆,batch 寫入,部分行解析失敗只回覆該行錯誤,不整批作廢
- [ ] 股票代碼/名稱模糊比對(`app/services/fuzzy_match.py`):代碼精確比對(O(1) 字典)優先,查不到才 fallback `thefuzz`
- [ ] 多帳戶記帳前選擇:單帳戶不問;多帳戶且訊息無標籤 → Quick Reply 選單(批次只問一次);訊息已標籤帳戶則跳過詢問
- [ ] 解析失敗/信心不足時回覆引導重新輸入,不做猜測性寫入

### 1.5 損益引擎(`app/services/pnl_engine.py`)
- [ ] 移動加權平均成本法(買進/賣出/配股/股息的均價重新計算公式),金額計算全面使用 `Decimal`
- [ ] 已實現損益:每次賣出立即結算(不等股數歸零),股數歸零僅作歷史封存標記
- [ ] 未實現損益:依每日收盤價動態計算當前庫存浮動損益
- [ ] 賣超防呆:以單一帳戶為準檢查庫存,超賣擋下不寫入並提示;試算表手動輸入路徑由 resync 檢查,異常標記狀態欄,不計入損益

### 1.6 試算表 resync(`app/services/sheets_client.py`)
- [ ] resync 核心邏輯:重新讀取 Sheet → 用模糊比對解析股票 → 重算損益 → 寫回快取(先寫 Sheet 成功才更新快取)
- [ ] 三種觸發來源串接同一段 resync 邏輯:LIFF 開啟時、14:30 排程、試算表操作面板「立即同步」按鈕
- [ ] resync 時掃描所有分頁,依欄位標題列結構辨認帳戶分頁,自動註冊(`account_tabs_cache` 每次整批覆寫,非局部增修)

### 1.7 防呆撤銷與查詢
- [ ] 記帳成功後 Quick Reply `[❌ 刪除上一筆]`,5 分鐘內有效,逾時失效
- [ ] LINE 直接查看(Rich Menu 觸發):回覆目前庫存、未實現損益、已實現損益、總資產;多帳戶預設列出全部帳戶各自摘要(不跨帳戶加總)

### 1.8 首次使用引導與操作面板
- [ ] 首次加好友:用 `follow` 事件的 `replyToken` 回覆說明訊息(Flex Message),**不可用 push** 避免占用推播額度
- [ ] Rich Menu「使用說明」按鈕,隨時可再看一次規則說明
- [ ] 說明訊息附加免責聲明(僅供個人記帳參考,非正式對帳/報稅依據)
- [ ] 試算表內建「操作面板」分頁(Apps Script 按鈕 + `UrlFetchApp.fetch()`):MVP 提供「立即同步」按鈕

### 1.9 排程(`app/routers/tick.py`)
- [ ] 單一外部 Cron 進入點 `/tick`:收到請求立即回應 200 OK,實際重任務丟 `BackgroundTasks` 背景執行
- [ ] 內部判斷現在時間(Asia/Taipei)是否已過今日 14:30,且 Firestore 是否已記錄「今日已執行」
- [ ] 14:30 收盤任務:抓 TWSE/TPEx 收盤價與代碼清單 → 逐位親友依序 resync(留間隔,避免撞 Sheets API per-project 共用配額約 300 次/分鐘)→ 標記今日已執行

### 1.10 共用通知元件(`app/services/notify_service.py`)
- [ ] 共用 LINE 推播元件,同時服務:親友 OAuth 失效通知、管理員系統通知(`ADMIN_LINE_USER_ID` 環境變數設定)
- [ ] 排程任務失敗(收盤價/代碼同步出錯、API 大量報錯)→ 通知管理員
- [ ] 新親友完成首次 OAuth 連結 → 通知管理員(僅一次)
- [ ] 親友日常記帳/查詢/開啟 LIFF → 不通知管理員(避免變相監看)

### 1.11 LIFF 網頁(`app/routers/liff.py`)
- [ ] **LIFF SDK `id_token` 驗證身分**(硬性安全要求,不可用 URL 參數判斷身分)
- [ ] MVP 範圍:登入連結狀態、目前庫存列表、簡單損益顯示

### 1.12 溫度感文案(Phase 1 即落實)
- [ ] 所有機器人回覆採朋友語氣(例如「好的,幫你記下囉 📝」取代「已記錄」)
- [ ] 取得親友 LINE 顯示名稱,訊息中個人化帶入名字

### 1.13 測試
- [ ] `tests/test_parser.py`
- [ ] `tests/test_pnl_engine.py`
- [ ] `tests/test_oauth_service.py`

---

## Phase 2 — 多帳戶與互動優化(未開始)

- [ ] 多帳戶(多分頁)管理:LIFF「新增帳戶」按鈕、試算表操作面板「新增帳戶」按鈕(後端同一動作)
- [ ] 試算表手動新增分頁辨識(依標題列結構自動註冊)
- [ ] 匯入舊試算表(換綁帳號資料搬移):親友提供舊表連結/分享權限,後端讀取寫入新分頁,分頁名稱對不上時提示確認
- [ ] 常用股票快捷與「重複上一筆」Quick Reply
- [ ] 今日紀錄總表(Flex Message 卡片列出今天所有交易)+ 選擇刪除任一筆(依隱藏 UUID 精準刪除,不限最後一筆、不限 5 分鐘)
- [ ] 通用廣播功能(管理員觸發,固定內容,遍歷 Firestore 啟用親友清單發送,不存取試算表資料)
- [ ] 里程碑通知(首次記帳、使用滿週年、累積記帳次數達門檻如第 50/100 筆)
- [ ] 主動關懷(親友長時間未互動如 2~3 個月,低頻友善提醒,避免「被監看」感)

## Phase 3 — 前端視覺化強化(未開始)

- [ ] 複選帳戶動態加總(Checkbox 篩選器 + 即時融合計算)
- [ ] 個股履歷表 Drill-down(跨帳戶持有比例、進出時間軸、勝率統計)
- [ ] 整體資產歷史趨勢圖
- [ ] 電腦端懸浮記帳 Modal
- [ ] 定期小結推送(月報/年報,需先有彙總計算邏輯與歷史趨勢資料)

## Phase 4 — 報表匯出(未開始)

- [ ] 一鍵匯出 Excel(`pandas` / `openpyxl` 排版)

## Phase 5 — 手續費/證交稅精算(未開始)

- [ ] 手續費折數來源設計(自訂折數)
- [ ] 低消門檻預設值設計
- [ ] ⚠️ 細節尚待確認(見下方「尚待確認事項」)

## Phase 6 — 照片 AI 記帳(未開始)

- [ ] Gemini Vision API 整合(唯一會產生費用的外部服務)
- [ ] 照片記帳 OCR 解析流程設計

## Phase 7 — 市場情報擴充功能(未開始)

- [ ] 除權息資訊(殖利率、股價、除權息時程,取自 TWSE/TPEx 除權息預告資料集)
- [ ] 填息天數與填息成功率(無現成 API,需自行設計追蹤邏輯,掛在既有 14:30 排程逐日累積計算)
- [ ] 警示股與處置股名單(取自 TWSE/TPEx 注意交易資訊/處置股票資料集,欄位格式待實際查看確認)

## Phase 8 — 發想階段,尚未確定開發範疇

- [ ] 依產業分類資產配置圖(TWSE 開放資料已有產業類別欄位)
- [ ] 損益卡片分享功能(分享與否由親友自行選擇)

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

0. 本機這批 Phase 0 變更已 commit(`e5db12c`),但 `git push origin main` 在這個沙箱環境失敗(沒有 GitHub 認證)。需要使用者自己在已登入 GitHub 的終端機/IDE 執行 `git push origin main`。
1. **使用者本人**依 `Instruction/cloud_setting.md` 完成 Phase 0 剩下唯一的區塊:雲端帳號設定(GCP 專案、OAuth 同意畫面、LINE Channel、Firestore、Render 服務、外部 Cron,選用的 Firestore 帳單硬上限保險)。本機開發環境(Poetry/Docker)已全部驗證完成。
2. Phase 0 雲端帳號到位後接續 **Phase 1**,建議依架構文件的模組順序實作:先 `parser.py` + `pnl_engine.py`(純邏輯、易單元測試)→ `oauth_service.py` + `sheets_client.py` → `line_webhook.py` 串接 → `tick.py` 排程 → `liff.py`。
