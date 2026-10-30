# scripts/overpass_servers_test.py
import time
import httpx

SERVERS = [
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

# "out count" returns only a number, so the reply is tiny
QUERY = '[out:json][timeout:25];nwr["amenity"="hospital"](around:3000,28.00722,83.22722);out count;'
HEADERS = {"User-Agent": "nepal-gis-assistant/0.1 (learning project)"}

for url in SERVERS:
    for attempt in (1, 2):  # twice per server, to see how much the time varies
        start = time.time()
        try:
            # one request at a time on purpose, to stay polite
            resp = httpx.post(
                url, data={"data": QUERY}, headers=HEADERS, timeout=60)
            print(
                f"{url} try {attempt}: {resp.status_code} in {time.time() - start:.1f}s")
        except httpx.RequestError as e:
            # repr(e) keeps the exception type (ReadTimeout vs ConnectError, etc.)
            print(
                f"{url} try {attempt}: FAILED after {time.time() - start:.1f}s: {e!r}")
