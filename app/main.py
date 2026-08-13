from __future__ import annotations

import logging

import uvicorn
from starlette.applications import Starlette

from app.bootstrap import load_store
from app.config import Settings
from app.http import create_app
from app.runner import Runner
from app.service import ReviewService


def build_application(settings: Settings | None = None) -> Starlette:
    current = settings or Settings.from_env()
    store, seed, history = load_store(current)
    service = ReviewService(store, Runner(current), seed, history)
    return create_app(service, current.web_dir)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings.from_env()
    uvicorn.run(build_application(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
