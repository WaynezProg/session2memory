"""Display-only topics for the console; never move, merge or revalidate stored memory.

Use bounded keyword hints rather than an LLM on each page load. Unknown content stays
under general rules. A topic is a navigation aid, not an assertion about provenance.
"""

from __future__ import annotations

import re

TOPICS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "retrieval",
        "查詢與索引",
        (r"embedding|graphify|檢索|同義詞|別名|召回|知識庫|identifier|bm25",),
    ),
    ("memory", "記憶與交接", (r"記憶|curat|write.?back|交接|digest|session2memory|\bs2m\b",)),
    (
        "environment",
        "安裝與環境",
        (r"安裝|runtime|venv|launchd|launchctl|plist|hook|環境變數|uv tool|路徑|資料夾|worktree",),
    ),
    (
        "review",
        "開發與驗收",
        (r"regression|pytest|測試|驗收|review|回歸|\bci\b|\bpr\b|commit|分支|唯讀審查",),
    ),
    ("data", "資料與安全", (r"備份|刪除|憑證|secret|token|個資|隱私|資料完整性|權限",)),
    ("rights", "素材與權利", (r"版權|授權素材|素材授權|素材權利|stock|著作權|商用",)),
    ("audio", "音訊與字幕", (r"音訊|字幕|混音|配音|聽檢|lufs|音量|旁白|srt|音樂",)),
    ("visual", "視覺與版面", (r"封面|縮圖|構圖|320\s*px|灰模|blender|運鏡|畫面|解析度|排版",)),
    ("workflow", "任務與流程", (r"paperclip|任務|工作流程|排程|審核|發布|發佈|上架",)),
)


def memory_topic(text: str) -> dict[str, str]:
    """The strongest hint wins; ties follow the fixed catalogue order."""
    scored = [
        (len(re.findall("|".join(patterns), text, flags=re.IGNORECASE)), topic_id, label)
        for topic_id, label, patterns in TOPICS
    ]
    score, topic_id, label = max(scored, key=lambda item: item[0])
    return {"id": topic_id, "label": label} if score else {"id": "general", "label": "其他專案規則"}
