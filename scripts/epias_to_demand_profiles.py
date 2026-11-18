# SPDX-FileCopyrightText: 2026 The PyPSA-Türkiye Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
EPİAŞ saatlik ulusal yükünü PyPSA bus dağılımıyla birleştirip
resources/demand_profiles.csv'yi gerçek TR verisiyle overwrite eder.

Mantık:
    1. GEGIS tabanlı demand_profiles.csv'i oku (saat × bus, MWh).
    2. Her bus'un ulusal toplama oranını çıkar (saatlik ortalama).
       Bu oran nüfus + GDP ağırlıklı zaten — pipeline'ın yaptığı dağılımdır.
    3. EPİAŞ consumption parquet'lerini birleştir → saatlik ulusal toplam (MWh).
    4. snapshot aralığına göre kırp.
    5. yeni_demand[saat, bus] = bus_payı[bus] × ulusal_toplam[saat]
    6. resources/demand_profiles.csv overwrite (yedek alınır).

Kullanım:
    # Önce snakemake ile GEGIS tabanlı demand_profiles üretilmiş olmalı:
    snakemake -j1 resources/demand_profiles.csv

    # Sonra bu script çalıştırılır:
    python scripts/epias_to_demand_profiles.py
    python scripts/epias_to_demand_profiles.py --epias-dir /alt/yol --start 2018-01-01 --end 2019-01-01

Notlar:
    - Snapshot saat çözünürlüğüyle uyumludur (saatlik).
    - EPİAŞ verisi UTC+3'tür; pandas otomatik UTC'ye normalize eder ve naive timestamp'a çevrilir
      (PyPSA snapshots naive UTC kullanır).
    - Eksik saat varsa interpolasyon yapılmaz; uyarı verir.
"""

from __future__ import annotations

import argparse
import glob
import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("epias_to_demand")


REPO_ROOT = Path(__file__).resolve().parent.parent


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="EPİAŞ saatlik yük → resources/demand_profiles.csv"
    )
    p.add_argument(
        "--epias-dir",
        default=None,
        help="EPİAŞ parquet klasörü (varsayılan: data/epias/ veya ../epias/)",
    )
    p.add_argument(
        "--demand-csv",
        default=str(REPO_ROOT / "resources" / "demand_profiles.csv"),
        help="GEGIS tabanlı bus dağılımı (kaynak); script bunu overwrite eder",
    )
    p.add_argument(
        "--config",
        default=str(REPO_ROOT / "config.yaml"),
        help="snapshots aralığını okumak için",
    )
    p.add_argument("--start", default=None, help="Override snapshot start (YYYY-MM-DD)")
    p.add_argument("--end", default=None, help="Override snapshot end (YYYY-MM-DD)")
    p.add_argument(
        "--no-backup",
        action="store_true",
        help="demand_profiles.csv yedeğini alma",
    )
    p.add_argument(
        "--kappa-loss",  # tarihsel ad; anlamı κ_cal (kapsam kalibrasyonu)
        type=float,
        default=1.0347,
        help="Kapsam kalibrasyon katsayısı (κ_cal). EPİAŞ gerçek-zamanlı tüketim = şebeke yükü "
             "(ikili anlaşmalar ve şebeke kayıpları DAHİL; santral iç ihtiyacı ve lisanssız GES "
             "öz-tüketimi HARİÇ). Hedef: TEİAŞ brüt tüketim − santral iç ihtiyacı = şebekeye verilen "
             "enerji (iletim düzeyi model için doğru büyüklük). 2023: 335.17−16.20=318.97 TWh / "
             "EPİAŞ 308.3 TWh → κ=1.0347. Kaynak: TEDAŞ 2024 Türkiye Elektrik Dağıtımı Sektör "
             "Raporu Tablo 5 (TEİAŞ verisi). Yöntem emsali: PyPSA-Eur load.scaling_factor / "
             "ENTSO-E representativity factor (Hörsch et al. 2018). Kapatmak için 1.0 ver.",
    )
    return p.parse_args()


def find_epias_dir(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit)
        if p.exists():
            return p
        sys.exit(f"epias-dir bulunamadı: {p}")
    candidates = [
        REPO_ROOT / "data" / "epias",
        REPO_ROOT.parent / "epias",
        REPO_ROOT / "epias",
    ]
    for c in candidates:
        if c.exists() and any(c.glob("consumption_*.parquet")):
            return c
    sys.exit(
        "EPİAŞ parquet klasörü bulunamadı. --epias-dir ile yolu belirt veya şuralardan birine koy:\n  "
        + "\n  ".join(str(c) for c in candidates)
    )


def load_epias_consumption(epias_dir: Path) -> pd.Series:
    """Tüm consumption_*.parquet'leri birleştir → saatlik ulusal toplam (MWh)."""
    files = sorted(epias_dir.glob("consumption_*.parquet"))
    if not files:
        sys.exit(f"consumption_*.parquet yok: {epias_dir}")
    log.info("EPİAŞ consumption: %d parquet dosyası birleştiriliyor", len(files))
    frames = [pd.read_parquet(f) for f in files]
    df = pd.concat(frames, ignore_index=True)
    df["dt"] = pd.to_datetime(df["dt"], utc=True)
    df = df.sort_values("dt").drop_duplicates(subset="dt").reset_index(drop=True)

    # 'consumption' final değer (load_plan + UEÇM + rt_cons öncelikli birleşim).
    # rt_cons da geçerli alternatif; consumption daha temiz.
    series = df.set_index("dt")["consumption"].astype(float)
    series.index = series.index.tz_convert("UTC").tz_localize(None)  # PyPSA naive UTC
    log.info(
        "EPİAŞ tüketim: %d saat, %s → %s, ortalama %.0f MW",
        len(series), series.index.min(), series.index.max(), series.mean(),
    )
    return series


def get_snapshot_range(args) -> tuple[pd.Timestamp, pd.Timestamp]:
    if args.start and args.end:
        return pd.Timestamp(args.start), pd.Timestamp(args.end)
    cfg_path = Path(args.config)
    cfg = yaml.safe_load(cfg_path.read_text())
    s = cfg["snapshots"]
    return pd.Timestamp(s["start"]), pd.Timestamp(s["end"])


def main() -> None:
    args = parse_args()

    epias_dir = find_epias_dir(args.epias_dir)
    log.info("EPİAŞ klasörü: %s", epias_dir)

    demand_path = Path(args.demand_csv)
    if not demand_path.exists():
        sys.exit(
            f"GEGIS demand_profiles.csv yok: {demand_path}\n"
            "Önce şunu çalıştır: snakemake -j1 resources/demand_profiles.csv"
        )
    log.info("Kaynak demand_profiles.csv: %s", demand_path)

    # GEGIS tabanlı dağılım
    gegis = pd.read_csv(demand_path, index_col=0, parse_dates=True)
    log.info("GEGIS demand: %d saat × %d bus", *gegis.shape)
    if gegis.empty or gegis.shape[1] == 0:
        sys.exit("GEGIS demand_profiles.csv boş — önce snakemake demand'ı düzgün üretmeli.")

    # Bus payı: her bus'un saatlik ortalamasının ulusal toplamdaki oranı.
    # GEGIS'te bus payı saatten saate sabit (sadece nüfus+GDP ile dağıtılmış).
    # Yine de güvenli olsun diye saatlerin ortalamasını kullanıyoruz.
    bus_means = gegis.mean(axis=0)
    bus_share = bus_means / bus_means.sum()
    log.info(
        "Bus payı çıkarıldı: %d bus, en yüksek pay %.4f, en düşük %.4f, toplam %.6f",
        len(bus_share), bus_share.max(), bus_share.min(), bus_share.sum(),
    )

    # EPİAŞ ulusal toplam
    ulusal = load_epias_consumption(epias_dir)

    # κ_cal uygula: EPİAŞ şebeke yükü (ikili anlaşmalar + kayıplar DAHİL; santral iç
    # ihtiyacı ve lisanssız GES öz-tüketimi HARİÇ) → TEİAŞ brüt − iç ihtiyaç = şebekeye
    # verilen enerji (iletim-düzeyi model hedefi). 2023: κ=1.0347, 2018: κ=1.0212.
    if args.kappa_loss != 1.0:
        log.info("κ_cal = %.4f uygulanıyor (EPİAŞ şebeke yükü → TEİAŞ şebekeye-verilen kalibrasyon)", args.kappa_loss)
        ulusal = ulusal * args.kappa_loss
    else:
        log.info("κ_cal = 1.0 (kapatıldı)")

    # Snapshot aralığına kırp
    start, end = get_snapshot_range(args)
    log.info("Snapshot aralığı: %s → %s (left-inclusive)", start, end)
    end_excl = end - pd.Timedelta(hours=1)  # PyPSA inclusive=left
    ulusal_sliced = ulusal.loc[start:end_excl]
    expected = int((end - start) / pd.Timedelta(hours=1))
    log.info("EPİAŞ kırpılmış: %d saat (beklenen %d)", len(ulusal_sliced), expected)
    if len(ulusal_sliced) != expected:
        log.warning(
            "Saat sayısı farklı! Snapshot %d, EPİAŞ %d. Eksik saatler 0 ile doldurulacak.",
            expected, len(ulusal_sliced),
        )
        full_idx = pd.date_range(start, end_excl, freq="h")
        ulusal_sliced = ulusal_sliced.reindex(full_idx, fill_value=0.0)

    # bus_share dış çarpım: saat × bus
    new_demand = pd.DataFrame(
        ulusal_sliced.values[:, None] * bus_share.values[None, :],
        index=ulusal_sliced.index,
        columns=bus_share.index,
    )
    new_demand.index.name = gegis.index.name or "time"

    # Yedek + yaz
    if not args.no_backup:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = demand_path.with_suffix(f".csv.bak.{ts}")
        shutil.copy2(demand_path, backup)
        log.info("Yedek: %s", backup.name)

    new_demand.to_csv(demand_path)
    log.info(
        "Yazıldı: %s — %d saat × %d bus, toplam %.1f TWh",
        demand_path, *new_demand.shape, new_demand.values.sum() / 1e6,
    )
    log.info(
        "Eski toplam (GEGIS): %.1f TWh — yeni (EPİAŞ): %.1f TWh",
        gegis.values.sum() / 1e6, new_demand.values.sum() / 1e6,
    )


if __name__ == "__main__":
    main()
