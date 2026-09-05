"""Shared routing pipeline: sample streets, score them by sun exposure, route.

Used by both the interactive app and the validation script, so the two never
drift apart.
"""

from __future__ import annotations

import networkx as nx
import osmnx as ox
import geopandas as gpd


def build_samples(G, spacing_m=10):
    """Points spaced along every edge, for testing shade coverage."""
    edges = ox.graph_to_gdfs(G, nodes=False)
    rows = []
    for (u, v, k), row in edges.iterrows():
        line = row.geometry
        n = max(int(line.length // spacing_m), 1)
        for i in range(n + 1):
            rows.append({"u": u, "v": v, "k": k,
                         "geometry": line.interpolate(i / n, normalized=True)})
    return gpd.GeoDataFrame(rows, crs=edges.crs)


def score_edges(G, samples, shadow):
    """{edge_key: sun_fraction} -- 0 = fully shaded, 1 = fully sunny."""
    if shadow is None:                       # sun down: nothing to avoid
        return {(u, v, k): 0.0 for u, v, k in G.edges(keys=True)}
    inside = samples.within(shadow)
    tmp = samples.assign(shaded=inside.values)
    frac_shaded = tmp.groupby(["u", "v", "k"])["shaded"].mean()
    return {(u, v, k): 1.0 - float(frac_shaded.get((u, v, k), 0.0))
            for u, v, k in G.edges(keys=True)}


def route(G, scores, orig, dest, lam):
    """Shortest path from node `orig` to `dest`, trading distance for shade.

    lam=0 is the plain shortest path; higher lam accepts more distance to
    avoid sun. Returns (path, length_m, sun_m).
    """
    for u, v, k, d in G.edges(keys=True, data=True):
        d["cost"] = d["length"] * (1 + lam * scores.get((u, v, k), 1.0))
    path = nx.shortest_path(G, orig, dest, weight="cost")

    length = sum(min(G[u][v][kk]["length"] for kk in G[u][v])
                 for u, v in zip(path[:-1], path[1:]))
    sun_m = sum(min(G[u][v][kk]["length"] * scores.get((u, v, kk), 1.0)
                    for kk in G[u][v])
                for u, v in zip(path[:-1], path[1:]))
    return path, length, sun_m
