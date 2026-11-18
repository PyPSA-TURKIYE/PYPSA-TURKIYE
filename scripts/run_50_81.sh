#!/bin/bash
# SPDX-FileCopyrightText: 2026 The PyPSA-Türkiye Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# run_50_81.sh — PyPSA-Türkiye 50 ve 81 node, 5 senaryo (taşınabilir)
# Temel: run_clean_matrix.sh (idempotent + talep-koruma kalkanı + tam-zincir -R add_electricity).
# Docker/Ubuntu uyumlu: mutlak yol yok; proje kökü script konumundan bulunur,
# Gurobi lisansı GRB_LICENSE_FILE (Docker'da /work/gurobi.lic) veya proje kökünden okunur.
#
# Çalıştırma (container içinde, proje kökünden):  bash scripts/run_50_81.sh
# Tek node:                                       bash scripts/run_50_81.sh 50
set -u

# --- Proje kökü (script .../pypsa-TR/scripts/run_50_81.sh) ---
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# --- Conda env: Docker'da PATH'te hazır; değilse PYPSA_ENV ile aktive et ---
if ! command -v snakemake >/dev/null 2>&1; then
  if [ -n "${PYPSA_ENV:-}" ] && [ -f "${PYPSA_ENV}/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "${PYPSA_ENV}/bin/activate"
  else
    echo "HATA: 'snakemake' PATH'te yok. Conda env'i aktive et veya PYPSA_ENV ver." >&2
    exit 1
  fi
fi

# --- Gurobi lisansı: env > proje kökü gurobi.lic > ~/.gurobi ---
if [ -z "${GRB_LICENSE_FILE:-}" ]; then
  if [ -f "$ROOT/gurobi.lic" ]; then export GRB_LICENSE_FILE="$ROOT/gurobi.lic"
  elif [ -f "$HOME/.gurobi/gurobi.lic" ]; then export GRB_LICENSE_FILE="$HOME/.gurobi/gurobi.lic"
  fi
fi
echo "Gurobi lisansı: ${GRB_LICENSE_FILE:-(yok — HiGHS fallback gerekebilir)}"

# --- mtime senkronu (bir kez): unzip sonrası tarih karışıklığı upstream kuralları
# tetiklemesin diye girdiler ESKİ, hazır çıktılar YENİ yapılır. Böylece snakemake
# asla GIS verisi indirmeye / profilleri yeniden üretmeye kalkmaz.
if [ ! -f .mtimes_synced ]; then
  echo "[HAZIRLIK] mtime senkronu (ilk çalıştırmada bir kez)..."
  find data sources cutouts -type f -exec touch -t 202001010000 {} + 2>/dev/null
  find resources networks/base.nc -type f -exec touch {} + 2>/dev/null
  touch networks/base.nc 2>/dev/null
  touch .mtimes_synced
fi

LOG="${LOG:-/tmp/run_50_81.log}"
DEMAND=resources/demand_profiles.csv
DEMAND_BAK=resources/demand_profiles.csv.bak.epias2018-kappa1.0212   # EPİAŞ 289.87 TWh (κ_cal2018=1.0212)
MIN_SIZE=200000000  # 200 MB altıysa talep ezilmiş demektir

check_demand() {
  local sz; sz=$(stat -c%s "$DEMAND" 2>/dev/null || echo 0)
  if [ "$sz" -lt "$MIN_SIZE" ]; then
    if [ -f "$DEMAND_BAK" ]; then
      echo "[UYARI] demand_profiles ezilmiş ($sz byte) — yedekten geri yükleniyor" | tee -a "$LOG"
      cp "$DEMAND_BAK" "$DEMAND"; touch "$DEMAND"
    else
      echo "[UYARI] demand_profiles küçük ve yedek yok ($DEMAND_BAK)!" | tee -a "$LOG"
    fi
    return 1
  fi
  return 0
}

run_one() {
  local NODE=$1 SCEN=$2 OPTS=$3
  local FINAL="results/networks/clean/elec_s_${NODE}_${SCEN}.nc"
  local TARGET="results/networks/elec_s_${NODE}_ec_lcopt_${OPTS}.nc"
  local CFG; CFG=$(ls senaryolar/n${NODE}/*_${SCEN}.yaml 2>/dev/null | head -1)
  if [ -z "$CFG" ]; then echo "[FAIL] n${NODE} ${SCEN}: config yok (önce make_n50_n81_configs.py)" | tee -a "$LOG"; return 1; fi
  if [ -f "$FINAL" ]; then echo "[SKIP] $FINAL var" | tee -a "$LOG"; return 0; fi
  check_demand
  echo "[RUN ] n${NODE} ${SCEN} (opts=${OPTS}) $(date +%H:%M:%S)" | tee -a "$LOG"
  rm -f "$TARGET"; rm -f .snakemake/locks/* 2>/dev/null
  snakemake -j1 --rerun-triggers mtime -R add_electricity \
    --configfile "$CFG" -- "$TARGET" >> "$LOG" 2>&1
  local RC=$?
  if ! check_demand; then
    echo "[FAIL] n${NODE} ${SCEN}: build_demand_profiles talebi EZDİ — sonuç geçersiz, atlandı" | tee -a "$LOG"
    rm -f "$TARGET"; return 1
  fi
  if [ $RC -eq 0 ] && [ -f "$TARGET" ]; then
    mkdir -p results/networks/clean; mv "$TARGET" "$FINAL"
    echo "[ OK ] $FINAL $(date +%H:%M:%S)" | tee -a "$LOG"
  else
    echo "[FAIL] n${NODE} ${SCEN} rc=$RC $(date +%H:%M:%S)" | tee -a "$LOG"
  fi
}

NODES="${1:-50 81}"   # argüman verilirse tek node, yoksa ikisi
echo "=== 50/81 MATRIS BAŞLADI $(date) | node: $NODES ===" | tee -a "$LOG"
for NODE in $NODES; do
  run_one "$NODE" REF          CCL
  run_one "$NODE" NDC2030      Co2L-CCL
  run_one "$NODE" HIGHRES      Co2L-CCL
  run_one "$NODE" NETZERO2053  Co2L-CCL
  run_one "$NODE" NOFOSSIL     Co2L-CCL
done
echo "=== 50/81 MATRIS BİTTİ $(date) ===" | tee -a "$LOG"
echo "Sonuçlar: results/networks/clean/elec_s_{50,81}_*.nc | Log: $LOG"
