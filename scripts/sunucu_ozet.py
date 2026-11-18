# SPDX-FileCopyrightText: 2026 The PyPSA-Türkiye Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""PyPSA-TR — çözülmüş bir ağın tüm özet değerlerini JSON olarak çıkarır.

Kullanım:
    python scripts/sunucu_ozet.py <ag.nc> <cikti.json> [--node N --scenario S --solver-log L]

Sunucu koşusu (scripts/sunucu_tum_kosu.sh) her senaryodan sonra bunu çağırır;
tüm JSON'lar sonda tek bir CSV'de birleştirilir. Yalnız okur, ağı değiştirmez.
"""
import argparse
import json
import logging
import re
import warnings

warnings.filterwarnings("ignore")
logging.disable(logging.WARNING)

import numpy as np
import pandas as pd
import pypsa


def r(x, d=4):
    try:
        return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), d)
    except Exception:
        return None


def solver_info(path):
    info = {}
    if not path:
        return info
    try:
        txt = open(path, errors="ignore").read()
    except OSError:
        return info
    m = re.search(r"Gurobi Optimizer version ([\d.]+)", txt)
    if m:
        info["gurobi_version"] = m.group(1)
    m = re.search(r"CPU model: (.+)", txt)
    if m:
        info["cpu_model"] = m.group(1).strip()
    m = re.search(r"Thread count: (.+)", txt)
    if m:
        info["thread_count"] = m.group(1).strip()
    m = re.findall(r"Barrier solved model in \d+ iterations and ([\d.]+) seconds", txt)
    if m:
        info["barrier_seconds"] = float(m[-1])
    m = re.findall(r"Solved in \d+ iterations and ([\d.]+) seconds", txt)
    if m:
        info["solve_seconds"] = float(m[-1])
    m = re.findall(r"Optimal objective\s+([-\d.e+]+)", txt)
    if m:
        info["optimal_objective"] = float(m[-1])
    if "HiGHS" in txt and "gurobi_version" not in info:
        info["solver"] = "highs"
    return info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("network")
    ap.add_argument("out")
    ap.add_argument("--node", type=int)
    ap.add_argument("--scenario")
    ap.add_argument("--solver-log")
    a = ap.parse_args()

    n = pypsa.Network(a.network)
    wg = n.snapshot_weightings.generators
    ws = n.snapshot_weightings.stores
    hours = float(wg.sum())
    g = n.generators
    out = {
        "node": a.node, "scenario": a.scenario, "network_file": a.network,
        "snapshots": len(n.snapshots), "hours_weighted": r(hours, 2),
        "objective_BEUR": r(n.objective / 1e9, 5),
        "objective_constant_BEUR": r(getattr(n, "objective_constant", 0.0) / 1e9, 5),
    }

    # Talep ve yük atma
    load = (n.loads_t.p_set.reindex(columns=n.loads.index).mul(wg, axis=0)).sum().sum() if not n.loads_t.p_set.empty else 0.0
    out["load_TWh"] = r(load / 1e6, 3)
    ls = g[g.carrier == "load shedding"].index
    out["load_shedding_TWh"] = r((n.generators_t.p[ls].mul(wg, axis=0)).sum().sum() / 1e6, 4) if len(ls) else 0.0

    # Üretim, kapasite, CF, kısıntı (generatorlar)
    gen = (n.generators_t.p.mul(wg, axis=0)).sum()
    out["generation_TWh"] = {k: r(v / 1e6, 3) for k, v in gen.groupby(g.carrier).sum().items()}
    out["capacity_existing_GW"] = {k: r(v / 1e3, 3) for k, v in g.p_nom.groupby(g.carrier).sum().items()}
    out["capacity_optimal_GW"] = {k: r(v / 1e3, 3) for k, v in g.p_nom_opt.groupby(g.carrier).sum().items()}
    new = (g.p_nom_opt - g.p_nom).clip(lower=0).groupby(g.carrier).sum()
    out["capacity_new_GW"] = {k: r(v / 1e3, 3) for k, v in new[new > 1].items()}
    cf = {}
    for c, cap in g.p_nom_opt.groupby(g.carrier).sum().items():
        if cap > 0:
            cf[c] = r(gen.groupby(g.carrier).sum().get(c, 0) / (cap * hours), 4)
    out["capacity_factor"] = cf
    pmax = n.get_switchable_as_dense("Generator", "p_max_pu")
    avail = (pmax.mul(g.p_nom_opt, axis=1).mul(wg, axis=0)).sum()
    curt = (avail - gen).groupby(g.carrier).sum()
    out["curtailment_TWh"] = {k: r(v / 1e6, 3) for k, v in curt.items() if k in ("solar", "onwind", "offwind-ac", "offwind-dc", "ror")}
    out["available_p_max_pu_mean"] = {k: r(v, 4) for k, v in pmax.mean().groupby(g.carrier).mean().items()}
    out["marginal_cost_mean"] = {k: r(v, 3) for k, v in g.marginal_cost.groupby(g.carrier).mean().items()}

    # CO2
    co2 = 0.0
    for c, e in gen.groupby(g.carrier).sum().items():
        if c in n.carriers.index:
            sub = g[g.carrier == c]
            eff = (sub.efficiency * sub.p_nom_opt).sum() / sub.p_nom_opt.sum() if sub.p_nom_opt.sum() > 0 else sub.efficiency.mean()
            co2 += e * n.carriers.at[c, "co2_emissions"] / (eff if eff else 1)
    out["co2_Mt"] = r(co2 / 1e6, 3)
    gc = n.global_constraints
    out["global_constraints"] = {i: {"constant": r(gc.at[i, "constant"], 1), "mu": r(gc.at[i, "mu"], 6)} for i in gc.index}

    # Depolama birimleri (hidro, batarya, PHS)
    su = n.storage_units
    sud = {}
    for c in su.carrier.unique():
        idx = su[su.carrier == c].index
        d = {"p_nom_GW": r(su.loc[idx, "p_nom_opt"].sum() / 1e3, 3),
             "energy_GWh": r((su.loc[idx, "p_nom_opt"] * su.loc[idx, "max_hours"]).sum() / 1e3, 2),
             "dispatch_TWh": r((n.storage_units_t.p_dispatch[idx].mul(wg, axis=0)).sum().sum() / 1e6, 3)}
        if not n.storage_units_t.inflow.empty:
            ii = idx.intersection(n.storage_units_t.inflow.columns)
            d["inflow_TWh"] = r((n.storage_units_t.inflow[ii].mul(wg, axis=0)).sum().sum() / 1e6, 3)
            if len(ii):
                ann = (n.storage_units_t.inflow[ii].mul(wg, axis=0)).sum()
                d["units_inflow_over_capacity"] = int((ann > su.loc[ii, "p_nom"] * hours).sum())
        if not n.storage_units_t.spill.empty:
            ii = idx.intersection(n.storage_units_t.spill.columns)
            d["spill_TWh"] = r((n.storage_units_t.spill[ii].mul(wg, axis=0)).sum().sum() / 1e6, 3)
        sud[c] = d
    out["storage_units"] = sud

    # Store + Link (H2, batarya store)
    st = n.stores
    out["stores"] = {}
    for c in st.carrier.unique():
        idx = st[st.carrier == c].index
        e = n.stores_t.e[idx].sum(axis=1) if not n.stores_t.e.empty else pd.Series(dtype=float)
        dis = (n.stores_t.p[idx].clip(lower=0).mul(ws, axis=0)).sum().sum() if not n.stores_t.p.empty else 0.0
        cap = st.loc[idx, "e_nom_opt"].sum()
        out["stores"][c] = {"e_nom_TWh": r(cap / 1e6, 4), "discharge_TWh": r(dis / 1e6, 3),
                            "cycles_per_year": r(dis / cap, 2) if cap > 0 else None,
                            "monthly_mean_soc_TWh": [r(v / 1e6, 3) for v in e.resample("ME").mean()] if len(e) else []}
    lk = n.links
    out["links_GW"] = {k: r(v / 1e3, 3) for k, v in lk.p_nom_opt.groupby(lk.carrier).sum().items()}
    if not n.links_t.p0.empty:
        out["links_flow_TWh"] = {k: r(v / 1e6, 3) for k, v in (n.links_t.p0.clip(lower=0).mul(wg, axis=0)).sum().groupby(lk.carrier).sum().items()}

    # İletim
    ln = n.lines
    out["lines"] = {"count": int(len(ln)),
                    "s_nom_GW": r(ln.s_nom.sum() / 1e3, 2), "s_nom_opt_GW": r(ln.s_nom_opt.sum() / 1e3, 2),
                    "s_nom_x_length_TWkm": r((ln.s_nom * ln.length).sum() / 1e6, 3),
                    "s_nom_opt_x_length_TWkm": r((ln.s_nom_opt * ln.length).sum() / 1e6, 3),
                    "max_expansion_ratio": r((ln.s_nom_opt / ln.s_nom.replace(0, np.nan)).max(), 3)}

    # Fiyatlar (yük ağırlıklı)
    try:
        lmp = n.buses_t.marginal_price
        lb = n.loads.bus
        lp = n.loads_t.p_set.reindex(columns=n.loads.index)
        num = (lmp[lb.values].values * lp.values).sum(axis=1)
        den = lp.values.sum(axis=1)
        series = pd.Series(num / np.where(den == 0, np.nan, den), index=n.snapshots)
        out["price_load_weighted_mean_EUR_MWh"] = r(np.nanmean(series), 3)
        out["price_p95"] = r(np.nanpercentile(series, 95), 2)
        out["price_max"] = r(np.nanmax(series), 2)
        out["hours_price_over_200"] = int((series > 200).sum())
    except Exception as ex:
        out["price_error"] = str(ex)

    # Nükleer konumu
    nuc = g[g.carrier == "nuclear"]
    nb = (nuc.p_nom_opt.groupby(nuc.bus).sum() / 1e3)
    out["nuclear_by_bus_GW"] = {b: {"GW": r(v, 3), "x": r(n.buses.at[b, "x"], 3), "y": r(n.buses.at[b, "y"], 3)} for b, v in nb[nb > 0.01].items()}

    out["solver"] = solver_info(a.solver_log)
    with open(a.out, "w") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    print(f"[ÖZET] {a.out}: obj={out['objective_BEUR']} B€, CO2={out['co2_Mt']} Mt")


if __name__ == "__main__":
    main()
