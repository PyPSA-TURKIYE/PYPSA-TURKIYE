<!--
SPDX-FileCopyrightText: 2026 The PyPSA-Türkiye Authors

SPDX-License-Identifier: CC-BY-4.0
-->

# PyPSA-Türkiye

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.24344886.svg)](https://doi.org/10.5281/zenodo.24344886)
[![License: AGPL-3.0-or-later](https://img.shields.io/badge/License-AGPL--3.0--or--later-blue.svg)](LICENSES/AGPL-3.0-or-later.txt)

**PyPSA-Türkiye** is an open-source, network-resolved energy system model of the
Turkish power system. It co-optimizes generation, storage and transmission
investment together with hourly system operation over a full year, and it is
parameterized from official national statistics combined with open geospatial
and reanalysis data.

PyPSA-Türkiye is a **fork of [PyPSA-Earth](https://github.com/pypsa-meets-earth/pypsa-earth)**
(based on upstream commit `634fdf15`, PyPSA-Earth v0.8.0, April 2026), which itself
builds on [PyPSA-Eur](https://github.com/PyPSA/pypsa-eur) and
[PyPSA](https://github.com/PyPSA/PyPSA). The optimization core, the Snakemake
workflow and most scripts are inherited from PyPSA-Earth; all upstream copyright and
licence notices are retained. The original PyPSA-Earth README is kept in
[`docs/README_pypsa-earth.md`](docs/README_pypsa-earth.md) and the upstream citation
file in [`docs/CITATION_pypsa-earth.cff`](docs/CITATION_pypsa-earth.cff).
Please cite PyPSA-Earth and PyPSA together with this model (see [Citation](#citation)).

## Model scope

| Item | Setting |
|---|---|
| Region | Türkiye (`countries: [TR]`), transmission network from OpenStreetMap |
| Spatial resolution | 7, 22, 50 and 81 clustered nodes (network clusters, not provinces) |
| Temporal resolution | 8736 hourly snapshots, 1 January–30 December 2018, weighted to a full year (scenarios); 8760 h for the 2023 backcast |
| Scenarios | `01_REF`, `02_NDC2030`, `03_HIGHRES`, `04_NETZERO2053`, `05_NOFOSSIL` (5 scenarios x 4 resolutions = 20 runs) |
| Validation | 2023 dispatch backcast (2023 weather, EPİAŞ 2023 demand, 2023 EPDK fleet) |
| Optimization | Linear capacity expansion + dispatch (PyPSA/linopy), Gurobi or HiGHS |

Main Türkiye-specific changes relative to PyPSA-Earth:

* power plant fleet from the EPDK licence registry (`data/custom_powerplants.csv`)
  instead of global power plant databases;
* hourly load from EPİAŞ Transparency Platform rescaled to the transmission-level
  accounting boundary (`scripts/fetch_epias.py`, `scripts/epias_to_demand_profiles.py`);
* national inventory emission factors and adjusted technology data (`data/costs.csv`);
* observed capacity-build-rate limits (`data/agg_p_nom_minmax*.csv`, `CCL` option);
* Global Wind Atlas bias correction of ERA5 onshore wind profiles
  (`scripts/apply_gwa_correction.py`);
* validation scripts for dispatch, seasonal patterns and market-clearing prices
  (`scripts/validate_*.py`);
* a few script-level corrections in upstream rules (files carrying the
  "modified for PyPSA-Türkiye" SPDX line, e.g. `scripts/add_electricity.py`,
  `scripts/solve_network.py`, `scripts/cluster_network.py`, `scripts/simplify_network.py`,
  `scripts/prepare_network.py`, `Snakefile`).

### Repository layout (Türkiye-specific parts)

| Path | Content |
|---|---|
| `config.turkey.yaml` | Base configuration used for all published runs (copy to `config.yaml`) |
| `senaryolar/n{7,22,50,81}/0{1..5}_*.yaml` | Scenario configurations (one file per resolution and scenario) |
| `senaryolar/00_VALIDATION_2023.yaml` | 2023 backcast configuration (`00_VALIDATION*.yaml` without `_2023` are superseded and kept for reference) |
| `senaryolar/solver_highs.yaml` | Optional override to use HiGHS instead of Gurobi |
| `data/costs.csv`, `data/custom_powerplants.csv`, `data/agg_p_nom_minmax*.csv` | Türkiye-specific input data |
| `scripts/sunucu_tum_kosu.sh` | One-command driver for the validation run and all 20 scenario runs |

Several in-code comments and log messages in the Türkiye-specific scripts are in Turkish.

## Installation

The 20 published scenario runs were solved on Linux with **Python 3.11** and
**Gurobi 12.0.3**. The workflow has also been tested on Ubuntu / WSL2 with
Python 3.12 and Gurobi 13.0.1. Package versions:

* `pypsa` 0.30.3, `linopy` 0.6.4, `snakemake` 7.32.4, `atlite` 0.5.0
* solver: **Gurobi** (requires a licence; free academic licences are available)
  or **HiGHS** (`highspy` 1.13.1, open source)

```bash
git clone https://github.com/PyPSA-TURKIYE/PYPSA-TURKIYE.git
cd PYPSA-TURKIYE
conda env create -f envs/environment.yaml      # or envs/linux-64.lock.yaml
conda activate pypsa-earth
pip install pypsa==0.30.3 linopy==0.6.4        # pin the versions used for the paper
pip install gurobipy==12.0.3                   # optional, needs a Gurobi licence
cp config.turkey.yaml config.yaml
```

Never commit licence files or credentials (`gurobi.lic`, `.env`, `~/.cdsapirc`);
they are listed in `.gitignore`.

## Reproducing the results

> **Warning — do not use `snakemake --forcerun` / `-F`.** Forcing a full re-evaluation
> of the DAG can trigger the upstream `clean` rule and delete all intermediate
> outputs. Always request a concrete target: `snakemake -j1 <target>`; missing
> dependencies are produced automatically. Use `snakemake -j1 -n <target>` for a dry run.

### 1. Inputs that are not in this repository

| Input | How to obtain |
|---|---|
| PyPSA-Earth data bundles, OSM network, GADM shapes, WorldPop, GEBCO, Copernicus land cover | Downloaded automatically by the workflow (`retrieve_databundle_light`, `download_osm_data`). GADM data may not be redistributed and is therefore never shipped here. |
| ERA5 cutouts (`cutouts/cutout-2018-era5.nc`, `cutout-2023-era5.nc`) | Register at the Copernicus Climate Data Store, create `~/.cdsapirc`, then set `enable: build_cutout: true` once (afterwards set it back to `false`). |
| EPİAŞ hourly consumption, generation and price data | Create a free account on the EPİAŞ Transparency Platform, copy `.env.example` to `.env` and fill in your credentials, then run `python scripts/fetch_epias.py --start 2018-01-01 --end 2024-12-31` (respect the rate limit; parquet files are written to `data/epias/`). Raw EPİAŞ data are not redistributed. |
| Global Wind Atlas 100 m wind-speed raster for Türkiye | Downloaded automatically by `scripts/apply_gwa_correction.py` from globalwindatlas.info. |
| 2023 EPDK fleet for the backcast (`custom_powerplants_2023.csv`) | Not redistributed. Prepare it from the EPDK Electricity Market Development Report 2023 (installed-capacity annex), https://www.epdk.gov.tr/Detay/Icerik/3-0-24-3/elektrikyillik-sektor-raporu, in the same format as `data/custom_powerplants.csv`; place it in `../Epdk/` or set `EPDK_DIR`. |

### 2. Build the base network and apply the national data

```bash
snakemake -j1 networks/base.nc                              # upstream data pipeline
snakemake -j1 resources/renewable_profiles/profile_onwind.nc
python scripts/apply_gwa_correction.py --year 2018 --variants p75 --fleet-csv data/custom_powerplants.csv
python scripts/apply_gwa_correction.py --year 2018 --apply p75   # overwrites the onwind profile (backup kept)
snakemake -j1 resources/demand_profiles.csv
python scripts/epias_to_demand_profiles.py --kappa-loss 1.0212   # EPİAŞ 2018 load, calibrated to TEDAŞ totals
                                                            # (default 1.0347 is the 2023 factor)
```

The GWA correction and the EPİAŞ demand overwrite modify files that Snakemake
considers upstream outputs. If `build_renewable_profile` or `build_demand_profiles`
are re-run afterwards (for example after a changed timestamp), the corrections are
lost and must be re-applied. The run scripts therefore restrict re-execution to the
rules downstream of `add_electricity`.

### 3. Solve the scenarios

A single scenario (example: reference scenario, 7 nodes):

```bash
snakemake -j1 --rerun-triggers mtime -R add_electricity \
  --configfile senaryolar/n7/01_REF.yaml -- results/networks/elec_s_7_ec_lcopt_CCL.nc
```

All other scenarios use the `Co2L-CCL` option, e.g.
`--configfile senaryolar/n50/02_NDC2030.yaml -- results/networks/elec_s_50_ec_lcopt_Co2L-CCL.nc`.
Add `--configfile senaryolar/solver_highs.yaml` to use HiGHS. HiGHS results agree
closely with Gurobi but are not bit-identical (alternative optima), and the 50- and
81-node cases are slow with HiGHS.

The complete set (2023 validation + 4 resolutions x 5 scenarios, resumable, with
run-time and memory logging) is driven by:

```bash
bash scripts/sunucu_tum_kosu.sh          # NODES="7 22", SOLVER=highs, SKIP_VALIDATION=1 are optional
```

### 4. 2023 backcast

`senaryolar/00_VALIDATION_2023.yaml` runs a dispatch-only model with the 2023 fleet,
2023 weather and EPİAŞ 2023 demand in an isolated run directory (`validation2023`).
See `scripts/run_validation_2023_portable.sh` and the `validate_*.py` scripts. Fuel
prices for 2023 are set only in this configuration; the scenario runs use the fuel
prices in `data/costs.csv`.

### Known limitations

* Hydro inflow is calibrated to observed annual generation; the artificial inflow
  shape influences convergence of the iterative solve and the hydro dispatch results.
* Wind profiles depend on the external GWA bias correction (see above).
* Results with HiGHS differ slightly from the published Gurobi runs.

## Data sources and licences

Code is licensed under **AGPL-3.0-or-later** (as PyPSA-Earth). Documentation and the
Türkiye-specific data files in `data/` are licensed under **CC-BY-4.0** unless stated
otherwise; see `REUSE.toml` and `LICENSES/`. Third-party data keep their own terms:

| Data | Licence / terms | Use in this repository |
|---|---|---|
| OpenStreetMap | ODbL, © OpenStreetMap contributors | downloaded by the workflow |
| ERA5 (Copernicus Climate Change Service) | Copernicus licence; "Generated using Copernicus Climate Change Service information" | downloaded by the user |
| GADM | free for academic/non-commercial use, redistribution not allowed | downloaded by the workflow, not redistributed |
| WorldPop | CC-BY-4.0 | downloaded by the workflow |
| GEBCO | public domain, attribution required | downloaded by the workflow |
| Global Wind Atlas (DTU / World Bank) | CC-BY-4.0 | downloaded by the script; only derived correction factors are used |
| EPİAŞ Transparency Platform | registered access; raw data not redistributed | fetched by the user |
| EPDK, TEİAŞ, TEDAŞ, ETKB, TurkStat publications | official statistics | numerical values used with attribution in `data/` and configuration comments |
| Technology cost data (`data/costs.csv`) | derived from PyPSA technology-data and upstream PyPSA-Earth data; source-specific terms apply | Türkiye-specific adjustments documented in the file |

## Citation

If you use PyPSA-Türkiye, please cite the model paper and the frameworks it builds on
(machine-readable metadata: [`CITATION.cff`](CITATION.cff)):

* Kıraç, A., Selçuklu, S. B. *PyPSA-Türkiye: An open-source energy system model for
  planning Türkiye's sustainable energy transition.* Submitted to Applied Energy, 2026.
* Kıraç, A., Selçuklu, S. B. (2026). PyPSA-Türkiye (software). Zenodo.
  https://doi.org/10.5281/zenodo.24344886
* Parzen, M., et al. (2023). PyPSA-Earth. A new global open energy system optimization
  model demonstrated in Africa. *Applied Energy*, 341, 121096.
  https://doi.org/10.1016/j.apenergy.2023.121096
* Brown, T., Hörsch, J., Schlachtberger, D. (2018). PyPSA: Python for Power System
  Analysis. *Journal of Open Research Software*, 6(1), 4. https://doi.org/10.5334/jors.188

## Funding

This work was supported by the Scientific and Technological Research Council of
Türkiye (TÜBİTAK) under the ARDEB 3501 programme, Grant No. 222M440, and by the
Erciyes University Scientific Research Projects Coordination Unit (BAP), project 15903.

## Authors

* **Assoc. Prof. Dr. Saltuk Buğra Selçuklu** — Department of Energy Systems Engineering,
  Erciyes University, Kayseri, Türkiye
  — ORCID [0000-0002-9295-3866](https://orcid.org/0000-0002-9295-3866)
  — [AVESIS](https://avesis.erciyes.edu.tr/sbselcuklu)
  — [LinkedIn](https://www.linkedin.com/in/sbselcuklu/)
  — sbselcuklu@erciyes.edu.tr
* **Abdurrahman Kıraç** (MSc student) — Department of Energy Systems Engineering,
  Erciyes University, Kayseri, Türkiye
  — ORCID [0009-0003-2843-5946](https://orcid.org/0009-0003-2843-5946)
  — [LinkedIn](https://www.linkedin.com/in/abdurrahmankirac/)
  — 4012530110@erciyes.edu.tr

PyPSA-Türkiye is built on the work of the PyPSA-Earth and PyPSA-Eur authors; see
[`docs/README_pypsa-earth.md`](docs/README_pypsa-earth.md) for the upstream project.
