# Kendeda Building IoT Pipeline

A simulated telemetry pipeline for a smart building (modeled on Georgia Tech's
Kendeda Building) that ingests sensor data of **unknown format** and
automatically classifies and stores it — without a human having to define
every sensor field ahead of time.

The core idea: real sensor fleets are messy. New hardware gets added, field
names vary, nobody documents everything up front. This project proves a
pipeline can stay useful anyway — it teaches itself what each measurement is
from the data alone, and only asks a human to step in when something
genuinely doesn't add up.

## Architecture

```
sensor_simulator.py  --stdout-->  sensor_normalizer.py  --insert-->  TimescaleDB
   (fake sensor fleet)         (classifies + stores)          (time-series store)
                                                                       |
                                                                    pgAdmin
                                                                (browser viewer)
```

- **[src/sensor_simulator.py](src/sensor_simulator.py)** — stands in for ~35 real
  sensors across 12 sensor types (electrical meters, solar, HVAC, air quality,
  water, weather, lighting, battery storage, plug loads). Emits one JSON
  payload per sensor per second, at 3600x speed (1 real second = 1 simulated
  hour). Randomly injects realistic failure modes per sensor type (stuck
  dampers, CO2 surges, inverter faults, thermal runaway, etc.) so the
  downstream logic has real anomalies to contend with.

- **[src/sensor_normalizer.py](src/sensor_normalizer.py)** — reads the JSON
  stream, flattens each payload into one row per metric, and classifies every
  metric into a telemetry class **without being told the schema in advance**.
  See [Classification](#how-classification-works) below.

- **[src/database.py](src/database.py)** — thin PostgreSQL/TimescaleDB access
  layer. Self-healing: auto-registers unknown sensors and metrics so writes
  never fail on a foreign-key constraint. Degrades to print-only mode if the
  database is unreachable — the pipeline never goes down because storage
  isn't available.

- **[src/START.py](src/START.py)** — one-command launcher. Runs the simulator
  piped into the normalizer, cross-platform, with clean shutdown.

- **[sql/001_schema.sql](sql/001_schema.sql)** — the TimescaleDB schema (see
  [Schema](#schema)).

- **[sql/002_seed.sql](sql/002_seed.sql)** — seeds the telemetry classes and
  adds classification-tracking columns to the registry.

- **[sql/docker-compose.yml](sql/docker-compose.yml)** — spins up TimescaleDB
  and pgAdmin (a browser-based DB viewer) with zero manual configuration.

## How classification works

Every reading needs to be tagged with a **telemetry class**
(`ANALOG_SIGNED`, `ANALOG_UNSIGNED`, `COUNTER`, `BINARY`) describing how the
*number behaves* — can it go negative, does it only ever increase, is it
strictly 0/1. This is metadata about shape, not meaning; the metric name and
sensor ID carry what the number physically represents.

Classification runs in three layers, escalating from most to least confident:

1. **Name inference** — the metric's field name is checked against a keyword
   list (`_kwh` → `COUNTER`, `voltage_` → `ANALOG_UNSIGNED`, `is_` → `BINARY`,
   etc.). Instant, but just a guess.
2. **Behavioral inference** — in parallel, each `(sensor_id, metric_name)`
   stream is watched over time. Does it ever go negative? Does it only ever
   increase? This confirms or resolves what the name alone can't.
3. **Conflict detection** — if the name says one thing and the observed
   behavior contradicts it (e.g. a field named like a counter that
   decreases), it's flagged `CONFLICT` and set aside. This is the only case
   that needs a human — either the keyword list needs extending, or the
   sensor field should be renamed.

A metric's classification status therefore lands in one of:
`VERIFIED` (human-entered) → `CONFIRMED` / `BEHAVIOR_INFERRED` (machine,
high confidence) → `NAME_INFERRED` / `PENDING` (machine, still gathering
evidence) → `CONFLICT` (needs a human).

Nothing is ever dropped — even data with no confident classification yet is
stored, just flagged, so the registry can catch up without losing history.

## Project structure

```
kenneda_proj/
├── src/
│   ├── sensor_simulator.py    # fake sensor fleet, 12 sensor types
│   ├── sensor_normalizer.py   # classification + stream processing
│   ├── database.py            # DB access layer, self-healing registry
│   └── START.py               # one-command pipeline launcher
├── sql/
│   ├── 001_schema.sql         # hypertable + dictionary tables
│   ├── 002_seed.sql           # telemetry class seed data
│   ├── docker-compose.yml     # TimescaleDB + pgAdmin
│   └── pgadmin/servers.json   # pre-registered DB connection for pgAdmin
└── requirements.txt
```

## Getting started

Requires [Docker Desktop](https://www.docker.com/products/docker-desktop/)
and Python 3.11+.

```bash
# 1. Start the database + browser-based viewer
docker compose -f sql/docker-compose.yml up -d

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Run the pipeline (Ctrl+C to stop)
python src/START.py
```

If PostgreSQL isn't reachable (e.g. `psycopg2` isn't installed, or the
container isn't running), the pipeline still runs — it just prints
classified readings to the console instead of storing them.

## Viewing the data

Open **http://localhost:5050** — pgAdmin comes pre-connected to the database
(no manual "Add Server" needed). First connection asks for the database
password: `secure_password_here`.

Useful tables to look at:
- `sensor_telemetry` — the raw readings (the hypertable)
- `metric_registry` — live classification state per metric (`CONFIRMED`,
  `BEHAVIOR_INFERRED`, `CONFLICT`, etc.) — the most interesting one to watch

Or from a terminal:
```bash
docker exec -it kendeda_timescaledb psql -U pipeline_service_account -d kendeda_iot_db
```

## Schema

Two-layer design: small **dictionary** tables describe the world once;
a large **fact** table (the hypertable) stores nothing but numbers and
references back to the dictionary.

```
telemetry_classes  (class_name, handling_logic)
        ▲
        │
metric_registry  (metric_name, class_name, classification_status, ...)
        ▲
        │
sensor_telemetry  (time, sensor_id, metric_name, reading_value)   ← hypertable
        │
        ▼
sensor_assets  (sensor_id, building_name, floor_level, zone_name)
```

`sensor_telemetry` is converted to a TimescaleDB hypertable, which
transparently partitions it into time-based chunks — recent writes and
recent-range queries only touch the current chunk, not the entire history.

Storage cost scales with total number of individual measurements, not with
how many fields any one sensor type has — every row has the same shape
`(time, sensor_id, metric_name, reading_value)` regardless of whether the
originating sensor reports 1 field or 9. There's no wide table with
per-sensor-type columns to keep in sync, and no wasted NULL padding for
sensors that don't report a given metric.

## Design notes

- **Never block on missing infrastructure.** No database, no registry entry,
  no known schema — the pipeline degrades gracefully at every layer instead
  of crashing or losing data.
- **Classification is additive, not destructive.** The telemetry class is a
  label attached to a metric name once, in the dictionary — it never
  replaces or reduces the stored reading.
- **A human's job shrinks over time.** Only `CONFLICT` rows need attention;
  everything else self-resolves as more data flows through.
