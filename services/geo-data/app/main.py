# services/geo-data/app/main.py
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
# coordinate -> plain dict (admin unit + demographics)
from tlf_geo_profiler import profile_dict


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Runs once at startup. We do one throwaway lookup so the package loads
    # its boundary data now, instead of making the first real user wait.
    await asyncio.to_thread(profile_dict, 27.7040, 85.3070)
    yield
    # Code after `yield` would run at shutdown (nothing to clean up yet).


app = FastAPI(title="geo-data service", lifespan=lifespan)


@app.get("/health")
async def health():
    # Other services and Docker will call this to ask "are you alive?"
    return {"status": "ok"}


@app.get("/profile")
async def get_profile(
    # Query(...) means required; ge/le reject impossible coordinates with an automatic 422
    lat: float = Query(..., ge=-90, le=90, description="Latitude"),
    lon: float = Query(..., ge=-180, le=180, description="Longitude"),
):
    try:
        # profile_dict is a normal blocking function (geopandas work). Calling it
        # directly inside `async def` would freeze the event loop while it runs,
        # so we hand it to a worker thread and await the result instead.
        data = await asyncio.to_thread(profile_dict, lat, lon)
    except ValueError:
        # The package raises ValueError when the point is outside all Nepal
        # boundaries. For an API caller that means "not found", so we use 404.
        raise HTTPException(
            status_code=404,
            detail="Coordinate is outside Nepal's known boundaries",
        )

    # The package returns None for missing data. None means "unknown",
    # never "doesn't exist", so we say that explicitly for the caller.
    notes = []
    admin = data["admin_unit"]
    if admin.get("ward") is None:
        notes.append("No ward polygon found here; ward is unknown.")
    if admin.get("local_level") is None:
        notes.append("Local level could not be resolved; it is unknown.")
    if data["demographics"].get("population") is None:
        notes.append("No demographic data; population is unknown, not zero.")

    return {
        "source": "tlf-geo-profiler",
        "partial": bool(notes),  # True when anything above was missing
        "notes": notes,
        **data,  # adds "admin_unit" and "demographics" to the response
    }
