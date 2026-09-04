"""Cast shadows from building footprints.

Method: a building of height h, with the sun at altitude a, throws a shadow
of length  h / tan(a)  along the ground, pointing directly away from the sun.
So we slide a copy of the footprint that far in the anti-sun direction and
fill in the region swept between the footprint and its copy. Union every
building's shadow into one shape.

Everything here assumes a PROJECTED CRS in metres (UTM 10N / EPSG:32610 for
Chico). In lat/lon the offsets would be in degrees, which is meaningless.
"""

from __future__ import annotations

import math
from datetime import datetime

from shapely.affinity import translate
from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union

from .sun import sun_position

# Below this sun altitude, shadows stretch toward infinity and stop being
# meaningful for walking. Treat the sun as effectively down.
MIN_SUN_ALTITUDE_DEG = 3.0


def _sweep_one(footprint: Polygon, dx: float, dy: float) -> Polygon:
    """Region swept by dragging one footprint along (dx, dy).

    Built from the footprint, its translated copy, and one quadrilateral per
    exterior edge covering the ground between them. This is exact for concave
    and L-shaped buildings, where a convex hull would wrongly fill the notch.
    """
    moved = translate(footprint, xoff=dx, yoff=dy)
    parts = [footprint, moved]

    coords = list(footprint.exterior.coords)
    for (x1, y1), (x2, y2) in zip(coords[:-1], coords[1:]):
        parts.append(Polygon([(x1, y1), (x2, y2), (x2 + dx, y2 + dy), (x1 + dx, y1 + dy)]))

    return unary_union(parts).buffer(0)   # buffer(0) heals self-intersections


def cast_all(buildings, sun_alt_deg: float, sun_az_deg: float):
    """All buildings' shadows, unioned into one (Multi)Polygon.

    buildings -- GeoDataFrame in EPSG:32610 with a 'height_m' column.
    Returns None if the sun is at or below MIN_SUN_ALTITUDE_DEG.
    """
    if sun_alt_deg <= MIN_SUN_ALTITUDE_DEG:
        return None

    a = math.radians(sun_az_deg)
    tan_alt = math.tan(math.radians(sun_alt_deg))

    # unit vector toward the sun is (sin az, cos az) in (east, north);
    # the shadow falls the opposite way, hence the minus signs below.
    parts = []
    for geom, height in zip(buildings.geometry, buildings.height_m):
        if geom is None or geom.is_empty:
            continue

        length = height / tan_alt
        dx = -length * math.sin(a)
        dy = -length * math.cos(a)

        polys = geom.geoms if isinstance(geom, MultiPolygon) else [geom]
        for p in polys:
            if isinstance(p, Polygon) and not p.is_empty:
                parts.append(_sweep_one(p, dx, dy))

    return unary_union(parts) if parts else None


def shadow_at(buildings, when: datetime):
    """The interface routing consumes: buildings + time -> one shadow shape.

    Returns a single (Multi)Polygon of all shade at that moment, or None if
    the sun is down. Routing tests whether street sample points fall within it.
    """
    alt, az = sun_position(when)
    return cast_all(buildings, alt, az)
