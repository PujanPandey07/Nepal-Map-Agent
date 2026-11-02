"""
scripts/test_db_connection.py
Quick sanity check: connect to the local PostGIS database and count rows
in planet_osm_point.  Reads credentials from environment / .env file.

Run from the geo-data service directory:
    mapenv\\Scripts\\python.exe scripts/test_db_connection.py
"""
import asyncio
import os
import selectors
import sys
from pathlib import Path

# Allow running as a standalone script (not through the package)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[3] / ".env")  # project root
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)  # service

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5433"))
DB_NAME = os.getenv("DB_NAME", "nepal_osm")
DB_USER = os.getenv("DB_USER", "map-osm")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

DSN = f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} user={DB_USER} password={DB_PASSWORD}"


async def main() -> None:
    print(f"Connecting to {DB_HOST}:{DB_PORT}/{DB_NAME} as {DB_USER} ...")
    async with await psycopg.AsyncConnection.connect(DSN, row_factory=psycopg.rows.dict_row) as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT count(*) AS n FROM planet_osm_point;")
            row = await cur.fetchone()
            count = row["n"]

    print(f"planet_osm_point row count: {count:,}")
    if count == 218996:
        print("OK - Count matches expected 218,996")
    else:
        print(f"WARNING: Expected 218,996 but got {count:,}")
        sys.exit(1)


if __name__ == "__main__":
    # psycopg requires SelectorEventLoop on Windows (not the default ProactorEventLoop)
    asyncio.run(main(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
