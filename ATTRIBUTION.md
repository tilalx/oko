# Attribution

OKO is licensed under the **GNU Affero General Public License v3.0** (see
[`LICENSE`](LICENSE)). Part of the reason for that choice: OKO adapts code
and data from [electricitymaps-contrib](https://github.com/electricitymaps/electricitymaps-contrib),
which is itself AGPLv3, and the AGPL's network-use clause requires that
anyone adapting it — including as a hosted service — keeps the complete
source, including modifications, publicly available. That's what this
repository is.

**OKO is not affiliated with or endorsed by Electricity Maps.** It is an
independent project that reuses specific, clearly identified pieces of their
open-source parser code and published emission-factor data under the terms
of the AGPLv3.

## Code and data adapted from electricitymaps-contrib

Repository: <https://github.com/electricitymaps/electricitymaps-contrib>
License: AGPL-3.0 (confirmed via GitHub repository metadata)

| OKO file | Adapted from (electricitymaps-contrib, `master` branch) | What was taken |
|---|---|---|
| `src/oko/fetchers/entsoe.py` | `electricitymap/contrib/parsers/ENTSOE.py` | Overall request/parse structure for the ENTSO-E Transparency Platform SOAP-ish REST API (`documentType`/`processType` query parameters, XML response parsing shape), the PSR production-type code table, and the zone → EIC domain-code mapping for the zones OKO uses (10 originally, expanded to 15 for flow tracing — see below). |
| `src/oko/emissions/factors.py` | `config/defaults.yaml` (`emissionFactors.direct`, `emissionFactors.lifecycle`) and `config/zones/{ZONE}.yaml` (same two sections) for every zone in `oko.config.FLOW_TRACING_ZONES` | Direct (combustion-stage) **and** lifecycle (cradle-to-grave) CO2 emission factors in g CO2eq/kWh per generation technology (coal, gas, oil, nuclear, wind, solar, hydro, biomass, geothermal, unknown/thermal fallback), global defaults plus a per-zone override table for all 15 modeled zones (DE-LU, FR, CH, AT, CZ, PL, DK-DK1, DK-DK2, NL, BE, ES, IT-NO, HU, SI, SK). Direct overrides mostly cover coal/gas/oil/biomass (fleet composition -- lignite vs. hard coal, CCGT vs. OCGT -- makes actual per-MWh intensity diverge from the generic global default); lifecycle overrides additionally cover hydro/nuclear/solar/wind, since lifecycle factors for those are *not* ~0 the way direct ones are (construction/fuel-cycle emissions dominate). Latest published values as of the September 2026 fetch. |
| `src/oko/config.py` (`EXCHANGE_BORDERS`) | `config/exchanges/*.yaml` file list | Only the **topology** — which zone pairs ENTSO-E publishes physical cross-border flow data for — reused as a plain list of pairs, filtered to zones in OKO's flow-tracing network. The per-border file *contents* (lonlat, parser assignments, rotation) were not used. |
| `src/oko/api/static/zones.geojson` | `geo/world.geojson` | Zone boundary polygons for the web UI's map, filtered down to OKO's 15 published zones (Germany + Luxembourg merged into one `DE-LU` feature, matching the ENTSO-E bidding zone). ~2.5 MB of global data reduced to ~52 KB. The repo also carries an `LICENSE_MIT.txt` covering contributions before a 2023 cutoff commit, but since this file's provenance relative to that cutoff isn't verifiable from here, it's treated as AGPLv3 like every other adapted asset in this table. |

OKO's PSR-type → generation-category grouping in `factors.py` follows the
same grouping as `ENTSOE_PARAMETER_GROUPS` in the file above (e.g. ENTSO-E
codes B02/B05/B07/B08 → "coal"), reproduced because the mapping itself is a
factual correspondence between ENTSO-E's fixed code list and generation
technology, not creative expression — but is listed here for transparency
since the grouping choice originates from that file.

**Not adapted from electricitymaps-contrib: the flow-tracing engine**
(`src/oko/emissions/flow_tracing.py`). Their public repository was checked
directly — `electricitymap/contrib/lib/` contains only data models
(typed containers for what a parser fetched), and nothing named or shaped
like a flow-tracing/carbon-intensity computation exists anywhere in the
~1,850-file tree. Electricity Maps' actual flow-tracing engine runs in
their proprietary backend, not in this open-source repository, so there
was nothing to adopt. `flow_tracing.py` implements the published
"proportional sharing" / average-participation method (Bialek 1996), an
OKO-original implementation of established academic methodology — see
that module's docstring for the full derivation.

Everything downstream of the fetch/parse layer — the flow-tracing engine,
feature engineering, the forecasting model, the export schema, the
pipeline orchestration, the Jenkins/Docker setup — is original to OKO.

## Data sources

| Source | License | Usage | Auth required |
|---|---|---|---|
| [ENTSO-E Transparency Platform](https://transparency.entsoe.eu/) | CC-BY 4.0 | Production mix and cross-border physical flows for DE-LU and neighbouring bidding zones | Free API token, server-side only (`ENTSOE_TOKEN`) |
| [NOAA GFS 0.25°](https://www.nco.ncep.noaa.gov/pmb/products/gfs/) | US Government work, public domain | 10 m wind speed and downward shortwave radiation (DSWRF) as forecast model features | None |
| [energy-charts.info](https://www.energy-charts.info/) (Fraunhofer ISE) | CC-BY 4.0 | Reference/validation data for backtesting only — never used as a live-forecast input | None |

Every additional data source considered before integration must be checked
against these same two constraints: an open license (attribution is fine,
paywalled/registration-gated is not), and no account or payment required on
the **consumer** side of OKO's own `/forecast` output.

## Third-party frontend libraries

The web UI (`src/oko/api/static/index.html`) loads, from a CDN at
`cdn.jsdelivr.net`, at runtime (not bundled into this repository, under
each library's own separate license):

- [Chart.js](https://www.chartjs.org/) (MIT license) — the forecast chart.
- [Leaflet](https://leafletjs.com/) (BSD-2-Clause license) — the zone map.

The map's basemap tiles are Esri's ["World Shaded Relief"](https://www.arcgis.com/home/item.html?id=9c5a3b3260a44f79920a7a3adde63b53)
raster tiles — free, keyless, credited in the map's attribution control —
loaded live from `server.arcgisonline.com`, not stored in this repository.
(CARTO's free raster tiles, tried first, turned out to require an API key;
plain OpenStreetMap tiles, tried next, bake in place-name text at every
zoom level; Esri's "Light Gray Base" layer, tried after that, still bakes
in country/sea names. Shaded-relief tiles carry no text at any zoom —
verified directly against the tile service — and are keyless like OSM,
closer to the reference UI this map is styled after.)

## Consumer-facing attribution

Every exported forecast (`forecast_de.json`, served as `de.json`; every
other published zone's `forecast_{ZONE}.json`, served as `/{zone}.json`
— see README "Deployment") carries an `attribution` array and a `source`
link back to this repository, per the API schema in the README. This is
intentional: the attribution obligation travels with the data, not just
with the source code.
