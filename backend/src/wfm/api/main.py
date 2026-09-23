from fastapi import FastAPI

from wfm import __version__

app = FastAPI(title="WFM POC", version=__version__)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
