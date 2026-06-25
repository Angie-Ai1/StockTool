# 開發進度 — LINE 股市零股記帳小工具

> 最後更新：2026-06-25 23:40

## Phase 0 — 基礎設施建置（🔄 95%）

| 狀態 | 項目 |
|---|---|
| ✅ | 本機開發環境（Poetry、依賴、`.env.example`、Dockerfile、`app/main.py` 最小骨架、本機驗證） |
| ✅ | 雲端帳號設定（GCP 專案、Sheets/Drive API、OAuth 同意畫面、LINE 官方帳號、Firestore、Cloud Run、Cloud Scheduler） |
| ⬜ | `ADMIN_LINE_USER_ID`（需先加自己的官方帳號好友後取得） |

## Phase 1 — MVP 實作（🔄 67%，32/48 子項）

| 狀態 | 項目 |
|---|---|
| 🔄 3/4 | **1.1 專案骨架**：`app/config.py`、`app/models/schemas.py`、`app/db/firestore_client.py` ✅；`app/main.py` 掛載所有路由 ⬜ |
| ✅ 5/5 | **1.2 LINE Webhook**：簽章驗證、1:1 私訊過濾、事件去重、follow/unfollow 處理 |
| ✅ 5/5 | **1.3 OAuth 與試算表建立**：Google OAuth 流程、複製範本到親友 Drive、Firestore 寫入對照表、OAuth 失效偵測、Sheets 404 處理 |
| 🔄 6/7 | **1.4 記帳文字解析**：固定欄位解析、三種輸入形式、股息/配股格式、批次記帳、模糊比對 ✅；多帳戶選擇 Quick Reply ⬜ |
| ✅ 4/4 | **1.5 損益引擎**：移動加權平均成本法、已實現損益、未實現損益、賣超防呆 |
| 🔄 2/3 | **1.6 試算表 resync**：核心邏輯 + 14:30 排程/LIFF 觸發 ✅；操作面板「立即同步」按鈕 ⬜ |
| ⬜ 0/2 | **1.7 防呆撤銷與查詢**：Quick Reply 刪除上一筆、LINE 直接查看庫存／損益 |
| ⬜ 0/4 | **1.8 首次使用引導與操作面板**：加好友說明訊息、使用說明按鈕、免責聲明、試算表「立即同步」按鈕 |
| ✅ 3/3 | **1.9 排程**：`/tick` 共享密鑰驗證、14:30 時間判斷、收盤任務（抓價格 + 逐位親友 resync） |
| ⬜ 0/4 | **1.10 共用通知元件**：LINE 推播元件、排程失敗通知管理員、新親友連結通知管理員 |
| ✅ 2/2 | **1.11 LIFF 網頁**：`id_token` 身分驗證、`GET /liff/summary` 資料查詢 API |
| ⬜ 0/2 | **1.12 溫度感文案**：朋友語氣回覆、個人化稱呼 |
| ✅ 10/10 | **1.13 測試**：101 案例全數通過，皆不依賴真實 `.env`／外部 API |

## 下次可從哪裡接續

1. **`app/main.py` 升級**：將已完成的 `line_webhook` / `liff` / `tick` 三個 router 掛上，從 Phase 0 最小骨架升級成正式 FastAPI 進入點
2. **1.8 操作面板「立即同步」按鈕**：Apps Script + `UrlFetchApp.fetch()` 呼叫後端，完成後 1.6 三個觸發來源全部到位
3. **1.7 防呆撤銷與查詢**：記帳成功後 Quick Reply 刪除上一筆（5 分鐘內有效）+ LINE 直接查看庫存／損益
4. **雲端設定補完**：Firestore Secret Manager 掛載、`TICK_SHARED_SECRET` + Cloud Scheduler 標頭更新、`LINE_LOGIN_CHANNEL_ID`（需建 LINE Login channel + LIFF app）
