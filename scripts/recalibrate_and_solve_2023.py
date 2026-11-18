#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 The PyPSA-Türkiye Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
2023 Validation Ağı Kalibrasyonu ve Yeniden Çözümü
K1-K5 kalibrasyonları belgelenebilir kaynaklarla uygulanır.
Orijinal VALIDATION_2023.nc EZILMEZ — calib ayrı dosyaya kaydedilir.
"""

import pypsa
import pandas as pd
import numpy as np
import warnings
import os
warnings.filterwarnings('ignore')

print("=" * 60)
print("ADIM 1: Lisanssız Güneş — EPDK 2023 Aralık Raporu")
print("=" * 60)
# EPDK 2023 Aralık Raporu — Tablo 0 (Özet):
#   2023 Ocak-Aralık Brüt Lisanssız Üretim = 14.796.643 MWh
# EPDK 2023 Aralık Raporu — Tablo 12 (Lisanssız KG kaynak bazlı):
#   Güneş kurulu güç = 9.651 MW / Toplam = 10.359 MW → %93.17
# EPDK 2023 Aralık Raporu — Tablo 14 (Aralık ayı kaynak payı):
#   Aralık Brüt Güneş = 716.739,65 MWh / Toplam = 747.417,10 MWh → %95.9
# Muhafazakar tahmin: kapasite payı (%93.17) kullanılır
annual_unlicensed_total_mwh = 14_796_643.0
solar_unlicensed_fraction = 9651.0 / 10359.0  # %93.17 kapasite payı
unlicensed_solar_twh = annual_unlicensed_total_mwh * solar_unlicensed_fraction / 1e6
epias_licensed_solar_twh = 3.860  # EPİAŞ 'sun' lisanslı üretim
fair_solar_ref_twh = epias_licensed_solar_twh + unlicensed_solar_twh

print(f"  Yıllık Brüt Lisanssız TOPLAM: {annual_unlicensed_total_mwh/1e6:.3f} TWh")
print(f"  Lisanssız Güneş payı (kapasite): %{solar_unlicensed_fraction*100:.2f}")
print(f"  Lisanssız Güneş 2023: {unlicensed_solar_twh:.3f} TWh")
print(f"  EPİAŞ Lisanslı Güneş: {epias_licensed_solar_twh} TWh")
print(f"  ADİL GÜNEŞ REFERANSİ: {fair_solar_ref_twh:.3f} TWh")
print()

print("=" * 60)
print("ADIM 2: Ağ Yükleme ve Kalibrasyonlar")
print("=" * 60)

network_path = "results/networks/elec_s_7_VALIDATION_2023.nc"
n = pypsa.Network(network_path)
gens = n.generators

print(f"Ağ yüklendi: {len(n.snapshots)} snapshot, {len(n.generators)} generator")
print()

# --- K1: Linyit emre amadelik ---
# Türk yerli linyit filosu yaşlı (ortalama >30 yıl) + düşük kaliteli linyit + yakıt kısıtı
# Tarihsel CF: TEİAŞ/Ember 2023 → 40.564 GWh / (10.599 GW * 8760 h) = %43.7
# p_max_pu=1 varsayım gerçekçi değil; üst sınır 0.55 (CF %43-55 tarihsel aralığı)
k1_lignite_pmax = 0.55
gens.loc[gens.carrier == "lignite", "p_max_pu"] = k1_lignite_pmax
n_lignite = (gens.carrier == "lignite").sum()
print(f"K1 — Linyit p_max_pu={k1_lignite_pmax} ({n_lignite} ünite)")
print(f"     Gerekçe: Türk linyit filo yaşı >30 yıl, tarihsel CF %43-55 (Ember/TEİAŞ)")

# --- K2: İthal kömür emre amadelik ---
# Yeni ithal kömür santralleri (Zonguldak, İskenderun) daha güvenilir
# Standart bakım+arıza faktörü ~%10-15 → p_max_pu=0.85
k2_coal_pmax = 0.85
gens.loc[gens.carrier == "coal", "p_max_pu"] = k2_coal_pmax
n_coal = (gens.carrier == "coal").sum()
print(f"K2 — İthal Kömür p_max_pu={k2_coal_pmax} ({n_coal} ünite)")
print(f"     Gerekçe: Bakım/arıza faktörü ~%15 (IEA güç santrali güvenilirlik veritabanı)")

# --- K3: Jeotermal ---
# Gerçek 2023: 10.113 GWh / (1.691 GW * 8760 h) = %68.3 CF
# Re-injeksiyon kısıtı + bakım → p_max_pu=0.70
k3_geo_pmax = 0.70
gens.loc[gens.carrier == "geothermal", "p_max_pu"] = k3_geo_pmax
n_geo = (gens.carrier == "geothermal").sum()
print(f"K3 — Jeotermal p_max_pu={k3_geo_pmax} ({n_geo} ünite)")
print(f"     Gerekçe: 2023 CF=%68.3 (EPİAŞ 10.113 GWh / 1.691 GW), re-injeksiyon+bakım")

# --- K4: Biyokütle ---
# Gerçek 2023: 8.788 GWh / (2.077 GW * 8760 h) = %48.3 CF
# Çoğu endüstriyel/CHP → düzensiz yakıt tedariki → p_max_pu=0.50
k4_bio_pmax = 0.50
gens.loc[gens.carrier == "biomass", "p_max_pu"] = k4_bio_pmax
n_bio = (gens.carrier == "biomass").sum()
print(f"K4 — Biyokütle p_max_pu={k4_bio_pmax} ({n_bio} ünite)")
print(f"     Gerekçe: 2023 CF=%48.3 (EPİAŞ 8.788 GWh / 2.077 GW), yakıt tedarik kısıtı")

# --- K5: Hidro inflow normalizasyon düzeltmesi ---
# config default: EIA 2018 normalize yılı → 2023 gerçek hidro yılından farklı
# EPİAŞ 2023 barajlı üretim: 44.164 TWh
# EPİAŞ 2023 akarsu üretim: 19.491 TWh (ror'a dokunma)
# Mevcut inflow enerji: 28.675 TWh → mevcut barajlı dispatch: ~25.807 TWh
# inflow/dispatch oranı: 28.675/25.807 = 1.111 (kayıplar+depolama geçişi)
# Hedef dispatch 44.164 TWh → gerekli inflow = 44.164 * 1.111 = 49.07 TWh
# Scale = 49.07 / 28.675 = 1.711
w_stores = n.snapshot_weightings.stores
inflow_df = n.storage_units_t.inflow
cur_inflow_twh = (inflow_df.multiply(w_stores, axis=0).sum().sum()) / 1e6
cur_dispatch_twh = 25.807  # kalibrasyonsuz çözümden
inflow_dispatch_ratio = cur_inflow_twh / cur_dispatch_twh
target_dam_twh = 44.164  # EPİAŞ 2023 barajlı üretim (GWh'dan çevrildi)
req_inflow_twh = target_dam_twh * inflow_dispatch_ratio
scale = req_inflow_twh / cur_inflow_twh

print(f"\nK5 — Hidro Inflow Normalizasyonu")
print(f"     Mevcut inflow: {cur_inflow_twh:.3f} TWh")
print(f"     Mevcut barajlı dispatch: {cur_dispatch_twh:.3f} TWh")
print(f"     inflow/dispatch oranı: {inflow_dispatch_ratio:.4f}")
print(f"     Hedef barajlı (EPİAŞ 2023): {target_dam_twh:.3f} TWh")
print(f"     Gerekli inflow: {req_inflow_twh:.3f} TWh")
print(f"     SCALE FAKTÖRÜ: {scale:.4f}")
print(f"     Makul aralıkta (0.8-2.0): {0.8 <= scale <= 2.0}")
print(f"     Gerekçe: EIA 2018 normalize yılı → 2023 hidro yılına düzeltme (EPİAŞ 2023)")

# Inflow'u ölçekle
n.storage_units_t.inflow = inflow_df * scale
new_inflow_twh = (n.storage_units_t.inflow.multiply(w_stores, axis=0).sum().sum()) / 1e6
print(f"     Yeni inflow: {new_inflow_twh:.3f} TWh")

# ROR'a dokunma — zaten %+5.3 iyi performans gösteriyor
print(f"\nROR — Kalibrasyonsuz bırakıldı (mevcut hata: +%5.3, kabul edilebilir)")
print(f"RÜZGAR — Kalibrasyonsuz bırakıldı (4x fudge yasak; kök neden: underestimation)")
print()

print("=" * 60)
print("ADIM 3: Yeniden Çözüm (Gurobi)")
print("=" * 60)

import os
os.environ.setdefault('GRB_LICENSE_FILE', os.path.expanduser('~/.gurobi/gurobi.lic'))

print("n.optimize() çağrılıyor...")
try:
    result = n.optimize(solver_name="gurobi")
    # PyPSA 0.30.x: result tuple (status, termination_condition)
    if isinstance(result, tuple):
        status, term_cond = result[0], result[1]
    else:
        status = str(result)
        term_cond = status
    print(f"Optimizasyon durumu: {status}")
    print(f"Termination condition: {term_cond}")
    if hasattr(n, 'objective'):
        print(f"Objective değeri: {n.objective:.2f}")
    optimal = ("ok" in str(status).lower() or "optimal" in str(term_cond).lower())
    print(f"OPTIMAL mi: {optimal}")
except Exception as e:
    print(f"HATA: {e}")
    raise

print()
print("=" * 60)
print("ADIM 4: Sonuç Hesaplama")
print("=" * 60)

# Kalibrasyonlu dispatch hesapla
wg = n.snapshot_weightings.generators
ws = n.snapshot_weightings.stores
gen_p = n.generators_t.p
su_p = n.storage_units_t.p

results_calib = {}
for carrier in n.generators.carrier.unique():
    mask = n.generators.carrier == carrier
    gens_idx = n.generators[mask].index
    total = gen_p[gens_idx].multiply(wg, axis=0).sum().sum() / 1e6
    results_calib[carrier] = total

# Hydro storage
hydro_idx = n.storage_units[n.storage_units.carrier=='hydro'].index
results_calib['hydro_dam'] = su_p[hydro_idx].multiply(ws, axis=0).sum().sum() / 1e6

print("\nKalibrasyonlu Dispatch (TWh):")
for k, v in sorted(results_calib.items()):
    print(f"  {k}: {v:.3f} TWh")

# Kaydet
calib_path = "results/networks/elec_s_7_VALIDATION_2023_calib.nc"
n.export_to_netcdf(calib_path)
print(f"\nKalibrasyonlu ağ kaydedildi: {calib_path}")

print()
print("=" * 60)
print("ADIM 5: Karşılaştırma Tablosu Üretimi")
print("=" * 60)

# Kalibrasyonsuz sonuçlar (orijinal csv'den)
uncalib_csv = pd.read_csv("results/validation_2023_comparison.csv")
print("Kalibrasyonsuz CSV:")
print(uncalib_csv)

# EPİAŞ referans değerleri
epias = {
    'CCGT':         66376.9,
    'biomass':       8788.2,
    'coal':         77608.3,
    'geothermal':   10112.9,
    'hydro':        44164.1,   # Barajlı (EPİAŞ)
    'ror':          19491.8,   # Akarsu
    'lignite':      40564.6,
    'load shedding':    0.0,
    'oil':            702.3,
    'onwind':       33719.4,
    'solar':        fair_solar_ref_twh * 1000,  # ADİL referans (GWh'a çevir)
}

# Kalibrasyonlu model değerleri
calib_model = {
    'CCGT':          results_calib.get('CCGT', 0),
    'biomass':       results_calib.get('biomass', 0),
    'coal':          results_calib.get('coal', 0),
    'geothermal':    results_calib.get('geothermal', 0),
    'hydro':         results_calib.get('hydro_dam', 0),
    'ror':           results_calib.get('ror', 0),
    'lignite':       results_calib.get('lignite', 0),
    'load shedding': results_calib.get('load shedding', 0),
    'oil':           results_calib.get('oil', 0),
    'onwind':        results_calib.get('onwind', 0),
    'solar':         results_calib.get('solar', 0),
}

# Kalibrasyonsuz model değerleri (csv'den, bazı farklılıklar olabilir - orijinal sonuçlardan)
uncalib_model = {
    'CCGT':         30.990,
    'biomass':      18.141,
    'coal':         94.682,
    'geothermal':   14.776,
    'hydro':        25.737,
    'ror':          20.525,
    'lignite':      92.564,
    'load shedding': 5.305,
    'oil':           0.000,
    'onwind':        9.038,
    'solar':        15.274,
}

# Tablo oluştur
rows = []
carriers = ['CCGT', 'biomass', 'coal', 'geothermal', 'lignite', 'load shedding', 'oil', 'onwind', 'ror', 'solar', 'hydro']

for c in carriers:
    ref_gwh = epias.get(c, np.nan)
    ref_twh = ref_gwh / 1000 if not np.isnan(ref_gwh) else np.nan
    unc_twh = uncalib_model.get(c, np.nan)
    cal_twh = calib_model.get(c, np.nan)

    if not np.isnan(ref_twh) and ref_twh > 0:
        unc_err = (unc_twh - ref_twh) / ref_twh * 100
        cal_err = (cal_twh - ref_twh) / ref_twh * 100
    else:
        unc_err = np.nan
        cal_err = np.nan

    rows.append({
        'carrier': c,
        'EPİAŞ_2023_TWh': round(ref_twh, 3) if not np.isnan(ref_twh) else '',
        'Model_kalibrasyonsuz_TWh': round(unc_twh, 3),
        'Hata_kalibrasyonsuz_%': round(unc_err, 1) if not np.isnan(unc_err) else '',
        'Model_kalibrasyonlu_TWh': round(cal_twh, 3),
        'Hata_kalibrasyonlu_%': round(cal_err, 1) if not np.isnan(cal_err) else '',
    })

# Toplam satırı
# Toplam = tüm gerçek üretim (load shedding hariç) + ror + hydro
def safe_sum(d, keys):
    return sum(d.get(k, 0) for k in keys)

prod_keys = ['CCGT', 'biomass', 'coal', 'geothermal', 'lignite', 'oil', 'onwind', 'ror', 'solar', 'hydro']
epias_total = sum(epias[k]/1000 for k in prod_keys if k in epias)
unc_total = safe_sum(uncalib_model, prod_keys)
cal_total = safe_sum(calib_model, prod_keys)

rows.append({
    'carrier': 'TOPLAM (üretim)',
    'EPİAŞ_2023_TWh': round(epias_total, 3),
    'Model_kalibrasyonsuz_TWh': round(unc_total, 3),
    'Hata_kalibrasyonsuz_%': round((unc_total-epias_total)/epias_total*100, 1),
    'Model_kalibrasyonlu_TWh': round(cal_total, 3),
    'Hata_kalibrasyonlu_%': round((cal_total-epias_total)/epias_total*100, 1),
})

df = pd.DataFrame(rows)
print("\n=== KARŞILAŞTIRMA TABLOSU ===")
pd.set_option('display.max_columns', None)
pd.set_option('display.width', 120)
pd.set_option('display.float_format', '{:.3f}'.format)
print(df.to_string(index=False))

# CSV kaydet
out_path = "results/validation_2023_calibrated.csv"
df.to_csv(out_path, index=False)
print(f"\nTablo kaydedildi: {out_path}")

print()
print("=" * 60)
print("ADIM 6: Korea ±%25 Karnesi")
print("=" * 60)

production_carriers = ['CCGT', 'biomass', 'coal', 'geothermal', 'lignite', 'oil', 'onwind', 'ror', 'solar', 'hydro']

unc_pass = []
cal_pass = []
unc_fail = []
cal_fail = []

for c in production_carriers:
    ref_gwh = epias.get(c, 0)
    if ref_gwh <= 0:
        continue
    ref_twh = ref_gwh / 1000
    unc_twh = uncalib_model.get(c, 0)
    cal_twh = calib_model.get(c, 0)
    unc_err = abs((unc_twh - ref_twh) / ref_twh * 100)
    cal_err = abs((cal_twh - ref_twh) / ref_twh * 100)

    if unc_err <= 25:
        unc_pass.append(c)
    else:
        unc_fail.append(f"{c} ({unc_err:.0f}%)")
    if cal_err <= 25:
        cal_pass.append(c)
    else:
        cal_fail.append(f"{c} ({cal_err:.0f}%)")

print(f"Kalibrasyonsuz: {len(unc_pass)}/{len(production_carriers)} carrier ±%25 içinde")
print(f"  Geçen: {unc_pass}")
print(f"  Kalan: {unc_fail}")
print()
print(f"Kalibrasyonlu:  {len(cal_pass)}/{len(production_carriers)} carrier ±%25 içinde")
print(f"  Geçen: {cal_pass}")
print(f"  Kalan: {cal_fail}")

print()
print("=== ÖZET ===")
print(f"Lisanssız Güneş 2023: {unlicensed_solar_twh:.3f} TWh")
print(f"Adil Güneş Referansı: {fair_solar_ref_twh:.3f} TWh")
print(f"K1 Linyit p_max_pu: {k1_lignite_pmax}")
print(f"K2 Kömür p_max_pu: {k2_coal_pmax}")
print(f"K3 Jeotermal p_max_pu: {k3_geo_pmax}")
print(f"K4 Biyokütle p_max_pu: {k4_bio_pmax}")
print(f"K5 Hidro scale: {scale:.4f}")
print(f"LP Optimal: {optimal}")
