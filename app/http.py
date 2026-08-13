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
        return JSONResponse(await service.status(), headers={"Cache-Control": "no-store"})

    async def revision(request: Request) -> JSONResponse:
        document = await service.revision(request.path_params["revision_id"])
        content_hash = cast(JsonObject, document["revision"])["contentHash"]
        etag = f'"{content_hash}"'
        if request.headers.get("if-none-match") == etag:
            return Response(
                status_code=304,
                headers={
                    "Cache-Control": "private, max-age=31536000, immutable",
                    "ETag": etag,
                },
            )
        return JSONResponse(
            document,
            headers={
                "Cache-Control": "private, max-age=31536000, immutable",
                "ETag": etag,
            },
        )

    async def graph(request: Request) -> Response:
        revision_id = request.query_params.get("revisionId")
        if not revision_id:
            raise GraphError("revisionId 必须显式提供")
        layer = request.query_params.get("layer")
        if not layer:
            raise GraphError("layer 必须显式提供")
        layers = {value for value in layer.split(",") if value}
        focus_id = request.query_params.get("focusId") or None
        view_mode = request.query_params.get("viewMode")
        if not view_mode:
            raise GraphError("viewMode 必须显式提供")
        direction = request.query_params.get("direction")
        if not direction:
            raise GraphError("direction 必须显式提供")
        svg, content_hash = await service.graph_svg(
            revision_id, layers, focus_id, view_mode, direction
        )
        etag = f'"{content_hash}:{layer}:{focus_id or ""}:{view_mode}:{direction}"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return Response(
            svg,
            media_type="image/svg+xml",
            headers={
                "Cache-Control": "private, max-age=31536000, immutable",
                "ETag": etag,
            },
        )

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
        agent_run_id = await service.submit_message(content)
        return JSONResponse(
            {"agentRunId": agent_run_id, "status": "AGENT_RUNNING"}, status_code=202
        )

    async def repository_baseline(_: Request) -> JSONResponse:
        agent_run_id = await service.start_repository_baseline()
        return JSONResponse(
            {"agentRunId": agent_run_id, "status": "AGENT_RUNNING"}, status_code=202
        )

    async def approve(request: Request) -> JSONResponse:
        body = await _body(request)
        content_hash = body.get("contentHash")
        if not isinstance(content_hash, str):
            raise GraphError("contentHash 必须是字符串")
        run_id = await service.approve_and_start(request.path_params["revision_id"], content_hash)
        return JSONResponse({"runId": run_id, "status": "PENDING"}, status_code=202)

    async def retry_delivery(request: Request) -> JSONResponse:
        body = await _body(request)
        content_hash = body.get("contentHash")
        if not isinstance(content_hash, str):
            raise GraphError("contentHash 必须是字符串")
        if body.get("confirmed") is not True:
            raise GraphError("重新自动交付必须由用户明确确认")
        run_id = await service.retry_delivery(request.path_params["revision_id"], content_hash)
        return JSONResponse({"runId": run_id, "status": "PENDING"}, status_code=202)

    async def expected_error(_: Request, error: Exception) -> JSONResponse:
        return JSONResponse({"error": str(error)}, status_code=409)

    async def unexpected_error(_: Request, error: Exception) -> JSONResponse:
        logger.exception("HTTP 请求处理失败", exc_info=error)
        return JSONResponse({"error": "服务器处理失败"}, status_code=500)

    routes = [
        Route("/healthz", health),
        Route("/api/state", state),
        Route("/api/revision/{revision_id:str}", revision),
        Route("/api/graph.svg", graph),
        Route("/api/requirement/{focus_id:str}/context", requirement_context),
        Route("/api/message", message, methods=["POST"]),
        Route("/api/repository/baseline", repository_baseline, methods=["POST"]),
        Route(
            "/api/revision/{revision_id:str}/approve-and-start",
            approve,
            methods=["POST"],
        ),
        Route(
            "/api/revision/{revision_id:str}/retry-delivery",
            retry_delivery,
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
