"""shadenav -- shade-aware pedestrian routing, interactive app.

Run locally:   python -m streamlit run app.py
Deploys as-is on Streamlit Community Cloud from the GitHub repo.

Streamlit reruns this whole script top to bottom every time a widget changes,
so anything slow (loading data, casting shadows, scoring streets) is wrapped
in @st.cache_* and only recomputes when its inputs change.
"""

from datetime import date, datetime, time

import folium
import geopandas as gpd
import networkx as nx
import osmnx as ox
import streamlit as st
from pvlib.solarposition import get_solarposition
from streamlit_folium import folium_static

from shadenav.shadows import shadow_at

CHICO_LAT, CHICO_LON, CHICO_TZ = 39.7285, -121.8375, "America/Los_Angeles"

st.set_page_config(page_title="shadenav", page_icon="🌤", layout="wide")


# --------------------------------------------------------------------------
# Data + scoring, cached so sliders stay responsive.
# --------------------------------------------------------------------------
@st.cache_resource
def load_data():
    G = ox.load_graphml("data/streets.graphml")
    if G.graph["crs"] != "EPSG:32610":
        G = ox.project_graph(G)
    buildings = gpd.read_file("data/buildings.gpkg")
    return G, buildings


@st.cache_data
def build_samples(spacing_m=10):
    G, _ = load_data()
    edges = ox.graph_to_gdfs(G, nodes=False)
    rows = []
    for (u, v, k), row in edges.iterrows():
        line = row.geometry
        n = max(int(line.length // spacing_m), 1)
        for i in range(n + 1):
            rows.append({"u": u, "v": v, "k": k,
                         "geometry": line.interpolate(i / n, normalized=True)})
    return gpd.GeoDataFrame(rows, crs=edges.crs)


@st.cache_data
def score_for_time(when: datetime, cloud_cover: float = 0.0):
    """{edge_key: sun_fraction} for a moment. cloud_cover scales sun down."""
    G, buildings = load_data()
    samples = build_samples()
    shadow = shadow_at(buildings, when)

    scores = {}
    if shadow is None:                       # sun down: nothing to avoid
        for u, v, k in G.edges(keys=True):
            scores[(u, v, k)] = 0.0
        return scores

    inside = samples.within(shadow)
    tmp = samples.assign(shaded=inside.values)
    frac_shaded = tmp.groupby(["u", "v", "k"])["shaded"].mean()

    for u, v, k in G.edges(keys=True):
        sun = 1.0 - float(frac_shaded.get((u, v, k), 0.0))
        scores[(u, v, k)] = sun * (1.0 - cloud_cover)   # weather hook
    return scores


def route(G, scores, orig_pt, dest_pt, lam):
    orig = ox.distance.nearest_nodes(G, orig_pt[0], orig_pt[1])
    dest = ox.distance.nearest_nodes(G, dest_pt[0], dest_pt[1])
    for u, v, k, d in G.edges(keys=True, data=True):
        d["cost"] = d["length"] * (1 + lam * scores.get((u, v, k), 1.0))
    path = nx.shortest_path(G, orig, dest, weight="cost")

    length = sum(min(G[u][v][kk]["length"] for kk in G[u][v])
                 for u, v in zip(path[:-1], path[1:]))
    sun_m = sum(min(G[u][v][kk]["length"] * scores.get((u, v, kk), 1.0)
                    for kk in G[u][v])
                for u, v in zip(path[:-1], path[1:]))
    return path, length, sun_m


def daylight_bounds(d: date):
    """Sunrise/sunset hours, so the time slider stays in daylight."""
    import pandas as pd
    times = pd.date_range(f"{d} 04:00", f"{d} 21:00", freq="15min", tz=CHICO_TZ)
    sp = get_solarposition(times, CHICO_LAT, CHICO_LON)
    up = sp[sp["apparent_elevation"] > 3]
    if up.empty:
        return 8, 17
    return up.index[0].hour, up.index[-1].hour


def to_latlon(G, path):
    nodes = ox.graph_to_gdfs(G, edges=False).to_crs(4326)
    return [(nodes.loc[n].geometry.y, nodes.loc[n].geometry.x) for n in path]


# --------------------------------------------------------------------------
# Interface
# --------------------------------------------------------------------------
st.title("shadenav")
st.caption("Walking routes that trade a little distance for a lot less sun. "
           "Chico State campus.")

G, buildings = load_data()

with st.sidebar:
    st.header("When are you walking?")
    the_date = st.date_input("Date", value=date.today())
    lo, hi = daylight_bounds(the_date)
    now_hour = min(max(datetime.now().hour, lo), hi)
    the_hour = st.slider("Time of day", lo, hi, now_hour,
                         help="Shadows shift through the day, so the shady "
                              "route changes with the sun.")

    st.header("How much do you care about shade?")
    lam = st.slider("Shade preference", 0.0, 3.0, 1.5, 0.1,
                    help="0 = shortest path. Higher = accept longer detours "
                         "to stay out of the sun.")

    st.header("Sky")
    sky = st.select_slider("Conditions",
                           ["Clear", "Partly cloudy", "Overcast"], value="Clear")
    cloud = {"Clear": 0.0, "Partly cloudy": 0.4, "Overcast": 0.9}[sky]

when = datetime.combine(the_date, time(the_hour, 0))
scores = score_for_time(when, cloud_cover=cloud)

nodes = ox.graph_to_gdfs(G, edges=False)
orig_pt = (nodes.geometry.x.min(), nodes.geometry.y.min())
dest_pt = (nodes.geometry.x.max(), nodes.geometry.y.max())

path_short, len_s, sun_s = route(G, scores, orig_pt, dest_pt, lam=0)
path_shade, len_h, sun_h = route(G, scores, orig_pt, dest_pt, lam=lam)

col_map, col_stats = st.columns([3, 1])

with col_stats:
    st.metric("Shortest route", f"{len_s:.0f} m")
    st.markdown(f"<span style='color:#ff2b2b'>{100*sun_s/len_s:.0f}% in sun</span>",
                unsafe_allow_html=True)
    st.metric("Shady route", f"{len_h:.0f} m")
    st.markdown(f"<span style='color:#09ab3b'>&#9660; {100*sun_h/len_h:.0f}% in sun</span>",
                unsafe_allow_html=True)
    extra = 100 * (len_h - len_s) / len_s if len_s else 0
    saved = 100 * (sun_s - sun_h) / sun_s if sun_s else 0
    st.write(f"The shady route is **{extra:.0f}% longer** "
             f"and cuts sun exposure by **{saved:.0f}%**.")

with col_map:
    ctr = nodes.to_crs(4326).geometry
    m = folium.Map(location=[ctr.y.mean(), ctr.x.mean()], zoom_start=15,
                   tiles=None)

    folium.TileLayer("OpenStreetMap", name="Street map").add_to(m)

    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
        attr="Esri", name="Minimal (grey)", overlay=False, control=True,
    ).add_to(m)

    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri", name="Satellite", overlay=False, control=True,
    ).add_to(m)

    shadow = shadow_at(buildings, when)
    if shadow is not None:
        folium.GeoJson(
            gpd.GeoSeries([shadow], crs=buildings.crs).to_crs(4326).iloc[0],
            name="Building shadows",
            style_function=lambda _: {"fillColor": "#1a1a2e", "color": "none",
                                      "fillOpacity": 0.35},
        ).add_to(m)

    folium.PolyLine(to_latlon(G, path_short), color="#d08770", weight=5,
                    opacity=0.9, tooltip="Shortest").add_to(m)
    folium.PolyLine(to_latlon(G, path_shade), color="#5e81ac", weight=5,
                    opacity=0.9, tooltip="Shady").add_to(m)
    folium.Marker(to_latlon(G, path_short)[0], tooltip="Start",
                  icon=folium.Icon(color="green")).add_to(m)
    folium.Marker(to_latlon(G, path_short)[-1], tooltip="End",
                  icon=folium.Icon(color="red")).add_to(m)

    folium.LayerControl(position="bottomright", collapsed=True).add_to(m)

    folium_static(m, height=560, width=None)
