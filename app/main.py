"""应用入口。"""
import logging
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import admin, auth, jobs, system, uploads
from app.api import hr, integrations
from app.core.config import get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("app")

settings = get_settings()

app = FastAPI(title=settings.app_name, version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def access_log(request: Request, call_next):
    t0 = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse({"detail": "internal error"}, status_code=500)
    dt = (time.perf_counter() - t0) * 1000
    logger.info("%s %s -> %s (%.1fms)", request.method, request.url.path, response.status_code, dt)
    return response


app.include_router(system.router, prefix="/api/v1")
app.include_router(auth.router, prefix="/api/v1")
app.include_router(jobs.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")
app.include_router(uploads.router, prefix="/api/v1")

app.include_router(integrations.router, prefix="/api/v1")

app.include_router(hr.router, prefix="/api/v1")
