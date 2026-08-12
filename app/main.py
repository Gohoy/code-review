from __future__ import annotations

import logging
from typing import cast

import uvicorn
from starlette.applications import Starlette

from app.config import Settings
from app.graph import JsonObject, load_object
from app.http import create_app
from app.runner import Runner
from app.service import ReviewService
from app.store import Store


def build_application(settings: Settings | None = None) -> Starlette:
    current = settings or Settings.from_env()
    seed = load_object(current.model_path)
    revision = cast(JsonObject, seed["revision"])
    base_id = revision.get("baseRevisionId")
    base_path = current.revision_dir / f"{base_id}.json"
    history = (load_object(base_path),) if isinstance(base_id, str) and base_path.is_file() else ()
    schema = load_object(current.graph_schema_path)
    store = Store(current.data_dir / "review.sqlite3", schema)
    service = ReviewService(store, Runner(current), seed, history)
    return create_app(service, current.web_dir)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings.from_env()
    uvicorn.run(build_application(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
