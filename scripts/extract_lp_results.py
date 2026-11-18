#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 The PyPSA-Türkiye Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""LP çözümünden manuscript için gerçek sayıları çıkar.

Kullanım:
    python scripts/extract_lp_results.py results/networks/elec_s_50_ec_lcopt_Co2L-CCL.nc

Çıktılar:
    - stdout: Section 5.1 Table 6 ve Table 7 için gerçek sayılar
    - manuscript/05_section_5_1_REF_results.md (yardımcı dosya)
"""

from __future__ import annotations
import sys
import pypsa
import pandas as pd

if len(sys.argv) < 2:
    print("Usage: extract_lp_results.py <network.nc>")
    sys.exit(1)

network_path = sys.argv[1]
n = pypsa.Network(network_path)

print("═" * 75)
print(f" PYPSA-TÜRKİYE LP ÇÖZÜMÜ ÇIKTI ANALİZİ")
print(f" Network: {network_path}")
print("═" * 75)

print(f"\nBoyut: {len(n.buses)} bus, {len(n.lines)} line, {len(n.generators)} gen, "
      f"{len(n.storage_units)} storage, {len(n.snapshots)} snapshot")
print(f"\n📊 Optimal objective: {n.objective / 1e9:.3f} milyar €/yıl")
print(f"   Objective constant: {n.objective_constant / 1e9:.3f} milyar €/yıl")
print(f"   Total system cost (obj + const): {(n.objective + n.objective_constant) / 1e9:.3f} milyar €/yıl")

# ─── 1. Kapasite by carrier (GW) ───
print("\n" + "─" * 75)
print("KAPASİTE (GW) — Optimal p_nom_opt")
print("─" * 75)
cap_opt = n.generators.groupby("carrier").p_nom_opt.sum() / 1000  # GW
cap_existing = n.generators.groupby("carrier").p_nom.sum() / 1000
cap_df = pd.DataFrame({
    "p_nom (existing, GW)": cap_existing.round(2),
    "p_nom_opt (post-LP, GW)": cap_opt.round(2),
    "Δ (GW)": (cap_opt - cap_existing).round(2),
})
cap_df = cap_df[cap_df["p_nom_opt (post-LP, GW)"] > 0.01]
cap_df = cap_df.sort_values("p_nom_opt (post-LP, GW)", ascending=False)
print(cap_df.to_string())
print(f"\n  TOPLAM optimum: {cap_opt.sum():.2f} GW")
print(f"  TOPLAM mevcut:  {cap_existing.sum():.2f} GW")

# ─── 2. Üretim by carrier (TWh) ───
print("\n" + "─" * 75)
print("ÜRETİM (TWh/yıl) — Dispatch sonucu")
print("─" * 75)
gen_twh = (n.generators_t.p.T.groupby(n.generators.carrier).sum().sum(axis=1) / 1e6)
gen_twh = gen_twh.sort_values(ascending=False)
total_gen = gen_twh.sum()
gen_df = pd.DataFrame({
    "Üretim (TWh/yr)": gen_twh.round(2),
    "Pay (%)": (gen_twh / total_gen * 100).round(1),
})
gen_df = gen_df[gen_df["Üretim (TWh/yr)"] > 0.01]
print(gen_df.to_string())
print(f"\n  TOPLAM üretim: {total_gen:.2f} TWh/yıl")
total_demand = n.loads_t.p_set.sum().sum() / 1e6 if not n.loads_t.p_set.empty else None
if total_demand:
    print(f"  TOPLAM talep:  {total_demand:.2f} TWh/yıl")
    print(f"  Karşılama:    {total_gen / total_demand * 100:.1f}%")

# ─── 3. Capacity factor (CF) ───
print("\n" + "─" * 75)
print("KAPASİTE FAKTÖRÜ (%) — Üretim / (kapasite × 8760)")
print("─" * 75)
cf = (gen_twh * 1e6 / (cap_opt * 1000 * 8760) * 100).fillna(0)
cf_df = pd.DataFrame({"CF (%)": cf.round(1)})
cf_df = cf_df[cap_opt > 0.01]
cf_df = cf_df.sort_values("CF (%)", ascending=False)
print(cf_df.to_string())

# ─── 4. CO2 emisyonu ───
print("\n" + "─" * 75)
print("CO₂ EMİSYONU")
print("─" * 75)
try:
    co2_factors = n.carriers.co2_emissions
    co2_per_carrier = (n.generators_t.p.T.groupby(n.generators.carrier).sum()
                       .multiply(co2_factors, axis=0).sum(axis=1) / 1e6)
    co2_total = co2_per_carrier.sum()
    print(co2_per_carrier[co2_per_carrier > 0.01].round(2).to_string())
    print(f"\n  TOPLAM CO₂: {co2_total:.1f} Mt/yıl")
    print(f"  Limit:      600 Mt/yıl (BAU)")
    print(f"  Şu durum:   {'altında' if co2_total < 600 else 'aşıldı'}")
except Exception as e:
    print(f"  CO₂ hesabı atlandı: {e}")

# ─── 5. Storage ───
if len(n.storage_units) > 0:
    print("\n" + "─" * 75)
    print("DEPOLAMA (GW)")
    print("─" * 75)
    stor = n.storage_units.groupby("carrier").p_nom_opt.sum() / 1000
    print(stor[stor > 0.01].round(2).to_string())

# ─── 6. LMP istatistik ───
if not n.buses_t.marginal_price.empty:
    print("\n" + "─" * 75)
    print("LMP (€/MWh) — Bus marginal prices")
    print("─" * 75)
    lmp = n.buses_t.marginal_price
    print(f"  Ortalama: {lmp.mean().mean():.2f} €/MWh")
    print(f"  Median:   {lmp.median().median():.2f} €/MWh")
    print(f"  Min:      {lmp.min().min():.2f} €/MWh")
    print(f"  Max:      {lmp.max().max():.2f} €/MWh")

# ─── 7. LCOE ───
print("\n" + "─" * 75)
print("LCOE (Ortalama Sistem)")
print("─" * 75)
if total_demand and total_demand > 0:
    lcoe = (n.objective + n.objective_constant) / (total_demand * 1e6)  # €/MWh
    print(f"  Sistem LCOE: {lcoe:.2f} €/MWh")
    print(f"  Total Cost / Total Demand = {(n.objective + n.objective_constant)/1e9:.2f} B€/yr / {total_demand:.0f} TWh/yr")

# ─── 8. Curtailment ───
print("\n" + "─" * 75)
print("CURTAILMENT")
print("─" * 75)
for carrier in ["solar", "onwind", "offwind-ac", "offwind-dc"]:
    gens = n.generators[n.generators.carrier == carrier]
    if len(gens) == 0:
        continue
    available = (gens.p_nom_opt * n.generators_t.p_max_pu[gens.index]).sum().sum() / 1e6
    actual = n.generators_t.p[gens.index].sum().sum() / 1e6
    if available > 0:
        curt_pct = (1 - actual / available) * 100
        print(f"  {carrier:14}: dispatched={actual:.1f}, available={available:.1f}, curtailment={curt_pct:.1f}%")

print("\n" + "═" * 75)
print(" ✅ ANALİZ TAMAM — Manuscript Section 5.1 için sayılar yukarıda")
print("═" * 75)
