"""The reconcile check scores what happened, not merely that something changed."""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

from session2memory.auto.curate import LlmReply

Script = Callable[[Callable[[str], str]], list[dict[str, object]]]


@pytest.fixture(scope="module")
def cases() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "evals" / "reconcile_cases.py"
    spec = importlib.util.spec_from_file_location("reconcile_cases", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def scored(cases: ModuleType, project: str, script: Script):  # type: ignore[no-untyped-def]
    """Run one case against a model that answers with ``script``."""

    def runner(prompt: str) -> LlmReply:
        listed = {}
        for line in prompt.splitlines():
            if line.startswith("- m_"):
                memory_id, _kind, _day, text = line[2:].split("｜", 3)
                listed[text] = memory_id

        def find(fragment: str) -> str:
            return next(memory_id for text, memory_id in listed.items() if fragment in text)

        return LlmReply(text=json.dumps({"memory": script(find)}, ensure_ascii=False))

    case = next(one for one in cases.CASES if one.project == project)
    score = cases.Score()
    cases.run_case(case, runner, score)
    return score


def test_a_retirement_counts_only_when_it_names_the_entry_that_replaced_it(
    cases: ModuleType,
) -> None:
    def retire(because: str) -> Script:
        return lambda find: [
            {"op": "retire", "id": find("用 Python len"), "because": [find(because)], "reason": "x"}
        ]

    right = scored(cases, "/eval/novel", retire("4800–5600"))
    wrong = scored(cases, "/eval/novel", retire("第三人稱"))

    assert (right.contradictions_found, right.contradictions) == (1, 1)
    assert (wrong.contradictions_found, wrong.contradictions) == (0, 1)
    assert right.kept == wrong.kept == right.keep


def test_a_merge_counts_only_for_the_right_members_and_content(cases: ModuleType) -> None:
    def merge(fragments: list[str], text: str, kind: str = "fact") -> Script:
        return lambda find: [
            {"op": "merge", "ids": [find(f) for f in fragments], "kind": kind, "text": text}
        ]

    thumbs = ["縮圖等比", "MealThumbnail"]
    right = scored(cases, "/eval/app", merge(thumbs, "縮圖等比縮到長邊 320 以內，輸出 JPEG。"))
    mixed = scored(cases, "/eval/app", merge([*thumbs, "除非使用者"], "所有原規則一律作廢。"))
    emptied = scored(cases, "/eval/app", merge(thumbs, "縮圖有規則。"))

    assert right.repeats_merged == 1, "one of the two groups"
    assert mixed.repeats_merged == 0 and emptied.repeats_merged == 0


def test_an_untouched_memory_is_all_left_alone(cases: ModuleType) -> None:
    quiet = scored(cases, "/eval/stack", lambda find: [])

    assert (quiet.kept, quiet.keep, quiet.wrong) == (8, 8, [])
