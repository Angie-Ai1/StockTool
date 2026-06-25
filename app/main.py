"""Phase 0 placeholder entrypoint — verifies Docker/Poetry infra runs end-to-end.

Phase 1.1 will replace this with the real FastAPI app (routers, config, db).
"""

from fastapi import FastAPI

app = FastAPI(title="Stocktool")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
