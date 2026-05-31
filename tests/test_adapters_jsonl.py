import json
import os
from datetime import datetime
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


def test_codex_adapter_includes_previous_session_modified_on_requested_date(
    tmp_path: Path,
) -> None:
    root = tmp_path / "codex"
    session_dir = root / "2026" / "05" / "22"
    session_dir.mkdir(parents=True)
    session_path = session_dir / "old-session.jsonl"
    session_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {
                            "id": "reopened-1",
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
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "決定：reopened session 也要進今天 import。",
                                }
                            ],
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    modified_at = datetime(2026, 5, 26, 9, 0).timestamp()
    os.utime(session_path, (modified_at, modified_at))

    records = list(CodexAdapter(root).iter_sessions("2026-05-26"))

    assert [record.session_id for record in records] == ["reopened-1"]


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


def test_qwen_adapter_includes_session_updated_on_requested_date(
    tmp_path: Path,
) -> None:
    root = tmp_path / "qwen"
    session_path = root / "projects" / "repo" / "chats" / "session.jsonl"
    session_path.parent.mkdir(parents=True)
    session_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "sessionId": "qwen-reopened",
                        "timestamp": "2026-05-22T03:00:00Z",
                        "type": "user",
                        "cwd": "/tmp/repo",
                        "message": {
                            "role": "user",
                            "parts": [{"text": "決定：原本的 Qwen 需求。"}],
                        },
                    }
                ),
                json.dumps(
                    {
                        "sessionId": "qwen-reopened",
                        "timestamp": "2026-05-26T03:00:00Z",
                        "type": "model",
                        "cwd": "/tmp/repo",
                        "message": {
                            "role": "model",
                            "parts": [{"text": "決定：Qwen 續聊新增的需求。"}],
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    records = list(QwenAdapter(root).iter_sessions("2026-05-26"))

    assert [record.session_id for record in records] == ["qwen-reopened"]


def test_qwen_adapter_discovers_modified_session_outside_primary_chat_paths(
    tmp_path: Path,
) -> None:
    root = tmp_path / "qwen"
    session_path = root / "2026" / "05" / "22" / "session.jsonl"
    session_path.parent.mkdir(parents=True)
    session_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "sessionId": "qwen-modified",
                        "timestamp": "2026-05-22T03:00:00Z",
                        "type": "model",
                        "cwd": "/tmp/repo",
                        "message": {
                            "role": "model",
                            "parts": [{"text": "決定：mtime discovery 不只 Codex 要做。"}],
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    modified_at = datetime(2026, 5, 26, 9, 0).timestamp()
    os.utime(session_path, (modified_at, modified_at))

    records = list(QwenAdapter(root).iter_sessions("2026-05-26"))

    assert [record.session_id for record in records] == ["qwen-modified"]


def test_claude_adapter_reads_jsonl_content_blocks() -> None:
    records = list(ClaudeAdapter(Path("tests/fixtures/claude")).iter_sessions("2026-05-22"))

    assert len(records) == 1
    assert records[0].tool == "claude"
    assert records[0].session_id == "claude-1"
    assert records[0].messages[0].text == "坑：不要把 raw transcript 丟進 HKS。"


def test_claude_adapter_includes_session_updated_on_requested_date(
    tmp_path: Path,
) -> None:
    root = tmp_path / "claude"
    session_path = root / "project" / "session.jsonl"
    session_path.parent.mkdir(parents=True)
    session_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "sessionId": "claude-reopened",
                        "timestamp": "2026-05-22T03:00:00Z",
                        "type": "user",
                        "cwd": "/tmp/repo",
                        "message": {
                            "role": "user",
                            "content": "決定：原本的需求。",
                        },
                    }
                ),
                json.dumps(
                    {
                        "sessionId": "claude-reopened",
                        "timestamp": "2026-05-26T03:00:00Z",
                        "type": "assistant",
                        "cwd": "/tmp/repo",
                        "message": {
                            "role": "assistant",
                            "content": "決定：續聊新增的需求。",
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    records = list(ClaudeAdapter(root).iter_sessions("2026-05-26"))

    assert [record.session_id for record in records] == ["claude-reopened"]


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
