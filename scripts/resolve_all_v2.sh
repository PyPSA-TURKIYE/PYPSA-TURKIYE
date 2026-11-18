#!/bin/bash
# SPDX-FileCopyrightText: 2026 The PyPSA-Türkiye Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# Cache-hijyenli tam yeniden çözüm: 5 senaryo × {7,22} node
# Her senaryo kendi config'iyle add_electricity'den itibaren TAM zincir yeniden kurulur
# (bayat cache bug fix: talep scale + extendable setler artık senaryoya doğru uygulanır)
set -u
command -v snakemake >/dev/null 2>&1 || source "${PYPSA_ENV:-/opt/pypsa-env}/bin/activate"
cd "$(dirname "$(readlink -f "$0")")/.."  # repository root
export GRB_LICENSE_FILE="${GRB_LICENSE_FILE:-$HOME/.gurobi/gurobi.lic}"
LOG=/tmp/resolve_all_v2.log
: > $LOG

# Eski (v1, hatalı cache'li) sonuçları arşivle — SİLME
mkdir -p results/networks/old_v1
for f in results/networks/elec_s_7_{REF,NDC2030,HIGHRES,NETZERO2053,NOFOSSIL}.nc results/networks/elec_s_22_{REF,NDC2030,HIGHRES,NETZERO2053,NOFOSSIL}.nc; do
  [ -f "$f" ] && mv "$f" results/networks/old_v1/
done
echo "v1 arşivlendi" | tee -a $LOG

run_one () {
  local SCEN=$1; local NAME=$2; local OPTS=$3; local C=$4
  local TGT=results/networks/elec_s_${C}_ec_lcopt_${OPTS}.nc
  echo "=== $NAME ${C}node START $(date +%H:%M:%S) ===" | tee -a $LOG
  rm -f .snakemake/locks/* 2>/dev/null
  rm -f "$TGT" 2>/dev/null
  snakemake -j1 --rerun-triggers mtime -R add_electricity --configfile senaryolar/${SCEN}.yaml -- "$TGT" >> $LOG 2>&1
  local EXIT=$?
  if [ $EXIT -eq 0 ] && [ -f "$TGT" ]; then
    mv "$TGT" results/networks/elec_s_${C}_${NAME}.nc
    echo "=== $NAME ${C}node OK $(date +%H:%M:%S) ===" | tee -a $LOG
  else
    echo "=== $NAME ${C}node FAILED exit=$EXIT $(date +%H:%M:%S) ===" | tee -a $LOG
  fi
}

# REF artık opts=CCL (dosya adı _CCL.nc); diğerleri Co2L-CCL
for C in 7 22; do run_one 01_REF REF CCL $C; done
for C in 7 22; do run_one 02_NDC2030 NDC2030 Co2L-CCL $C; done
for C in 7 22; do run_one 03_HIGHRES HIGHRES Co2L-CCL $C; done
for C in 7 22; do run_one 04_NETZERO2053 NETZERO2053 Co2L-CCL $C; done
for C in 7 22; do run_one 05_NOFOSSIL NOFOSSIL Co2L-CCL $C; done

echo "ALL_DONE $(date +%H:%M:%S)" | tee -a $LOG
