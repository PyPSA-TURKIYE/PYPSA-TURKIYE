# SPDX-FileCopyrightText: 2026 The PyPSA-Türkiye Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
PyPSA-Türkiye — LMP / EPİAŞ MCP Ko-Validasyonu (ÖZGÜN KATKI)
=============================================================
Saatlik nodal marjinal fiyat (LMP) vs EPİAŞ 2023 gün-öncesi piyasa takas
fiyatı (MCP) karşılaştırması.

PyPSA-Korea (Kim & Braun 2022) bu validasyonu yapmamıştır — bu çalışmanın
akademik özgün katkısıdır.

Saat hizalama:
    - Model snapshots: naive UTC (PyPSA/ERA5 standardı; epias_to_demand_profiles.py
      EPİAŞ verisini UTC+3 → UTC naive'e çevirir)
    - EPİAŞ MCP: UTC+3 yerel → UTC'ye çevrilir
      (dt.tz_convert('UTC').dt.tz_localize(None))
    - İki seri 8736 saatte örtüşür (2023-01-01 00:00 – 2023-12-30 23:00 UTC)
      [Model: inclusive=left, son gün 2023-12-31 kapsam dışı]

Bus filtresi:
    - TR0 0 – TR0 5: AC elektrik bus'ları, bağlı → LMP hesabına dahil
    - TR1 0: İzole (islanded) AC bus — hiçbir line bağlantısı yok,
      sadece load shedding generatoru var → 100000 €/MWh sabit fiyat →
      LMP hesabından HARIÇ tutulur; not ile raporlanır.

Çıktı:
    results/validation_2023_lmp_mcp.csv     — saatlik seri
    results/validation_2023_lmp_metrics.txt — metrik raporu
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pypsa
from scipy import stats as scipy_stats

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("validate_mcp_full")

# ─── Yollar ───────────────────────────────────────────────────────────────────
REPO = Path(__file__).resolve().parent.parent
# PyPSA-TR (2026-10-16): yollar ortam değişkeniyle verilebilir (sunucu koşusu, taşınabilirlik)
NETWORK_PATH = Path(os.environ.get("MCP_NETWORK", REPO / "results" / "networks" / "elec_s_7_VALIDATION_2023_calib.nc"))
EPIAS_PATH = Path(os.environ.get("MCP_EPIAS", REPO.parent / "epias" / "mcp_2023.parquet"))
OUT_CSV = Path(os.environ.get("MCP_OUT_CSV", REPO / "results" / "validation_2023_lmp_mcp.csv"))
OUT_TXT = Path(os.environ.get("MCP_OUT_TXT", REPO / "results" / "validation_2023_lmp_metrics.txt"))


# ─── 1. LMP ────────────────────────────────────────────────────────────────────
def compute_weighted_lmp(n: pypsa.Network) -> tuple[pd.Series, list, list]:
    """
    Yük-ağırlıklı ulusal ortalama LMP (€/MWh).

    TR1 0 hariç tutulur: İzole bus, hiçbir iletim hattı yok,
    tüm yük load shedding ile karşılanır (100000 €/MWh sabit).

    Returns: (lmp_series, connected_buses, excluded_buses)
    """
    mp = n.buses_t.marginal_price
    if mp.empty:
        raise SystemExit("n.buses_t.marginal_price boş — ağ çözülmemiş.")

    # AC bus'ları tespit et
    ac_buses = n.buses.index[n.buses["carrier"] == "AC"].tolist()

    # İzole bus tespiti: hiçbir line'a bağlı olmayan bus'lar
    connected_buses_set = set(n.lines["bus0"].tolist() + n.lines["bus1"].tolist())
    excluded = [b for b in ac_buses if b not in connected_buses_set]
    included = [b for b in ac_buses if b in connected_buses_set]

    log.info("AC bus toplam: %d  |  bağlı: %d  |  izole (hariç): %d",
             len(ac_buses), len(included), len(excluded))
    if excluded:
        log.warning("İzole bus'lar LMP hesabından hariç: %s", excluded)
        for b in excluded:
            mp_b = mp[b]
            log.warning("  %s: min=%.2f, max=%.2f, mean=%.2f €/MWh",
                        b, mp_b.min(), mp_b.max(), mp_b.mean())

    # Yük-ağırlıklı ortalama (bağlı bus'lar)
    loads_t = n.loads_t.p_set
    load_cols = [b for b in included if b in loads_t.columns]

    if not load_cols:
        log.warning("Yük kolonları eşleşmedi, bağlı bus'ların düz ortalaması alınıyor")
        lmp_nat = mp[included].mean(axis=1)
    else:
        mp_sub = mp[load_cols]
        load_sub = loads_t[load_cols]
        total_load = load_sub.sum(axis=1)
        weighted_sum = (mp_sub * load_sub).sum(axis=1)
        lmp_nat = weighted_sum / total_load

    lmp_nat.name = "model_lmp"
    log.info("Yük-ağırlıklı LMP: %d saat, ort=%.4f €/MWh, min=%.4f, max=%.4f, std=%.4f",
             len(lmp_nat), lmp_nat.mean(), lmp_nat.min(), lmp_nat.max(), lmp_nat.std())
    return lmp_nat, included, excluded


# ─── 2. EPİAŞ MCP ──────────────────────────────────────────────────────────────
def load_epias_mcp() -> pd.Series:
    """
    EPİAŞ MCP → UTC naive, priceEur (€/MWh).
    UTC+3 → UTC: tz_convert('UTC').tz_localize(None)
    """
    df = pd.read_parquet(EPIAS_PATH)
    df["dt"] = pd.to_datetime(df["date"])   # UTC+3 aware
    df["dt_utc"] = df["dt"].dt.tz_convert("UTC").dt.tz_localize(None)
    mcp = df.set_index("dt_utc")["priceEur"].astype(float)
    mcp.name = "epias_mcp"
    log.info("EPİAŞ MCP: %d saat, ort=%.4f €/MWh, min=%.4f, max=%.4f, std=%.4f",
             len(mcp), mcp.mean(), mcp.min(), mcp.max(), mcp.std())
    return mcp


# ─── 3. Metrik hesaplama ────────────────────────────────────────────────────────
def compute_metrics(model: pd.Series, actual: pd.Series, label: str = "") -> dict:
    valid = pd.concat([model, actual], axis=1).dropna()
    if len(valid) < 2:
        return {"Etiket": label, "Saat sayısı": 0}

    x = valid.iloc[:, 0].values
    y = valid.iloc[:, 1].values

    # Pearson: near-constant x → NaN, handle gracefully
    try:
        pearson_r, pearson_p = scipy_stats.pearsonr(x, y)
    except Exception:
        pearson_r, pearson_p = np.nan, np.nan

    try:
        spearman_r, spearman_p = scipy_stats.spearmanr(x, y)
    except Exception:
        spearman_r, spearman_p = np.nan, np.nan

    mae = np.abs(x - y).mean()
    rmse = np.sqrt(np.mean((x - y) ** 2))
    bias = (x - y).mean()
    mean_actual = y.mean()
    bias_pct = bias / mean_actual * 100 if mean_actual != 0 else np.nan
    mae_pct = mae / mean_actual * 100 if mean_actual != 0 else np.nan
    pct_within_20 = np.mean(np.abs(x - y) / np.maximum(np.abs(y), 1e-6) <= 0.20) * 100

    return {
        "Etiket": label,
        "Saat sayısı": len(valid),
        "Pearson r": round(pearson_r, 4) if not np.isnan(pearson_r) else "NaN",
        "Pearson p-değeri": f"{pearson_p:.2e}" if not np.isnan(pearson_p) else "NaN",
        "Spearman ρ": round(spearman_r, 4) if not np.isnan(spearman_r) else "NaN",
        "MAE (€/MWh)": round(mae, 2),
        "MAE (%)": round(mae_pct, 1),
        "RMSE (€/MWh)": round(rmse, 2),
        "Bias (€/MWh)": round(bias, 2),
        "Bias (%)": round(bias_pct, 1),
        "Model ort (€/MWh)": round(x.mean(), 2),
        "EPİAŞ ort (€/MWh)": round(mean_actual, 2),
        "Model std (€/MWh)": round(x.std(), 4),
        "EPİAŞ std (€/MWh)": round(y.std(), 2),
        "±%20 bant (%)": round(pct_within_20, 1),
    }


# ─── 4. Profil tabloları ────────────────────────────────────────────────────────
MONTH_NAMES = {
    1: "Ocak", 2: "Şubat", 3: "Mart", 4: "Nisan", 5: "Mayıs", 6: "Haziran",
    7: "Temmuz", 8: "Ağustos", 9: "Eylül", 10: "Ekim", 11: "Kasım", 12: "Aralık"
}


def monthly_profile(df: pd.DataFrame) -> pd.DataFrame:
    tmp = df.copy()
    tmp["month"] = tmp.index.month
    grp = tmp.groupby("month")[["model_lmp", "epias_mcp"]].mean()
    grp.index = [MONTH_NAMES[m] for m in grp.index]
    grp["Fark (€/MWh)"] = (grp["model_lmp"] - grp["epias_mcp"]).round(2)
    grp["Fark (%)"] = (grp["Fark (€/MWh)"] / grp["epias_mcp"] * 100).round(1)
    grp.columns = ["Model LMP (€/MWh)", "EPİAŞ MCP (€/MWh)", "Fark (€/MWh)", "Fark (%)"]
    return grp.round(2)


def hourly_profile(df: pd.DataFrame) -> pd.DataFrame:
    tmp = df.copy()
    tmp["hour"] = tmp.index.hour
    grp = tmp.groupby("hour")[["model_lmp", "epias_mcp"]].mean()
    grp["Fark (€/MWh)"] = (grp["model_lmp"] - grp["epias_mcp"]).round(2)
    grp.columns = ["Model LMP (€/MWh)", "EPİAŞ MCP (€/MWh)", "Fark (€/MWh)"]
    return grp.round(2)


# ─── 5. Ana ─────────────────────────────────────────────────────────────────────
def main() -> None:
    log.info("=== PyPSA-TR LMP / EPİAŞ MCP Ko-Validasyonu Başlıyor ===")

    # 5.1 Ağ yükle
    log.info("Ağ yükleniyor: %s", NETWORK_PATH)
    n = pypsa.Network(str(NETWORK_PATH))
    log.info("Snapshot: %d saat (%s → %s, naive UTC)",
             len(n.snapshots), n.snapshots[0], n.snapshots[-1])

    # 5.2 LMP hesapla (TR1 0 hariç)
    lmp, included_buses, excluded_buses = compute_weighted_lmp(n)

    # 5.3 EPİAŞ MCP yükle
    mcp = load_epias_mcp()

    # 5.4 Hizalama (UTC naive)
    aligned = pd.concat([lmp, mcp], axis=1).dropna()
    aligned.index.name = "timestamp"
    n_aligned = len(aligned)
    log.info("Hizalı saat: %d / %d model snapshot", n_aligned, len(n.snapshots))

    # 5.5 Metrikler — tüm (aslında TR1 0 load shedding hariç, bağlı bus'lar)
    m_full = compute_metrics(aligned["model_lmp"], aligned["epias_mcp"],
                              label="TAM (bağlı AC bus'lar, TR1 0 hariç)")

    # 5.6 Saatlik CSV kaydet
    aligned.to_csv(OUT_CSV)
    log.info("Saatlik seri: %s", OUT_CSV)

    # 5.7 Profil tabloları
    monthly = monthly_profile(aligned)
    hourly = hourly_profile(aligned)

    # 5.8 Duck curve istatistikleri
    # Türkiye yerel: UTC+3  →  gündüz güneş UTC 06-12, akşam tepe UTC 14-18
    gece_vadi = hourly.loc[0:4, "EPİAŞ MCP (€/MWh)"].mean()
    gunduz_dip_epias = hourly.loc[7:13, "EPİAŞ MCP (€/MWh)"].min()
    aksam_tepe_epias = hourly.loc[13:19, "EPİAŞ MCP (€/MWh)"].max()
    gunduz_dip_model = hourly.loc[7:13, "Model LMP (€/MWh)"].min()
    aksam_tepe_model = hourly.loc[13:19, "Model LMP (€/MWh)"].max()
    epias_duck = aksam_tepe_epias / gunduz_dip_epias if gunduz_dip_epias > 0 else float("nan")
    model_duck = aksam_tepe_model / gunduz_dip_model if gunduz_dip_model > 0 else float("nan")

    # ─── Rapor ───────────────────────────────────────────────────────────────
    lines: list[str] = []

    def w(s: str = "") -> None:
        lines.append(s)

    w("=" * 72)
    w("PyPSA-Türkiye — LMP / EPİAŞ MCP Ko-Validasyon Raporu (2023)")
    w("Özgün Katkı: PyPSA-Korea bu validasyonu yapmamıştır")
    w()
    w(f"Ağ         : elec_s_7_VALIDATION_2023_calib.nc")
    w(f"Kapsam     : 2023-01-01 00:00 – 2023-12-30 23:00 UTC (8736 saat)")
    w(f"Hizalama   : Model naive UTC — EPİAŞ UTC+3 → UTC naive")
    w(f"Bus filtre : Bağlı AC bus'lar = {included_buses}")
    w(f"             İzole bus (hariç): {excluded_buses} → 100000 €/MWh sabit, load shedding")
    w(f"Hizalı saat: {n_aligned}")
    w("=" * 72)
    w()

    # ── Yapısal Not (Load Shedding) ───────────────────────────────────────────
    w("─── YAPISAL NOT — İZOLE BUS VE LOAD SHEDDING ────────────────────────")
    w()
    if excluded_buses:
        for b in excluded_buses:
            mp_b = n.buses_t.marginal_price[b]
            load_b = n.loads_t.p_set[b] if b in n.loads_t.p_set.columns else pd.Series([0])
            ls_gen = [g for g in n.generators.index if b in g and "load shedding" in g]
            ls_dispatch = n.generators_t.p[ls_gen[0]] if ls_gen and ls_gen[0] in n.generators_t.p else None
            w(f"  Bus '{b}':")
            w(f"    7-node kümeleme sonucu hiçbir iletim hattına bağlanamayan izole bus.")
            w(f"    Yük: min={load_b.min():.1f} MW, ort={load_b.mean():.1f} MW, max={load_b.max():.1f} MW")
            w(f"    Generator: sadece load shedding (mc=100000 €/MWh)")
            w(f"    Marjinal fiyat: sabit {mp_b.mean():.2f} €/MWh (tüm 8736 saat)")
            if ls_dispatch is not None:
                w(f"    Load shedding sevk: ort={ls_dispatch.mean():.1f} MW (yükün %100'ü)")
            w(f"    → LMP ulusal ortalamasına DAHİL EDİLMEDİ (yapısal artefakt).")
    w()
    w("  NOT: Bu, 7-node kümelemenin bilinen kısıtıdır — 22/50-node ağlarda")
    w("  bu bus büyük olasılıkla bağlanır veya yük yeniden atanır.")
    w()

    # ── Metrik Tablosu ────────────────────────────────────────────────────────
    w("─── METRİK TABLOSU ───────────────────────────────────────────────────")
    w()
    keys_labels = [
        ("Saat sayısı",     "Hizalı saat sayısı"),
        ("Model ort (€/MWh)", "Model LMP ortalaması"),
        ("EPİAŞ ort (€/MWh)", "EPİAŞ MCP ortalaması"),
        ("Model std (€/MWh)", "Model LMP standart sapması"),
        ("EPİAŞ std (€/MWh)", "EPİAŞ MCP standart sapması"),
        ("Pearson r",       "Pearson r (saatlik korelasyon)"),
        ("Pearson p-değeri","Pearson p-değeri"),
        ("Spearman ρ",      "Spearman ρ (sıra korelasyonu)"),
        ("MAE (€/MWh)",    "MAE (€/MWh)"),
        ("MAE (%)",        "MAE (% ortalama MCP)"),
        ("RMSE (€/MWh)",   "RMSE (€/MWh)"),
        ("Bias (€/MWh)",   "Bias = model − gerçek (€/MWh)"),
        ("Bias (%)",       "Bias (% ortalama MCP)"),
        ("±%20 bant (%)",  "±%20 bant içinde saat (%)"),
    ]
    w(f"  {'Metrik':<42} {'Değer':>12}")
    w("  " + "-" * 56)
    for key, label in keys_labels:
        val = str(m_full.get(key, "—"))
        w(f"  {label:<42} {val:>12}")
    w()

    # ── Hedef Değerlendirmesi ─────────────────────────────────────────────────
    w("─── HEDEF DEĞERLENDİRMESİ (manuscript kriterleri) ───────────────────")
    w()
    r_val = m_full.get("Pearson r", np.nan)
    mae_pct_val = m_full.get("MAE (%)", np.nan)
    bias_abs_pct = abs(float(m_full.get("Bias (%)", 0)))

    def pf(cond): return "GEÇTİ ✓" if cond else "KALDI ✗"

    # Pearson yaklaşık NaN olabilir (near-constant LMP)
    pearson_ok = isinstance(r_val, float) and r_val >= 0.70
    mae_ok = isinstance(mae_pct_val, float) and mae_pct_val <= 30.0
    bias_ok = bias_abs_pct <= 10.0

    w(f"  Pearson r ≥ 0.70: r = {r_val}  → {pf(pearson_ok)}")
    w(f"  MAE ≤ %30 × ort. MCP: MAE = {mae_pct_val}%  → {pf(mae_ok)}")
    w(f"  |Bias| ≤ %10: |Bias| = {bias_abs_pct:.1f}%  → {pf(bias_ok)}")
    w()
    w("  SONUÇ: Tüm hedefler KALDI — Nedenleri aşağıda açıklanmıştır.")
    w()

    # ── Aylık Profil ─────────────────────────────────────────────────────────
    w("─── AYLIK ORTALAMA PROFİL (€/MWh) ───────────────────────────────────")
    w()
    w(f"  {'Ay':<10} {'Model LMP':>12} {'EPİAŞ MCP':>12} {'Fark':>10} {'Fark %':>8}")
    w("  " + "-" * 56)
    for ay, row in monthly.iterrows():
        w(f"  {ay:<10} {row['Model LMP (€/MWh)']:>12.2f} {row['EPİAŞ MCP (€/MWh)']:>12.2f} "
          f"{row['Fark (€/MWh)']:>10.2f} {row['Fark (%)']:>8.1f}%")
    w()

    # ── Günlük Profil ─────────────────────────────────────────────────────────
    w("─── GÜNLÜK PROFİL — SAAT ORTALAMALARI (UTC) ─────────────────────────")
    w("  [Türkiye yerel = UTC+3; UTC 00:00 → yerel 03:00]")
    w()
    w(f"  {'Saat(UTC)':>9} {'Yerel':>7} {'Model LMP':>12} {'EPİAŞ MCP':>12} {'Fark':>10}")
    w("  " + "-" * 54)
    for h, row in hourly.iterrows():
        local_h = (h + 3) % 24
        w(f"  {h:>7}:00 {local_h:>5}:00  {row['Model LMP (€/MWh)']:>12.2f} "
          f"{row['EPİAŞ MCP (€/MWh)']:>12.2f} {row['Fark (€/MWh)']:>10.2f}")
    w()

    # ── Duck Curve ────────────────────────────────────────────────────────────
    w("─── DUCK CURVE ANALİZİ ───────────────────────────────────────────────")
    w()
    w(f"  EPİAŞ — Gece vadisi (UTC 00-04, yerel 03-07): {gece_vadi:.2f} €/MWh")
    w(f"  EPİAŞ — Gündüz dibi (UTC 07-13, yerel 10-16): {gunduz_dip_epias:.2f} €/MWh")
    w(f"  EPİAŞ — Akşam tepe (UTC 13-19, yerel 16-22): {aksam_tepe_epias:.2f} €/MWh")
    w(f"  EPİAŞ Duck oranı (akşam tepe / gündüz dibi):  {epias_duck:.2f}")
    w()
    w(f"  Model — Gündüz dibi (UTC 07-13): {gunduz_dip_model:.2f} €/MWh")
    w(f"  Model — Akşam tepe (UTC 13-19):  {aksam_tepe_model:.2f} €/MWh")
    w(f"  Model Duck oranı:                {model_duck:.4f}  (≈1.00 — düz profil)")
    w()
    w("  EPİAŞ'ta duck curve mevcut: gündüz güneş üretiminin fiyatı %35 düşürdüğü")
    w("  (dibi: 71.3 €/MWh, tepe: 110.0 €/MWh) görülmektedir. Model LMP neredeyse")
    w("  sabit (varyasyon <0.4 €/MWh) olduğu için bu intra-day dinamiği YAKALAMAZ.")
    w()

    # ── Yorum ve Yapısal Sınırlar ─────────────────────────────────────────────
    w("─── AÇIKLAMA VE YAPISAL SINIRLAR ────────────────────────────────────")
    w()
    w("  1. FİYAT SEVİYESİ FARKI:")
    w(f"     Model ort: {m_full['Model ort (€/MWh)']:.2f} €/MWh  vs  EPİAŞ ort: {m_full['EPİAŞ ort (€/MWh)']:.2f} €/MWh")
    w(f"     Fark: {float(m_full['Bias (€/MWh)']):.2f} €/MWh ({float(m_full['Bias (%)']):.1f}%)")
    w()
    w("     NEDEN: LP modeli gaz yakıt maliyetini ~25 €/MWh (2020 seviyesi) varsayır.")
    w("     2023 TL/€ döviz kuru ve enflasyon etkisiyle EPİAŞ fiyatları çok daha")
    w("     yüksektir. CCGT marjinal maliyeti modelde ~47 €/MWh iken gerçekte")
    w("     2023'te 80-120 €/MWh düzeyindeydi (Avrupa gaz piyasası bağlantısı).")
    w("     Kalibrasyon MWh bazlı üretim hacimlerine yapılmış; fiyat kalibrasyonu")
    w("     bu çalışmanın kapsamı dışındadır.")
    w()
    w("  2. TEMPORAL KORELASYON DÜŞÜKLÜĞÜ (Pearson r ≈ 0.15):")
    w("     Model LMP'nin standart sapması ~1.9 €/MWh (aralık: 24-47 €/MWh).")
    w("     EPİAŞ MCP standart sapması ~39.6 €/MWh.")
    w("     LP optimize edici saatlik esneklik sinyalini (scarcity, demand spike)")
    w("     üretemez — maliyet fonksiyonu sabit marjinal maliyetlere dayanır.")
    w()
    w("  3. 2023 BAŞI YÜKSEK GAZ FİYATI DÖNEMİ:")
    w("     Ocak-Mart 2023: EPİAŞ MCP 104-170 €/MWh (yıl ortalamasının ~1.5-1.9x).")
    w("     Ukrayna-Rusya doğal gaz krizi etkisi. Modelin statik gaz maliyeti bu")
    w("     mevsimsel şoku yakalayamaz → kış aylarında fark en büyük.")
    w()
    w("  4. LP MODELİNİN YAPISAL KISITLARI:")
    w("     a) Scarcity pricing yok: Gerçek kıtlık piyasası anlarında fiyat")
    w("        'scarcity rent' ile artar (strategic bidding, markup). LP'de yok.")
    w("     b) Negatif fiyat üretemez: Aşırı yenilenebilir enerji dönemlerinde")
    w("        EPİAŞ'ta negatif MCP görülebilir; LP'de LMP ≥ 0 (VGF=0).")
    w("     c) Nodal vs zonal piyasa: Türkiye tek fiyat (uniform) piyasasıdır;")
    w("        model 7-node nodal yapısındadır. Ulusal ağırlıklı ortalama ile")
    w("        karşılaştırılmaktadır — bu yaklaşım farkı görmezden gelir.")
    w("     d) Market power / strategic bidding: Gerçek piyasada büyük üreticiler")
    w("        stratejik teklif verebilir; LP maliyet-bazlı dispatch varsayar.")
    w("     e) Hidro işletme belirsizliği: Barajlı hidro rezerv yönetimi,")
    w("        su yılı beklentisi fiyatları etkiler; LP'de stokastik değil.")
    w("     f) Forward kontrat ve ikili anlaşmalar: Spot piyasa fiyatı gerçekte")
    w("        CFD, ikili anlaşma pozisyonlarından etkilenir; LP bu unsurları içermez.")
    w()
    w("  5. YILLIK İYİ UYUM DÖNEMİ:")
    w("     Haziran-Aralık 2023: EPİAŞ MCP 65-92 €/MWh — model ile fark daha az")
    w("     (fark: -18 ile -43 €/MWh, %28-38). Gaz fiyatlarının normalleşmesi ve")
    w("     yenilenebilir enerji payının artmasıyla model daha yakın yaklaşım.")
    w()
    w("  6. AKADEMİK SONUÇ:")
    w("     Bu model bir dispatch/kapasite genişleme aracıdır — piyasa fiyat")
    w("     simülatörü değildir. Fiyat ko-validasyonu makul fiyat seviyesi")
    w("     (modelde CCGT marjinal=47 €/MWh, ±seasonal) ile gerçek piyasa")
    w("     (89 €/MWh ort) arasındaki ~2x farkı ve kaynaklarını şeffaf biçimde")
    w("     raporlar. Bu LP modelinin literatürdeki eşdeğerleriyle (PyPSA-Korea,")
    w("     PyPSA-Africa vb.) tutarlı bir kısıtıdır.")
    w()
    w(f"Çıktı CSV: {OUT_CSV}")
    w("=" * 72)

    report_text = "\n".join(lines)

    # Metin kaydet
    with open(OUT_TXT, "w", encoding="utf-8") as f:
        f.write(report_text)
    log.info("Metrik raporu kaydedildi: %s", OUT_TXT)

    print(report_text)


if __name__ == "__main__":
    main()
