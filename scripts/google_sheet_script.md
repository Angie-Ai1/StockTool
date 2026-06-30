const BACKEND_URL = "https://stocktool-22843182344.asia-southeast1.run.app";
const SOURCE_TAB = "個人帳戶"; // 新增分頁時複製格式的來源分頁

/**
 * 打後端 /sheets/sync，回傳 HTTP 狀態碼
 */
function postSync(spreadsheetId) {
  const response = UrlFetchApp.fetch(`${BACKEND_URL}/sheets/sync`, {
    method: "post",
    contentType: "application/json",
    payload: JSON.stringify({ spreadsheet_id: spreadsheetId }),
    muteHttpExceptions: true,
  });
  return response.getResponseCode();
}

/**
 * 立即同步：打後端 /sheets/sync，以 spreadsheetId 識別使用者
 */
function syncNow() {
  const spreadsheetId = SpreadsheetApp.getActiveSpreadsheet().getId();
  const code = postSync(spreadsheetId);
  const ui = SpreadsheetApp.getUi();
  if (code === 200) {
    ui.alert("✅ 同步完成！試算表狀態欄已更新。");
  } else if (code === 401) {
    ui.alert("⚠️ 授權已過期，請重新連結 LINE 機器人。");
  } else {
    ui.alert(`同步失敗（HTTP ${code}），請稍後再試。`);
  }
}

/**
 * 新增帳戶分頁：複製 SOURCE_TAB 的格式，並通知後端登記新分頁
 */
function addAccountTab() {
  const ui = SpreadsheetApp.getUi();
  const result = ui.prompt(
    "新增帳戶分頁",
    "請輸入新分頁名稱（例如：海外帳戶）：",
    ui.ButtonSet.OK_CANCEL
  );

  if (result.getSelectedButton() !== ui.Button.OK) return;

  const tabName = result.getResponseText().trim();
  if (!tabName) {
    ui.alert("名稱不可為空。");
    return;
  }

  const ss = SpreadsheetApp.getActiveSpreadsheet();
  if (ss.getSheetByName(tabName)) {
    ui.alert(`「${tabName}」分頁已存在，請換一個名稱。`);
    return;
  }

  const source = ss.getSheetByName(SOURCE_TAB);
  if (!source) {
    ui.alert(`找不到來源分頁「${SOURCE_TAB}」，請確認名稱是否正確。`);
    return;
  }

  const newSheet = source.copyTo(ss);
  newSheet.setName(tabName);

  // 只清左邊流水帳資料（A:G），保留 row1-3（含標頭列）與右邊 I:Q 統計摘要公式。
  // 後端新版面：row1-2 留白、row3 標頭、row4 起為資料；清除起點必須從 row4。
  // 不可用 deleteRows：統計摘要延伸到 row100，整列刪除會連右邊公式一起刪/位移。
  const LEDGER_DATA_FIRST_ROW = 4; // 與後端 LEDGER_DATA_FIRST_ROW 常數一致
  const maxRows = newSheet.getMaxRows();
  if (maxRows >= LEDGER_DATA_FIRST_ROW) {
    newSheet.getRange(`A${LEDGER_DATA_FIRST_ROW}:G${maxRows}`).clearContent();
  }
  ss.setActiveSheet(newSheet);

  // 通知後端重新掃描分頁，讓新分頁登記進帳戶清單（否則多帳戶記帳會認不得），
  // 同時刷新隱藏報價區 S:T，讓統計摘要的「今日收盤價」有值。
  const code = postSync(ss.getId());
  if (code === 200) {
    ui.alert(`✅ 已新增分頁「${tabName}」並完成同步！`);
  } else if (code === 401) {
    ui.alert(`分頁「${tabName}」已建立，但授權已過期，請重新連結 LINE 機器人後再傳「立即同步」。`);
  } else {
    ui.alert(`分頁「${tabName}」已建立，但同步失敗（HTTP ${code}），請手動傳「立即同步」。`);
  }
}
