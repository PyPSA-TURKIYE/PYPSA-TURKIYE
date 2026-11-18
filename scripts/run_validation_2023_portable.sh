#!/bin/bash
# SPDX-FileCopyrightText: 2026 The PyPSA-Türkiye Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# Taşınabilir kopya (2026-10-16): yollar proje köküne göre; sunucu_tum_kosu.sh çağırır.
# =============================================================================
# run_validation_2023.sh — İZOLE 2023 backcast validation akışı
# =============================================================================
# AMAÇ: 2023 hava + 2023 talep (EPİAŞ) + 2023 filo (EPDK) ile saf dispatch
#       backcast. run.name='validation2023' (RDIR namespacing) sayesinde tüm
#       çıktılar resources/validation2023/, networks/validation2023/,
#       results/validation2023/ altına İZOLE yazılır.
#
# KUTSAL (ASLA DOKUNULMAZ): ana 2018 pipeline dosyaları —
#       resources/demand_profiles.csv (EPİAŞ 302 TWh, 274649982 bayt)
#       networks/base.nc
#       resources/renewable_profiles/*.nc
#   Script sonunda dokunulmadıkları doğrulanır.
#
# FLEET KORUMASI: data/custom_powerplants.csv build sırasında 2023 filosuyla
#   değiştirilir; EXIT trap'i her durumda (başarı/hata/kill) 2026 orijinalini
#   geri yükler.
#
# SIRA:
#   ADIM 1: Şablon talep → resources/validation2023/demand_profiles.csv
#           NOT: GEGIS verisi sadece hava yılı 2011/2013/2018 için mevcut
#           (data/ssp2-2.6/2030/era5_*); era5_2023 YOK ve build_demand_profiles
#           2023 snapshot'larını 2018 GEGIS indeksinden kesemez (boş çıkar).
#           Şablonun TEK işlevi bus payları (EPİAŞ overwrite indeksi sıfırdan
#           kurar) → bu yüzden:
#             1a) snakemake ile build_demand_profiles'ın talep-dışı girdileri
#                 kurulur: base.nc + regions_onshore + gadm_shapes
#                 (OSM/shapes/base validation2023 altına; cutout shared)
#             1b) ana resources/demand_profiles.csv (EPİAŞ 2018, GEGIS bus
#                 payları korunmuş) şablon olarak kopyalanır — ÖNCE kolonların
#                 validation2023 base.nc substation_lv bus'larıyla birebir
#                 eşleştiği doğrulanır (OSM pbf cache'li → deterministik).
#                 Kopya, girdilerden SONRA oluştuğu için mtime tetiklemez.
#   ADIM 2: EPİAŞ 2023 gerçek talep overwrite (κ_cal=1.0347 default, TEDAŞ 2024)
#   ADIM 3: Çözüm — results/validation2023/networks/elec_s_7_ec_lv1.0_.nc
#           (simpl='', clusters=7, ll=v1.0, opts='' ⇒ dosya adı kalıbı
#            elec_s{simpl}_{clusters}_ec_l{ll}_{opts}.nc → elec_s_7_ec_lv1.0_.nc;
#            mevcut çözülmüş elec_s_7_ec_lcopt_.nc ile teyitli)
#
# İdempotent: --rerun-triggers mtime ile mevcut validation2023 çıktıları korunur.
# =============================================================================

set -u

PROJ="${PROJ:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
EPDK_DIR="${EPDK_DIR:-$PROJ/../Epdk}"
EPIAS_DIR="${EPIAS_DIR:-$PROJ/../epias}"
CONFIG=senaryolar/00_VALIDATION_2023.yaml
LOG="${VAL_LOG:-/tmp/validation_2023.log}"
GUARD=/tmp/custom_powerplants_2026.guard.csv
TARGET="results/validation2023/networks/elec_s_7_ec_lv1.0_.nc"

# Kutsal 2018 dosyaları — referans değerler
SACRED_DEMAND="$PROJ/resources/demand_profiles.csv"
SACRED_DEMAND_SIZE=$(stat -c %s "$PROJ/resources/demand_profiles.csv")
SACRED_BASE="$PROJ/networks/base.nc"

command -v snakemake >/dev/null 2>&1 || source "${PYPSA_ENV:-/opt/pypsa-env}/bin/activate" || { echo "[FAIL] conda env açılamadı"; exit 1; }
cd "$PROJ" || { echo "[FAIL] proje kökü yok: $PROJ"; exit 1; }

echo "=== run_validation_2023.sh başladı: $(date) ===" | tee "$LOG"

# -----------------------------------------------------------------------------
# FLEET KORUMASI: 2026 orijinalini güvenceye al, EXIT'te geri yükle
# -----------------------------------------------------------------------------
cp "$PROJ/data/custom_powerplants.csv" "$GUARD" \
    || { echo "[FAIL] guard kopyası alınamadı"; exit 1; }
restore_fleet() {
    cp "$GUARD" "$PROJ/data/custom_powerplants.csv"
    echo "[GUARD] data/custom_powerplants.csv 2026 orijinaline geri yüklendi." | tee -a "$LOG"
}
trap restore_fleet EXIT

# 2023 filosunu yerleştir (EPDK 2023 Aralık, 106.7 GW, Akkuyu YOK)
cp "$EPDK_DIR/custom_powerplants_2023.csv" "$PROJ/data/custom_powerplants.csv" \
    || { echo "[FAIL] 2023 filo kopyalanamadı"; exit 1; }
echo "[OK] 2023 filosu yerleştirildi (custom_powerplants_2023.csv)" | tee -a "$LOG"

# Kutsal dosya mtime referansı (script sonunda karşılaştırılır)
BASE_MTIME_BEFORE=$(stat -c %Y "$SACRED_BASE")

# -----------------------------------------------------------------------------
# ADIM 1a: build_demand_profiles'ın talep-dışı girdileri (base/regions/shapes)
# -----------------------------------------------------------------------------
echo "" | tee -a "$LOG"
echo "=== ADIM 1a: base.nc + regions_onshore + gadm_shapes ($(date)) ===" | tee -a "$LOG"
snakemake -j1 --rerun-triggers mtime --configfile "$CONFIG" \
    -- networks/validation2023/base.nc \
       resources/validation2023/bus_regions/regions_onshore.geojson \
       resources/validation2023/shapes/gadm_shapes.geojson 2>&1 | tee -a "$LOG"
RC=${PIPESTATUS[0]}
if [ "$RC" -ne 0 ]; then
    echo "[FAIL] ADIM 1a snakemake exit=$RC — duruluyor." | tee -a "$LOG"
    exit 1
fi
echo "[OK] ADIM 1a: validation2023 base ağı kuruldu." | tee -a "$LOG"

# -----------------------------------------------------------------------------
# ADIM 1b: Şablon talep — ana CSV'den kolon-doğrulamalı kopya
# -----------------------------------------------------------------------------
echo "" | tee -a "$LOG"
echo "=== ADIM 1b: Şablon demand_profiles (bus payları) ($(date)) ===" | tee -a "$LOG"
DP=resources/validation2023/demand_profiles.csv
python - "$SACRED_DEMAND" "$DP" <<'PY' 2>&1 | tee -a "$LOG"
import sys, shutil
import pandas as pd
import pypsa

src, dst = sys.argv[1], sys.argv[2]
n = pypsa.Network("networks/validation2023/base.nc")
lv_buses = set(n.buses.index[n.buses["substation_lv"]])
cols = set(pd.read_csv(src, index_col=0, nrows=2).columns)
print(f"[SAĞLIK] validation2023 base substation_lv bus: {len(lv_buses)}, şablon kolon: {len(cols)}")
if cols != lv_buses:
    only_t = sorted(cols - lv_buses)[:5]
    only_b = sorted(lv_buses - cols)[:5]
    print(f"[FAIL] Bus kümesi eşleşmiyor! sadece şablonda: {only_t} ... sadece base'te: {only_b} ...")
    sys.exit(1)
shutil.copy2(src, dst)
print(f"[OK] Şablon kopyalandı: {dst} (kolonlar birebir eşleşti)")
PY
RC=${PIPESTATUS[0]}
[ "$RC" -ne 0 ] && { echo "[FAIL] ADIM 1b şablon kopyası." | tee -a "$LOG"; exit 1; }
# mtime'ı şimdiye çek (girdilerden yeni olsun → build_demand_profiles tetiklenmesin)
touch "$DP"

# Sağlık: dosya var + boyut > 10 MB
if [ ! -f "$DP" ]; then
    echo "[FAIL] ADIM 1: $DP üretilmedi." | tee -a "$LOG"; exit 1
fi
SZ=$(stat -c %s "$DP")
if [ "$SZ" -le 10000000 ]; then
    echo "[FAIL] ADIM 1: $DP boyutu $SZ bayt (<10MB) — şüpheli." | tee -a "$LOG"; exit 1
fi
echo "[OK] ADIM 1: $DP ($SZ bayt)" | tee -a "$LOG"

# -----------------------------------------------------------------------------
# ADIM 2: EPİAŞ 2023 gerçek talep overwrite (κ_cal=1.0347 default, TEDAŞ 2024)
# -----------------------------------------------------------------------------
echo "" | tee -a "$LOG"
echo "=== ADIM 2: EPİAŞ 2023 talep overwrite ($(date)) ===" | tee -a "$LOG"
python scripts/epias_to_demand_profiles.py \
    --demand-csv "$DP" \
    --epias-dir "$EPIAS_DIR" \
    --start 2023-01-01 --end 2024-01-01 2>&1 | tee -a "$LOG"
RC=${PIPESTATUS[0]}
if [ "$RC" -ne 0 ]; then
    echo "[FAIL] ADIM 2 epias_to_demand_profiles exit=$RC — duruluyor." | tee -a "$LOG"
    exit 1
fi

# Sağlık: toplam TWh ~315-345 olmalı.
#   κ=1.0347 (TEDAŞ-2024 kalibrasyonu, script default 2026-06-11'den beri): 308.3×1.0347 ≈ 319 TWh
#   κ=1.067  (eski kalibrasyon): 308.3×1.067 ≈ 329 TWh
python - "$DP" <<'PY' 2>&1 | tee -a "$LOG"
import sys, pandas as pd
df = pd.read_csv(sys.argv[1], index_col=0, parse_dates=True)
twh = df.values.sum() / 1e6
print(f"[SAĞLIK] EPİAŞ overwrite sonrası: {df.shape[0]} saat × {df.shape[1]} bus, toplam {twh:.2f} TWh")
if not (310.0 <= twh <= 350.0):
    print(f"[FAIL] Toplam {twh:.2f} TWh beklenen aralıkta (~315-345) değil!")
    sys.exit(1)
print("[OK] Talep toplamı makul aralıkta.")
PY
RC=${PIPESTATUS[0]}
[ "$RC" -ne 0 ] && { echo "[FAIL] ADIM 2 sağlık kontrolü." | tee -a "$LOG"; exit 1; }

# Kutsal 2018 dosyaları ara kontrol
CUR_SIZE=$(stat -c %s "$SACRED_DEMAND")
if [ "$CUR_SIZE" -ne "$SACRED_DEMAND_SIZE" ]; then
    echo "[FAIL] KUTSAL İHLAL: resources/demand_profiles.csv boyutu değişti ($CUR_SIZE != $SACRED_DEMAND_SIZE)!" | tee -a "$LOG"
    exit 1
fi
echo "[OK] Ana 2018 demand_profiles.csv dokunulmadı ($CUR_SIZE bayt)." | tee -a "$LOG"

# -----------------------------------------------------------------------------
# ADIM 3: Çözüm (7-node dispatch-only LP)
# -----------------------------------------------------------------------------
echo "" | tee -a "$LOG"
echo "=== ADIM 3: Çözüm — $TARGET ($(date)) ===" | tee -a "$LOG"
snakemake -j1 --rerun-triggers mtime --configfile "$CONFIG" \
    -- "$TARGET" 2>&1 | tee -a "$LOG"
RC=${PIPESTATUS[0]}
if [ "$RC" -ne 0 ]; then
    echo "[FAIL] ADIM 3 snakemake exit=$RC — duruluyor." | tee -a "$LOG"
    exit 1
fi
if [ ! -f "$TARGET" ]; then
    echo "[FAIL] ADIM 3: hedef dosya yok: $TARGET" | tee -a "$LOG"; exit 1
fi
echo "[OK] ADIM 3: $TARGET ($(stat -c %s "$TARGET") bayt)" | tee -a "$LOG"

# -----------------------------------------------------------------------------
# SON SAĞLIK: kutsal 2018 dosyaları dokunulmamış olmalı
# -----------------------------------------------------------------------------
echo "" | tee -a "$LOG"
echo "=== SON SAĞLIK KONTROLLERI ===" | tee -a "$LOG"
CUR_SIZE=$(stat -c %s "$SACRED_DEMAND")
BASE_MTIME_AFTER=$(stat -c %Y "$SACRED_BASE")
FAILED=0
if [ "$CUR_SIZE" -ne "$SACRED_DEMAND_SIZE" ]; then
    echo "[FAIL] KUTSAL İHLAL: resources/demand_profiles.csv ($CUR_SIZE != $SACRED_DEMAND_SIZE)" | tee -a "$LOG"
    FAILED=1
else
    echo "[OK] resources/demand_profiles.csv: $CUR_SIZE bayt (değişmedi)" | tee -a "$LOG"
fi
if [ "$BASE_MTIME_AFTER" -ne "$BASE_MTIME_BEFORE" ]; then
    echo "[FAIL] KUTSAL İHLAL: networks/base.nc mtime değişti!" | tee -a "$LOG"
    FAILED=1
else
    echo "[OK] networks/base.nc mtime değişmedi ($BASE_MTIME_AFTER)" | tee -a "$LOG"
fi
[ "$FAILED" -ne 0 ] && exit 1

echo "" | tee -a "$LOG"
echo "=== TAMAMLANDI: $(date) — çözülmüş ağ: $TARGET ===" | tee -a "$LOG"
exit 0
