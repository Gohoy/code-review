from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True, slots=True)
class Settings:
    host: str
    port: int
    data_dir: Path
    repository: Path
    worktree_root: Path
    web_dir: Path
    model_path: Path
    revision_dir: Path
    graph_schema_path: Path
    agent_result_schema_path: Path
    prompt_dir: Path

    @classmethod
    def from_env(cls) -> Settings:
        data_dir = Path(os.getenv("SDBP_REVIEW_DATA_DIR", ROOT / "data")).resolve()
        repository = Path(os.getenv("SDBP_REVIEW_REPOSITORY", ROOT)).resolve()
        return cls(
            host=os.getenv("SDBP_REVIEW_HOST", "127.0.0.1"),
            port=int(os.getenv("SDBP_REVIEW_PORT", "8417")),
            data_dir=data_dir,
            repository=repository,
            worktree_root=Path(
                os.getenv("SDBP_REVIEW_WORKTREE_ROOT", data_dir / "worktree")
            ).resolve(),
            web_dir=ROOT / "prototype" / "dist" / "client",
            model_path=ROOT / "model" / "review-tool.json",
            revision_dir=ROOT / "model" / "revision",
            graph_schema_path=ROOT / "model" / "graph.schema.json",
            agent_result_schema_path=ROOT / "app" / "schema" / "agent-result.schema.json",
            prompt_dir=ROOT / "prompt",
        )
