"""Sun position for Chico, CA.

Two numbers describe where the sun is at a given moment:
  altitude -- degrees above the horizon (0 = horizon, 90 = straight overhead)
  azimuth  -- compass bearing, degrees clockwise from north
              (90 = due east, 180 = due south, 270 = due west)

Shadows fall in the OPPOSITE direction from the sun, and their length grows
as the sun gets lower. Both facts live in shadows.py; this module only
answers "where is the sun."
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
from pvlib.solarposition import get_solarposition

# Chico, CA
CHICO_LAT = 39.7285
CHICO_LON = -121.8375
CHICO_TZ = "America/Los_Angeles"   # IANA name for US Pacific Time


def sun_position(when: datetime, lat=CHICO_LAT, lon=CHICO_LON, tz=CHICO_TZ):
    """Return (altitude_deg, azimuth_deg) for a local wall-clock time.

    `when` is treated as local time in `tz`. A negative altitude means the
    sun is below the horizon (night) -- no shadow to cast.
    """
    idx = pd.DatetimeIndex([when]).tz_localize(tz)
    r = get_solarposition(idx, lat, lon)
    return (
        float(r["apparent_elevation"].iloc[0]),
        float(r["azimuth"].iloc[0]),
    )
