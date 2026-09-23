"""Offline coordinates for common cities, so "I live in Lahore" is enough for prayer times
(no internet lookup, nothing sent anywhere)."""

from __future__ import annotations

from difflib import get_close_matches

CITIES: dict[str, tuple[float, float]] = {
    # Pakistan
    "Karachi": (24.8607, 67.0011), "Lahore": (31.5204, 74.3587), "Islamabad": (33.6844, 73.0479),
    "Rawalpindi": (33.5651, 73.0169), "Faisalabad": (31.4504, 73.1350), "Multan": (30.1575, 71.5249),
    "Peshawar": (34.0151, 71.5249), "Quetta": (30.1798, 66.9750), "Hyderabad": (25.3960, 68.3578),
    "Gujranwala": (32.1877, 74.1945), "Sialkot": (32.4945, 74.5229), "Bahawalpur": (29.3956, 71.6836),
    "Sargodha": (32.0836, 72.6711), "Sukkur": (27.7052, 68.8574), "Larkana": (27.5570, 68.2264),
    "Abbottabad": (34.1688, 73.2215), "Mardan": (34.2010, 72.0490), "Gujrat": (32.5731, 74.1005),
    "Sheikhupura": (31.7167, 73.9850), "Rahim Yar Khan": (28.4202, 70.2952), "Jhelum": (32.9425, 73.7257),
    "Mirpur": (33.1478, 73.7519), "Muzaffarabad": (34.3700, 73.4711), "Gilgit": (35.9208, 74.3080),
    "Dera Ghazi Khan": (30.0561, 70.6348), "Okara": (30.8138, 73.4534), "Kasur": (31.1187, 74.4504),
    "Nawabshah": (26.2442, 68.4100), "Mingora": (34.7717, 72.3600), "Chiniot": (31.7200, 72.9789),
    # India / Bangladesh / region
    "Delhi": (28.6139, 77.2090), "Mumbai": (19.0760, 72.8777), "Hyderabad India": (17.3850, 78.4867),
    "Bangalore": (12.9716, 77.5946), "Lucknow": (26.8467, 80.9462), "Dhaka": (23.8103, 90.4125), "Kabul": (34.5553, 69.2075),
    # Middle East
    "Dubai": (25.2048, 55.2708), "Abu Dhabi": (24.4539, 54.3773), "Sharjah": (25.3463, 55.4209),
    "Riyadh": (24.7136, 46.6753), "Jeddah": (21.4858, 39.1925), "Makkah": (21.3891, 39.8579),
    "Madinah": (24.5247, 39.5692), "Doha": (25.2854, 51.5310), "Kuwait City": (29.3759, 47.9774),
    "Muscat": (23.5880, 58.3829), "Manama": (26.2285, 50.5860), "Istanbul": (41.0082, 28.9784),
    "Cairo": (30.0444, 31.2357),
    # Europe / Americas / Asia
    "London": (51.5072, -0.1276), "Birmingham": (52.4862, -1.8904), "Manchester": (53.4808, -2.2426),
    "Bradford": (53.7960, -1.7594), "Glasgow": (55.8642, -4.2518), "Paris": (48.8566, 2.3522),
    "Berlin": (52.5200, 13.4050), "Oslo": (59.9139, 10.7522), "Toronto": (43.6532, -79.3832),
    "New York": (40.7128, -74.0060), "Chicago": (41.8781, -87.6298), "Houston": (29.7604, -95.3698),
    "Los Angeles": (34.0522, -118.2437), "Kuala Lumpur": (3.1390, 101.6869), "Singapore": (1.3521, 103.8198),
    "Sydney": (-33.8688, 151.2093),
}
ALIASES = {"pindi": "Rawalpindi", "isb": "Islamabad", "khi": "Karachi", "lhr": "Lahore", "mecca": "Makkah",
           "makkah mukarramah": "Makkah", "medina": "Madinah", "madina": "Madinah", "nyc": "New York",
           "d g khan": "Dera Ghazi Khan", "swat": "Mingora", "pindi city": "Rawalpindi", "karanchi": "Karachi"}


def lookup(name: str) -> tuple[str, float, float] | None:
    """('Lahore', 31.52, 74.36) for 'lahore' / 'Lahore, Pakistan' / 'lahor'; None if unknown."""
    key = name.strip().split(",")[0].strip().lower()
    if key in ALIASES:
        key = ALIASES[key].lower()
    by_lower = {c.lower(): c for c in CITIES}
    match = by_lower.get(key) or next(iter(get_close_matches(key, list(by_lower), n=1, cutoff=0.8)), None)
    if match is None:
        return None
    city = by_lower.get(match, match) if match in by_lower else match
    lat, lon = CITIES[city]
    return city, lat, lon
