from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from app.graph import JsonObject, canonical_json

TASK_FILES = {
    "REPOSITORY_BASELINE": "repository-baseline.md",
    "REQUIREMENT_CHANGE": "requirement-change.md",
    "IMPLEMENTATION": "implementation.md",
    "SEMANTIC_REVIEW": "semantic-review.md",
}


@dataclass(frozen=True, slots=True)
class Prompt:
    id: str
    version: str
    text: str
    hash: str


class PromptCatalog:
    def __init__(self, root: Path) -> None:
        self.root = root

    def load(self, task: str) -> Prompt:
        filename = TASK_FILES.get(task)
        if filename is None:
            raise ValueError(f"未知 Agent 任务：{task}")
        common = self._read("common.md")
        task_text = self._read(filename)
        prompt_id, version = _header(task_text)
        text = f"{common}\n\n{task_text}"
        return Prompt(prompt_id, version, text, hashlib.sha256(text.encode()).hexdigest())

    def render(self, task: str, context: JsonObject) -> Prompt:
        prompt = self.load(task)
        text = f"{prompt.text}\n\n# 本次运行上下文\n\n{canonical_json(context)}\n"
        return Prompt(prompt.id, prompt.version, text, prompt.hash)

    def text(self, task: str) -> str:
        return self.load(task).text

    def _read(self, filename: str) -> str:
        return (self.root / filename).read_text(encoding="utf-8").strip()


def _header(text: str) -> tuple[str, str]:
    first = text.splitlines()[0].removeprefix("# ")
    prompt_id, separator, version = first.partition("@")
    if not separator or not prompt_id or not version:
        raise ValueError("Prompt 首行必须是 # <id>@<version>")
    return prompt_id, version
