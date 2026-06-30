# LINE 股市記帳小工具 — 開發進度

> 最後更新：2026-06-30 18:36

## 整體進度

| 維度 | 比例 | 說明 |
|---|---|---|
| 規格設計（Phase 1 MVP） | 100% | 已定案 |
| 規格設計（Phase 2~8） | 約 70% | 功能範圍已定，部分細節待展開 |
| Phase 0 基礎設施 | 97% | 僅缺 `ADMIN_LINE_USER_ID` |
| Phase 1 MVP 程式碼 | ~93%（約 45/48） | 1.8 完成、1.11 儀表板上線、1.10/1.12 未開始 |
| 部署上線：基礎設施 | 100% | Cloud Run + Cloud Build CD 正常 |
| 部署上線：功能 | ~95% | 記帳/刪除/查詢/同步/LIFF 儀表板端對端驗證通過；待設 APP_BASE_URL / LIFF_DASHBOARD_URL |

---

## Phase 0 — 基礎設施（97%，僅缺 ADMIN_LINE_USER_ID）

| 狀態 | 項目 |
|---|---|
| ✅ | 本機開發環境（Poetry、.env、Docker、測試框架） |
| ✅ | Google Cloud 專案、Sheets/Drive API、OAuth 同意畫面 |
| ✅ | LINE 官方帳號、Rich Menu |
| ✅ | Firestore（Native mode）+ Service Account 金鑰 |
| ✅ | Firestore Secret Manager 磁碟區掛載（Cloud Run） |
| ✅ | Cloud Run 服務（asia-southeast1，Continuously deploy） |
| ✅ | Cloud Scheduler（每 10 分鐘呼叫 `/tick`） |
| ✅ | `GOOGLE_SHEETS_TEMPLATE_ID`（試算表範本 ID 已填入 Cloud Run） |
| ⬜ | `ADMIN_LINE_USER_ID`（需先加官方帳號好友才能取得） |

---

## Phase 1 — MVP 程式碼（~88%，約 42/48）

| 狀態 | 項目 | 備註 |
|---|---|---|
| ✅ | 1.1 專案骨架 | `main.py` 掛載四個 router |
| ✅ | 1.2 LINE Webhook | 簽章驗證、事件去重、follow/unfollow、記帳寫入 |
| ✅ | 1.3 OAuth 與試算表建立 | 含 `/oauth/callback`，OAuth 完成後立即 resync 填好帳戶快取，使用者連結後可直接記帳 |
| ✅ | 1.4 記帳文字解析 | parser + fuzzy_match + 多帳戶 Quick Reply |
| ✅ | 1.5 損益引擎 | 移動加權平均、已實現/未實現損益、賣超防呆 |
| ✅ | 1.6 試算表 resync | resync + `立即同步` LINE 指令 + `POST /sheets/sync` 端點 |
| ✅ | 1.7 防呆刪除與查詢 | 刪除上一筆 + Rich Menu 查詢 |
| ✅ | 1.8 首次使用引導與使用說明頁 | 歡迎訊息（含歡迎海報圖片）、使用說明指令、試算表說明分頁、格式統一；新增「新增分頁」多帳戶說明 |
| ✅ | 1.9 排程 | `/tick`，共享密鑰驗證，14:30 收盤任務 |
| ⬜ | 1.10 共用通知元件 | 未開始 |
| ✅ | 1.11 LIFF 網頁 | `GET /liff/dashboard` 儀表板上線，含圖表/篩選/主題；`/liff/summary` + `/liff/history` 串接完成 |
| ⬜ | 1.12 溫度感文案 | 未開始 |
| ✅ | 1.13 測試 | 148 案例全數通過 |

---

## Cloud Run 路由（全數上線）

| 路由 | 狀態 |
|---|---|
| `GET /health` | ✅ 200 |
| `POST /line/webhook` | ✅ 簽章驗證正常 |
| `GET /liff/summary` | ✅ Bearer token 驗證正常 |
| `GET /oauth/callback` | ✅ 正常 |
| `POST /tick` | ✅ 共享密鑰驗證正常 |
| `POST /sheets/sync` | ✅ 以 spreadsheet_id 觸發 resync 的通用同步入口 |
| `GET /oauth/liff` | ✅ LIFF 授權頁面（LIFF ID 由 settings 注入） |
| `GET /oauth/url` | ✅ LIFF 用，驗 id_token 後回傳 Google auth URL |
| `GET /liff/dashboard` | ✅ 動態儀表板頁面，串接 summary + history |
| `GET /static/*` | ✅ StaticFiles 掛載，供歡迎圖片公開存取 |

---

## 下次接續

1. **Cloud Run 環境變數**（待設定）：`APP_BASE_URL=https://stocktool-22843182344.asia-southeast1.run.app`、`LIFF_DASHBOARD_URL=https://stocktool-22843182344.asia-southeast1.run.app/liff/dashboard`
2. **Cloud Run 啟動 CPU 加速**：開啟後縮短 cold start，改善手機開儀表板 503 問題
3. **範本試算表共用設定**：確認設為「知道連結的人可檢視」，親友 OAuth 後才能複製
4. **Rich Menu 三欄設定**：製作 2500×1686px 背景圖，三欄 Action 設為 MessageAction（使用說明/查詢/立即同步）
5. **歷史資料匯入**：照流水帳格式貼入，resync 後重建歷史曲線
6. **1.10 共用通知元件 / 1.12 溫度感文案**（Phase 1 剩餘）
