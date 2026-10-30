"""
Manual integration test: resolve famous Nepal destinations via real Nominatim.
Run from services/geo-data with the venv active:
    python scripts/test_resolve_live.py
"""
import asyncio
import sys
import os

# Force UTF-8 output on Windows so Nepali script doesn't crash the terminal.
# If the terminal can't display it, characters are replaced with '?'.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Make sure app/ is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.resolve_client import resolve_place, parse_candidates, pick_best

PLACES = [
    # Iconic treks
    "Everest Base Camp",
    "Annapurna Base Camp",
    "Namche Bazaar",
    "Langtang",
    "Thorong La",
    # Major cities
    "Pokhara",
    "Kathmandu",
    "Chitwan",
    "Lumbini",
    # Neighbourhoods / landmarks
    "Thamel",
    "Pashupatinath",
    "Boudhanath",
    # Tricky ones
    "Mustang",          # also a place in USA — will Nepal filter hold?
    "Manang",
    "EBC",              # abbreviation — expect no results
    "Sindhupalchok",    # disaster-relevant district
    "Gorkha",           # also generic word
]


async def main():
    for place in PLACES:
        raw = await resolve_place(place)
        candidates = parse_candidates(raw)
        best, ambiguous = pick_best(candidates)

        status = "NO RESULTS" if not candidates else (
            "AMBIGUOUS" if ambiguous else "AUTO-PICKED"
        )

        print(f"\n{'-'*60}")
        print(f"Query   : {place}")
        print(f"Status  : {status}  ({len(candidates)} candidate(s))")

        if best:
            print(f"Best    : {best['short_name']}")
            print(f"Type    : {best['osm_class']} / {best['type']}")
            print(f"Coords  : {best['lat']:.4f}, {best['lon']:.4f}")
            print(f"Score   : {best['importance']:.4f}")
            addr = best.get("address", {})
            if addr:
                addr_str = ", ".join(f"{k}={v}" for k, v in addr.items())
                print(f"Address : {addr_str}")

        if ambiguous and len(candidates) > 1:
            print("Candidates (need user to pick):")
            for i, c in enumerate(candidates[:3], 1):
                print(f"  {i}. {c['short_name']} "
                      f"({c['osm_class']}/{c['type']}) "
                      f"score={c['importance']:.4f}")

        # Polite delay — Nominatim policy: 1 req/sec
        await asyncio.sleep(1.1)

    print(f"\n{'-'*60}")
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
