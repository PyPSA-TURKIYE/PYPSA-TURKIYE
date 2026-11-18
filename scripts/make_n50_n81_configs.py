#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 The PyPSA-Türkiye Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
make_n50_n81_configs.py — 50 ve 81 node senaryo config'lerini üretir.

Mevcut senaryolar/n22/*.yaml desenini birebir kopyalar, yalnızca
scenario.clusters değerini [50] / [81] yapar. scale, hydro_inflow_calibration,
agg_p_nom (trajectory/accel), opts, co2limit AYNEN korunur.

Ayrıca config.yaml'a kümeleme build_year/lifetime aggregation düzeltmesini
ekler (bekleyen düzeltme: clustering'de conventional carrier'lara
capacity_weighted_average uygulanıp build_year=1010 gibi anomali oluşuyordu;
overnight'ta zararsız ama temizlik + myopic'e dönülürse kritik).

Çalıştırma (proje kökünden):  python scripts/make_n50_n81_configs.py
Taşınabilir: mutlak yol yok; script kendi konumundan proje kökünü bulur.
"""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent      # .../pypsa-TR
SEN = ROOT / "senaryolar"
SRC = SEN / "n22"
SCENARIOS = ["01_REF", "02_NDC2030", "03_HIGHRES", "04_NETZERO2053", "05_NOFOSSIL"]


def make_node_configs(node: int) -> None:
    outdir = SEN / f"n{node}"
    outdir.mkdir(exist_ok=True)
    for scen in SCENARIOS:
        src = SRC / f"{scen}.yaml"
        if not src.exists():
            raise FileNotFoundError(f"Kaynak desen yok: {src}")
        txt = src.read_text()
        # clusters: \n  - 22  →  - {node}   (yalnızca scenario.clusters bloğu)
        out_lines, in_clusters = [], False
        for line in txt.splitlines():
            stripped = line.strip()
            if stripped == "clusters:":
                in_clusters = True
                out_lines.append(line)
                continue
            if in_clusters and stripped.startswith("- "):
                indent = line[: len(line) - len(line.lstrip())]
                out_lines.append(f"{indent}- {node}")
                in_clusters = False
                continue
            in_clusters = False
            # başlık yorumundaki "@ 22 node" / "clusters: 22" ifadelerini güncelle
            line = line.replace("@ 22 node", f"@ {node} node").replace("clusters: 22", f"clusters: {node}")
            out_lines.append(line)
        (outdir / f"{scen}.yaml").write_text("\n".join(out_lines) + "\n")
        print(f"  yazıldı: senaryolar/n{node}/{scen}.yaml (clusters={node})")


def patch_aggregation_strategies() -> None:
    """config.yaml aggregation_strategies.generators'a build_year + lifetime: first ekle."""
    cfg = ROOT / "config.yaml"
    txt = cfg.read_text()
    if "build_year: first" in txt:
        print("  config.yaml: build_year/lifetime düzeltmesi zaten var, atlandı")
        return
    anchor = "      p_max_pu: capacity_weighted_average"
    if anchor not in txt:
        print("  UYARI: aggregation_strategies p_max_pu satırı bulunamadı — config elle kontrol edilmeli")
        return
    addition = (
        anchor
        + "\n      build_year: first  # FIX: clustering'de capacity_weighted_average build_year'ı bozuyordu (1010 anomali); tüm değerler 2020 → first temiz"
        + "\n      lifetime: first    # FIX: aynı; myopic'e dönülürse kritik, overnight'ta zararsız"
    )
    cfg.write_text(txt.replace(anchor, addition, 1))
    print("  config.yaml: build_year/lifetime: first eklendi (aggregation_strategies.generators)")


if __name__ == "__main__":
    print("50/81 node senaryo config'leri üretiliyor (kaynak: senaryolar/n22/)...")
    make_node_configs(50)
    make_node_configs(81)
    print("\nconfig.yaml build_year/lifetime aggregation düzeltmesi:")
    patch_aggregation_strategies()
    print("\nTAMAM. Sonraki: bash scripts/run_50_81.sh")
