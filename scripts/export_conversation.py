"""從 Claude Code 的 JSONL 對話紀錄提取完整對話，輸出為 Markdown。

用法：
  python scripts/export_conversation.py <output_path> [session_id]

  output_path : 要寫入的 .md 檔案路徑
  session_id  : (選填) 指定 session UUID；省略時自動使用最新修改的 JSONL

範例：
  python scripts/export_conversation.py logs/2026-06-26_0314.md
"""

import json
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
# Claude Code 以「-」取代「/」把專案路徑轉成目錄名稱
PROJECT_KEY = str(PROJECT_DIR.resolve()).replace("/", "-").replace("_", "-")
CLAUDE_PROJECT_DIR = Path.home() / ".claude" / "projects" / PROJECT_KEY


def find_jsonl(session_id: str | None) -> Path:
    if session_id:
        path = CLAUDE_PROJECT_DIR / f"{session_id}.jsonl"
        if not path.exists():
            raise FileNotFoundError(f"找不到 {path}")
        return path
    # 取最新修改的 JSONL
    files = sorted(CLAUDE_PROJECT_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError(f"在 {CLAUDE_PROJECT_DIR} 找不到任何 JSONL")
    return files[0]


def extract_text(content) -> str:
    """從 content 欄位提取純文字（跳過 thinking / tool_use / tool_result）"""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "").strip()
                if text:
                    parts.append(text)
        return "\n".join(parts)
    return ""


def parse_jsonl(path: Path) -> list[dict]:
    """回傳 [{"role": "user"/"assistant", "text": "..."}] 列表，依對話順序"""
    messages = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            role = obj.get("type")  # "user" or "assistant"
            if role not in ("user", "assistant"):
                continue

            msg = obj.get("message", {})
            content = msg.get("content", "")
            text = extract_text(content)

            # 過濾掉空白訊息與純系統 reminder 訊息
            if not text or text.startswith("<system-reminder>"):
                continue
            # 使用者訊息有時以 system-reminder block 開頭，只取前面的人工文字
            if "<system-reminder>" in text:
                text = text[:text.index("<system-reminder>")].strip()
            if not text:
                continue

            messages.append({"role": role, "text": text})
    return messages


def to_markdown(messages: list[dict], timestamp: str) -> str:
    lines = [f"# 對話紀錄 - {timestamp}", ""]
    for msg in messages:
        if msg["role"] == "user":
            lines.append("## 使用者")
        else:
            lines.append("## Claude")
        lines.append(msg["text"])
        lines.append("")
    return "\n".join(lines)


def main():
    args = sys.argv[1:]
    if not args:
        print("用法：python scripts/export_conversation.py <output_path> [session_id]")
        sys.exit(1)

    output_path = Path(args[0])
    session_id = args[1] if len(args) > 1 else None

    jsonl_path = find_jsonl(session_id)
    print(f"讀取：{jsonl_path}")

    messages = parse_jsonl(jsonl_path)
    print(f"提取到 {len(messages)} 則訊息")

    # 從輸出路徑的檔名取時間戳記（格式 YYYY-MM-DD_HHMM）
    stem = output_path.stem  # e.g. "2026-06-26_0314"
    timestamp = stem.replace("_", " ").replace("-", "-", 2)  # "2026-06-26 0314"
    if len(stem) >= 15:
        timestamp = f"{stem[:10]} {stem[11:13]}:{stem[13:15]}"

    md = to_markdown(messages, timestamp)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(md, encoding="utf-8")
    print(f"已寫入：{output_path}（{len(md)} 字元）")


if __name__ == "__main__":
    main()
