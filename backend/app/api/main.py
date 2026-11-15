from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import auth, groups, logs, members, settings
from app.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="Bot Talker 2 — Admin API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(auth.router)
app.include_router(settings.router)
app.include_router(groups.router)
app.include_router(members.router)
app.include_router(logs.router)

