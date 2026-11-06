"""DEMO economics (synthetic, INR-like units). All numbers are arbitrary illustration values."""
from dataclasses import dataclass

HANDLING_COST = 300
VALUE_FACTOR = {"A": 0.90, "B": 0.80, "refurbished": 0.65, "repaired": 0.60, "liquidation": 0.25}
MIN_REFURB_MARGIN = 500
REPAIR_GAIN_FACTOR = 1.2   # repair must beat liquidation net value by 20%


@dataclass(frozen=True)
class ProductEconomics:
    category: str
    price: int
    repair_cost: int
    refurb_cost: int
    liquidation_cost: int
    hygiene_restricted: bool  # opened units may not be resold as new (sample policy RP-6)


ECONOMICS = {
    "P-PHONE-01": ProductEconomics("smartphone", 24000, 4000, 1500, 300, False),
    "P-LAPTOP-01": ProductEconomics("laptop", 55000, 9000, 3000, 500, False),
    "P-HEADPH-01": ProductEconomics("headphones", 6000, 1500, 400, 300, True),
    "P-WATCH-01": ProductEconomics("smartwatch", 9000, 2500, 800, 300, False),
    "P-SHOES-01": ProductEconomics("footwear", 3500, 2500, 300, 900, True),
}
# condition label -> grade (A unopened, B opened/working, C used, D damaged)
GRADE = {"unopened": "A", "like_new": "B", "good": "B", "used": "C", "used_with_damage": "D", "damaged": "D"}
