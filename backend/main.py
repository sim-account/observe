import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import upload, discovery, simulate, learnings

app = FastAPI(title="OBSERVE API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(upload.router, prefix="/api")
app.include_router(discovery.router, prefix="/api")
app.include_router(simulate.router, prefix="/api")
app.include_router(learnings.router, prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok"}
