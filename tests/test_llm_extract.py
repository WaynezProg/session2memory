import json
import sys
from pathlib import Path

import pytest

from session2memory.llm_extract import (
    LlmExtractError,
    LlmExtractItem,
    SubprocessLlmExtractBackend,
    items_to_candidates,
    merge_llm_candidates,
    parse_llm_extract_payload,
)
from session2memory.llm_extract_mock import MockLlmExtractBackend
from session2memory.models import EvidencePointer, MemoryCandidate, SessionMessage


def _msg(text: str, index: int = 1) -> SessionMessage:
    evidence = EvidencePointer(
        tool="codex",
        session_id="s1",
        source_path=Path("/tmp/s.jsonl"),
        message_start=index,
        message_end=index,
        workspace_path=Path("/tmp/repo"),
        digest="sha256:x",
    )
    return SessionMessage(
        index=index,
        role="assistant",
        text=text,
        timestamp=None,
        raw_pointer=evidence,
    )


def test_mock_backend_parses_items() -> None:
    payload = [
        {
            "kind": "decision",
            "text": "Use uv",
            "evidence_quote": "use uv for python",
            "confidence": 0.9,
            "durable_suggestion": False,
            "message_index": 1,
        }
    ]
    backend = MockLlmExtractBackend(items_payload=payload)
    items = backend.extract(messages=[_msg("use uv for python")], workspace_id="repo-123")
    assert len(items) == 1
    assert items[0].text == "Use uv"


def test_merge_dedupes_against_marker_candidate() -> None:
    evidence = _msg("Decision: use pip").raw_pointer
    marker = MemoryCandidate(
        kind="decision",
        text="use pip",
        workspace_id="repo-123",
        evidence=evidence,
        durable=True,
        extraction="marker",
    )
    llm = MemoryCandidate(
        kind="decision",
        text="use pip",
        workspace_id="repo-123",
        evidence=evidence,
        durable=False,
        extraction="llm",
        confidence=0.7,
    )
    merged = merge_llm_candidates(existing=[marker], llm_candidates=[llm])
    assert merged == []


def test_items_to_candidates_maps_message_index() -> None:
    messages = [_msg("hello world", 2)]
    items = [
        LlmExtractItem(
            kind="decision",
            text="Greet",
            evidence_quote="hello",
            confidence=0.8,
            durable_suggestion=False,
            message_index=2,
        )
    ]
    out = items_to_candidates(items=items, messages=messages, workspace_id="repo-123")
    assert len(out) == 1
    assert out[0].extraction == "llm"
    assert out[0].evidence.message_start == 2


def test_parse_llm_extract_payload_rejects_invalid_rows() -> None:
    payload = json.dumps([{"kind": "decision", "text": "ok", "message_index": 1, "confidence": 2}])
    items = parse_llm_extract_payload(payload)
    assert len(items) == 1
    assert items[0].confidence == 1.0


def test_subprocess_backend_can_send_prompt_on_stdin(tmp_path: Path) -> None:
    script = tmp_path / "fake_llm.py"
    script.write_text(
        "import json, sys\n"
        "prompt = sys.stdin.read()\n"
        "assert 'workspace_id' in prompt\n"
        "print(json.dumps([{\n"
        "  'kind': 'decision',\n"
        "  'text': 'Use stdin transport',\n"
        "  'evidence_quote': prompt[:20],\n"
        "  'confidence': 0.74,\n"
        "  'durable_suggestion': True,\n"
        "  'message_index': 1,\n"
        "}]))\n",
        encoding="utf-8",
    )
    backend = SubprocessLlmExtractBackend(
        command=f"{sys.executable} {script}",
        input_mode="stdin",
    )

    items = backend.extract(messages=[_msg("Decision: use stdin")], workspace_id="repo-123")

    assert len(items) == 1
    assert items[0].text == "Use stdin transport"
    assert backend.last_error is None


def test_subprocess_backend_strict_mode_raises_on_failed_command(tmp_path: Path) -> None:
    script = tmp_path / "bad_llm.py"
    script.write_text("import sys\nsys.stderr.write('boom')\nsys.exit(7)\n", encoding="utf-8")
    backend = SubprocessLlmExtractBackend(
        command=f"{sys.executable} {script}",
        strict=True,
    )

    with pytest.raises(LlmExtractError, match="exit code 7"):
        backend.extract(messages=[_msg("hello")], workspace_id="repo-123")
