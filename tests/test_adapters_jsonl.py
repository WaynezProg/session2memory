import json
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


def test_codex_adapter_skips_malformed_jsonl_file_and_keeps_valid_session(
    tmp_path: Path,
) -> None:
    root = tmp_path / "codex"
    date_dir = root / "2026" / "05" / "22"
    date_dir.mkdir(parents=True)
    valid_path = date_dir / "valid.jsonl"
    bad_path = date_dir / "bad.jsonl"
    valid_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {
                            "id": "valid-1",
                            "timestamp": "2026-05-22T01:00:00Z",
                            "cwd": "/tmp/repo",
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "Decision: keep valid."}],
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    bad_path.write_text("{bad json\n", encoding="utf-8")
    adapter = CodexAdapter(root)

    records = list(adapter.iter_sessions("2026-05-22"))

    assert len(records) == 1
    assert records[0].session_id == "valid-1"
    assert any(bad_path.as_posix() in reason for reason in adapter.skipped)
    assert any("malformed JSON" in reason for reason in adapter.skipped)
