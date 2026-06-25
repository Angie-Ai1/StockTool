from fastapi import FastAPI

from app.routers import line_webhook, liff, oauth_callback, tick

app = FastAPI(title="Stocktool")


@app.get("/health")  # not /healthz — Cloud Run's *.run.app domain reserves that path and never forwards it to the container
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(line_webhook.router)
app.include_router(liff.router)
app.include_router(oauth_callback.router)
app.include_router(tick.router)
