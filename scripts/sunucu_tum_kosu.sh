#!/bin/bash
# SPDX-FileCopyrightText: 2026 The PyPSA-Türkiye Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# =============================================================================
# PyPSA-Türkiye — TEK KOMUTLA TÜM KOŞU (sunucu için)
#
#   bash scripts/sunucu_tum_kosu.sh
#
# Yapar (sırayla, kaldığı yerden devam eder):
#   0. Sistem/donanım/yazılım/kod durumunu kaydeder (CPU, RAM, disk, OS/WSL,
#      Python/PyPSA/Snakemake/Gurobi sürümleri, git commit + değişiklik yaması,
#      ayar dosyalarının sha256'sı). Gurobi lisansını test eder.
#   1. 2023 doğrulama koşusu (7 düğüm dispatch) + saatlik fiyat doğrulaması.
#   2. 7, 22, 50, 81 düğüm × 5 senaryo = 20 koşu. Her koşu için: başlangıç/bitiş,
#      süre, en yüksek bellek, çözücü ve tüm Snakemake günlükleri, çözülmüş ağ,
#      tüm özet değerler (JSON).
#   3. Hepsini tek bir tabloda (CSV) birleştirir ve tüm çıktıyı tek ZIP yapar.
#
# Ayar gerekmez. İsteğe bağlı ortam değişkenleri:
#   NODES="7 22 50 81"   hangi düğümler      SKIP_VALIDATION=1  doğrulamayı atla
#   SOLVER=highs         Gurobi yerine HiGHS (50/81 için önerilmez)
#   PYPSA_ENV=/yol       conda ortamı (snakemake PATH'te değilse)
# Yarıda kalırsa aynı komutu tekrar çalıştırın: biten koşular atlanır.
# =============================================================================
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
HOST="$(hostname 2>/dev/null || echo host)"
OUT="$ROOT/results/sunucu_kosu"
mkdir -p "$OUT"/{networks,ozet,logs,sistem,dogrulama}
LOG="$OUT/ANA_LOG.txt"
TABLE="$OUT/kosu_tablosu.csv"
[ -f "$TABLE" ] || echo "node,senaryo,durum,baslangic,bitis,sure_sn,tepe_bellek_MB,ag_dosyasi" > "$TABLE"
say() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

say "=== PyPSA-TR SUNUCU KOŞUSU BAŞLADI | kök: $ROOT | makine: $HOST ==="

# ---------- Ortam ----------
if ! command -v snakemake >/dev/null 2>&1; then
  for e in "${PYPSA_ENV:-}" /opt/pypsa-env "$HOME/miniforge3/envs/pypsa-earth" "$HOME/mambaforge/envs/pypsa-earth" "$HOME/anaconda3/envs/pypsa-earth" "$HOME/miniconda3/envs/pypsa-earth"; do
    if [ -n "$e" ] && [ -f "$e/bin/activate" ]; then source "$e/bin/activate"; break; fi
  done
fi
if ! command -v snakemake >/dev/null 2>&1 && command -v conda >/dev/null 2>&1; then
  eval "$(conda shell.bash hook)" 2>/dev/null; conda activate pypsa-earth 2>/dev/null
fi
command -v snakemake >/dev/null 2>&1 || { say "[HATA] snakemake bulunamadı. README.md (Installation) bölümüne bakın veya PYPSA_ENV=/yol verin."; exit 1; }
say "Python: $(command -v python)"

# ---------- Gurobi lisansı ----------
SOLVER="${SOLVER:-gurobi}"
EXTRA_CFG=""
if [ "$SOLVER" = "gurobi" ]; then
  if [ -z "${GRB_LICENSE_FILE:-}" ]; then
    for f in "$ROOT/gurobi.lic" "$HOME/gurobi.lic" "$HOME/.gurobi/gurobi.lic" /opt/gurobi/gurobi.lic /work/gurobi.lic; do
      [ -f "$f" ] && { export GRB_LICENSE_FILE="$f"; break; }
    done
  fi
  if ! python - >"$OUT/sistem/gurobi_lisans_testi.txt" 2>&1 <<'PY'
import gurobipy as gp
m = gp.Model(); x = m.addVar(); m.setObjective(x); m.addConstr(x >= 1)
m.Params.OutputFlag = 0; m.optimize()
print("GUROBI_OK", gp.gurobi.version(), m.ObjVal)
PY
  then
    say "[HATA] Gurobi lisansı çalışmıyor (ayrıntı: sistem/gurobi_lisans_testi.txt)."
    say "       gurobi.lic dosyasını $ROOT/ ya da ~/.gurobi/ içine koyup tekrar çalıştırın."
    exit 1
  fi
  grep -v -i -E "secret|accessid" "$OUT/sistem/gurobi_lisans_testi.txt" | tail -3 | tee -a "$LOG"
else
  cat > "$OUT/sistem/solver_highs.yaml" <<'EOF'
solving:
  solver:
    name: highs
    options: highs-default
EOF
  EXTRA_CFG="$OUT/sistem/solver_highs.yaml"
  say "[UYARI] Çözücü: HiGHS (50/81 düğüm çok uzun sürebilir)."
fi

# ---------- 0. Sistem ve kod durumu ----------
S="$OUT/sistem"
{
  echo "tarih: $(date -Iseconds)"; echo "makine: $HOST"; uname -a
  echo; cat /etc/os-release 2>/dev/null
  echo; echo "--- /proc/version (WSL tespiti) ---"; cat /proc/version 2>/dev/null
  echo; echo "--- CPU ---"; lscpu 2>/dev/null; echo "nproc: $(nproc 2>/dev/null)"
  echo; echo "--- BELLEK ---"; free -h 2>/dev/null; grep -E "MemTotal|SwapTotal" /proc/meminfo 2>/dev/null
  echo; echo "--- DİSK ---"; df -h "$ROOT" 2>/dev/null
} > "$S/donanim.txt" 2>&1
if grep -qi microsoft /proc/version 2>/dev/null && command -v powershell.exe >/dev/null 2>&1; then
  powershell.exe -NoProfile -Command "Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer,Model,TotalPhysicalMemory | Format-List; Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors | Format-List" > "$S/windows_donanim.txt" 2>&1 || true
fi
{
  python --version 2>&1
  python - <<'PY'
import importlib
for m in ["pypsa", "linopy", "pandas", "numpy", "xarray", "atlite", "gurobipy", "highspy", "snakemake"]:
    try:
        mod = importlib.import_module(m); v = getattr(mod, "__version__", None)
        if m == "gurobipy": v = ".".join(map(str, mod.gurobi.version()))
        print(f"{m}: {v}")
    except Exception as e:
        print(f"{m}: YOK ({type(e).__name__})")
PY
  echo "snakemake: $(snakemake --version 2>/dev/null)"
} > "$S/yazilim_surumleri.txt" 2>&1
pip freeze > "$S/pip_freeze.txt" 2>/dev/null || true
conda list --export > "$S/conda_list.txt" 2>/dev/null || true
if git -C "$ROOT" rev-parse HEAD >/dev/null 2>&1; then
  git -C "$ROOT" rev-parse HEAD > "$S/git_commit.txt"
  git -C "$ROOT" status --short > "$S/git_status.txt"
  git -C "$ROOT" diff > "$S/git_degisiklik.patch"
fi
sha256sum config.yaml senaryolar/n*/*.yaml senaryolar/00_VALIDATION_2023.yaml data/agg_p_nom_minmax*.csv \
  scripts/add_electricity.py scripts/solve_network.py scripts/sunucu_tum_kosu.sh 2>/dev/null > "$S/dosya_sha256.txt"
cp config.yaml "$S/config.yaml"; mkdir -p "$S/senaryolar"; cp -r senaryolar/n* senaryolar/00_VALIDATION_2023.yaml "$S/senaryolar/" 2>/dev/null
say "Sistem bilgisi kaydedildi: $S"
grep -E "Model name|^CPU\(s\)" "$S/donanim.txt" | head -2 | tee -a "$LOG"; grep MemTotal "$S/donanim.txt" | tee -a "$LOG"

# ---------- Korumalar ----------
# (1) Yalnız add_electricity → solve_network zinciri çalışmalı. Üst akış (OSM, bölgeler,
#     rüzgâr/güneş/hidro profilleri) ASLA yeniden üretilmemeli: rüzgâr profiline elle
#     uygulanmış Global Wind Atlas düzeltmesi silinir. Toplu 'touch' KULLANILMAZ;
#     gerekirse snakemake --touch (dosyaları yeniden üretmeden tarih sırasını düzeltir).
ALLOWED="add_electricity simplify_network cluster_network add_extra_components prepare_network solve_network"
snakemake --unlock -- results/networks/elec_s_7_ec_lcopt_CCL.nc >/dev/null 2>&1 || true
ensure_upstream() {  # $1=config  $2=hedef ; 0 → güvenli
  local CFG=$1 TGT=$2 bad
  bad=$(snakemake -n -j1 --rerun-triggers mtime -R add_electricity --configfile $CFG $EXTRA_CFG -- "$TGT" 2>/dev/null \
        | awk '/^rule /{gsub(":","",$2); print $2}' | sort -u | grep -v -x -F -f <(tr ' ' '\n' <<<"$ALLOWED"))
  [ -z "$bad" ] && return 0
  say "[KORUMA] üst akış eski görünüyor ($(echo $bad | tr '\n' ' ')) → snakemake --touch ile tarih sırası düzeltiliyor"
  snakemake -j1 --touch --rerun-triggers mtime --configfile $CFG $EXTRA_CFG -- "$TGT" >> "$LOG" 2>&1
  bad=$(snakemake -n -j1 --rerun-triggers mtime -R add_electricity --configfile $CFG $EXTRA_CFG -- "$TGT" 2>/dev/null \
        | awk '/^rule /{gsub(":","",$2); print $2}' | sort -u | grep -v -x -F -f <(tr ' ' '\n' <<<"$ALLOWED"))
  [ -z "$bad" ] && return 0
  say "[HATA] üst akış hâlâ yeniden üretilmek isteniyor: $bad — koşu atlandı (veri koruması)"
  return 1
}
# (2) Profil parmak izleri: koşu boyunca değişmemeli
profile_hash() { sha256sum resources/renewable_profiles/*.nc resources/profile_*.nc 2>/dev/null | sort; }
profile_hash > "$OUT/sistem/profil_sha256_baslangic.txt"
check_profiles() {
  if ! diff -q <(profile_hash) "$OUT/sistem/profil_sha256_baslangic.txt" >/dev/null; then
    say "[DUR] yenilenebilir profilleri DEĞİŞTİ — koşu durduruldu (sistem/profil_sha256_baslangic.txt ile karşılaştırın)"
    exit 2
  fi
}
# (3) 2026 filosu yedeği (doğrulama 2023 filosunu geçici koyar)
FLEET=data/custom_powerplants.csv
[ -f "$OUT/sistem/custom_powerplants_2026.csv" ] || cp "$FLEET" "$OUT/sistem/custom_powerplants_2026.csv"
restore_fleet_if_needed() {
  if ! cmp -s "$FLEET" "$OUT/sistem/custom_powerplants_2026.csv"; then
    say "[KORUMA] santral listesi 2026 hâline geri yükleniyor"
    cp "$OUT/sistem/custom_powerplants_2026.csv" "$FLEET"
  fi
}

# ---------- Talep koruması ----------
DEMAND=resources/demand_profiles.csv
DEMAND_BAK=resources/demand_profiles.csv.bak.epias2018-kappa1.0212
check_demand() {
  local sz; sz=$(stat -c%s "$DEMAND" 2>/dev/null || echo 0)
  if [ "$sz" -lt 200000000 ]; then
    say "[UYARI] demand_profiles ezilmiş ($sz bayt) — yedekten geri yükleniyor"
    [ -f "$DEMAND_BAK" ] && { cp "$DEMAND_BAK" "$DEMAND"; touch "$DEMAND"; }
    return 1
  fi
  return 0
}

TIMEBIN=""; [ -x /usr/bin/time ] && TIMEBIN=/usr/bin/time
# Bellek örnekleyici (/usr/bin/time yoksa da çalışır): her 10 sn tüm python/snakemake
# süreçlerinin toplam RSS'ini ölçer, en yükseği MB olarak yazar; sistem genelinde
# kullanılan bellek (MemTotal-MemAvailable) de kaydedilir.
mem_sampler() {  # $1=çıktı dosyası; ana süreç bitene kadar çalışır
  local out=$1 peak=0 syspeak=0 cur sys
  while true; do
    cur=$(ps -eo rss,comm 2>/dev/null | awk '$2 ~ /python|snakemake|gurobi/ {s+=$1} END {printf "%d", s/1024}')
    sys=$(awk '/MemTotal/{t=$2}/MemAvailable/{a=$2}END{printf "%d",(t-a)/1024}' /proc/meminfo)
    [ "${cur:-0}" -gt "$peak" ] && peak=$cur
    [ "${sys:-0}" -gt "$syspeak" ] && syspeak=$sys
    echo "$peak $syspeak" > "$out"
    sleep 10
  done
}

# ---------- 1. Doğrulama ----------
VAL_READY=1
for f in cutouts/cutout-2023-era5.nc resources/validation2023/renewable_profiles/profile_onwind.nc \
         resources/validation2023/demand_profiles.csv networks/validation2023/base.nc ../Epdk/custom_powerplants_2023.csv ../epias/mcp_2023.parquet; do
  [ -f "$f" ] || { VAL_READY=0; say "[BİLGİ] doğrulama dosyası yok: $f"; }
done
if [ "$VAL_READY" = "0" ] && [ "${SKIP_VALIDATION:-0}" != "1" ]; then
  say "[BİLGİ] 2023 doğrulama eki kurulu değil → doğrulama ATLANDI (sonucu makineden bağımsızdır; yerelde yapıldı). Senaryolarla devam."
  SKIP_VALIDATION=1
fi
if [ "${SKIP_VALIDATION:-0}" != "1" ] && [ ! -f "$OUT/dogrulama/TAMAM" ]; then
  say "=== 1. 2023 DOĞRULAMA KOŞUSU ==="
  snakemake --unlock -- results/networks/elec_s_7_ec_lcopt_CCL.nc >/dev/null 2>&1 || true
  ensure_upstream senaryolar/00_VALIDATION_2023.yaml results/validation2023/networks/elec_s_7_ec_lv1.0_.nc || say "[UYARI] doğrulama üst akış kontrolü başarısız"
  T0=$(date +%s)
  VAL_LOG="$OUT/dogrulama/validation_2023.log" bash scripts/run_validation_2023_portable.sh > /dev/null 2>&1
  RC=$?
  VNET=results/validation2023/networks/elec_s_7_ec_lv1.0_.nc
  if [ $RC -eq 0 ] && [ -f "$VNET" ]; then
    cp "$VNET" "$OUT/dogrulama/validation2023_elec_s_7.nc"
    cp logs/validation2023/*.log "$OUT/dogrulama/" 2>/dev/null
    python scripts/sunucu_ozet.py "$VNET" "$OUT/dogrulama/validation2023_ozet.json" --node 7 --scenario VALIDATION2023 >> "$LOG" 2>&1
    MCP_NETWORK="$ROOT/$VNET" MCP_OUT_CSV="$OUT/dogrulama/lmp_mcp_saatlik.csv" MCP_OUT_TXT="$OUT/dogrulama/lmp_mcp_metrikler.txt" \
      python scripts/validate_mcp_full.py >> "$OUT/dogrulama/fiyat_dogrulama.log" 2>&1 || say "[UYARI] fiyat doğrulaması başarısız (log: dogrulama/fiyat_dogrulama.log)"
    echo "7,VALIDATION2023,OK,$(date -d @$T0 -Iseconds),$(date -Iseconds),$(( $(date +%s)-T0 )),,dogrulama/validation2023_elec_s_7.nc" >> "$TABLE"
    touch "$OUT/dogrulama/TAMAM"; say "[ OK ] doğrulama ($(( $(date +%s)-T0 )) sn)"
  else
    echo "7,VALIDATION2023,FAIL,$(date -d @$T0 -Iseconds),$(date -Iseconds),$(( $(date +%s)-T0 )),," >> "$TABLE"
    say "[HATA] doğrulama başarısız (rc=$RC, log: dogrulama/validation_2023.log) — senaryolarla devam ediliyor"
  fi
fi
restore_fleet_if_needed
check_profiles

# ---------- 2. Senaryo matrisi ----------
run_one() {
  local NODE=$1 SCEN=$2 OPTS=$3
  local NAME="n${NODE}_${SCEN}"
  local FINAL="$OUT/networks/elec_s_${NODE}_${SCEN}.nc"
  local TARGET="results/networks/elec_s_${NODE}_ec_lcopt_${OPTS}.nc"
  local CFG; CFG=$(ls senaryolar/n${NODE}/*_${SCEN}.yaml 2>/dev/null | head -1)
  [ -z "$CFG" ] && { say "[HATA] $NAME: config yok"; echo "$NODE,$SCEN,CONFIG_YOK,,,,," >> "$TABLE"; return 1; }
  [ -f "$FINAL" ] && { say "[ATLA] $NAME zaten bitti"; return 0; }
  check_demand
  restore_fleet_if_needed
  local LD="$OUT/logs/$NAME"; mkdir -p "$LD"
  snakemake --unlock -- results/networks/elec_s_7_ec_lcopt_CCL.nc >/dev/null 2>&1 || true
  rm -f "$TARGET"
  if ! ensure_upstream "$CFG" "$TARGET"; then
    echo "$NODE,$SCEN,UST_AKIS_KORUMASI,,,,," >> "$TABLE"; return 1
  fi
  say "[BAŞLA] $NAME (opts=$OPTS, config=$CFG)"
  local T0; T0=$(date +%s)
  mem_sampler "$LD/bellek_tepe.txt" & local MSP=$!
  if [ -n "$TIMEBIN" ]; then
    $TIMEBIN -v -o "$LD/time_v.txt" snakemake -j1 --rerun-triggers mtime -R add_electricity \
      --configfile "$CFG" $EXTRA_CFG -- "$TARGET" > "$LD/snakemake.log" 2>&1
  else
    snakemake -j1 --rerun-triggers mtime -R add_electricity \
      --configfile "$CFG" $EXTRA_CFG -- "$TARGET" > "$LD/snakemake.log" 2>&1
  fi
  local RC=$?; local T1; T1=$(date +%s)
  kill $MSP 2>/dev/null; wait $MSP 2>/dev/null
  # bu koşunun tüm günlükleri (sonraki koşu üstüne yazmadan önce)
  for f in logs/add_electricity.log logs/simplify_network.log logs/cluster_network/elec_s_${NODE}.log \
           logs/add_extra_components/elec_s_${NODE}.log logs/prepare_network/elec_s_${NODE}_ec_lcopt_${OPTS}.log \
           logs/solve_network/elec_s_${NODE}_ec_lcopt_${OPTS}_solver.log logs/solve_network/elec_s_${NODE}_ec_lcopt_${OPTS}_python.log \
           logs/solve_network/elec_s_${NODE}_ec_lcopt_${OPTS}_memory.log; do
    [ -f "$f" ] && cp "$f" "$LD/"
  done
  find logs -newermt "@$T0" -type f -name "*.log" -exec cp --parents {} "$LD/" \; 2>/dev/null
  check_profiles
  local PEAK=""; [ -f "$LD/time_v.txt" ] && PEAK=$(awk -F: '/Maximum resident set size/{printf "%.0f", $2/1024}' "$LD/time_v.txt")
  [ -z "$PEAK" ] && [ -f "$LD/bellek_tepe.txt" ] && PEAK=$(awk '{print $1}' "$LD/bellek_tepe.txt")
  if ! check_demand; then
    say "[HATA] $NAME: talep dosyası ezildi — sonuç geçersiz"; rm -f "$TARGET"
    echo "$NODE,$SCEN,TALEP_EZILDI,$(date -d @$T0 -Iseconds),$(date -d @$T1 -Iseconds),$((T1-T0)),$PEAK," >> "$TABLE"; return 1
  fi
  if [ $RC -eq 0 ] && [ -f "$TARGET" ]; then
    mv "$TARGET" "$FINAL"
    python scripts/sunucu_ozet.py "$FINAL" "$OUT/ozet/${NAME}.json" --node "$NODE" --scenario "$SCEN" \
      --solver-log "$LD/elec_s_${NODE}_ec_lcopt_${OPTS}_solver.log" >> "$LOG" 2>&1
    echo "$NODE,$SCEN,OK,$(date -d @$T0 -Iseconds),$(date -d @$T1 -Iseconds),$((T1-T0)),$PEAK,networks/elec_s_${NODE}_${SCEN}.nc" >> "$TABLE"
    say "[ OK ] $NAME ($((T1-T0)) sn, tepe bellek ${PEAK:-?} MB)"
  else
    echo "$NODE,$SCEN,FAIL_rc$RC,$(date -d @$T0 -Iseconds),$(date -d @$T1 -Iseconds),$((T1-T0)),$PEAK," >> "$TABLE"
    say "[HATA] $NAME rc=$RC — log: logs/$NAME/snakemake.log (diğer koşulara devam)"
  fi
}

for NODE in ${NODES:-7 22 50 81}; do
  say "=== 2. DÜĞÜM $NODE ==="
  run_one "$NODE" REF          CCL
  run_one "$NODE" NDC2030      Co2L-CCL
  run_one "$NODE" HIGHRES      Co2L-CCL
  run_one "$NODE" NETZERO2053  Co2L-CCL
  run_one "$NODE" NOFOSSIL     Co2L-CCL
done

# ---------- 3. Birleştir + ZIP ----------
python - "$OUT" <<'PY' >> "$LOG" 2>&1
import json, glob, os, sys
import pandas as pd
out = sys.argv[1]
rows = []
for f in sorted(glob.glob(os.path.join(out, "ozet", "*.json"))) + glob.glob(os.path.join(out, "dogrulama", "*ozet.json")):
    d = json.load(open(f))
    flat = {}
    for k, v in d.items():
        if isinstance(v, dict):
            for k2, v2 in v.items():
                flat[f"{k}.{k2}"] = json.dumps(v2, ensure_ascii=False) if isinstance(v2, (dict, list)) else v2
        elif isinstance(v, list):
            flat[k] = json.dumps(v)
        else:
            flat[k] = v
    rows.append(flat)
if rows:
    pd.DataFrame(rows).to_csv(os.path.join(out, "OZET_TUM_KOSULAR.csv"), index=False)
    print(f"[BİRLEŞTİR] {len(rows)} koşu -> OZET_TUM_KOSULAR.csv")
PY
say "=== KOŞU TABLOSU ==="; column -t -s, "$TABLE" 2>/dev/null | tee -a "$LOG" || cat "$TABLE"
ZIP="$ROOT/../SONUC_${HOST}_$(date +%Y%m%d_%H%M).zip"
say "ZIP hazırlanıyor: $ZIP"
( cd "$ROOT/results" && zip -r -q "$ZIP" sunucu_kosu ) && say "[BİTTİ] Tüm çıktılar: $ZIP ($(du -h "$ZIP" | cut -f1))" || say "[UYARI] zip başarısız; klasör: $OUT"
say "=== PyPSA-TR SUNUCU KOŞUSU BİTTİ ==="
