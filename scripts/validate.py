"""Validate shade-aware routing: does it actually cut sun exposure, and at what cost?

Runs a fixed set of random walks at a range of shade preferences (lambda),
measures extra distance vs sun reduction for each, and writes a tradeoff curve.

Because the random seed and OD pairs are fixed, this can be re-run after
swapping in LiDAR building heights to produce a directly comparable curve --
the only thing that changes between runs is data/buildings.gpkg.

Usage:
    python scripts/validate.py                  # baseline (OSM heights)
    python scripts/validate.py --tag lidar      # after swapping in LiDAR heights
    python scripts/validate.py --overlay osm     # plot this run against a saved one

Outputs (in data/, tagged by --tag):
    validation_<tag>.csv          per-route raw results
    validation_<tag>_summary.csv  averaged per-lambda table
    tradeoff_curve_<tag>.png      the figure
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import geopandas as gpd
import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd

from shadenav.routing import build_samples, route, score_edges
from shadenav.shadows import shadow_at

# --- experiment settings -------------------------------------------------
SEED = 42
N_PAIRS = 200
MIN_SEP_M = 300                                   # OD pairs at least this far apart
LAMBDAS = [0, 0.1, 0.2, 0.3, 0.5, 0.75, 1, 1.5, 2, 3, 5]
WHEN = datetime(2025, 9, 15, 9)                   # 9am mid-September, long shadows
STREETS = "data/streets.graphml"
BUILDINGS = "data/buildings.gpkg"


# --- experiment ----------------------------------------------------------
def pick_pairs(G, rng):
    nodes = list(G.nodes())
    pairs = []
    while len(pairs) < N_PAIRS:
        o, d = rng.choice(nodes, 2, replace=False)
        try:
            if nx.shortest_path_length(G, o, d, weight="length") > MIN_SEP_M:
                pairs.append((int(o), int(d)))
        except nx.NetworkXNoPath:
            continue
    return pairs


def run(tag):
    G = ox.load_graphml(STREETS)
    if G.graph["crs"] != "EPSG:32610":
        G = ox.project_graph(G)
    buildings = gpd.read_file(BUILDINGS)
    print(f"{G.number_of_nodes()} nodes, {len(buildings)} buildings")

    samples = build_samples(G)
    shadow = shadow_at(buildings, WHEN)
    scores = score_edges(G, samples, shadow)

    rng = np.random.default_rng(SEED)
    pairs = pick_pairs(G, rng)
    print(f"{len(pairs)} OD pairs")

    rows, t0 = [], time.time()
    for i, (o, d) in enumerate(pairs):
        _, base_len, base_sun = route(G, scores, o, d, lam=0)
        if base_len == 0:
            continue
        for lam in LAMBDAS:
            _, length, sun_m = route(G, scores, o, d, lam=lam)
            rows.append({
                "pair": i, "lambda": lam, "length_m": length, "sun_m": sun_m,
                "extra_dist_pct": 100 * (length - base_len) / base_len,
                "sun_reduction_pct": (100 * (base_sun - sun_m) / base_sun
                                      if base_sun > 0 else 0),
            })
        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(pairs)} ({time.time()-t0:.0f}s)")

    df = pd.DataFrame(rows)
    summary = df.groupby("lambda").agg(
        mean_extra_dist=("extra_dist_pct", "mean"),
        mean_sun_reduction=("sun_reduction_pct", "mean"),
        median_extra_dist=("extra_dist_pct", "median"),
        median_sun_reduction=("sun_reduction_pct", "median"),
    ).round(2)

    df.to_csv(f"data/validation_{tag}.csv", index=False)
    summary.to_csv(f"data/validation_{tag}_summary.csv")
    print(f"\n{summary}\n")
    return summary


def plot(summary, tag, overlay_tag=None):
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.plot(summary["mean_extra_dist"], summary["mean_sun_reduction"],
            "o-", color="#5e81ac", linewidth=2, markersize=8, label=tag)

    for lam, row in summary.iterrows():
        ax.annotate(f"λ={lam}",
                    (row["mean_extra_dist"], row["mean_sun_reduction"]),
                    textcoords="offset points", xytext=(8, -4), fontsize=8)

    if overlay_tag:
        prev = pd.read_csv(f"data/validation_{overlay_tag}_summary.csv")
        ax.plot(prev["mean_extra_dist"], prev["mean_sun_reduction"],
                "s--", color="#d08770", linewidth=2, markersize=7,
                label=overlay_tag, alpha=0.8)
        ax.legend(title="height source")

    ax.set_xlabel("Extra distance walked (%)")
    ax.set_ylabel("Sun exposure reduction (%)")
    ax.set_title("shadenav tradeoff: shade gained vs distance added\n"
                 "Chico State campus, 9am mid-September, 200 routes")
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    out = f"data/tradeoff_curve_{tag}.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    print(f"saved {out}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--tag", default="osm",
                   help="label for this run's outputs (e.g. osm, lidar)")
    p.add_argument("--overlay", default=None,
                   help="tag of a previous run to overlay on the plot")
    args = p.parse_args()

    summary = run(args.tag)
    plot(summary, args.tag, overlay_tag=args.overlay)
