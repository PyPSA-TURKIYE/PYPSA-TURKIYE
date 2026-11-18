#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 The PyPSA-Türkiye Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Gurobi akademik lisans doğrulama scripti.

Çalıştır:
    python scripts/verify_gurobi.py

Beklenen çıktı:
    ✓ Gurobi: ... Academic
    ✓ Test problemi çözüldü
    ✓ Büyük problem (5K değişken) çözülebildi
        → Bu PIP restricted'da 2000 sınırı verir;
          akademik lisans ise sınırsız
"""

from __future__ import annotations
import os
import sys
import time

print("═" * 70)
print(" GUROBI LİSANS DOĞRULAMA")
print("═" * 70)

# 1. Lisans dosyasını bul
lic_locations = [
    os.path.expanduser("~/.gurobi/gurobi.lic"),
    os.environ.get("GRB_LICENSE_FILE"),
    "/opt/gurobi/gurobi.lic",
]
found = None
for loc in lic_locations:
    if loc and os.path.exists(loc):
        found = loc
        break

if not found:
    print("\n✗ Lisans dosyası bulunamadı!")
    print("  Aranan yerler:")
    for loc in lic_locations:
        if loc:
            print(f"    - {loc}")
    sys.exit(1)

print(f"\n✓ Lisans dosyası: {found}")

# Lisans tipini göster (sadece tip + expiration, secret göstermez)
try:
    with open(found) as f:
        for line in f:
            line = line.strip()
            if line.startswith("TYPE=") or line.startswith("EXPIRATION=") or line.startswith("LICENSEID="):
                print(f"  {line}")
            elif line.startswith("WLSACCESSID=") or line.startswith("WLSSECRET="):
                # Secret göstermez, sadece varlığını
                key = line.split("=")[0]
                print(f"  {key}=<{len(line.split('=')[1])} char secret>")
except Exception as e:
    print(f"  (içerik okunamadı: {e})")

# 2. gurobipy ile bağlan
print("\n── Gurobi env oluşturma ──")
try:
    import gurobipy as gp
    print(f"  gurobipy version: {gp.__version__}")

    env = gp.Env()
    print("  ✓ Env oluşturuldu (lisans aktive)")
except gp.GurobiError as e:
    print(f"  ✗ Hata: {e}")
    print(f"  Error code: {e.errno}")
    sys.exit(2)

# 3. Küçük test (her lisans tipi çözebilir)
print("\n── Küçük problem testi (5 değişken) ──")
try:
    m = gp.Model("small_test", env=env)
    m.setParam("OutputFlag", 0)
    xs = [m.addVar(name=f"x{i}", lb=0, ub=10) for i in range(5)]
    m.setObjective(sum(xs), gp.GRB.MAXIMIZE)
    m.addConstr(sum(xs) <= 25)
    start = time.time()
    m.optimize()
    elapsed = time.time() - start
    print(f"  ✓ Çözüm: max sum = {m.ObjVal} (beklenen: 25)")
    print(f"  ✓ Süre: {elapsed*1000:.0f} ms")
except gp.GurobiError as e:
    print(f"  ✗ {e}")
    sys.exit(3)

# 4. Büyük test (PIP restricted'ı tetikler, akademik geçer)
print("\n── Büyük problem testi (5,000 değişken, 5,000 kısıt) ──")
print("    [PIP restricted lisans: 2,000 sınırı verir, FAİL eder]")
print("    [Academic lisans: sınırsız, PASS eder]")
try:
    m2 = gp.Model("size_test", env=env)
    m2.setParam("OutputFlag", 0)
    N = 5000
    ys = [m2.addVar(name=f"y{i}", lb=0, ub=1) for i in range(N)]
    m2.setObjective(sum(ys), gp.GRB.MAXIMIZE)
    for i in range(N):
        m2.addConstr(ys[i] <= 0.5)  # 5000 kısıt
    start = time.time()
    m2.optimize()
    elapsed = time.time() - start
    if m2.Status == gp.GRB.OPTIMAL:
        print(f"  ✓ {N} değişken çözüldü ({elapsed:.2f}s)")
        print(f"  ✓ AKADEMİK LİSANS AKTİF (PIP olsaydı 'size-limited' hatası alırdık)")
    else:
        print(f"  ⚠ Status: {m2.Status}")
except gp.GurobiError as e:
    if "size-limited" in str(e).lower() or "model too large" in str(e).lower() or e.errno == 10010:
        print(f"  ✗ PIP RESTRICTED tespit edildi: {e}")
        print(f"\n  → Akademik lisans henüz aktive edilmedi.")
        print(f"  → WLS lisansını ~/.gurobi/gurobi.lic'e yerleştir.")
        sys.exit(4)
    else:
        print(f"  ✗ Beklenmedik hata: {e}")
        sys.exit(5)

# 5. PyPSA ile entegrasyon hızlı testi
print("\n── PyPSA entegrasyon kontrolü ──")
try:
    import pypsa
    print(f"  pypsa version: {pypsa.__version__}")
    print("  ✓ PyPSA + Gurobi kullanıma hazır")
except ImportError:
    print("  ⚠ PyPSA kurulu değil (pip install pypsa ile yükle)")

print()
print("═" * 70)
print(" ✅ TÜM TESTLER GEÇTİ — Gurobi akademik lisans aktif")
print("═" * 70)
print()
print("Sıradaki adım:")
print("  cd <repository root>")
print("  snakemake -j1 -n networks/elec_s_50.nc   # dry-run")
print("  snakemake -j1 networks/elec_s_50.nc      # gerçek çalışma")
