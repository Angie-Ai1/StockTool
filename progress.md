# LINE 股市記帳小工具 — 開發進度

> 最後更新：2026-06-26 03:20

## 整體進度

| 維度 | 比例 | 說明 |
|---|---|---|
| 規格設計（Phase 1 MVP） | 100% | 已定案 |
| 規格設計（Phase 2~8） | 約 70% | 功能範圍已定，部分細節待展開 |
| Phase 0 基礎設施 | 97% | 僅缺 `ADMIN_LINE_USER_ID`；Firestore Secret Manager ✅、`GOOGLE_SHEETS_TEMPLATE_ID` ✅ |
| Phase 1 MVP 程式碼 | 83%（40/48） | 新增 `oauth_callback.py`，測試升至 122/122（11 個測試檔） |
| 部署上線：基礎設施 | 100% | Cloud Run 五條路由全上線 |
| 部署上線：功能 | 85% | 所有 Phase 1 已完成程式碼已 push；尚未做真實 LINE 端對端測試 |

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

## Phase 1 — MVP 程式碼（83%，40/48）

| 狀態 | 項目 |
|---|---|
| ✅ | 1.1 專案骨架（`main.py` 掛載四個 router） |
| ✅ | 1.2 LINE Webhook（簽章驗證、事件去重、follow/unfollow、記帳寫入） |
| ✅ | 1.3 OAuth 與試算表建立（含 `/oauth/callback` 路由，本次完成） |
| ✅ | 1.4 記帳文字解析（parser + fuzzy_match + 多帳戶 Quick Reply） |
| ✅ | 1.5 損益引擎（移動加權平均、已實現/未實現損益、賣超防呆） |
| 🔄 | 1.6 試算表 resync（2/3，缺操作面板「立即同步」按鈕） |
| ⬜ | 1.7 防呆撤銷與查詢（記帳後 Quick Reply 刪除、Rich Menu 查詢） |
| ⬜ | 1.8 首次使用引導與操作面板 |
| ✅ | 1.9 排程（`/tick`，共享密鑰驗證，14:30 收盤任務） |
| ⬜ | 1.10 共用通知元件 |
| ✅ | 1.11 LIFF 網頁（id_token 驗證、`/liff/summary`） |
| ⬜ | 1.12 溫度感文案 |
| ✅ | 1.13 測試（11 個測試檔，122 案例全數通過） |

---

## Cloud Run 路由（全數上線）

| 路由 | 狀態 |
|---|---|
| `GET /health` | ✅ 200 |
| `POST /line/webhook` | ✅ 簽章驗證正常 |
| `GET /liff/summary` | ✅ Bearer token 驗證正常 |
| `GET /oauth/callback` | ✅ 本次新增部署 |
| `POST /tick` | ✅ 共享密鑰驗證正常 |

---

## 下次接續

1. **LINE 端對端測試**（最優先）：加官方帳號好友 → 傳訊息 → 點 OAuth 連結 → 試算表連結成功
2. **1.7 防呆撤銷與查詢**：記帳成功後 Quick Reply 刪除上一筆 + Rich Menu 查詢，完成後 MVP 記帳迴圈完全閉合
3. 試算表範本完善（`Instruction/claude_cowork.md` 任務 1）
