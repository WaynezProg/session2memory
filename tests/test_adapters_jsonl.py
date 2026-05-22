from pathlib import Path

from session2memory.adapters.claude import ClaudeAdapter
from session2memory.adapters.codex import CodexAdapter
from session2memory.adapters.qwen import QwenAdapter


def test_codex_adapter_reads_session_meta_and_messages() -> None:
    records = list(CodexAdapter(Path("tests/fixtures/codex")).iter_sessions("2026-05-22"))

    assert len(records) == 1
    assert records[0].tool == "codex"
    assert records[0].session_id == "codex-1"
    assert records[0].cwd == Path("/tmp/repo/sub")
    assert [message.role for message in records[0].messages] == ["user", "assistant"]
    assert records[0].messages[0].raw_pointer.message_start == 2


def test_qwen_adapter_reads_jsonl_messages() -> None:
    records = list(QwenAdapter(Path("tests/fixtures/qwen")).iter_sessions("2026-05-22"))

    assert len(records) == 2
    fixture_record = next(record for record in records if record.session_id == "qwen-1")
    assert fixture_record.tool == "qwen"
    assert fixture_record.messages[0].text == "決定：P0 不用 LLM 摘要。"


def test_qwen_adapter_reads_projects_root_jsonl_messages() -> None:
    records = list(QwenAdapter(Path("tests/fixtures/qwen")).iter_sessions("2026-05-22"))

    real_root_record = next(record for record in records if record.session_id == "qwen-projects-1")
    assert real_root_record.tool == "qwen"
    assert real_root_record.messages[0].text == "決定：Qwen real root 使用 projects/*/chats。"


def test_claude_adapter_reads_jsonl_content_blocks() -> None:
    records = list(ClaudeAdapter(Path("tests/fixtures/claude")).iter_sessions("2026-05-22"))

    assert len(records) == 1
    assert records[0].tool == "claude"
    assert records[0].session_id == "claude-1"
    assert records[0].messages[0].text == "坑：不要把 raw transcript 丟進 HKS。"
