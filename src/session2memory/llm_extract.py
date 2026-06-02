from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from importlib import resources
from typing import Literal, Protocol

from session2memory.models import MemoryCandidate, MemoryKind, SessionMessage
from session2memory.review_normalize import normalize_review_text

_LLM_KINDS: frozenset[str] = frozenset(
    {"decision", "completed", "pitfall", "constraint", "verification", "daily"}
)
LlmInputMode = Literal["argument", "stdin"]


class LlmExtractError(RuntimeError):
    pass


@dataclass(frozen=True)
class LlmExtractItem:
    kind: MemoryKind
    text: str
    evidence_quote: str
    confidence: float
    durable_suggestion: bool
    message_index: int


class LlmExtractBackend(Protocol):
    def extract(
        self,
        *,
        messages: Sequence[SessionMessage],
        workspace_id: str,
    ) -> list[LlmExtractItem]:
        raise NotImplementedError


def merge_llm_candidates(
    *,
    existing: Sequence[MemoryCandidate],
    llm_candidates: Sequence[MemoryCandidate],
) -> list[MemoryCandidate]:
    seen_text = {normalize_review_text(candidate.text) for candidate in existing}
    seen_ranges = {
        (
            candidate.kind,
            candidate.evidence.tool,
            candidate.evidence.session_id,
            candidate.evidence.message_start,
        )
        for candidate in existing
    }
    merged: list[MemoryCandidate] = []
    for candidate in llm_candidates:
        normalized = normalize_review_text(candidate.text)
        if not normalized or normalized in seen_text:
            continue
        key = (
            candidate.kind,
            candidate.evidence.tool,
            candidate.evidence.session_id,
            candidate.evidence.message_start,
        )
        if key in seen_ranges:
            continue
        merged.append(candidate)
        seen_text.add(normalized)
        seen_ranges.add(key)
    return merged


def items_to_candidates(
    *,
    items: Sequence[LlmExtractItem],
    messages: Sequence[SessionMessage],
    workspace_id: str,
) -> list[MemoryCandidate]:
    by_index = {message.index: message for message in messages}
    candidates: list[MemoryCandidate] = []
    for item in items:
        message = by_index.get(item.message_index)
        if message is None:
            continue
        candidates.append(
            MemoryCandidate(
                kind=item.kind,
                text=item.text,
                workspace_id=workspace_id,
                evidence=message.raw_pointer,
                durable=item.durable_suggestion,
                extraction="llm",
                confidence=item.confidence,
                evidence_quote=item.evidence_quote,
            )
        )
    return candidates


def parse_llm_extract_payload(raw: str) -> list[LlmExtractItem]:
    payload = json.loads(raw)
    if not isinstance(payload, list):
        return []
    items: list[LlmExtractItem] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("kind", ""))
        if kind not in _LLM_KINDS:
            continue
        text = str(row.get("text", "")).strip()
        if not text:
            continue
        try:
            message_index = int(row.get("message_index", 0))
        except (TypeError, ValueError):
            continue
        try:
            confidence = float(row.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        items.append(
            LlmExtractItem(
                kind=kind,  # type: ignore[arg-type]
                text=text,
                evidence_quote=str(row.get("evidence_quote", "")).strip(),
                confidence=max(0.0, min(1.0, confidence)),
                durable_suggestion=bool(row.get("durable_suggestion", False)),
                message_index=message_index,
            )
        )
    return items


def _messages_for_prompt(messages: Sequence[SessionMessage]) -> str:
    lines: list[str] = []
    for message in messages:
        lines.append(f"[{message.index}] {message.role}: {message.text}")
    return "\n".join(lines)


class SubprocessLlmExtractBackend:
    def __init__(
        self,
        *,
        command: str | None = None,
        timeout_seconds: int = 120,
        input_mode: LlmInputMode = "argument",
        strict: bool = False,
    ) -> None:
        self._command = command or os.environ.get("SESSION2MEMORY_LLM_CMD", "")
        self._timeout_seconds = timeout_seconds
        self._input_mode = input_mode
        self._strict = strict
        self.last_error: str | None = None

    def extract(
        self,
        *,
        messages: Sequence[SessionMessage],
        workspace_id: str,
    ) -> list[LlmExtractItem]:
        self.last_error = None
        if not self._command.strip():
            return self._fail("LLM extraction command is not configured")
        prompt = _render_prompt(messages=messages, workspace_id=workspace_id)
        try:
            completed = subprocess.run(
                self._argv(prompt),
                check=False,
                text=True,
                input=prompt if self._input_mode == "stdin" else None,
                capture_output=True,
                timeout=self._timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return self._fail(
                f"LLM extraction command timed out after {self._timeout_seconds}s"
            )
        if completed.returncode != 0:
            stderr = completed.stderr.strip()
            detail = f": {stderr}" if stderr else ""
            return self._fail(
                f"LLM extraction command failed with exit code {completed.returncode}{detail}"
            )
        raw = _extract_json_array(completed.stdout)
        if not raw:
            return self._fail("LLM extraction command returned no JSON")
        try:
            return parse_llm_extract_payload(raw)
        except json.JSONDecodeError:
            return self._fail("LLM extraction command returned invalid JSON")

    def _argv(self, prompt: str) -> list[str]:
        argv = shlex.split(self._command)
        if "{prompt}" in argv:
            return [prompt if item == "{prompt}" else item for item in argv]
        if self._input_mode == "stdin":
            return argv
        return [*argv, prompt]

    def _fail(self, message: str) -> list[LlmExtractItem]:
        self.last_error = message
        if self._strict:
            raise LlmExtractError(message)
        return []


def _render_prompt(*, messages: Sequence[SessionMessage], workspace_id: str) -> str:
    template = resources.files("session2memory").joinpath("prompts/llm_extract.txt").read_text(
        encoding="utf-8"
    )
    return template.format(
        workspace_id=workspace_id,
        transcript=_messages_for_prompt(messages),
    )


def _extract_json_array(stdout: str) -> str:
    match = re.search(r"\[[\s\S]*\]", stdout)
    return match.group(0) if match else stdout.strip()
