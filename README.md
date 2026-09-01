# OKO

Self-hosted, open-source **hourly CO2 intensity forecast for a 48-zone
EU/EEA continental-synchronous-area network** (g CO2eq/kWh, 5-day /
120-hour horizon), centered on DE-LU. Built primarily for [evcc](https://evcc.io)
custom-tariff CO2-optimized charging, but the output is a plain, keyless
JSON file anyone can consume.

> **Status: MVP complete, multi-zone.** All six phases below have landed,
> plus a Phase 6 expansion from DE-LU-only to every zone in the
> flow-tracing network. Real historical training/backtesting still needs a
> live `ENTSOE_TOKEN` and a running Jenkins instance to fully exercise —
> see the phase commits for exactly what was and wasn't verified against
> live services during development.

## Why AGPLv3

OKO adapts parser logic and published emission-factor data from
[electricitymaps-contrib](https://github.com/electricitymaps/electricitymaps-contrib)
(AGPLv3). Under the AGPL's network-use clause, that means this entire
repository — including all OKO-original code — must stay AGPLv3 and publicly
available. See [`ATTRIBUTION.md`](ATTRIBUTION.md) for exactly what was
adapted from where. OKO is **not** affiliated with or endorsed by
Electricity Maps; it is an independent project that reuses specific,
attributed pieces of their open-source code under license.

## What you get

- `GET https://<your-domain>/de.json` — DE-LU's forecast as JSON, updated
  hourly, no account, no API key.
- `GET https://<your-domain>/{zone}.json` — the same, for every other
  zone in the flow-tracing network (e.g. `/FR.json`, `/DK-DK1.json`) —
  see `GET /zones` for the full list and which ones currently have data.
- `GET https://<your-domain>/api/evcc/co2` (DE-LU) and
  `/api/evcc/co2/{zone}` (any other zone) — the forecast reshaped into
  evcc's custom co2-tariff rate format, ready to drop into `tariffs.co2`
  — see "evcc custom tariff integration" below.
- `GET https://<your-domain>/history/{zone}` — recent raw observed
  history (default 48h, capped at 30 days) for a zone, including which
  method (multi-hop flow trace vs. one-hop fallback) produced each hour.
- `GET https://<your-domain>/exchanges.json` — the network's latest
  cross-border physical flow snapshot (net MW per border, see
  `oko.config.EXCHANGE_BORDERS`), refreshed every pipeline run.
- `GET https://<your-domain>/` — a small web UI: an interactive map (all
  48 zones, colored by current intensity, click a zone to select it) plus
  a chart for the selected zone, kept in sync with a dropdown.
- `GET https://<your-domain>/openapi.json` / `/docs` — a real,
  fully-typed OpenAPI spec / Swagger UI for every endpoint above.
- All served by one small self-hosted FastAPI container (see below)
  behind your own reverse proxy — you choose and own the domain.
- 120 hourly forecast points (5 days) per zone, computed via multi-hop
  flow tracing over the same 48-zone network (see "Carbon intensity
  calculation" below), with a coarse confidence label that degrades for
  the later days of the horizon, both direct and lifecycle emission
  intensities, and the zone's current (non-forecast) power mix.

See [API schema](#api-schema) below for the exact shape.

## Architecture (MVP)

```
Jenkins (self-hosted, hourly cron trigger)
  -> docker build (multi-stage: lint / test / runtime / serve)
  -> run pipeline in container ("runtime" target):
       fetch recent history (ENTSO-E: production for a 48-zone network,
       per-zone load, cross-border exchange for 96 borders -- ~49h rolling window)
       -> flow-tracing engine (multi-hop carbon-intensity calculation
          across the whole network in one linear solve per hour --
          direct AND lifecycle emissions together -- one-hop fallback
          if numerically unstable)
       -> per zone: upsert into SQLite history (data/oko.sqlite3,
          accumulates hour by hour) -> retrain that zone's direct model
          (and lifecycle model, once it has its own history) on
          everything accumulated so far -> fetch that zone's NOAA GFS
          5-day forecast, predict -> export forecast_{ZONE}.json (to a
          volume shared with "serve"); DE-LU keeps the legacy
          forecast_de.json name/path
  -> long-lived "serve" container (FastAPI, "serve" target): reads each
     zone's export file live, serves /de.json, /{zone}.json,
     /api/evcc/co2(/{zone}), /history/{zone}, /zones, /openapi.json,
     /docs, and a small web UI
  -> your existing reverse proxy (Caddy/Traefik/nginx) -> public internet
```

Jenkins runs inside a private homelab and only ever talks to ENTSO-E, NOAA,
and (for backtesting) energy-charts.info outbound. `oko-serve` is the one
piece that's publicly reachable: one small container (`src/oko/api/`), no
database, stateless (it only ever reads the export file), no auth to get
wrong — the "public, keyless" constraint applies to the whole API surface,
not just `/de.json`.

While a data source is unreachable (e.g. an ENTSO-E outage), `python -m
oko.mockdata` generates a synthetic `forecast_de.json` in the same schema
for local development — see "Local development" below. It's a dev-only
tool, never wired into the hourly pipeline.

### Bootstrap behaviour (first ~2 weeks after a fresh install)

There's no offline training step and no bundled pre-trained model. Every
hourly run fetches a small, cheap rolling window of recent ENTSO-E history,
accumulates it into `data/oko.sqlite3`, and retrains fresh each time —
simple, and cheap enough at this data volume (a LightGBM fit over a few
thousand rows is a sub-second operation) to not need incremental-model
complexity. **Each zone bootstraps independently**, needing its own 336
accumulated hours (14 days) before it produces a forecast; a zone's
lifecycle-intensity model bootstraps separately again on top of that
(`forecast[].value_lifecycle` stays `null` for a zone until its lifecycle
model clears the same threshold — this also means every zone reset to
`null` lifecycle values the hour this feature shipped, since existing
history rows have no lifecycle value to train on yet).

DE-LU is treated as the bellwether zone Jenkins' publish gate keys off
of: while *it* is bootstrapping (or its NOAA fetch fails entirely), the
whole run stops and produces no exports at all, so every zone's files
stay consistent as of the same last successful run — this is expected,
not a failure; the Jenkins `Publish Dataset` stage treats a missing
`forecast_de.json` as a no-op (nothing gets committed to `oko-dataset`,
so `oko-serve` just keeps serving whatever it last pulled), not a build
failure. A *non*-DE-LU zone's own bootstrap or NOAA failure only skips
that one zone's export for the run — the other zones (DE-LU included)
are unaffected. In Docker Compose, `data/` lives in the `oko-data` named
volume so it survives restarts (now scoped to trained models only, see
"Deployment" — the history DB itself moved to `oko-dataset`); in
Jenkins, see the `oko-history-data` Docker volume mounted in the
`Publish Dataset` stage.

## Data sources

| Source | License | Role |
|---|---|---|
| [ENTSO-E Transparency Platform](https://transparency.entsoe.eu/) | CC-BY 4.0 | Production mix + cross-border flows, 15-zone flow-tracing network (see below) |
| [NOAA GFS 0.25°](https://www.nco.ncep.noaa.gov/pmb/products/gfs/) | Public domain | Wind (10 m) + solar radiation (DSWRF) forecast features, via NOAA's public S3/NODD mirror (anonymous, unrate-limited byte-range GRIB2 subsetting) |
| [energy-charts.info](https://www.energy-charts.info/) (Fraunhofer ISE) | CC-BY 4.0 | Backtesting reference only, never a live-forecast input |

Full attribution detail: [`ATTRIBUTION.md`](ATTRIBUTION.md).

## Carbon intensity calculation

Every published zone's consumption-based carbon intensity is computed by
multi-hop flow tracing — the "proportional sharing" / average-participation
method (Bialek 1996; the real-time-accounting adaptation of the same
method, Tranberg et al. 2019, is what Electricity Maps' own published
methodology cites) — across a 15-zone network: DE-LU, its 9 direct
ENTSO-E neighbours, and 5 second-hop zones (`ES`, `IT-NO`, `HU`, `SI`,
`SK`) added so genuine multi-hop effects are captured (e.g. Austria
importing from Italy, which itself imports from Switzerland). One linear
solve per hour produces every zone's result simultaneously (not run
separately per zone), both **direct** (combustion-stage) and **lifecycle**
(cradle-to-grave) intensity together. 28 real cross-border pairs among
those zones (`oko.config.EXCHANGE_BORDERS`, sourced from
electricitymaps-contrib's exchange config file list — topology only, see
`ATTRIBUTION.md`) are fetched every run.

OKO publishes a forecast for **all 15** network zones, not just DE-LU —
each with its own NOAA GFS weather fetch (`oko.config.ZONE_BBOXES`), its
own accumulated history, and its own model (see "Bootstrap behaviour"
above for what that means per zone). DE-LU keeps its original
`forecast_de.json` / `/de.json` naming for backward compatibility; every
other zone follows `forecast_{ZONE}.json` / `/{zone}.json`.

This is **not** the same "simplified one-hop import correction" originally
scoped for the MVP — it's a deliberate, explicitly-requested revision of
that decision (see `src/oko/emissions/flow_tracing.py`'s docstring for the
full derivation, and `oko.emissions.calculator` for the one-hop method
kept as an automatic fallback when a given hour's linear system is
numerically singular). It is still **not** full pan-European flow tracing:
the 15-zone network is a bounded "2-hop continental core" around DE-LU,
not the complete ~35-zone ENTSO-E area — Nordic/Baltic/Iberian reaches
beyond it are a documented scope boundary, expandable later by extending
`FLOW_TRACING_ZONES` / `EXCHANGE_BORDERS` in `oko/config.py`.

electricitymaps-contrib does **not** publish its own flow-tracing engine
in the open-source repository (only parsers and static config) — this was
verified directly before implementing, not assumed — so `flow_tracing.py`
is an OKO-original implementation of the published academic method, not
adapted code.

## Local development

Requires [`uv`](https://docs.astral.sh/uv/) and Docker.

```bash
uv sync --group dev --extra api
uv run pytest
uv run ruff check .
uv run mypy src
```

Run the full pipeline locally in a container (needs a free ENTSO-E token,
see below):

```bash
export ENTSOE_TOKEN=your-token-here
docker compose up --build oko-pipeline
# output/forecast_de.json once enough history has accumulated -- see
# "Bootstrap behaviour" above for what to expect on a fresh install.
```

If ENTSO-E is unreachable (or you just want a `forecast_de.json` to
develop the API/web UI against without waiting on real accumulated
history), generate mock data instead — same schema, clearly tagged as
synthetic via `model_version`, never used in production:

```bash
uv run python -m oko.mockdata --out output/forecast_de.json
# or every published zone at once, e.g. to exercise the web UI's zone
# picker / GET /zones / GET /{zone}.json locally:
uv run python -m oko.mockdata --all-zones --out-dir output
```

Lint/test-only container targets (used by CI, runnable locally too):

```bash
docker compose --profile ci build lint test
docker compose --profile ci run --rm lint
docker compose --profile ci run --rm test
```

Try the API/web UI locally once a `forecast_de.json` exists (via
`oko-pipeline` or `oko.mockdata` above):

```bash
docker compose --profile serve build serve
docker compose --profile serve up serve
open http://localhost:8090/          # web UI, with a zone picker
open http://localhost:8090/docs      # interactive OpenAPI/Swagger UI
curl http://localhost:8090/de.json
curl http://localhost:8090/FR.json   # any other published zone
curl http://localhost:8090/zones     # which zones currently have data
curl http://localhost:8090/history/DE-LU
curl http://localhost:8090/api/evcc/co2
```

If `de.json`/`api/evcc/co2` return `503` even though `output/forecast_de.json`
exists, the `serve` container (running as the non-root `oko` user) likely
can't read the bind-mounted file — `chmod -R a+rX output/` fixes the usual
case; on SELinux hosts (e.g. Fedora with Podman), add a `:z`/`:Z` suffix to
the volume mount instead.

Or run it directly on the host without Docker:

```bash
EXPORT_PATH=output/forecast_de.json uv run uvicorn oko.api.app:app --reload
```

### Frontend development

The web UI (`frontend/`) is a Vite + Svelte 5 + TypeScript app styled with
Tailwind CSS and shadcn-svelte, using Leaflet for the map and Chart.js for
the forecast chart. It builds straight into `src/oko/api/app.py`'s
`StaticFiles` mount, so there's no separate deploy step -- `uv run uvicorn
oko.api.app:app` already serves whatever's currently built there.

```bash
cd frontend
npm install
npm run build   # writes to ../src/oko/api/static -- do this once before
                 # running the backend directly (see above)
```

For iterating on the UI itself, run the Vite dev server (hot reload,
proxies API requests to a backend running on `:8000`) alongside the
backend from the previous section:

```bash
EXPORT_PATH=output/forecast_de.json uv run uvicorn oko.api.app:app --reload &
cd frontend && npm run dev   # opens on :5173, proxying /de.json, /zones, etc.
```

`npm run check` type-checks the frontend (`svelte-check` + `tsc`). The
`serve` Docker target (and thus `docker compose --profile serve` above)
builds the frontend itself in a dedicated `frontend-builder` stage -- no
local `npm run build` needed for that path.

### Getting an `ENTSOE_TOKEN`

1. Register a free account at <https://transparency.entsoe.eu/>.
2. Request API access under "My Account Settings" -> "Web API Security
   Token" (usually granted automatically).
3. Set it as `ENTSOE_TOKEN`, either in a local `.env` file (untracked, see
   `.gitignore`) or as a Jenkins credential (see below) — **never commit
   it**.

This is the only required secret for the whole project.

## Jenkins setup

0. Install the **GitHub Checks** Jenkins plugin — the `Jenkinsfile` reports
   each stage's status via `publishChecks`, shown as a GitHub check run on
   commits/PRs rather than only in Jenkins' own UI.
1. Create a **Multibranch Pipeline** job pointed at this repository, with a
   GitHub webhook so PR/branch pushes trigger `Lint & Type-Check` + `Test`
   automatically, and so a merge to `main` triggers `Build Server Image`
   (see "Deployment" below — it only builds the image, it doesn't deploy).
2. Add two credentials in the Jenkins credential store (not in the repo):
   - `oko-entsoe-token` — Secret text, your ENTSO-E token.
   - `oko-dataset-deploy-key` — SSH Username with private key, an SSH
     deploy key (write access) for the separate `oko-dataset` repo (see
     "Deployment" below). Generate a dedicated keypair
     (`ssh-keygen -t ed25519 -f oko-dataset-key -N ""`), add the public
     half as a deploy key with write access on `oko-dataset` on GitHub,
     and the private half as this credential.
3. The `Jenkinsfile` restricts `Publish Dataset` to `branch 'main'` builds
   triggered by the hourly `cron('H * * * *')` trigger
   (`triggeredBy 'TimerTrigger'`) — PR builds only ever run lint and
   tests, never the real fetch/forecast/publish path. `Build Server Image`
   runs the opposite way: `branch 'main'` builds *not* triggered by the
   timer, i.e. real pushes/merges — it builds the `oko-serve` image, not
   data, and never runs/deploys a container (see "Deployment" below for
   the manual `docker run` step).

## Deployment

Publishing data and deploying the server are two independent, decoupled
paths — they used to be one hourly Jenkins job that did both; now a CI
task produces and publishes data, and prod pulls it on its own schedule.

**Publishing** (`Publish Dataset`, hourly): runs the pipeline and writes
its output — each zone's forecast JSON, `exchanges.json`, and the
`intensity_history` SQLite DB — straight into a clone of a separate
GitHub repo, **[`oko-dataset`](https://github.com/tilalx/oko-dataset)**,
which holds only published data (no code). Every run overwrites those
files in place and commits — git's own history *is* the versioning: `git
log`, `git revert <sha>`, `git show <sha>:forecast_de.json` all work
against it directly, no extra tooling. A run that produces nothing new
(bootstrap, or every fetch failing that hour) makes no commit.

**Prod host setup** (once, independent of Jenkins):

```bash
git clone git@github.com:tilalx/oko-dataset.git /data/oko-dataset
# cron, staggered off the hourly publish so a pull usually lands on a
# fresh commit rather than racing it:
echo '*/5 * * * *  git -C /data/oko-dataset pull --ff-only --quiet origin main' \
  | crontab -
```

`oko-serve` (see below) mounts `/data/oko-dataset` read-only and reads
`EXPORT_PATH`/`SQLITE_PATH` from it directly — it never needs restarting
just because new data landed, the next `git pull` alone is enough. A
request that lands mid-pull can in principle see a moment of
inconsistency between files (or a transient SQLite read error) — rare at
this project's traffic level, and self-heals on the next request.
Rolling back bad data is `git revert <sha> --no-edit && git push` against
`oko-dataset`; the next cron pull (≤5 min) serves the reverted data.

**Code builds** (`Build Server Image`, on `main` pushes/merges): builds
the FastAPI query layer (`Dockerfile`'s `serve` target, `src/oko/api/`)
into local images `oko-serve:latest` / `oko-serve:<build-number>` on the
Jenkins/Docker host. CI's job ends there — it does **not** run or
redeploy a container; running `oko-serve` is a separate, manual step
(below), so a serving host stays under your control independent of
every code push.

**Running `oko-serve`** (manual, once — then only when you choose to
pick up a new image build):

```bash
docker network create oko-net 2>/dev/null || true   # once
docker rm -f oko-serve 2>/dev/null || true
docker run -d \
  --name oko-serve \
  --restart unless-stopped \
  --network oko-net \
  -e EXPORT_PATH=/data/oko-dataset/forecast_de.json \
  -e SQLITE_PATH=/data/oko-dataset/oko.sqlite3 \
  -v /data/oko-dataset:/data/oko-dataset:ro \
  oko-serve:latest
```

If your reverse proxy lives on the bare host instead of Docker, drop
`--network oko-net` and add `-p 127.0.0.1:8090:8000` instead, then point
the proxy at `127.0.0.1:8090`. `oko-serve` mounts the same
`/data/oko-dataset` clone from "Prod host setup" above — no rebuild or
redeploy needed as new data lands there, only for a new code build. It
exposes:

- `GET /de.json` / `GET /{zone}.json` — the forecast, with permissive CORS.
- `GET /api/evcc/co2` / `GET /api/evcc/co2/{zone}` — the forecast,
  reshaped for evcc (see below).
- `GET /history/{zone}` — recent raw observed history for a zone.
- `GET /exchanges.json` — the network's latest cross-border flow snapshot.
- `GET /zones` — every published zone and whether it has data yet.
- `GET /` — a small web UI charting the forecast, with a zone picker.
- `GET /openapi.json` / `GET /docs` — OpenAPI spec / Swagger UI.
- `GET /healthz` — plain `200 ok`, for your proxy's health checks.

Point your existing reverse proxy at container `oko-serve`, port `8000`
(not 80 — the container runs as a non-root user, which can't bind a
privileged port), on the `oko-net` network — for example a Caddy
`reverse_proxy oko-serve:8000` block, or a Traefik label-based route,
whichever you already run.

Because the container reads each zone's export file live, a run that
produces no new forecast for a given zone (bootstrap, or that zone's
NOAA/ENTSO-E fetch failing that hour) has no effect on what's served for
it — the previous file is still sitting in the mount, so that zone's
endpoint never goes dark, it just serves slightly stale data until the
next successful run. If a zone has *never* produced a forecast yet
(fresh install, or that zone bootstrapping independently of DE-LU), its
`/{zone}.json`, `/api/evcc/co2/{zone}`, and `/history/{zone}` return
`503`/empty until the first one lands — check `GET /zones` for current
availability. `/exchanges.json` follows the same pattern: `503` until at
least one border fetch has ever succeeded.

## API schema

`GET /de.json` (DE-LU) and `GET /{zone}.json` (every other published
zone, e.g. `/FR.json`) return the same shape — see `/openapi.json` or
`/docs` for the fully-typed version (`src/oko/api/schemas.py`):

```json
{
  "zone": "DE",
  "generated_at": "2026-09-01T06:00:00Z",
  "model_version": "0.1.0",
  "unit": "gCO2eq/kWh",
  "training_rows": 2016,
  "current": {
    "timestamp": "2026-09-01T18:00:00Z",
    "power_breakdown_percent": { "coal": 12.3, "gas": 8.1, "wind": 30.2 },
    "renewable_percent": 45.6,
    "fossil_free_percent": 61.2,
    "emissions_breakdown_percent": { "coal": 68.4, "gas": 31.6 }
  },
  "forecast": [
    { "timestamp": "2026-09-01T07:00:00Z", "value": 342, "value_lifecycle": 410, "confidence": "high" }
  ],
  "attribution": [
    "ENTSO-E Transparency Platform (CC-BY 4.0)",
    "NOAA GFS",
    "Emission factors adapted from electricitymaps-contrib (AGPLv3)"
  ],
  "source": "https://github.com/tilalx/oko"
}
```

- `confidence` is `high` for day 1, `medium` for days 2-3, `low` for days
  4-5 of the horizon — see `src/oko/forecast/model.py`.
- `training_rows` is that zone's accumulated direct-model training rows —
  an honest, coarse model-maturity signal (see "Bootstrap behaviour").
- `current` is the zone's most recent *real, observed* hour (not a
  forecast point) — `null` if that zone has never had a usable production
  fetch yet. There's no per-forecast-point power breakdown: the model
  predicts a single intensity scalar, not a per-source mix.
  `power_breakdown_percent` weights each category by its share of MW;
  `emissions_breakdown_percent` weights the same categories by their
  share of that hour's actual gCO2 output instead — a zero-direct-factor
  category (wind, solar, hydro, nuclear, biomass, geothermal) can
  dominate the first and be entirely absent from the second.
- `value_lifecycle` is `null` until that zone's lifecycle model has
  bootstrapped (see "Bootstrap behaviour").
- `GET /history/{zone}?hours=N` (default 48, capped at 720) returns that
  zone's recent raw observed values, each tagged with which method
  produced it (`"flow_trace"` or `"one_hop_fallback"`).
- `GET /exchanges.json` returns the network's latest cross-border flow
  snapshot -- one entry per border in `oko.config.EXCHANGE_BORDERS` with
  at least one record that run, each border's timestamp picked
  independently (a border whose fetch partially failed that run simply
  reports an older hour rather than being dropped):
  ```json
  {
    "generated_at": "2026-09-01T18:00:00Z",
    "exchanges": [
      { "zone_from": "AT", "zone_to": "DE-LU", "timestamp": "2026-09-01T18:00:00Z", "net_flow_mw": 340 }
    ],
    "source": "https://github.com/tilalx/oko"
  }
  ```
  `net_flow_mw` is positive for flow from `zone_from` to `zone_to`,
  negative for the reverse; zone keys are OKO's internal keys (e.g.
  `"DE-LU"`, not the simplified `"DE"` used in `/{zone}.json`'s `"zone"`
  field).
- `GET /zones` lists every zone OKO publishes a forecast for, and
  whether each currently has data.

### evcc custom tariff integration

`GET /api/evcc/co2` (DE-LU) / `GET /api/evcc/co2/{zone}` (any other
zone) returns a bare JSON array already shaped for evcc's [custom
tariff](https://docs.evcc.io/en/tariffs/) `forecast` plugin — no `jq`
reshaping needed:

```json
[
  { "start": "2026-09-01T07:00:00Z", "end": "2026-09-01T08:00:00Z", "value": 342 }
]
```

```yaml
tariffs:
  currency: EUR
  co2:
    type: custom
    tariff: co2
    features: ["cacheable"]
    forecast:
      source: http
      uri: https://<your-domain>/api/evcc/co2
```

## Disclaimer

This forecast is a statistical estimate derived from public grid and
weather data. It is **not** a guarantee of actual grid carbon intensity and
should not be relied on for safety-, compliance-, or billing-critical
decisions.

## Phasen (implementation progress)

- [x] Phase 0 — Grundgerüst (repo scaffold, Docker, Jenkinsfile, empty test suite)
- [x] Phase 1 — Fetcher (ENTSO-E, NOAA GFS, energy-charts)
- [x] Phase 2 — Emissionsberechnung
- [x] Phase 3 — Forecast-Modell
- [x] Phase 4 — Export & Deployment
- [x] Phase 5 — FastAPI query layer (web UI + evcc endpoint)
- [x] Phase 6 — Typed OpenAPI spec, power breakdown, data-quality flags,
      `/history`, lifecycle emission factors, multi-zone expansion (all
      15 `FLOW_TRACING_ZONES`, not just DE-LU)

## License

AGPLv3 — see [`LICENSE`](LICENSE). Source: <https://github.com/tilalx/oko>.
