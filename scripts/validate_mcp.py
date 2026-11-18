# SPDX-FileCopyrightText: 2026 The PyPSA-Türkiye Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
PyPSA shadow price (KKT marginal / LMP) vs EPİAŞ MCP korelasyon analizi.

ÖZGÜN KATKI: PyPSA-Korea makalesi bu validasyonu yapmamış. PyPSA-Türkiye'nin akademik
unique-selling-point'i: dispatch + price co-validation.

PyPSA tarafı: n.buses_t.marginal_price (TL/MWh varsayılan; MWh başına LMP)
EPİAŞ tarafı: epias/mcp_<year>.parquet → 'price' kolonu (TL/MWh)

Kullanım:
    python scripts/validate_mcp.py --network networks/elec_s_50_solved.nc --year 2018

Çıktı:
    results/validation/mcp_correlation_<year>.csv  (saatlik hizalama + Pearson)
    results/validation/mcp_timeseries_<year>.png   (zaman serisi karşılaştırma)
    results/validation/mcp_scatter_<year>.png      (scatter + regression)

Not: Bu scripti çalıştırmak için PyPSA Network'ünün **çözülmüş** olması gerekir
(solve_network rule'u tamamlanmalı). networks/elec.nc çözülmemiştir; çözülmüş
networks için elec_s_50_ec_lcopt_*.nc benzeri dosyaları kullanın.
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
log = logging.getLogger("validate_mcp")

REPO_ROOT = Path(__file__).resolve().parent.parent


def get_pypsa_lmp(n: pypsa.Network) -> pd.Series:
    """Saatlik ulusal ortalama LMP (yük-ağırlıklı, TL/MWh)."""
    if n.buses_t.marginal_price.empty:
        raise SystemExit("Network çözülmemiş: n.buses_t.marginal_price boş. "
                         "Önce snakemake -j1 results/networks/<solved>.nc çalıştır.")
    if n.loads_t.p_set.empty:
        log.warning("loads_t.p_set boş, düz ortalama LMP alınıyor")
        return n.buses_t.marginal_price.mean(axis=1)
    weights = n.loads_t.p_set
    weighted = (n.buses_t.marginal_price.reindex(columns=weights.columns) * weights).sum(axis=1)
    total_load = weights.sum(axis=1)
    return weighted / total_load


def load_epias_mcp(epias_dir: Path, year: int) -> pd.Series:
    f = epias_dir / f"mcp_{year}.parquet"
    if not f.exists():
        raise SystemExit(f"EPİAŞ MCP dosyası yok: {f}")
    df = pd.read_parquet(f)
    df["dt"] = pd.to_datetime(df["date"], utc=True).dt.tz_convert("UTC").dt.tz_localize(None)
    return df.set_index("dt")["price"].astype(float)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--network", required=True,
                   help="ÇÖZÜLMÜŞ PyPSA Network (örn. networks/elec_s_50_ec_lcopt_Co2L.nc)")
    p.add_argument("--year", type=int, default=2018)
    p.add_argument("--epias-dir", default=str(REPO_ROOT.parent / "epias"))
    p.add_argument("--output-dir", default=str(REPO_ROOT / "results" / "validation"))
    args = p.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    log.info("PyPSA Network yükleniyor: %s", args.network)
    n = pypsa.Network(args.network)
    lmp = get_pypsa_lmp(n)
    log.info("PyPSA LMP: %d saat, ortalama %.2f TL/MWh", len(lmp), lmp.mean())

    log.info("EPİAŞ MCP yükleniyor")
    mcp = load_epias_mcp(Path(args.epias_dir), args.year)
    log.info("EPİAŞ MCP: %d saat, ortalama %.2f TL/MWh", len(mcp), mcp.mean())

    aligned = pd.concat({"pypsa_lmp": lmp, "epias_mcp": mcp}, axis=1).dropna()
    log.info("Hizalı saat sayısı: %d", len(aligned))

    # Pearson korelasyon
    pearson = aligned["pypsa_lmp"].corr(aligned["epias_mcp"])
    spearman = aligned["pypsa_lmp"].corr(aligned["epias_mcp"], method="spearman")
    rmse = np.sqrt(((aligned["pypsa_lmp"] - aligned["epias_mcp"]) ** 2).mean())
    mae = (aligned["pypsa_lmp"] - aligned["epias_mcp"]).abs().mean()
    bias = (aligned["pypsa_lmp"] - aligned["epias_mcp"]).mean()

    summary = pd.DataFrame({
        "Metric": ["Pearson r", "Spearman ρ", "RMSE (TL/MWh)", "MAE (TL/MWh)",
                   "Bias (TL/MWh)", "Saat sayısı",
                   "PyPSA ort. (TL/MWh)", "EPİAŞ ort. (TL/MWh)"],
        "Value": [pearson, spearman, rmse, mae, bias, len(aligned),
                  aligned["pypsa_lmp"].mean(), aligned["epias_mcp"].mean()],
    })
    out_csv = out_dir / f"mcp_correlation_{args.year}.csv"
    summary.to_csv(out_csv, index=False)
    log.info("Yazıldı: %s", out_csv)
    print()
    print(summary.to_string(index=False))

    # Hedef akademik metrik:
    if pearson >= 0.7:
        log.info("✓ Pearson r ≥ 0.7 — model ile gerçek piyasa fiyatı GÜÇLÜ uyumlu")
    elif pearson >= 0.5:
        log.info("△ Pearson r ≥ 0.5 — orta uyum")
    else:
        log.info("✗ Pearson r < 0.5 — uyum zayıf, model kalibrasyonu gerekli")

    # Görsel
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        # Zaman serisi
        fig, ax = plt.subplots(figsize=(14, 5))
        aligned[["pypsa_lmp", "epias_mcp"]].plot(ax=ax, alpha=0.6)
        ax.set_title(f"PyPSA Shadow Price vs EPİAŞ MCP — {args.year}\n"
                     f"Pearson r = {pearson:.3f}")
        ax.set_ylabel("TL/MWh")
        plt.tight_layout()
        plt.savefig(out_dir / f"mcp_timeseries_{args.year}.png", dpi=150)

        # Scatter
        fig, ax = plt.subplots(figsize=(7, 7))
        aligned.plot.scatter(x="epias_mcp", y="pypsa_lmp", alpha=0.3, s=8, ax=ax)
        lim = max(aligned.max())
        ax.plot([0, lim], [0, lim], "r--", label="y=x ideal")
        ax.set_xlabel("EPİAŞ MCP (TL/MWh)")
        ax.set_ylabel("PyPSA Shadow Price (TL/MWh)")
        ax.set_title(f"Scatter — {args.year}, Pearson r = {pearson:.3f}")
        ax.legend()
        plt.tight_layout()
        plt.savefig(out_dir / f"mcp_scatter_{args.year}.png", dpi=150)
        log.info("Görseller yazıldı: mcp_timeseries_%d.png, mcp_scatter_%d.png",
                 args.year, args.year)
    except Exception as e:
        log.warning("Görsel üretilemedi: %s", e)


if __name__ == "__main__":
    main()
