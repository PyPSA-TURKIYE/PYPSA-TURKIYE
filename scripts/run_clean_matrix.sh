#!/bin/bash
# SPDX-FileCopyrightText: 2026 The PyPSA-Türkiye Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# PyPSA-Türkiye TEMİZ AKIŞ v2: her (node × senaryo) kendi config'iyle TAM ZİNCİR rebuild
# v2: talep-koruma kalkanı (EPİAŞ demand_profiles.csv ezilirse otomatik geri yükle + dur)
set -u
command -v snakemake >/dev/null 2>&1 || source "${PYPSA_ENV:-/opt/pypsa-env}/bin/activate"
export GRB_LICENSE_FILE="${GRB_LICENSE_FILE:-$HOME/.gurobi/gurobi.lic}"
cd "$(dirname "$(readlink -f "$0")")/.."  # repository root
LOG=/tmp/clean_matrix.log
DEMAND=resources/demand_profiles.csv
DEMAND_BAK=resources/demand_profiles.csv.bak.epias2018-kappa1.0212   # EPİAŞ 289.87 TWh (κ_cal2018=1.0212, TEDAŞ 2022 Tablo 7)
MIN_SIZE=200000000  # 200 MB altıysa ezilmiş demektir

check_demand() {
  local sz=$(stat -c%s "$DEMAND" 2>/dev/null || echo 0)
  if [ "$sz" -lt "$MIN_SIZE" ]; then
    echo "[UYARI] demand_profiles ezilmiş ($sz byte) — EPİAŞ yedeğinden geri yükleniyor" | tee -a $LOG
    cp "$DEMAND_BAK" "$DEMAND"
    touch -d "2026-06-12 17:00:00" "$DEMAND"   # profil mtime'larından (GWA 16:26) yeni kalmalı
    return 1
  fi
  return 0
}

run_one() {
  local NODE=$1 SCEN=$2 OPTS=$3
  local FINAL="results/networks/clean/elec_s_${NODE}_${SCEN}.nc"
  local TARGET="results/networks/elec_s_${NODE}_ec_lcopt_${OPTS}.nc"
  if [ -f "$FINAL" ]; then echo "[SKIP] $FINAL var" | tee -a $LOG; return 0; fi
  check_demand
  echo "[RUN ] n${NODE} ${SCEN} (opts=${OPTS}) $(date +%H:%M:%S)" | tee -a $LOG
  rm -f "$TARGET"; rm -f .snakemake/locks/* 2>/dev/null
  snakemake -j1 --rerun-triggers mtime -R add_electricity \
    --configfile senaryolar/n${NODE}/*_${SCEN}.yaml -- "$TARGET" >> $LOG 2>&1
  local RC=$?
  # Koşum sonrası talep kontrolü — ezildiyse sonuç geçersiz, geri yükle ve işaretle
  if ! check_demand; then
    echo "[FAIL] n${NODE} ${SCEN}: build_demand_profiles EZDİ — sonuç geçersiz, atlandı" | tee -a $LOG
    rm -f "$TARGET"; return 1
  fi
  if [ $RC -eq 0 ] && [ -f "$TARGET" ]; then
    mv "$TARGET" "$FINAL"
    echo "[ OK ] $FINAL $(date +%H:%M:%S)" | tee -a $LOG
  else
    echo "[FAIL] n${NODE} ${SCEN} rc=$RC $(date +%H:%M:%S)" | tee -a $LOG
  fi
}

echo "=== TEMİZ MATRIS v2 BAŞLADI $(date) ===" | tee -a $LOG
for NODE in 7 22; do
  run_one $NODE REF          CCL
  run_one $NODE NDC2030      Co2L-CCL
  run_one $NODE HIGHRES      Co2L-CCL
  run_one $NODE NETZERO2053  Co2L-CCL
  run_one $NODE NOFOSSIL     Co2L-CCL
done
echo "=== TEMİZ MATRIS v2 BİTTİ $(date) ===" | tee -a $LOG
