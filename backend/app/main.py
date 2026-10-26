"""针语平台后端入口。"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import VERSION
from app.config import DATA_DIR, FRONTEND_DIR, ROOT_DIR, settings
from app.db.base import init_db
from app.errors import register_exception_handlers
from app.routers import admin, auth, community, generate, home, knowledge, points, stitches

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
)
log = logging.getLogger("zhenyu")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="湘绣文化数字化传播与消费平台 MVP：AI 成品图生成 + 规则化针法推荐 + 可追溯知识问答。",
    )
    register_exception_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    api = FastAPI(title=settings.app_name + " API")
    # 子应用要有自己的异常处理器，否则 422 会以 FastAPI 原生格式漏出
    register_exception_handlers(api)
    for mod in (auth, generate, stitches, knowledge, community, points, admin, home):
        api.include_router(mod.router)
    app.mount("/api", api)

    @app.middleware("http")
    async def _revalidate_static(request, call_next):
        """开发/内测期：页面与前端资源强制每次回源校验。

        没有 Cache-Control 时浏览器会按 Last-Modified 做启发式缓存（约为文件年龄的 10%），
        改完 CSS/JS 刷新页面仍是旧样，是最常见的「改了没生效」来源。
        页面路由（/generate、/community 等）没有扩展名，不能用后缀判断，故按前缀排除接口与文件服务。
        """
        resp = await call_next(request)
        path = request.url.path
        if not path.startswith(("/api", "/files", "/docs", "/openapi")):
            resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        return resp

    @app.get("/health", tags=["系统"])
    def health():
        return {
            "status": "ok",
            "app": settings.app_name,
            "version": VERSION,
            "provider": settings.image_provider,
            "ts": int(time.time()),
        }

    storage = Path(settings.storage_dir)
    storage.mkdir(parents=True, exist_ok=True)
    app.mount("/files", StaticFiles(directory=str(storage)), name="files")

    if FRONTEND_DIR.exists():
        app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIR / "assets")), name="assets")

        @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
        def _index():
            return FileResponse(FRONTEND_DIR / "index.html")

        for name, file in {
            "generate": "generate.html",
            "stitches": "stitches.html",
            "knowledge": "knowledge.html",
            "community": "community.html",
            "me": "me.html",
            "admin": "admin.html",
            "about": "about.html",
        }.items():
            _register_page(app, name, file)
    else:  # pragma: no cover
        @app.get("/", include_in_schema=False)
        def _fallback():
            return JSONResponse({"message": "frontend not found"})

    return app


def _register_page(app: FastAPI, name: str, filename: str) -> None:
    def _handler():
        return FileResponse(FRONTEND_DIR / filename)

    # 同时接受 HEAD：预检、监控探活与部分爬虫只发 HEAD，只注册 GET 会返回 405
    app.api_route(f"/{name}", methods=["GET", "HEAD"], include_in_schema=False)(_handler)


app = create_app()


@app.on_event("startup")
def _bootstrap() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db_file = Path(str(settings.db_url).replace("sqlite:///", ""))
    first_run = not db_file.exists()
    init_db()
    if first_run:
        try:
            from app.db.seed import seed_all

            seed_all()
            log.info("初始种子数据已写入")
        except Exception as exc:  # noqa: BLE001
            log.warning("种子数据写入失败: %s", exc)
    log.info("%s v%s 已启动 (provider=%s, root=%s)",
             settings.app_name, VERSION, settings.image_provider, ROOT_DIR)
