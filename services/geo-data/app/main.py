from fastapi import FastAPI
app = FastAPI(title="geo-data-service")


@app.get("/health")
async def health():
    return {"status": "ok"}
