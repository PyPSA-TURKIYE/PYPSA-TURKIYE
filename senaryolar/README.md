<!--
SPDX-FileCopyrightText: 2026 The PyPSA-Türkiye Authors

SPDX-License-Identifier: CC-BY-4.0
-->

# Scenario configurations (`senaryolar/`)

Each file is a Snakemake config overlay applied on top of `config.yaml`
(a copy of `config.turkey.yaml`). The header comments of every file document the
parameter choices and their sources (comments are partly in Turkish).

## Scenario runs

`n{7,22,50,81}/` contain one file per scenario for each spatial resolution; the
files differ only in the number of clusters.

| File | Target year | `opts` | Emission constraint (`co2limit`, t CO2/a) | Build-rate limits |
|---|---|---|---|---|
| `01_REF.yaml` | 2030 | `CCL` | none | `data/agg_p_nom_minmax_trajectory.csv` |
| `02_NDC2030.yaml` | 2030 | `Co2L-CCL` | 1.49e8 | `data/agg_p_nom_minmax_trajectory.csv` |
| `03_HIGHRES.yaml` | 2030 | `Co2L-CCL` | 7.5e7 | `data/agg_p_nom_minmax_accel.csv` |
| `04_NETZERO2053.yaml` | 2053 | `Co2L-CCL` | 0 | `data/agg_p_nom_minmax_trajectory.csv` |
| `05_NOFOSSIL.yaml` | 2030 | `Co2L-CCL` | 8.2e7 | `data/agg_p_nom_minmax_accel.csv` |

All scenario runs use 2018 as weather and demand-shape year; demand is scaled to the
target year via `load_options.scale`. Fuel prices are the defaults in `data/costs.csv`.

Example (50 nodes, NDC scenario):

```bash
snakemake -j1 --rerun-triggers mtime -R add_electricity \
  --configfile senaryolar/n50/02_NDC2030.yaml -- results/networks/elec_s_50_ec_lcopt_Co2L-CCL.nc
```

Never use `--forcerun` / `-F` (see the main README).

## Validation and solver overrides

| File | Purpose |
|---|---|
| `00_VALIDATION_2023.yaml` | 2023 dispatch backcast used in the paper (isolated run directory `validation2023`; 2023 fuel prices are set here only) |
| `00_VALIDATION.yaml`, `00_VALIDATION_2018.yaml` | earlier 2018-based validation set-ups, superseded by `00_VALIDATION_2023.yaml`; kept for reference |
| `solver_highs.yaml` | switches the solver from Gurobi to HiGHS |
