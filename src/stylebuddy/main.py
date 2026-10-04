from fastapi import FastAPI

app = FastAPI(title="StyleBuddy")


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}
