"""Phase 0 placeholder entrypoint — verifies Docker/Poetry infra runs end-to-end.

Phase 1.1 will replace this with the real FastAPI app (routers, config, db).
"""

from fastapi import FastAPI

app = FastAPI(title="Stocktool")


@app.get("/health")  # not /healthz — Cloud Run's default *.run.app domain reserves that exact path and never forwards it to the container
def health() -> dict[str, str]:
    return {"status": "ok"}
