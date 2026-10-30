"""
Curated knowledge base of famous Nepal destinations, viewpoints, trekking points,
heritage sites, lakes, and common aliases/abbreviations.

Each entry includes:
  - canonical short_name
  - category / type
  - exact coordinates (lat, lon)
  - common aliases / search query variants (lowercase)
"""

FAMOUS_DESTINATIONS = [
    # ── Trekking Base Camps & Passes ──────────────────────────────────────────
    {
        "short_name": "Everest Base Camp",
        "type": "base_camp",
        "lat": 28.0026,
        "lon": 86.8528,
        "district": "Solukhumbu",
        "aliases": ["everest base camp", "ebc", "everest bc", "sagarmatha base camp", "south base camp"],
    },
    {
        "short_name": "Annapurna Base Camp",
        "type": "base_camp",
        "lat": 28.5306,
        "lon": 83.8780,
        "district": "Kaski",
        "aliases": ["annapurna base camp", "abc", "annapurna sanctuary", "annapurna bc"],
    },
    {
        "short_name": "Thorong La Pass",
        "type": "mountain_pass",
        "lat": 28.7930,
        "lon": 83.9430,
        "district": "Manang",
        "aliases": ["thorong la", "thorong la pass", "thorung la", "thorong pass"],
    },
    {
        "short_name": "Kala Patthar",
        "type": "viewpoint",
        "lat": 27.9958,
        "lon": 86.8284,
        "district": "Solukhumbu",
        "aliases": ["kala patthar", "kalapatthar", "kalapathar", "black rock"],
    },
    {
        "short_name": "Gokyo Ri",
        "type": "viewpoint",
        "lat": 27.9583,
        "lon": 86.6850,
        "district": "Solukhumbu",
        "aliases": ["gokyo ri", "gokyo peak"],
    },
    {
        "short_name": "Poon Hill",
        "type": "viewpoint",
        "lat": 28.3990,
        "lon": 83.6900,
        "district": "Myagdi",
        "aliases": ["poon hill", "poonhill"],
    },
    {
        "short_name": "Mardi Himal Base Camp",
        "type": "base_camp",
        "lat": 28.4891,
        "lon": 83.9213,
        "district": "Kaski",
        "aliases": ["mardi himal base camp", "mardi base camp", "mardi bc", "mardi himal"],
    },

    # ── Mountain Peaks ────────────────────────────────────────────────────────
    {
        "short_name": "Mount Everest",
        "type": "peak",
        "lat": 27.9881,
        "lon": 86.9250,
        "district": "Solukhumbu",
        "aliases": ["mount everest", "everest", "sagarmatha", "chomolungma", "mt everest"],
    },
    {
        "short_name": "Machhapuchhre",
        "type": "peak",
        "lat": 28.4950,
        "lon": 83.9510,
        "district": "Kaski",
        "aliases": ["machhapuchhre", "machapuchare", "fishtail", "fishtail mountain", "machhapuchare"],
    },
    {
        "short_name": "Ama Dablam",
        "type": "peak",
        "lat": 27.8553,
        "lon": 86.8683,
        "district": "Solukhumbu",
        "aliases": ["ama dablam", "amadablam"],
    },
    {
        "short_name": "Annapurna I",
        "type": "peak",
        "lat": 28.5961,
        "lon": 83.8203,
        "district": "Myagdi",
        "aliases": ["annapurna i", "annapurna 1", "annapurna main"],
    },
    {
        "short_name": "Manaslu",
        "type": "peak",
        "lat": 28.5497,
        "lon": 84.5619,
        "district": "Gorkha",
        "aliases": ["manaslu", "mount manaslu", "mt manaslu", "kutang"],
    },
    {
        "short_name": "Dhaulagiri",
        "type": "peak",
        "lat": 28.6983,
        "lon": 83.4875,
        "district": "Myagdi",
        "aliases": ["dhaulagiri", "dhaulagiri i", "mount dhaulagiri"],
    },
    {
        "short_name": "Kanchenjunga",
        "type": "peak",
        "lat": 27.7025,
        "lon": 88.1475,
        "district": "Taplejung",
        "aliases": ["kanchenjunga", "kanchanjangha", "mount kanchenjunga"],
    },

    # ── UNESCO Heritage Sites & Famous Temples ────────────────────────────────
    {
        "short_name": "Pashupatinath Temple",
        "type": "temple",
        "lat": 27.7097,
        "lon": 85.3486,
        "district": "Kathmandu",
        "aliases": ["pashupatinath", "pashupatinath temple", "pashupati mandir", "पशुपतिनाथ"],
    },
    {
        "short_name": "Boudhanath Stupa",
        "type": "stupa",
        "lat": 27.7215,
        "lon": 85.3620,
        "district": "Kathmandu",
        "aliases": ["boudhanath", "boudha", "boudhanath stupa", "bouddha", "bauddha", "बौद्धनाथ"],
    },
    {
        "short_name": "Swayambhunath Stupa",
        "type": "stupa",
        "lat": 27.7150,
        "lon": 85.2906,
        "district": "Kathmandu",
        "aliases": ["swayambhunath", "monkey temple", "swayambhu", "swayambhunath stupa", "स्वयम्भुनाथ"],
    },
    {
        "short_name": "Kathmandu Durbar Square",
        "type": "heritage_site",
        "lat": 27.7042,
        "lon": 85.3077,
        "district": "Kathmandu",
        "aliases": ["kathmandu durbar square", "basantapur", "basantapur durbar square", "hanuman dhoka"],
    },
    {
        "short_name": "Patan Durbar Square",
        "type": "heritage_site",
        "lat": 27.6766,
        "lon": 85.3255,
        "district": "Lalitpur",
        "aliases": ["patan durbar square", "lalitpur durbar square", "mangalbazar patan", "mangal bazar"],
    },
    {
        "short_name": "Bhaktapur Durbar Square",
        "type": "heritage_site",
        "lat": 27.6722,
        "lon": 85.4298,
        "district": "Bhaktapur",
        "aliases": ["bhaktapur durbar square", "bhadgaon", "bhaktapur square"],
    },
    {
        "short_name": "Changunarayan Temple",
        "type": "temple",
        "lat": 27.7028,
        "lon": 85.4278,
        "district": "Bhaktapur",
        "aliases": ["changunarayan", "changu narayan", "changunarayan temple"],
    },
    {
        "short_name": "Lumbini Sacred Garden (Maya Devi Temple)",
        "type": "religious_site",
        "lat": 27.4705,
        "lon": 83.2750,
        "district": "Rupandehi",
        "aliases": [
            "lumbini", "lumbini sacred garden", "maya devi temple",
            "birthplace of buddha", "lumbini garden", "लुम्बिनी"
        ],
    },
    {
        "short_name": "Muktinath Temple",
        "type": "temple",
        "lat": 28.8167,
        "lon": 83.8667,
        "district": "Mustang",
        "aliases": ["muktinath", "muktinath temple", "muktinath dham", "मुक्तिनाथ"],
    },
    {
        "short_name": "Manakamana Temple",
        "type": "temple",
        "lat": 27.8789,
        "lon": 84.5714,
        "district": "Gorkha",
        "aliases": ["manakamana", "manakamana temple", "मनकामना"],
    },
    {
        "short_name": "Janaki Mandir",
        "type": "temple",
        "lat": 26.7297,
        "lon": 85.9258,
        "district": "Dhanusha",
        "aliases": ["janaki mandir", "janaki temple", "nau lakha mandir", "जानकी मन्दिर"],
    },
    {
        "short_name": "Budhanilkantha Temple",
        "type": "temple",
        "lat": 27.7681,
        "lon": 85.3614,
        "district": "Kathmandu",
        "aliases": ["budhanilkantha", "budhanilkantha temple", "sleeping vishnu", "बुढानीलकण्ठ"],
    },
    {
        "short_name": "Dakshinkali Temple",
        "type": "temple",
        "lat": 27.6019,
        "lon": 85.2589,
        "district": "Kathmandu",
        "aliases": ["dakshinkali", "dakshinkali temple", "दक्षिणकाली"],
    },

    # ── Famous Lakes ──────────────────────────────────────────────────────────
    {
        "short_name": "Phewa Lake",
        "type": "lake",
        "lat": 28.2140,
        "lon": 83.9560,
        "district": "Kaski",
        "aliases": ["phewa lake", "fewa lake", "phewa tal", "fewa tal", "फेवा ताल"],
    },
    {
        "short_name": "Begnas Lake",
        "type": "lake",
        "lat": 28.1725,
        "lon": 84.0950,
        "district": "Kaski",
        "aliases": ["begnas lake", "begnas tal", "बेग्नास ताल"],
    },
    {
        "short_name": "Rara Lake",
        "type": "lake",
        "lat": 29.5293,
        "lon": 82.0926,
        "district": "Mugu",
        "aliases": ["rara lake", "rara tal", "lake rara", "रारा ताल"],
    },
    {
        "short_name": "Shey Phoksundo Lake",
        "type": "lake",
        "lat": 29.1556,
        "lon": 82.9622,
        "district": "Dolpa",
        "aliases": ["phoksundo lake", "shey phoksundo", "phoksundo", "lake phoksundo", "फोक्सुण्डो"],
    },
    {
        "short_name": "Tilicho Lake",
        "type": "lake",
        "lat": 28.6833,
        "lon": 83.8500,
        "district": "Manang",
        "aliases": ["tilicho lake", "tilicho tal", "tilicho", "तिलिचो ताल"],
    },
    {
        "short_name": "Gosaikunda Lake",
        "type": "lake",
        "lat": 28.0833,
        "lon": 85.4167,
        "district": "Rasuwa",
        "aliases": ["gosaikunda", "gosainkunda", "gosaikunda lake", "गोसाइँकुण्ड"],
    },
    {
        "short_name": "Gokyo Lakes",
        "type": "lake",
        "lat": 27.9550,
        "lon": 86.6833,
        "district": "Solukhumbu",
        "aliases": ["gokyo lakes", "gokyo lake", "gokyo cho", "dudh pokhari"],
    },
    {
        "short_name": "Panch Pokhari",
        "type": "lake",
        "lat": 28.0417,
        "lon": 85.7167,
        "district": "Sindhupalchok",
        "aliases": ["panch pokhari", "panchpokhari", "पाँचपोखरी"],
    },

    # ── Hill Stations, Viewpoints & Valleys ────────────────────────────────────
    {
        "short_name": "Nagarkot",
        "type": "hill_station",
        "lat": 27.7236,
        "lon": 85.5247,
        "district": "Bhaktapur",
        "aliases": ["nagarkot", "nagarkot viewpoint", "नगरकोट"],
    },
    {
        "short_name": "Sarangkot",
        "type": "viewpoint",
        "lat": 28.2420,
        "lon": 83.9670,
        "district": "Kaski",
        "aliases": ["sarangkot", "sarangkot viewpoint", "सराङकोट"],
    },
    {
        "short_name": "Bandipur",
        "type": "town",
        "lat": 27.9170,
        "lon": 84.4070,
        "district": "Tanahun",
        "aliases": ["bandipur", "बन्दीपुर"],
    },
    {
        "short_name": "Dhulikhel",
        "type": "hill_station",
        "lat": 27.6180,
        "lon": 85.5520,
        "district": "Kavrepalanchok",
        "aliases": ["dhulikhel", "धुलिखेल"],
    },
    {
        "short_name": "Chandragiri Hills",
        "type": "viewpoint",
        "lat": 27.6719,
        "lon": 85.2078,
        "district": "Kathmandu",
        "aliases": ["chandragiri", "chandragiri hills", "chandragiri cable car"],
    },
    {
        "short_name": "Namche Bazaar",
        "type": "village",
        "lat": 27.8053,
        "lon": 86.7106,
        "district": "Solukhumbu",
        "aliases": ["namche bazaar", "namche bazar", "namche", "नाम्चे बजार"],
    },
    {
        "short_name": "Lukla",
        "type": "town",
        "lat": 27.6889,
        "lon": 86.7306,
        "district": "Solukhumbu",
        "aliases": ["lukla", "tenzing hillary airport", "लुक्ला"],
    },
    {
        "short_name": "Ghorepani",
        "type": "village",
        "lat": 28.3986,
        "lon": 83.6992,
        "district": "Myagdi",
        "aliases": ["ghorepani", "ghodepani", "घोडेपानी"],
    },
    {
        "short_name": "Jomsom",
        "type": "town",
        "lat": 28.7833,
        "lon": 83.7306,
        "district": "Mustang",
        "aliases": ["jomsom", "dzongsam", "जोमसोम"],
    },
    {
        "short_name": "Lo Manthang",
        "type": "village",
        "lat": 29.1828,
        "lon": 83.9567,
        "district": "Mustang",
        "aliases": ["lo manthang", "lomanthang", "walled city of lo", "upper mustang capital"],
    },
    {
        "short_name": "Kyanjin Gompa (Langtang)",
        "type": "village",
        "lat": 28.2100,
        "lon": 85.5800,
        "district": "Rasuwa",
        "aliases": ["kyanjin gompa", "kyangjin gompa", "langtang valley", "kyanjin"],
    },
    {
        "short_name": "Sauraha (Chitwan)",
        "type": "town",
        "lat": 27.5747,
        "lon": 84.4936,
        "district": "Chitwan",
        "aliases": ["sauraha", "chitwan safari hub", "सौराहा"],
    },
    {
        "short_name": "Thamel",
        "type": "neighbourhood",
        "lat": 27.7167,
        "lon": 85.3127,
        "district": "Kathmandu",
        "aliases": ["thamel", "ठमेल"],
    },

    # ── National Parks & Conservation Areas ───────────────────────────────────
    {
        "short_name": "Chitwan National Park",
        "type": "national_park",
        "lat": 27.5341,
        "lon": 84.4525,
        "district": "Chitwan",
        "aliases": ["chitwan national park", "royal chitwan national park"],
    },
    {
        "short_name": "Sagarmatha National Park",
        "type": "national_park",
        "lat": 27.9330,
        "lon": 86.7330,
        "district": "Solukhumbu",
        "aliases": ["sagarmatha national park", "everest national park"],
    },
    {
        "short_name": "Bardiya National Park",
        "type": "national_park",
        "lat": 28.3833,
        "lon": 81.3500,
        "district": "Bardiya",
        "aliases": ["bardiya national park", "bardia national park"],
    },
    {
        "short_name": "Annapurna Conservation Area",
        "type": "conservation_area",
        "lat": 28.6000,
        "lon": 83.9000,
        "district": "Kaski",
        "aliases": ["annapurna conservation area", "acap"],
    },

    # ── City & Airport Acronyms / Common Shortcodes ───────────────────────────
    {
        "short_name": "Kathmandu",
        "type": "city",
        "lat": 27.7172,
        "lon": 85.3240,
        "district": "Kathmandu",
        "aliases": ["ktm"],
    },
    {
        "short_name": "Pokhara",
        "type": "city",
        "lat": 28.2096,
        "lon": 83.9856,
        "district": "Kaski",
        "aliases": ["pkr"],
    },
]


def match_destination(query: str) -> dict | None:
    """
    Look up a query against the curated destinations table.
    Returns destination dict if matched, None otherwise.
    """
    clean_q = query.strip().lower()
    for dest in FAMOUS_DESTINATIONS:
        # Check canonical short name
        if clean_q == dest["short_name"].lower():
            return dest
        # Check aliases
        for alias in dest["aliases"]:
            if clean_q == alias:
                return dest
    return None
