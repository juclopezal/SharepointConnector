import logging

from fastapi import FastAPI

from app.routers import list_item, upload

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)

app = FastAPI(title="SharePoint Connector", version="1.0.0")

app.include_router(upload.router)
app.include_router(list_item.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
