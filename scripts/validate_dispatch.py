# SPDX-FileCopyrightText: 2026 The PyPSA-Türkiye Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
PyPSA modeled dispatch vs EPİAŞ generation karşılaştırması (Kore Tablo A1 muadili).

EPİAŞ saatlik üretim verisi 16 carrier (naturalGas, lignite, river, sun, wind, ...)
ile PyPSA modelinin yıllık carrier toplamlarını kıyaslar.

Kullanım:
    python scripts/validate_dispatch.py --network networks/elec.nc --year 2018
    python scripts/validate_dispatch.py --network networks/elec_s_50.nc --year 2023

Çıktı:
    results/validation/dispatch_carrier_<year>.csv  (modeled, observed, % hata)
    results/validation/dispatch_carrier_<year>.png  (bar chart)
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import pypsa


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("validate_dispatch")

REPO_ROOT = Path(__file__).resolve().parent.parent

# EPİAŞ kolonu → PyPSA carrier eşlemesi (Kore Tablo A1 mantığı)
EPIAS_TO_PYPSA = {
    "naturalGas": "CCGT",  # CCGT + OCGT toplamı (PyPSA'da ayrı)
    "lignite": "lignite",
    "importCoal": "coal",  # PyPSA "coal" ithal kömürü temsil eder
    "blackCoal": "coal",   # taşkömürü → "coal" altında
    "asphaltiteCoal": "coal",
    "fueloil": "oil",
    "naphta": "oil",
    "lng": "CCGT",  # LNG → CCGT
    "dammedHydro": "hydro",  # barajlı
    "river": "ror",  # akarsu (run-of-river)
    "wind": "onwind",
    "sun": "solar",
    "geothermal": "geothermal",
    "biomass": "biomass",
    "wasteheat": "biomass",  # atık ısı → biomass altında topla
}


def load_epias_generation(epias_dir: Path, year: int) -> pd.DataFrame:
    f = epias_dir / f"generation_{year}.parquet"
    if not f.exists():
        raise SystemExit(f"EPİAŞ generation dosyası yok: {f}")
    df = pd.read_parquet(f)
    df["dt"] = pd.to_datetime(df["date"], utc=True).dt.tz_convert("UTC").dt.tz_localize(None)
    return df.set_index("dt")


def aggregate_epias_to_pypsa(epias: pd.DataFrame) -> pd.Series:
    """EPİAŞ 16 kaynağı PyPSA carrier'larına eşle, yıllık MWh topla."""
    out: dict[str, float] = {}
    for ep_col, py_carrier in EPIAS_TO_PYPSA.items():
        if ep_col not in epias.columns:
            continue
        out[py_carrier] = out.get(py_carrier, 0.0) + epias[ep_col].sum()
    return pd.Series(out, name="observed_TWh") / 1e6  # MWh → TWh


def aggregate_pypsa_dispatch(n: pypsa.Network) -> pd.Series:
    """PyPSA modelinin carrier bazında yıllık dispatch'i (TWh)."""
    if n.generators_t.p.empty:
        # Çözülmemiş ağ — kapasite × snapshot saatleri × kapasite faktörü kullanılamaz
        log.warning("PyPSA n.generators_t.p boş; ağ henüz çözülmemiş. Kapasite × CF kullanılacak (yaklaşık).")
        # Yaklaşım: p_nom × snapshots saat sayısı × ortalama p_max_pu (RES için)
        gen = n.generators
        # CF dataframe
        if not n.generators_t.p_max_pu.empty:
            cf = n.generators_t.p_max_pu.mean()
            gen = gen.copy()
            gen["mean_cf"] = gen.index.map(cf).fillna(1.0)
        else:
            gen["mean_cf"] = 1.0
        gen["dispatch_TWh"] = (
            gen["p_nom"] * gen["mean_cf"] * len(n.snapshots) / 1e6
        )
        return gen.groupby("carrier")["dispatch_TWh"].sum()
    else:
        return n.generators_t.p.sum().groupby(n.generators.carrier).sum() / 1e6


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--network", default=str(REPO_ROOT / "networks/elec.nc"),
                   help="PyPSA Network NetCDF dosyası")
    p.add_argument("--year", type=int, default=2018, help="EPİAŞ yıl")
    p.add_argument("--epias-dir", default=str(REPO_ROOT.parent / "epias"),
                   help="EPİAŞ parquet klasörü")
    p.add_argument("--output-dir", default=str(REPO_ROOT / "results" / "validation"),
                   help="Çıktı klasörü")
    args = p.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    log.info("PyPSA Network yükleniyor: %s", args.network)
    n = pypsa.Network(args.network)
    log.info("EPİAŞ generation_%d.parquet yükleniyor", args.year)
    epias = load_epias_generation(Path(args.epias_dir), args.year)

    obs = aggregate_epias_to_pypsa(epias)
    mod = aggregate_pypsa_dispatch(n)

    df = pd.DataFrame({"observed_TWh": obs, "modeled_TWh": mod}).fillna(0.0)
    df["error_TWh"] = df["modeled_TWh"] - df["observed_TWh"]
    df["error_pct"] = np.where(df["observed_TWh"] > 0,
                                100 * df["error_TWh"] / df["observed_TWh"], np.nan)
    df = df.sort_values("observed_TWh", ascending=False)

    out_csv = out_dir / f"dispatch_carrier_{args.year}.csv"
    df.to_csv(out_csv)
    log.info("Yazıldı: %s", out_csv)

    # Özet
    total_obs = df["observed_TWh"].sum()
    total_mod = df["modeled_TWh"].sum()
    log.info("Toplam yıllık (TWh): observed=%.1f, modeled=%.1f, fark=%.2f%%",
             total_obs, total_mod, 100 * (total_mod - total_obs) / total_obs)
    print()
    print(df.round(2).to_string())

    # Görsel (matplotlib)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        ax = df[["observed_TWh", "modeled_TWh"]].plot.bar(figsize=(10, 5),
                                                          color=["#1f77b4", "#ff7f0e"])
        ax.set_ylabel("Yıllık Üretim (TWh)")
        ax.set_title(f"PyPSA-Türkiye vs EPİAŞ Dispatch Karşılaştırması ({args.year})")
        ax.set_xlabel("Carrier")
        plt.xticks(rotation=45, ha="right")
        plt.tight_layout()
        out_png = out_dir / f"dispatch_carrier_{args.year}.png"
        plt.savefig(out_png, dpi=150)
        log.info("Görsel yazıldı: %s", out_png)
    except Exception as e:
        log.warning("Görsel üretilemedi: %s", e)


if __name__ == "__main__":
    main()
