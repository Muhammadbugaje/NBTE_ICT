"""Small helpers shared by the attendance app."""
from math import radians, cos, sin, asin, sqrt


def haversine_meters(lat1, lng1, lat2, lng2):
    """Great-circle distance in meters between two lat/lng points."""
    R = 6_371_000  # Earth radius in meters
    try:
        lat1, lng1, lat2, lng2 = (
            radians(float(lat1)), radians(float(lng1)),
            radians(float(lat2)), radians(float(lng2)),
        )
    except (TypeError, ValueError):
        return None
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlng / 2) ** 2
    c = 2 * asin(sqrt(a))
    return int(R * c)