"""FastAPI application entry point.

Run from the WEwards/ directory:
    uvicorn backend.main:app --reload --port 8000
"""
from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .db import init_db
from .routers import (
    blacklist,
    benefits,
    cards,
    catalog,
    categories,
    household,
    ingestion,
    pipeline,
    profiles,
    references,
    redemption,
    run,
    watchlist,
)

# Create tables at import time so they exist under uvicorn AND test clients.
init_db()

app = FastAPI(title="WEwards — Credit Card Household Rewards Dashboard", version="1.0.0")

# Dev: Vite runs on :5173 and proxies /api to :8000. CORS is permissive for
# localhost only.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "llm_available": config.llm_available(),
        "crypto_available": config.crypto_available(),
    }


@app.get("/api/users")
def users():
    return config.USERS


for r in (
    cards,
    catalog,
    categories,
    benefits,
    watchlist,
    blacklist,
    profiles,
    pipeline,
    household,
    references,
    redemption,
    ingestion,
    run,
):
    app.include_router(r.router)


# --- Optionally serve the built frontend (npm run build → frontend/dist) ----
_DIST = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend", "dist")
if os.path.isdir(_DIST):
    app.mount("/assets", StaticFiles(directory=os.path.join(_DIST, "assets")), name="assets")

    @app.get("/")
    def _index():
        return FileResponse(os.path.join(_DIST, "index.html"))

    @app.get("/{full_path:path}")
    def _spa(full_path: str):
        # SPA fallback for client-side routing. API misses must stay JSON.
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API route not found")
        candidate = os.path.join(_DIST, full_path)
        if os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(_DIST, "index.html"))
