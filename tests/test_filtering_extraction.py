from pathlib import Path

from session2memory.extraction import extract_candidates
from session2memory.filtering import is_noise
from session2memory.models import EvidencePointer, SessionMessage, SessionRecord
from session2memory.workspace import resolve_workspace


def pointer(text: str) -> EvidencePointer:
    return EvidencePointer(
        tool="codex",
        session_id="s1",
        source_path=Path("/tmp/session.jsonl"),
        message_start=1,
        message_end=1,
        workspace_path=Path("/tmp/repo"),
        digest="sha256:test",
    )


def message(index: int, role: str, text: str) -> SessionMessage:
    return SessionMessage(
        index=index, role=role, text=text, timestamp=None, raw_pointer=pointer(text)
    )


def record(messages: list[SessionMessage]) -> SessionRecord:
    return SessionRecord(
        tool="codex",
        session_id="s1",
        source_path=Path("/tmp/session.jsonl"),
        started_at=None,
        updated_at=None,
        cwd=Path("/tmp/repo"),
        repo_root=None,
        tool_workspace_id=None,
        messages=messages,
    )


def test_noise_filter_removes_system_prompt_agents_and_huge_output() -> None:
    assert is_noise(message(1, "system", "You are Codex, a coding agent based on GPT-5."))
    assert is_noise(message(2, "user", "# AGENTS.md instructions for /tmp/repo\n<INSTRUCTIONS>"))
    assert is_noise(message(3, "tool", "line\n" * 401))
    assert is_noise(message(4, "assistant", "Traceback (most recent call last):\nValueError: bad"))
    assert not is_noise(message(5, "assistant", "驗證：uv run pytest -q passed。"))


def test_noise_filter_removes_single_line_huge_tool_output() -> None:
    assert is_noise(message(1, "tool", "x" * 20_001))


def test_noise_filter_removes_wrapped_instruction_blocks() -> None:
    text = "wrapper header\n\nSome context before pasted policy.\n<INSTRUCTIONS>\nRules..."

    assert is_noise(message(1, "user", text))


def test_noise_filter_removes_plain_wrapped_agents_instruction_blocks() -> None:
    text = "wrapper header\n\nAGENTS.md instructions for /tmp/repo\nRules..."

    assert is_noise(message(1, "user", text))


def test_noise_filter_removes_plain_wrapped_claude_instruction_blocks() -> None:
    text = "wrapper header\n\nCLAUDE.md instructions for /tmp/repo\nRules..."

    assert is_noise(message(1, "user", text))


def test_noise_filter_removes_plain_wrapped_gemini_instruction_blocks() -> None:
    text = "wrapper header\n\nGEMINI.md instructions for /tmp/repo\nRules..."

    assert is_noise(message(1, "user", text))


def test_noise_filter_removes_markdown_agents_instruction_heading() -> None:
    assert is_noise(message(1, "user", "## AGENTS.md instructions for /tmp/repo"))


def test_noise_filter_removes_path_suffix_instruction_heading() -> None:
    assert is_noise(message(1, "user", "AGENTS.md instructions for /tmp/repo"))


def test_noise_filter_removes_markdown_claude_instruction_heading() -> None:
    assert is_noise(message(1, "user", "### CLAUDE.md instructions"))


def test_noise_filter_keeps_normal_claude_md_content() -> None:
    assert not is_noise(message(1, "assistant", "完成：更新 CLAUDE.md 的使用說明"))


def test_noise_filter_keeps_mid_sentence_claude_md_instructions_content() -> None:
    assert not is_noise(message(1, "assistant", "完成：更新 CLAUDE.md instructions 區段"))


def test_noise_filter_keeps_line_start_claude_md_instructions_prose() -> None:
    assert not is_noise(message(1, "assistant", "CLAUDE.md instructions 區段需要更新"))


def test_noise_filter_keeps_non_path_for_suffix_prose() -> None:
    assert not is_noise(
        message(1, "assistant", "CLAUDE.md instructions for contributors need update")
    )


def test_extracts_only_high_signal_candidates() -> None:
    session = record(
        [
            message(1, "user", "決定：P0 不用 LLM 摘要。"),
            message(2, "assistant", "驗證：uv run pytest -q passed。"),
            message(3, "assistant", "這裡只是一般聊天，沒有穩定記憶。"),
        ]
    )
    workspace = resolve_workspace(session)

    candidates = extract_candidates(session, workspace)

    assert [candidate.kind for candidate in candidates] == ["decision", "verification"]
    assert candidates[0].durable is True
    assert candidates[1].durable is True
    assert candidates[0].text == "P0 不用 LLM 摘要。"


def test_extraction_skips_negated_decision_marker() -> None:
    session = record([message(1, "user", "這不是決定：先別記")])
    workspace = resolve_workspace(session)

    assert extract_candidates(session, workspace) == []
