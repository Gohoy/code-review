from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from app.graph import GraphError, JsonObject
from app.service import ReviewService
from app.store import StoreError

logger = logging.getLogger(__name__)


def create_app(service: ReviewService, web_dir: Path) -> Starlette:
    @asynccontextmanager
    async def lifespan(_: Starlette):
        await service.initialize()
        yield
        await service.close()

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def state(_: Request) -> JSONResponse:
        return JSONResponse(await service.state(), headers={"Cache-Control": "no-store"})

    async def graph(request: Request) -> Response:
        layers = {
            value
            for value in request.query_params.get("layers", "requirement,design").split(",")
            if value
        }
        focus_id = request.query_params.get("focusId") or None
        svg = await service.graph_svg(layers, focus_id)
        return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "no-store"})

    async def requirement_context(request: Request) -> JSONResponse:
        return JSONResponse(
            await service.requirement_context(request.path_params["focus_id"]),
            headers={"Cache-Control": "no-store"},
        )

    async def message(request: Request) -> JSONResponse:
        body = await _body(request)
        content = body.get("content")
        if not isinstance(content, str):
            raise GraphError("content 必须是字符串")
        await service.submit_message(content)
        return JSONResponse({"status": "MODELING"}, status_code=202)

    async def approve(request: Request) -> JSONResponse:
        body = await _body(request)
        content_hash = body.get("contentHash")
        if not isinstance(content_hash, str):
            raise GraphError("contentHash 必须是字符串")
        run_id = await service.approve_and_start(request.path_params["revision_id"], content_hash)
        return JSONResponse({"runId": run_id, "status": "PENDING"}, status_code=202)

    async def expected_error(_: Request, error: Exception) -> JSONResponse:
        return JSONResponse({"error": str(error)}, status_code=409)

    async def unexpected_error(_: Request, error: Exception) -> JSONResponse:
        logger.exception("HTTP 请求处理失败", exc_info=error)
        return JSONResponse({"error": "服务器处理失败"}, status_code=500)

    routes = [
        Route("/healthz", health),
        Route("/api/state", state),
        Route("/api/graph.svg", graph),
        Route("/api/requirement/{focus_id:str}/context", requirement_context),
        Route("/api/message", message, methods=["POST"]),
        Route(
            "/api/revision/{revision_id:str}/approve-and-start",
            approve,
            methods=["POST"],
        ),
        Mount("/", StaticFiles(directory=web_dir, html=True, check_dir=False), name="web"),
    ]
    return Starlette(
        routes=routes,
        lifespan=lifespan,
        exception_handlers={
            GraphError: expected_error,
            StoreError: expected_error,
            Exception: unexpected_error,
        },
    )


async def _body(request: Request) -> JsonObject:
    try:
        value = await request.json()
    except Exception as error:
        raise GraphError("请求体必须是 JSON 对象") from error
    if not isinstance(value, dict):
        raise GraphError("请求体必须是 JSON 对象")
    return cast(JsonObject, value)
