# SPDX-FileCopyrightText: 2026 The PyPSA-Türkiye Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Mevsimsel demand error tablosu (Kore makalesi Tablo 1 muadili).

PyPSA modeled saatlik demand vs EPİAŞ saatlik consumption (κ_cal=1.0347/2023, 1.0212/2018 uygulanmış).
Mevsim bazında ortalama hata %.

Mevsimler:
    İlkbahar: Mar-Nis-May
    Yaz:      Haz-Tem-Ağu
    Sonbahar: Eyl-Eki-Kas
    Kış:      Ara-Oca-Şub

Kullanım:
    python scripts/validate_seasonal.py --network networks/elec.nc --year 2018

Çıktı:
    results/validation/seasonal_demand_error_<year>.csv
    results/validation/seasonal_demand_error_<year>.png
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
log = logging.getLogger("validate_seasonal")

REPO_ROOT = Path(__file__).resolve().parent.parent

SEASONS = {
    "Kış (Ara-Oca-Şub)": [12, 1, 2],
    "İlkbahar (Mar-Nis-May)": [3, 4, 5],
    "Yaz (Haz-Tem-Ağu)": [6, 7, 8],
    "Sonbahar (Eyl-Eki-Kas)": [9, 10, 11],
}


def get_pypsa_total_demand(n: pypsa.Network) -> pd.Series:
    """PyPSA modeled saatlik toplam ulusal yük (MW), tüm bus'ları toplar."""
    if n.loads_t.p_set.empty:
        # Eğer p_set boş ise loads.p (çözülmüş) kullan
        if n.loads_t.p.empty:
            raise SystemExit("PyPSA Network'te demand verisi yok (loads_t.p_set ve loads_t.p boş)")
        return n.loads_t.p.sum(axis=1)
    return n.loads_t.p_set.sum(axis=1)


def load_epias_consumption(epias_dir: Path, year: int, kappa_loss: float = 1.0347) -> pd.Series:
    f = epias_dir / f"consumption_{year}.parquet"
    if not f.exists():
        raise SystemExit(f"EPİAŞ consumption dosyası yok: {f}")
    df = pd.read_parquet(f)
    df["dt"] = pd.to_datetime(df["dt"], utc=True).dt.tz_convert("UTC").dt.tz_localize(None)
    series = df.set_index("dt")["consumption"].astype(float) * kappa_loss
    return series


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--network", default=str(REPO_ROOT / "networks/elec.nc"))
    p.add_argument("--year", type=int, default=2018)
    p.add_argument("--epias-dir", default=str(REPO_ROOT.parent / "epias"))
    p.add_argument("--output-dir", default=str(REPO_ROOT / "results" / "validation"))
    p.add_argument("--kappa-loss", type=float, default=1.0347)  # κ_cal 2023; 2018 için 1.0212 ver
    args = p.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    log.info("PyPSA Network yükleniyor: %s", args.network)
    n = pypsa.Network(args.network)
    pypsa_demand = get_pypsa_total_demand(n)
    log.info("PyPSA demand: %d saat, ortalama %.0f MW",
             len(pypsa_demand), pypsa_demand.mean())

    log.info("EPİAŞ consumption_%d.parquet yükleniyor (κ_loss=%.4f)", args.year, args.kappa_loss)
    epias = load_epias_consumption(Path(args.epias_dir), args.year, args.kappa_loss)
    log.info("EPİAŞ demand: %d saat, ortalama %.0f MW", len(epias), epias.mean())

    # Hizala — ortak indeks
    aligned = pd.concat({"modeled": pypsa_demand, "observed": epias}, axis=1).dropna()
    log.info("Hizalı saat sayısı: %d", len(aligned))
    if aligned.empty:
        raise SystemExit("Hizalı saat yok — snapshots/year uyumsuz olabilir")

    aligned["month"] = aligned.index.month
    aligned["error_pct"] = 100 * (aligned["modeled"] - aligned["observed"]) / aligned["observed"]
    aligned["abs_error_pct"] = aligned["error_pct"].abs()

    # Mevsim bazında özet
    rows = []
    for season, months in SEASONS.items():
        sub = aligned[aligned["month"].isin(months)]
        if sub.empty:
            continue
        rows.append({
            "Mevsim": season,
            "Saat sayısı": len(sub),
            "Modeled ort. (GW)": sub["modeled"].mean() / 1000,
            "Observed ort. (GW)": sub["observed"].mean() / 1000,
            "Mean error %": sub["error_pct"].mean(),
            "Mean abs error %": sub["abs_error_pct"].mean(),
            "RMSE (MW)": np.sqrt(((sub["modeled"] - sub["observed"]) ** 2).mean()),
        })

    # Yıllık toplam
    rows.append({
        "Mevsim": "YILLIK ORTALAMA",
        "Saat sayısı": len(aligned),
        "Modeled ort. (GW)": aligned["modeled"].mean() / 1000,
        "Observed ort. (GW)": aligned["observed"].mean() / 1000,
        "Mean error %": aligned["error_pct"].mean(),
        "Mean abs error %": aligned["abs_error_pct"].mean(),
        "RMSE (MW)": np.sqrt(((aligned["modeled"] - aligned["observed"]) ** 2).mean()),
    })

    df = pd.DataFrame(rows).set_index("Mevsim")
    out_csv = out_dir / f"seasonal_demand_error_{args.year}.csv"
    df.round(3).to_csv(out_csv)
    log.info("Yazıldı: %s", out_csv)
    print()
    print(df.round(2).to_string())

    # Görsel
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(1, 2, figsize=(14, 5))
        df_no_yearly = df.drop("YILLIK ORTALAMA", errors="ignore")
        df_no_yearly[["Modeled ort. (GW)", "Observed ort. (GW)"]].plot.bar(ax=axes[0])
        axes[0].set_title(f"Mevsim Bazında Ortalama Yük ({args.year})")
        axes[0].set_ylabel("GW")
        axes[0].tick_params(axis="x", rotation=20)
        df_no_yearly[["Mean error %", "Mean abs error %"]].plot.bar(ax=axes[1])
        axes[1].set_title(f"Mevsim Bazında Hata % ({args.year})")
        axes[1].set_ylabel("%")
        axes[1].tick_params(axis="x", rotation=20)
        plt.tight_layout()
        out_png = out_dir / f"seasonal_demand_error_{args.year}.png"
        plt.savefig(out_png, dpi=150)
        log.info("Görsel yazıldı: %s", out_png)
    except Exception as e:
        log.warning("Görsel üretilemedi: %s", e)


if __name__ == "__main__":
    main()
