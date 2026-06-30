"""產生一組模擬流水帳,跑 history_engine 重建時間序,輸出 JSON + 自包含 demo 網頁。

用途:在還沒接真實 LIFF/OAuth/試算表前,先讓人「看圖表呈現效果」。

  poetry run python scripts/generate_mock_history.py

會在 docs/demo/ 產生:
  - mock_history.json   重建後的時間序(可餵給未來的正式前端)
  - dashboard_demo.html  自包含網頁(ECharts via CDN),直接用瀏覽器開即可預覽

注意:demo 為了展示「淨值/未實現損益曲線」的完整樣貌,額外『合成』了每檔的每日股價
(隨機漫步,純假資料),所以這支腳本會帶 price_history 進引擎。正式階段 1 的
`/liff/history` 不帶歷史股價(那條曲線要等階段 3 每日快照才會真的有資料)。
"""

from __future__ import annotations

import json
import random
from datetime import date as Date, timedelta
from decimal import Decimal
from pathlib import Path

from app.models.schemas import StockQuote, TransactionAction, TransactionRow
from app.services.history_engine import reconstruct_history

random.seed(20260630)

# 模擬標的:代碼 → (名稱, 起始股價)
STOCKS = {
    "2330": ("台積電", 600.0),
    "2454": ("聯發科", 900.0),
    "2317": ("鴻海", 105.0),
    "0050": ("元大台灣50", 140.0),
    "2412": ("中華電", 120.0),
    "2603": ("長榮", 180.0),
}
ACCOUNTS = ["永豐證券", "國泰證券"]

START = Date(2025, 10, 1)
DAYS = 270  # 約 9 個月


def _trading_days() -> list[Date]:
    return [START + timedelta(days=i) for i in range(DAYS) if (START + timedelta(days=i)).weekday() < 5]


def _price_paths(days: list[Date]) -> dict[str, dict[Date, Decimal]]:
    """每檔股價做幾何隨機漫步,當作 demo 的歷史收盤價(純假資料)。"""
    paths: dict[str, dict[Date, Decimal]] = {}
    for code, (_name, start_price) in STOCKS.items():
        price = start_price
        drift = random.uniform(-0.0004, 0.0010)  # 每日輕微趨勢
        series: dict[Date, Decimal] = {}
        for day in days:
            shock = random.gauss(drift, 0.018)
            price = max(price * (1 + shock), start_price * 0.4)
            series[day] = Decimal(str(round(price, 1)))
        paths[code] = series
    return paths


def _generate_transactions(
    days: list[Date], prices: dict[str, dict[Date, Decimal]]
) -> dict[str, list[TransactionRow]]:
    """隨機灑出買/賣/配息/配股,賣出不超過當前持股(避免賣超列被略過)。"""
    txns: dict[str, list[TransactionRow]] = {acct: [] for acct in ACCOUNTS}
    holdings: dict[tuple[str, str], Decimal] = {}  # (帳戶, 代碼) → 股數
    seq = 0

    for day in days:
        if random.random() > 0.22:  # 約兩成交易日有動作
            continue
        for _ in range(random.randint(1, 2)):
            acct = random.choice(ACCOUNTS)
            code = random.choice(list(STOCKS))
            price = float(prices[code][day])
            key = (acct, code)
            held = holdings.get(key, Decimal("0"))
            roll = random.random()
            seq += 1

            if held > 0 and roll < 0.30:  # 賣出一部分
                sell_qty = max(1, int(float(held) * random.uniform(0.2, 1.0)))
                sell_qty = min(sell_qty, int(held))
                amount = round(price * sell_qty * random.uniform(0.97, 1.06))
                holdings[key] = held - Decimal(sell_qty)
                txns[acct].append(_row(seq, day, TransactionAction.SELL, code, sell_qty, amount))
            elif held > 0 and roll < 0.38:  # 配息
                amount = round(float(held) * random.uniform(1.5, 4.0))
                txns[acct].append(_row(seq, day, TransactionAction.DIVIDEND, code, None, amount))
            elif held > 0 and roll < 0.43:  # 配股
                bonus = max(1, int(float(held) * random.uniform(0.02, 0.08)))
                holdings[key] = held + Decimal(bonus)
                txns[acct].append(_row(seq, day, TransactionAction.STOCK_DIVIDEND, code, bonus, None))
            else:  # 買進
                buy_qty = random.choice([10, 20, 50, 100, 200])
                amount = round(price * buy_qty * random.uniform(0.97, 1.03))
                holdings[key] = held + Decimal(buy_qty)
                txns[acct].append(_row(seq, day, TransactionAction.BUY, code, buy_qty, amount))

    return txns


def _row(seq, day, action, code, qty, amount):
    return TransactionRow(
        row_uuid=f"mock-{seq}",
        date=day,
        action=action,
        stock_query=code,
        quantity=Decimal(str(qty)) if qty is not None else None,
        amount=Decimal(str(amount)) if amount is not None else None,
    )


def _resolver(query: str) -> StockQuote | None:
    if query in STOCKS:
        return StockQuote(code=query, name=STOCKS[query][0])
    return None


def _to_jsonable(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, Date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_jsonable(v) for v in obj]
    return obj


def main() -> None:
    days = _trading_days()
    prices = _price_paths(days)
    txns = _generate_transactions(days, prices)

    history = reconstruct_history(txns, _resolver, price_history=prices)
    payload = _to_jsonable(history.model_dump())

    out_dir = Path("docs/demo")
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "mock_history.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    html_path = out_dir / "dashboard_demo.html"
    html_path.write_text(_render_demo_html(payload), encoding="utf-8")

    n_events = len(payload["events"])
    n_days = len(payload["points"])
    print(f"✅ 產生完成：{n_events} 筆模擬交易、{n_days} 個時間點")
    print(f"   JSON  → {json_path}")
    print(f"   網頁  → {html_path}（用瀏覽器開啟即可預覽圖表）")


def _render_demo_html(payload: dict) -> str:
    data_json = json.dumps(payload, ensure_ascii=False)
    return _DEMO_HTML_TEMPLATE.replace("__DATA__", data_json)


_DEMO_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>持股趨勢儀表板（Demo）</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<style>
  :root { --fs:1; --bg:#f4ece0; --card:#fffdf8; --line:#e6d9c6; --split:#efe6d6;
          --text:#4a3f33; --muted:#8a7c68; --accent:#c9772f; --pos:#5a9c52; --neg:#cf5b46; --warn:#c9952f;
          --shadow:0 4px 16px rgba(60,40,20,.08); --shadow-sm:0 1px 4px rgba(60,40,20,.10); }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text);
         font-family:-apple-system,"Noto Sans TC",Segoe UI,Roboto,sans-serif;
         font-size:calc(14px * var(--fs)); transition:background .2s,color .2s; }
  header { padding:16px 22px; border-bottom:1px solid var(--line); }
  header h1 { margin:0; font-size:calc(18px * var(--fs)); }
  header p { margin:4px 0 0; color:var(--muted); font-size:calc(13px * var(--fs)); }
  .demo-tag { display:inline-block; margin-left:8px; padding:2px 8px; border-radius:10px;
              background:var(--warn); color:#1a1300; font-size:calc(11px * var(--fs)); vertical-align:middle; }
  /* 控制列：卡片化、陰影、分段切換、色票、晶片 */
  .controls { display:flex; flex-wrap:wrap; gap:16px 26px; align-items:flex-end;
              margin:18px 22px 0; padding:16px 20px; background:var(--card);
              border:1px solid var(--line); border-radius:16px; box-shadow:var(--shadow); }
  .ctrl { display:flex; flex-direction:column; gap:7px; }
  .ctrl > label { color:var(--muted); font-size:calc(11.5px * var(--fs)); font-weight:600; letter-spacing:.02em; }
  .selectbox { position:relative; display:inline-block; }
  .selectbox::after { content:'▾'; position:absolute; right:11px; top:50%; transform:translateY(-50%);
                      color:var(--muted); pointer-events:none; font-size:calc(11px * var(--fs)); }
  select { appearance:none; -webkit-appearance:none; background:var(--bg); color:var(--text);
           border:1px solid var(--line); border-radius:10px; padding:9px 30px 9px 12px;
           font-size:calc(13px * var(--fs)); min-width:150px; cursor:pointer; transition:border-color .15s, box-shadow .15s; }
  select:hover { border-color:var(--accent); }
  select:focus { outline:none; border-color:var(--accent); box-shadow:0 0 0 3px color-mix(in srgb, var(--accent) 22%, transparent); }
  /* 分段切換 (segmented control) */
  .seg { display:inline-flex; background:var(--split); border:1px solid var(--line); border-radius:11px; padding:3px; gap:2px; }
  .seg button { border:none; background:transparent; color:var(--muted); padding:7px 14px; border-radius:8px;
                cursor:pointer; font-size:calc(12.5px * var(--fs)); font-weight:500; transition:all .15s; }
  .seg button:hover:not(.active) { color:var(--text); }
  .seg button.active { background:var(--card); color:var(--accent); font-weight:700; box-shadow:var(--shadow-sm); }
  /* 主題色票 */
  .swatches { display:inline-flex; gap:9px; }
  .swatch { width:28px; height:28px; border-radius:50%; cursor:pointer; border:none; padding:0;
            box-shadow:inset 0 0 0 1px rgba(0,0,0,.12); transition:transform .12s; }
  .swatch:hover { transform:scale(1.12); }
  .swatch.active { box-shadow:0 0 0 2px var(--card), 0 0 0 4px var(--accent); }
  /* 個股晶片 */
  .chips { display:flex; flex-wrap:wrap; gap:8px; max-width:520px; }
  .chip { border:1px solid var(--line); background:var(--bg); color:var(--muted); padding:7px 14px;
          border-radius:20px; cursor:pointer; font-size:calc(12.5px * var(--fs)); transition:all .15s; }
  .chip:hover { border-color:var(--accent); color:var(--text); }
  .chip.active { background:var(--accent); color:#fff; border-color:var(--accent); box-shadow:var(--shadow-sm); }

  .kpis { display:flex; flex-wrap:wrap; gap:14px; padding:18px 22px 0; }
  .kpi { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:14px 18px;
         min-width:148px; box-shadow:var(--shadow-sm); border-top:3px solid var(--accent); }
  .kpi .k { color:var(--muted); font-size:calc(12px * var(--fs)); }
  .kpi .v { font-size:calc(21px * var(--fs)); font-weight:700; margin-top:5px; }
  .pos { color:var(--pos); } .neg { color:var(--neg); }
  .card { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:12px;
          margin:16px 22px 0; box-shadow:var(--shadow-sm); }
  .card h3 { margin:4px 6px 10px; font-size:calc(14px * var(--fs)); }
  .chart { width:100%; height:300px; }
  /* 下方版面：兩欄。左＝總表/成本組成/交易分布；右＝總損益/已實現vs未實現 */
  .cols { display:flex; gap:16px; margin:16px 22px 0; align-items:stretch; }
  .col { flex:1 1 0; min-width:0; display:flex; flex-direction:column; gap:16px; }
  .col .card { margin:0; }
  .colR .card { flex:1 1 0; min-height:320px; }   /* 右欄兩圖平分左欄總高度 */
  .colR .chart { height:100%; }
  .tablecard { display:flex; flex-direction:column; }
  .tablecard .tablewrap { flex:1 1 auto; max-height:none; overflow:auto; }
  table.summary { width:100%; border-collapse:collapse; font-size:calc(12.5px * var(--fs)); }
  table.summary th, table.summary td { padding:8px 8px; text-align:right; border-bottom:1px solid var(--split); white-space:nowrap; }
  table.summary th { color:var(--muted); font-weight:600; position:sticky; top:0; background:var(--card); }
  table.summary tbody tr:hover td { background:var(--split); }
  table.summary td.name, table.summary th.name { text-align:left; }
  table.summary tr.total td { border-top:2px solid var(--line); border-bottom:none; font-weight:700; }
  .tablewrap { max-height:300px; overflow:auto; }
  .hint { color:var(--muted); font-size:calc(12px * var(--fs)); padding:20px 22px 28px; }
  @media (max-width:880px){ .cols{ flex-direction:column; } }
</style>
</head>
<body>
<header>
  <h1>持股趨勢儀表板 <span class="demo-tag">DEMO・模擬資料</span></h1>
  <p>篩選帳戶／個股、切換時間粒度與主題字級，左側總表看整體狀況、右側圖表看趨勢。</p>
</header>

<div class="controls">
  <div class="ctrl"><label>🏦 帳戶</label><span class="selectbox"><select id="account"></select></span></div>
  <div class="ctrl"><label>📈 主圖指標</label><span class="selectbox">
    <select id="metric">
      <option value="total_pnl">總損益（已實現＋未實現）</option>
      <option value="market_value">資產淨值（市值）</option>
      <option value="unrealized_pnl">未實現損益</option>
      <option value="realized_pnl">累積已實現損益</option>
      <option value="cost_basis">持倉成本</option>
    </select></span></div>
  <div class="ctrl"><label>🗓 時間粒度</label><div class="seg" id="granSeg"></div></div>
  <div class="ctrl"><label>🔤 字體大小</label><div class="seg" id="fontSeg"></div></div>
  <div class="ctrl"><label>🎨 主題色調</label><div class="swatches" id="themeSw"></div></div>
  <div class="ctrl" style="flex:1; min-width:260px;"><label>📊 個股（點選切換，未選＝全部）</label><div class="chips" id="stockChips"></div></div>
</div>

<div class="kpis" id="kpis"></div>

<div class="cols">
  <div class="col colL">
    <div class="card tablecard">
      <h3>持股總表（依目前篩選）</h3>
      <div class="tablewrap"><div id="summaryTable"></div></div>
    </div>
    <div class="card"><div id="costStack" class="chart"></div></div>
    <div class="card"><div id="scatter" class="chart"></div></div>
  </div>
  <div class="col colR">
    <div class="card"><div id="equity" class="chart"></div></div>
    <div class="card"><div id="pnlSplit" class="chart"></div></div>
  </div>
</div>

<p class="hint">※ 此為純前端 Demo：資產淨值/未實現損益用「合成股價」呈現完整樣貌；正式階段 1 只有持倉成本與已實現損益曲線，淨值曲線要等階段 3 每日快照累積。時間粒度套用在折線圖（取每期期末值）；交易分布散點維持逐筆顯示。</p>

<script>
const DATA = __DATA__;
const METRIC_LABEL = { total_pnl:"總損益", market_value:"資產淨值", unrealized_pnl:"未實現損益", realized_pnl:"累積已實現損益", cost_basis:"持倉成本" };
const PALETTE = ['#4ea1ff','#3ddc84','#f0b95b','#b07cff','#ff8fab','#36c5d0','#ff9f43','#7ed957'];

const THEMES = {
  '暗夜藍': { bg:'#0f1115', card:'#1a1e27', line:'#2a2f3a', split:'#23272f', text:'#e6e8ec', muted:'#8b93a3', accent:'#4ea1ff', pos:'#3ddc84', neg:'#ff6b6b', warn:'#f0b95b', head:'#13161d' },
  '純淨白': { bg:'#f5f7fa', card:'#ffffff', line:'#e3e8ef', split:'#eef1f5', text:'#1f2733', muted:'#6b7480', accent:'#2f7bf0', pos:'#1aa260', neg:'#e23b3b', warn:'#d98a16', head:'#eef1f5' },
  '暖陽米': { bg:'#f4ece0', card:'#fffdf8', line:'#e6d9c6', split:'#efe6d6', text:'#4a3f33', muted:'#8a7c68', accent:'#c9772f', pos:'#5a9c52', neg:'#cf5b46', warn:'#c9952f', head:'#efe6d6' },
  '森林綠': { bg:'#0e1a17', card:'#14241f', line:'#22362f', split:'#1c2e28', text:'#e6f0ec', muted:'#85a397', accent:'#2fbf91', pos:'#46d39a', neg:'#ff7a6b', warn:'#e6c15a', head:'#102019' },
};
const FONT = { '小':0.85, '中':1.0, '大':1.2 };

const state = { theme:'暖陽米', fontScale:1.0, gran:'日', selectedStocks:new Set() };

const charts = {
  equity: echarts.init(document.getElementById('equity')),
  pnlSplit: echarts.init(document.getElementById('pnlSplit')),
  costStack: echarts.init(document.getElementById('costStack')),
  scatter: echarts.init(document.getElementById('scatter')),
};
window.addEventListener('resize', () => Object.values(charts).forEach(c => c.resize()));

// --- 篩選器選項 ---
const accountSel = document.getElementById('account');
accountSel.innerHTML = '<option value="__all__">全部帳戶</option>' +
  DATA.accounts.map(a => `<option value="${a.tab_name}">${a.tab_name}</option>`).join('');

const stockMap = {};
DATA.accounts.forEach(a => a.stocks.forEach(s => { stockMap[s.stock_code] = s.stock_name; }));

// 原生 select（帳戶、指標）變更即重繪
accountSel.addEventListener('change', renderAll);
document.getElementById('metric').addEventListener('change', renderAll);

// 分段切換 (segmented control)：時間粒度、字級
function buildSeg(id, options, current, onPick){
  const el = document.getElementById(id);
  el.innerHTML = options.map(o => `<button data-v="${o}" class="${o===current?'active':''}">${o}</button>`).join('');
  el.querySelectorAll('button').forEach(b => b.onclick = () => {
    el.querySelectorAll('button').forEach(x => x.classList.remove('active'));
    b.classList.add('active'); onPick(b.dataset.v); renderAll();
  });
}
buildSeg('granSeg', ['日','月','季','年'], state.gran, v => state.gran = v);
buildSeg('fontSeg', ['小','中','大'], '中', v => state.fontScale = FONT[v]);

// 主題色票
const swEl = document.getElementById('themeSw');
swEl.innerHTML = Object.entries(THEMES).map(([name,t]) =>
  `<button class="swatch ${name===state.theme?'active':''}" title="${name}" data-v="${name}"
     style="background:linear-gradient(135deg, ${t.bg} 46%, ${t.accent} 54%)"></button>`).join('');
swEl.querySelectorAll('button').forEach(b => b.onclick = () => {
  state.theme = b.dataset.v;
  swEl.querySelectorAll('button').forEach(x => x.classList.remove('active'));
  b.classList.add('active'); renderAll();
});

// 個股晶片（多選切換）
const chipEl = document.getElementById('stockChips');
chipEl.innerHTML = Object.entries(stockMap).map(([code,name]) =>
  `<button class="chip" data-v="${code}">${name}</button>`).join('');
chipEl.querySelectorAll('button').forEach(b => b.onclick = () => {
  const c = b.dataset.v;
  if(state.selectedStocks.has(c)){ state.selectedStocks.delete(c); b.classList.remove('active'); }
  else { state.selectedStocks.add(c); b.classList.add('active'); }
  renderAll();
});

// --- 工具 ---
function theme(){ return THEMES[state.theme]; }
function hexA(hex,a){ const h=hex.replace('#',''); const r=parseInt(h.slice(0,2),16),g=parseInt(h.slice(2,4),16),b=parseInt(h.slice(4,6),16); return `rgba(${r},${g},${b},${a})`; }
function fmt(n){ return Math.round(n).toLocaleString(); }
function signClass(n){ return n >= 0 ? 'pos' : 'neg'; }
function selectedAccounts(){ const v=accountSel.value; return v==='__all__'?DATA.accounts:DATA.accounts.filter(a=>a.tab_name===v); }
function selectedStockCodes(){ return [...state.selectedStocks]; }
function axisDates(){ return DATA.points.map(p => p.date); }

// 把選定帳戶（與選定個股）的逐日數值加總成一條序列（對齊整體日期軸）
function aggregate(metric){
  const accts = selectedAccounts(), codes = selectedStockCodes(), dates = axisDates();
  const sum = dates.map(()=>0);
  accts.forEach(a => {
    if(codes.length===0){
      a.points.forEach((p,i)=>{ sum[i]+=(p[metric]??0); });
    } else {
      a.stocks.filter(s=>codes.includes(s.stock_code)).forEach(s=>{
        const byDate={}; s.points.forEach(p=>byDate[p.date]=p);
        let last=null;
        dates.forEach((d,i)=>{ if(byDate[d]) last=byDate[d]; if(last) sum[i]+=(last[metric]??0); });
      });
    }
  });
  return sum;
}

// 時間粒度：把逐日序列重新取樣成 日/月/季/年（取每期期末值）
function bucketKey(dateStr){
  const [y,m] = dateStr.split('-').map(Number);
  if(state.gran==='年') return `${y}`;
  if(state.gran==='季') return `${y} Q${Math.floor((m-1)/3)+1}`;
  if(state.gran==='月') return `${y}-${String(m).padStart(2,'0')}`;
  return dateStr;
}
function resample(values){
  const dates = axisDates(); const seen = new Map();
  dates.forEach((d,i)=> seen.set(bucketKey(d), values[i]));   // 後者覆蓋前者＝期末值
  return { labels:[...seen.keys()], values:[...seen.values()] };
}

// --- 個股當前快照（給左側總表）---
function holdingsSnapshot(){
  const accts = selectedAccounts(), codes = selectedStockCodes();
  const agg = {};
  accts.forEach(a => a.stocks.forEach(s => {
    if(codes.length && !codes.includes(s.stock_code)) return;
    const last = s.points[s.points.length-1];
    const o = agg[s.stock_code] || (agg[s.stock_code]={name:s.stock_name, qty:0, cost:0, mv:0, unreal:0, realized:0});
    o.qty += last.quantity ?? 0; o.cost += last.cost_basis ?? 0; o.mv += last.market_value ?? 0;
    o.unreal += last.unrealized_pnl ?? 0; o.realized += last.realized_pnl ?? 0;
  }));
  return agg;
}

function renderAll(){
  applyChrome();
  renderKpis();
  renderSummary();
  renderEquity();
  renderPnlSplit();
  renderCostStack();
  renderScatter();
  setTimeout(()=>Object.values(charts).forEach(c=>c.resize()), 0);
}

function applyChrome(){
  const t = theme(), root = document.documentElement.style;
  root.setProperty('--fs', state.fontScale);
  ['bg','card','line','split','text','muted','accent','pos','neg','warn']
    .forEach(k => root.setProperty('--'+k, t[k]));
}

function renderKpis(){
  const i = axisDates().length-1;
  const mv=aggregate('market_value')[i]||0, cost=aggregate('cost_basis')[i]||0;
  const realized=aggregate('realized_pnl')[i]||0, unreal=aggregate('unrealized_pnl')[i]||0;
  const total=realized+unreal, roi=cost>0?(total/cost*100):0;
  const items=[['資產淨值',fmt(mv),''],['持倉成本',fmt(cost),''],
    ['總損益',(total>=0?'+':'')+fmt(total),signClass(total)],
    ['累積已實現',(realized>=0?'+':'')+fmt(realized),signClass(realized)],
    ['報酬率',(roi>=0?'+':'')+roi.toFixed(1)+'%',signClass(roi)]];
  document.getElementById('kpis').innerHTML = items.map(([k,v,c])=>
    `<div class="kpi"><div class="k">${k}</div><div class="v ${c}">${v}</div></div>`).join('');
}

function renderSummary(){
  const agg = holdingsSnapshot();
  const rows = Object.entries(agg).map(([code,o])=>({code,...o}))
    .sort((a,b)=> b.mv-a.mv || b.realized-a.realized);
  const tot = rows.reduce((s,r)=>({qty:0,cost:s.cost+r.cost,mv:s.mv+r.mv,unreal:s.unreal+r.unreal,realized:s.realized+r.realized}),{cost:0,mv:0,unreal:0,realized:0});
  const cls = n => n>=0?'pos':'neg';
  const roiCell = (pnl,cost) => cost>0 ? `<td class="${cls(pnl)}">${(pnl/cost*100>=0?'+':'')+(pnl/cost*100).toFixed(1)}%</td>` : '<td>—</td>';
  const body = rows.map(r=>`<tr>
      <td class="name">${r.name}<br><span style="color:var(--muted);font-size:.85em">${r.code}</span></td>
      <td>${fmt(r.qty)}</td><td>${fmt(r.cost)}</td><td>${fmt(r.mv)}</td>
      <td class="${cls(r.unreal)}">${(r.unreal>=0?'+':'')+fmt(r.unreal)}</td>
      <td class="${cls(r.realized)}">${(r.realized>=0?'+':'')+fmt(r.realized)}</td>
      ${roiCell(r.unreal+r.realized, r.cost)}
    </tr>`).join('');
  const totalPnl = tot.unreal+tot.realized;
  document.getElementById('summaryTable').innerHTML = `
    <table class="summary">
      <thead><tr><th class="name">個股</th><th>股數</th><th>成本</th><th>市值</th><th>未實現</th><th>已實現</th><th>報酬率</th></tr></thead>
      <tbody>${body}
        <tr class="total"><td class="name">合計</td><td>—</td><td>${fmt(tot.cost)}</td><td>${fmt(tot.mv)}</td>
          <td class="${cls(tot.unreal)}">${(tot.unreal>=0?'+':'')+fmt(tot.unreal)}</td>
          <td class="${cls(tot.realized)}">${(tot.realized>=0?'+':'')+fmt(tot.realized)}</td>
          ${roiCell(totalPnl, tot.cost)}</tr>
      </tbody>
    </table>`;
}

function commonAxis(labels){
  const t=theme(), fs=state.fontScale;
  return {
    grid:{ left:62, right:24, top:42, bottom:62 },
    tooltip:{ trigger:'axis', backgroundColor:t.card, borderColor:t.line, textStyle:{color:t.text, fontSize:12*fs}, valueFormatter:v=>fmt(v) },
    xAxis:{ type:'category', data:labels, boundaryGap:false, axisLine:{lineStyle:{color:t.line}}, axisLabel:{color:t.muted, fontSize:11*fs} },
    yAxis:{ type:'value', splitLine:{lineStyle:{color:t.split}}, axisLabel:{color:t.muted, fontSize:11*fs, formatter:v=>fmt(v)} },
    dataZoom:[{type:'inside'},{type:'slider', height:18, backgroundColor:t.head, fillerColor:hexA(t.accent,0.18), borderColor:t.line, textStyle:{color:t.muted, fontSize:10*fs}}],
  };
}
function titleOf(text){ const t=theme(); return { text, left:12, top:6, textStyle:{color:t.text, fontSize:14*state.fontScale} }; }

function renderEquity(){
  const t=theme(), metric=document.getElementById('metric').value;
  const r=resample(aggregate(metric)); const base=commonAxis(r.labels);
  charts.equity.setOption(Object.assign({}, base, {
    backgroundColor:'transparent', title:titleOf(METRIC_LABEL[metric]+'　時間軸（'+state.gran+'）'),
    series:[{ name:METRIC_LABEL[metric], type:'line', smooth:true, showSymbol:false, data:r.values,
      lineStyle:{width:2,color:t.accent},
      areaStyle:{ color:new echarts.graphic.LinearGradient(0,0,0,1,[{offset:0,color:hexA(t.accent,0.35)},{offset:1,color:hexA(t.accent,0.02)}]) } }]
  }), true);
}
function renderPnlSplit(){
  const t=theme(); const r1=resample(aggregate('realized_pnl')), r2=resample(aggregate('unrealized_pnl'));
  const base=commonAxis(r1.labels);
  charts.pnlSplit.setOption(Object.assign({}, base, {
    backgroundColor:'transparent', title:titleOf('已實現 vs 未實現損益'),
    legend:{ top:8, right:12, textStyle:{color:t.muted, fontSize:12*state.fontScale} },
    series:[
      { name:'累積已實現', type:'line', showSymbol:false, smooth:true, data:r1.values, lineStyle:{color:t.warn} },
      { name:'未實現', type:'line', showSymbol:false, smooth:true, data:r2.values, lineStyle:{color:t.pos} },
    ]
  }), true);
}
function renderCostStack(){
  const t=theme(), accts=selectedAccounts(), dates=axisDates(), codes=Object.keys(stockMap);
  let labels=null;
  const series = codes.map((code,idx)=>{
    const sum=dates.map(()=>0);
    accts.forEach(a=>{ const s=a.stocks.find(x=>x.stock_code===code); if(!s) return;
      const byDate={}; s.points.forEach(p=>byDate[p.date]=p); let last=null;
      dates.forEach((d,i)=>{ if(byDate[d]) last=byDate[d]; if(last) sum[i]+=(last.cost_basis??0); }); });
    const r=resample(sum); labels=r.labels;
    return { name:stockMap[code], type:'line', stack:'cost', areaStyle:{opacity:0.85}, showSymbol:false, smooth:true,
             data:r.values, lineStyle:{width:1}, itemStyle:{color:PALETTE[idx%PALETTE.length]} };
  });
  const base=commonAxis(labels||[]);
  charts.costStack.setOption(Object.assign({}, base, {
    backgroundColor:'transparent', title:titleOf('持倉成本組成（依個股堆疊）'),
    tooltip:{ trigger:'axis', backgroundColor:t.card, borderColor:t.line, textStyle:{color:t.text, fontSize:12*state.fontScale} },
    legend:{ type:'scroll', top:8, right:12, textStyle:{color:t.muted, fontSize:11*state.fontScale} },
    series
  }), true);
}
function renderScatter(){
  const t=theme(), fs=state.fontScale;
  const ACTION_COLOR={ '買進':t.pos, '賣出':t.neg, '配息':t.warn, '配股':t.accent };
  const accts=selectedAccounts().map(a=>a.tab_name), codes=selectedStockCodes();
  const groups={};
  DATA.events.forEach(e=>{ if(!accts.includes(e.tab_name)) return; if(codes.length && !codes.includes(e.stock_code)) return;
    (groups[e.action]||(groups[e.action]=[])).push([e.date, e.amount??0, e.stock_name, e.quantity]); });
  const series=Object.entries(groups).map(([action,pts])=>({
    name:action, type:'scatter', symbolSize:9, itemStyle:{color:ACTION_COLOR[action]||t.muted, opacity:0.82},
    data:pts.map(p=>({value:[p[0],p[1]], name:p[2], qty:p[3]})) }));
  charts.scatter.setOption({
    backgroundColor:'transparent', title:titleOf('交易分布（金額／時間）'),
    tooltip:{ trigger:'item', backgroundColor:t.card, borderColor:t.line, textStyle:{color:t.text, fontSize:12*fs},
      formatter:p=>`${p.seriesName}・${p.data.name}<br/>${p.value[0]}<br/>金額 ${fmt(p.value[1])}　股數 ${p.data.qty??'-'}` },
    legend:{ top:8, right:12, textStyle:{color:t.muted, fontSize:12*fs} },
    grid:{ left:62, right:24, top:42, bottom:62 },
    xAxis:{ type:'time', axisLine:{lineStyle:{color:t.line}}, axisLabel:{color:t.muted, fontSize:11*fs} },
    yAxis:{ type:'value', name:'金額', nameTextStyle:{color:t.muted}, splitLine:{lineStyle:{color:t.split}}, axisLabel:{color:t.muted, fontSize:11*fs, formatter:v=>fmt(v)} },
    dataZoom:[{type:'inside'},{type:'slider', height:18, backgroundColor:t.head, fillerColor:hexA(t.accent,0.18), borderColor:t.line, textStyle:{color:t.muted, fontSize:10*fs}}],
    series
  }, true);
}

renderAll();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
