from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.pipeline.routes import router as pipeline_router
from app.incidents.routes import router as incident_router
from app.replay.routes import router as replay_router

app = FastAPI(title="ProofLine AI")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5174", "http://127.0.0.1:5174"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "proofline-ai"}


app.include_router(pipeline_router)

app.include_router(incident_router)

app.include_router(replay_router)
