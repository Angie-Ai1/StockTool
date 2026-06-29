# LINE 股市記帳小工具 — 開發進度

> 最後更新：2026-06-29 02:23

## 整體進度

| 維度 | 比例 | 說明 |
|---|---|---|
| 規格設計（Phase 1 MVP） | 100% | 已定案 |
| 規格設計（Phase 2~8） | 約 70% | 功能範圍已定，部分細節待展開 |
| Phase 0 基礎設施 | 97% | 僅缺 `ADMIN_LINE_USER_ID` |
| Phase 1 MVP 程式碼 | ~88%（約 42/48） | 1.8 部分完成、立即同步/新增帳戶指令上線 |
| 部署上線：基礎設施 | 100% | Cloud Run + Cloud Build CD 正常 |
| 部署上線：功能 | ~90% | 記帳/撤銷/查詢/同步端對端驗證通過；LIFF OAuth 行動裝置已驗證通過、debug code 已移除 |

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
| ✅ | 1.7 防呆撤銷與查詢 | 撤銷上一筆 + Rich Menu 查詢 |
| 🔄 | 1.8 首次使用引導與使用說明頁 | follow 歡迎訊息 ✅、使用說明指令 ✅、試算表「使用說明」分頁 ✅（取代舊「操作面板」，移除按鈕/重複統計，改為純操作指南）；首次使用流程引導待確認 |
| ✅ | 1.9 排程 | `/tick`，共享密鑰驗證，14:30 收盤任務 |
| ⬜ | 1.10 共用通知元件 | 未開始 |
| ✅ | 1.11 LIFF 網頁 | id_token 驗證、`/liff/summary` |
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

---

## 下次接續

1. **✅ LIFF OAuth（已完成）**：行動裝置授權流程已驗證通過，`oauth_liff.html` 的 idToken debug 顯示已移除
2. **試算表手動修復**：把目前已壞掉的試算表 row 1 資料剪下貼回 row 2 下方，讓標題列回到 row 1，再傳「查詢」重新同步
3. **端對端驗證**：記帳/撤銷/查詢/同步完整流程（手機端）
4. **1.10 共用通知元件** / **1.12 溫度感文案**（Phase 1 剩餘項目）
5. 後續 LIFF 動態網頁（圖表 + 篩選 + 搜尋）為獨立前端專案
