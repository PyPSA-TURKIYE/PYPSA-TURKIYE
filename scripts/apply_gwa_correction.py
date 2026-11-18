#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 The PyPSA-Türkiye Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Global Wind Atlas (GWA) bias-correction for ERA5 onwind profiles.

ERA5 (0.3 deg) smooths wind speeds in complex terrain (Anatolian ridges),
yielding unrealistically low onshore-wind capacity factors. This script
applies the standard cell-wise GWA/ERA5 mean-wind-speed ratio correction
(cf. atlite docs, Murcia et al. 2022) WITHOUT touching the original cutout:

  1. ensures the GWA 100 m mean-wind-speed GeoTIFF for Turkey is present
     (data/gwa/TUR_wind-speed_100m.tif, ~250 m resolution, EPSG:4326);
  2. computes per-ERA5-cell correction ratios. Two variants:
       - mean : r = GWA_cell_mean / ERA5_2023_mean   (plain average)
       - p75  : r = GWA_cell_p75  / ERA5_2023_mean   (ridge-representative:
                wind farms are sited on the upper tail of the sub-cell
                GWA distribution, not at the cell average)
     Ratios are clipped to [0.8, 2.5] (physical sanity cap);
  3. writes a corrected COPY of the cutout (cutouts/cutout-{year}-era5-gwa-{variant}.nc)
     with wnd100m scaled by the ratio (original cutout untouched);
  4. recomputes ONLY the onwind profile exactly as scripts/build_renewable_profiles.py
     does (same exclusions, turbine, layout, potential method), writing
     resources/{rdir}/renewable_profiles/profile_onwind_gwa_{variant}.nc;
  5. computes the implied fleet CF for a powerplant CSV (per-plant -> bus
     region -> p_nom-weighted mean, same logic as Epdk/validate_wind_relocation_cf.py);
  6. with --apply VARIANT: backs the original profile up
     (profile_onwind.nc.bak.before-gwa-<date>) and copies the chosen
     variant over profile_onwind.nc; renames the chosen corrected cutout
     to cutouts/cutout-{year}-era5-gwa.nc and removes the other variant.

Usage
-----
  # 2023 validation run: build both variants + report fleet CFs (no overwrite)
  python scripts/apply_gwa_correction.py --year 2023 --variants mean p75

  # apply the chosen variant (backup + overwrite profile_onwind.nc)
  python scripts/apply_gwa_correction.py --year 2023 --apply p75

  # 2018 main pipeline (rdir='', config.yaml) — same procedure
  python scripts/apply_gwa_correction.py --year 2018 --variants p75 \
      --fleet-csv data/custom_powerplants.csv
  python scripts/apply_gwa_correction.py --year 2018 --apply p75
"""
import argparse
import datetime as dt
import logging
import os
import shutil
import subprocess
import sys
import time

import numpy as np
import pandas as pd
import xarray as xr
import yaml

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
logger = logging.getLogger("gwa_correction")

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repository root
GWA_TIF = f"{PROJ}/data/gwa/TUR_wind-speed_100m.tif"
GWA_URL = "https://globalwindatlas.info/api/gis/country/TUR/wind-speed/100"
RATIO_CLIP = (0.8, 2.5)
MIN_PIXELS = 10  # min valid GWA pixels per ERA5 cell, else ratio=1

# year -> (run-dir under resources/, scenario config with renewable.onwind)
YEAR_SETUP = {
    2023: ("validation2023", f"{PROJ}/senaryolar/00_VALIDATION_2023.yaml"),
    2018: ("", f"{PROJ}/config.yaml"),
}


def paths_for(year):
    rdir, cfg = YEAR_SETUP[year]
    sub = f"{rdir}/" if rdir else ""
    return dict(
        config=cfg,
        cutout=f"{PROJ}/cutouts/cutout-{year}-era5.nc",
        regions=f"{PROJ}/resources/{sub}bus_regions/regions_onshore.geojson",
        natura=f"{PROJ}/resources/{sub}natura.tiff",
        copernicus=f"{PROJ}/data/copernicus/"
        "PROBAV_LC100_global_v3.0.1_2019-nrt_Discrete-Classification-map_EPSG-4326.tif",
        profile_dir=f"{PROJ}/resources/{sub}renewable_profiles",
        profile=f"{PROJ}/resources/{sub}renewable_profiles/profile_onwind.nc",
    )


def ensure_gwa():
    if os.path.exists(GWA_TIF) and os.path.getsize(GWA_TIF) > 10e6:
        logger.info(f"GWA raster present: {GWA_TIF}")
        return
    os.makedirs(os.path.dirname(GWA_TIF), exist_ok=True)
    logger.info(f"Downloading GWA raster from {GWA_URL}")
    subprocess.run(
        ["curl", "-sL", "--max-time", "600", "-o", GWA_TIF, GWA_URL], check=True
    )
    import rasterio  # validation only

    with rasterio.open(GWA_TIF) as src:
        assert src.crs is not None and src.count >= 1
    logger.info(f"Downloaded GWA raster ({os.path.getsize(GWA_TIF)/1e6:.0f} MB)")


def compute_ratios(cutout_path, pct=75):
    """Return xr.Dataset with ratio_mean / ratio_pXX on the full cutout (y,x) grid."""
    import rasterio

    with xr.open_dataset(cutout_path) as ds:
        era5_mean = ds["wnd100m"].mean("time").load()  # (y, x)
    ys, xs = era5_mean.y.values, era5_mean.x.values
    dy = round(float(ys[1] - ys[0]), 6)
    dx = round(float(xs[1] - xs[0]), 6)

    with rasterio.open(GWA_TIF) as src:
        gwa = src.read(1)
        tr = src.transform
        glon = tr.c + tr.a * (np.arange(src.width) + 0.5)
        glat = tr.f + tr.e * (np.arange(src.height) + 0.5)

    valid = np.isfinite(gwa) & (gwa > 0)
    lon_g, lat_g = np.meshgrid(glon, glat)
    lon_v, lat_v, spd_v = lon_g[valid], lat_g[valid], gwa[valid].astype(np.float64)

    # nearest ERA5 cell-center index (cells are dx x dy boxes around centers)
    ix = np.round((lon_v - xs[0]) / dx).astype(np.int64)
    iy = np.round((lat_v - ys[0]) / dy).astype(np.int64)
    inside = (ix >= 0) & (ix < len(xs)) & (iy >= 0) & (iy < len(ys))
    ix, iy, spd_v = ix[inside], iy[inside], spd_v[inside]
    cell = iy * len(xs) + ix

    ncell = len(ys) * len(xs)
    counts = np.bincount(cell, minlength=ncell)
    sums = np.bincount(cell, weights=spd_v, minlength=ncell)
    gwa_mean = np.full(ncell, np.nan)
    np.divide(sums, counts, out=gwa_mean, where=counts > 0)

    # per-cell upper percentile via sort + group offsets
    order = np.argsort(cell, kind="stable")
    cell_s, spd_s = cell[order], spd_v[order]
    starts = np.searchsorted(cell_s, np.arange(ncell))
    ends = np.searchsorted(cell_s, np.arange(ncell) + 1)
    gwa_pct = np.full(ncell, np.nan)
    for c in np.nonzero(counts >= MIN_PIXELS)[0]:
        gwa_pct[c] = np.percentile(spd_s[starts[c] : ends[c]], pct)
    gwa_mean[counts < MIN_PIXELS] = np.nan

    shape = (len(ys), len(xs))
    era5 = era5_mean.values
    with np.errstate(invalid="ignore", divide="ignore"):
        r_mean = gwa_mean.reshape(shape) / era5
        r_pct = gwa_pct.reshape(shape) / era5
    r_mean = np.clip(np.where(np.isfinite(r_mean), r_mean, 1.0), *RATIO_CLIP)
    r_pct = np.clip(np.where(np.isfinite(r_pct), r_pct, 1.0), *RATIO_CLIP)

    out = xr.Dataset(
        {
            "era5_mean": (("y", "x"), era5),
            "gwa_mean": (("y", "x"), gwa_mean.reshape(shape)),
            f"gwa_p{pct}": (("y", "x"), gwa_pct.reshape(shape)),
            "ratio_mean": (("y", "x"), r_mean),
            f"ratio_p{pct}": (("y", "x"), r_pct),
            "n_gwa_pixels": (("y", "x"), counts.reshape(shape)),
        },
        coords={"y": ys, "x": xs},
        attrs={
            "gwa_source": GWA_URL,
            "ratio_clip": str(RATIO_CLIP),
            "percentile": pct,
        },
    )
    return out


def ratio_stats(ratios, name):
    r = ratios[name].values
    cov = ratios["n_gwa_pixels"].values >= MIN_PIXELS
    rv = r[cov]
    logger.info(
        f"{name}: covered cells={cov.sum()}/{r.size}, "
        f"min={rv.min():.3f} p25={np.percentile(rv,25):.3f} "
        f"median={np.median(rv):.3f} p75={np.percentile(rv,75):.3f} "
        f"max={rv.max():.3f} mean={rv.mean():.3f}, "
        f"at clip-hi={(rv >= RATIO_CLIP[1]-1e-9).sum()} clip-lo={(rv <= RATIO_CLIP[0]+1e-9).sum()}"
    )


def write_corrected_cutout(src, dst, ratio):
    """Copy cutout and scale wnd100m in place by ratio(y,x)."""
    import netCDF4

    logger.info(f"Copying cutout -> {dst}")
    shutil.copy2(src, dst)
    with netCDF4.Dataset(dst, "r+") as nc:
        v = nc.variables["wnd100m"]
        nt = v.shape[0]
        step = 1000
        for t0 in range(0, nt, step):
            block = v[t0 : t0 + step]
            v[t0 : t0 + step] = block * ratio[None, :, :]
        nc.setncattr("gwa_correction", "wnd100m scaled by GWA/ERA5 ratio")
        nc.setncattr("gwa_correction_date", dt.date.today().isoformat())
    logger.info("wnd100m scaled in corrected cutout (original untouched).")


def build_onwind_profile(cutout_path, p, out_path):
    """Replicates the onwind branch of scripts/build_renewable_profiles.py."""
    import atlite
    import geopandas as gpd
    from pypsa.geo import haversine

    with open(p["config"]) as f:
        cfg = yaml.safe_load(f)
    config = cfg["renewable"]["onwind"]
    resource = dict(config["resource"])
    correction_factor = config.get("correction_factor", 1.0)
    p_nom_max_meth = config.get("potential", "conservative")
    area_crs = cfg.get("crs", {}).get("area_crs", "ESRI:54009")

    regions = gpd.read_file(p["regions"]).set_index("name").rename_axis("bus")
    buses = regions.index

    cutout = atlite.Cutout(cutout_path)
    # filter_cutout_region
    minx, miny, maxx, maxy = regions.total_bounds
    cutout.data = cutout.data.sel(
        x=slice(max(-180.0, minx - cutout.dx), min(180.0, maxx + cutout.dx)),
        y=slice(max(-90.0, miny - cutout.dy), min(90.0, maxy + cutout.dy)),
    )

    func = getattr(cutout, resource.pop("method"))
    resource["dask_kwargs"] = {"scheduler": None}

    capacity_per_sqkm = config["capacity_per_sqkm"]
    excluder = atlite.ExclusionContainer(crs=area_crs, res=100)
    if config.get("natura", False):
        excluder.add_raster(p["natura"], nodata=0, allow_no_overlap=True)
    cop = config.get("copernicus", {})
    if cop:
        excluder.add_raster(
            p["copernicus"], codes=cop["grid_codes"], invert=True, crs="EPSG:4326"
        )
        if cop.get("distance", 0) > 0:
            excluder.add_raster(
                p["copernicus"],
                codes=cop["distance_grid_codes"],
                buffer=cop["distance"],
                crs="EPSG:4326",
            )

    logger.info("Calculating land-use availability matrix ...")
    t0 = time.time()
    availability = cutout.availabilitymatrix(
        regions, excluder, nprocesses=1, disable_progressbar=True
    )
    logger.info(f"Availability done ({time.time()-t0:.0f}s)")

    area = cutout.grid.to_crs(area_crs).area / 1e6
    area = xr.DataArray(
        area.values.reshape(cutout.shape), [cutout.coords["y"], cutout.coords["x"]]
    )
    potential = capacity_per_sqkm * availability.sum("bus") * area

    capacity_factor = correction_factor * func(capacity_factor=True, **resource)
    layout = capacity_factor * area * capacity_per_sqkm

    logger.info("Converting wind speeds to onwind profile ...")
    profile, capacities = func(
        matrix=availability.stack(spatial=["y", "x"]),
        layout=layout,
        index=buses,
        per_unit=True,
        return_capacity=True,
        **resource,
    )

    if p_nom_max_meth == "simple":
        p_nom_max = capacity_per_sqkm * availability @ area
    elif p_nom_max_meth == "conservative":
        max_cap_factor = capacity_factor.where(availability != 0).max(["x", "y"])
        p_nom_max = capacities / max_cap_factor
    else:
        raise ValueError(p_nom_max_meth)

    layoutmatrix = (layout * availability).stack(spatial=["y", "x"])
    coords = cutout.grid[["x", "y"]]
    bus_coords = regions[["x", "y"]]
    average_distance = []
    for bus in buses:
        row = layoutmatrix.sel(bus=bus).data
        nz_b = (row != 0) & (~pd.isnull(row))
        row = row[nz_b]
        co = coords[nz_b]
        distances = haversine(bus_coords.loc[bus], co)
        average_distance.append((distances * (row / row.sum())).sum())
    average_distance = xr.DataArray(average_distance, [buses])

    ds = xr.merge(
        [
            (correction_factor * profile).rename("profile"),
            capacities.rename("weight"),
            p_nom_max.rename("p_nom_max"),
            potential.rename("potential"),
            average_distance.rename("average_distance"),
        ]
    )
    ds = ds.sel(
        bus=(
            (ds["profile"].mean("time") >= config.get("min_p_max_pu", 0.0))
            & (ds["p_nom_max"] >= config.get("min_p_nom_max", 0.0))
        )
    )
    if "clip_p_max_pu" in config:
        ds["profile"] = ds["profile"].where(
            ds["profile"] >= config["clip_p_max_pu"], 0
        )

    pmax = float(ds["profile"].max())
    assert pmax <= 1.0 + 1e-9, f"p_max_pu > 1 (max={pmax})"
    ds.to_netcdf(out_path)
    logger.info(f"Wrote {out_path} (profile max={pmax:.4f})")
    return ds


def fleet_cf(profile_path, regions_path, fleet_csv, label):
    """p_nom-weighted fleet CF; logic of Epdk/validate_wind_relocation_cf.py."""
    import geopandas as gpd

    with xr.open_dataset(profile_path) as ds:
        cf_bus = ds["profile"].mean("time").to_pandas()
    regions = gpd.read_file(regions_path)[["name", "geometry"]]
    regions["name"] = regions["name"].astype(str)
    df = pd.read_csv(fleet_csv)
    w = df[df["Fueltype"] == "onwind"].copy()
    pts = gpd.GeoDataFrame(
        w, geometry=gpd.points_from_xy(w["lon"], w["lat"]), crs="EPSG:4326"
    )
    j = gpd.sjoin(pts, regions, how="left", predicate="within")
    j = j[~j.index.duplicated()]
    miss = j["name"].isna()
    if miss.any():
        near = gpd.sjoin_nearest(pts.loc[miss[miss].index], regions, how="left")
        near = near[~near.index.duplicated()]
        j.loc[near.index, "name"] = near["name"]
    j["cf"] = j["name"].map(cf_bus).fillna(0.0)
    fleet = (j["cf"] * j["Capacity"]).sum() / j["Capacity"].sum()
    logger.info(
        f"{label}: fleet CF = {fleet*100:.2f}% "
        f"({len(j)} plants, {j['Capacity'].sum():.0f} MW)"
    )
    return fleet, j


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--year", type=int, required=True, choices=sorted(YEAR_SETUP))
    ap.add_argument("--variants", nargs="+", default=[], choices=["mean", "p75"],
                    help="build corrected cutout+profile for these variants")
    ap.add_argument("--pct", type=int, default=75)
    ap.add_argument("--fleet-csv",
                    default=os.path.join(os.environ.get("EPDK_DIR", os.path.join(PROJ, "..", "Epdk")),
                                         "custom_powerplants_2023.csv"))
    ap.add_argument("--apply", choices=["mean", "p75"], default=None,
                    help="backup original profile and overwrite with this variant")
    ap.add_argument("--ratios-only", action="store_true")
    args = ap.parse_args()

    p = paths_for(args.year)
    ensure_gwa()

    ratios = compute_ratios(p["cutout"], pct=args.pct)
    ratio_stats(ratios, "ratio_mean")
    ratio_stats(ratios, f"ratio_p{args.pct}")
    ratios_nc = f"{PROJ}/data/gwa/gwa_era5_ratios_{args.year}.nc"
    ratios.to_netcdf(ratios_nc)
    logger.info(f"Ratios saved: {ratios_nc}")
    if args.ratios_only:
        return

    for variant in args.variants:
        rname = "ratio_mean" if variant == "mean" else f"ratio_p{args.pct}"
        cutout_v = f"{PROJ}/cutouts/cutout-{args.year}-era5-gwa-{variant}.nc"
        profile_v = f"{p['profile_dir']}/profile_onwind_gwa_{variant}.nc"
        if not os.path.exists(cutout_v):
            write_corrected_cutout(p["cutout"], cutout_v, ratios[rname].values)
        build_onwind_profile(cutout_v, p, profile_v)
        fleet_cf(profile_v, p["regions"], args.fleet_csv,
                 f"{args.year} GWA-{variant}")

    if args.apply:
        variant = args.apply
        profile_v = f"{p['profile_dir']}/profile_onwind_gwa_{variant}.nc"
        assert os.path.exists(profile_v), f"build variant first: {profile_v}"
        stamp = dt.date.today().strftime("%Y%m%d")
        bak = f"{p['profile']}.bak.before-gwa-{stamp}"
        if not os.path.exists(bak):
            shutil.copy2(p["profile"], bak)
            logger.info(f"Backup: {bak}")
        shutil.copy2(profile_v, p["profile"])
        logger.info(f"Applied {variant} variant -> {p['profile']}")
        # keep chosen cutout under canonical name, drop the other variant
        chosen = f"{PROJ}/cutouts/cutout-{args.year}-era5-gwa-{variant}.nc"
        canon = f"{PROJ}/cutouts/cutout-{args.year}-era5-gwa.nc"
        if os.path.exists(chosen):
            shutil.move(chosen, canon)
            logger.info(f"Corrected cutout kept as {canon}")
        other = "mean" if variant == "p75" else "p75"
        other_path = f"{PROJ}/cutouts/cutout-{args.year}-era5-gwa-{other}.nc"
        if os.path.exists(other_path):
            os.remove(other_path)
            logger.info(f"Removed unused variant cutout {other_path}")


if __name__ == "__main__":
    main()
