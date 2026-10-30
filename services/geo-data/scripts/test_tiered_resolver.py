import asyncio
import sys
import os

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.resolve_client import resolve_query

PLACES = [
    # Curated / Aliases
    "EBC",
    "ABC",
    "KTM",
    "PKR",
    "Everest Base Camp",
    "Boudhanath",
    "Lumbini",
    "Pashupatinath",
    "Thorong La",
    "Phewa Lake",
    "Tilicho Lake",
    "Rara Lake",
    "Nagarkot",
    "Poon Hill",
    # Administrative (offline tlf-geo matching)
    "Sindhupalchok",
    "Gorkha",
    "Pokhara",
    "Kathmandu",
    "Mustang",
    "Manang",
    # Non-curated / obscure to test Nominatim fallback
    "Namche Bazaar",
    "Langtang",
    "Thamel",
]


async def main():
    print(f"{'QUERY':<22} | {'MATCHED AS':<32} | {'TYPE/SRC':<20} | {'COORDINATES'}")
    print("-" * 90)
    for q in PLACES:
        res = await resolve_query(q)
        bm = res.get("best_match")
        if bm:
            name = bm["short_name"]
            info = f"{bm['type']} [{bm['source']}]"
            coords = f"{bm['lat']:.4f}, {bm['lon']:.4f}"
            print(f"{q:<22} | {name:<32} | {info:<20} | {coords}")
        else:
            print(f"{q:<22} | {'NO MATCH':<32} | {'-':<20} | -")
        await asyncio.sleep(0.3)


if __name__ == "__main__":
    asyncio.run(main())
