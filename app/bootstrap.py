from __future__ import annotations

from typing import cast

from app.config import Settings
from app.graph import JsonObject, load_object
from app.store import Store


def load_store(settings: Settings) -> tuple[Store, JsonObject, tuple[JsonObject, ...]]:
    seed = load_object(settings.model_path)
    revision = cast(JsonObject, seed["revision"])
    base_id = revision.get("baseRevisionId")
    base_path = settings.revision_dir / f"{base_id}.json"
    history = (load_object(base_path),) if isinstance(base_id, str) and base_path.is_file() else ()
    store = Store(settings.data_dir / "review.sqlite3", load_object(settings.graph_schema_path))
    return store, seed, history
