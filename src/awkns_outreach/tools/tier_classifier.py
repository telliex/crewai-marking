TIER_KEYWORDS: dict[int, list[str]] = {
    1: ["nail", "facial", "pilates", "spa", "esthetics", "lash", "waxing", "threading", "skincare", "beauty salon"],
    2: ["barber", "barbershop", "yoga", "trainer", "fitness", "gym", "crossfit", "boxing", "martial arts", "dance studio"],
    3: ["coffee", "café", "cafe", "bubble tea", "boba", "dessert", "bakery", "ice cream", "smoothie", "juice bar"],
}


def classify_tier(industry: str) -> int:
    """Return the outreach tier (1, 2, or 3) for a given industry string.

    Defaults to 2 for unrecognised industries.
    """
    lower = industry.lower()
    for tier, keywords in TIER_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return tier
    return 2
