"""
Database access layer for the Kendeda pipeline.

Wraps psycopg2 and gives the normalizer a small, safe API for:
  - ensuring a sensor exists in sensor_assets   (auto-registered placeholder)
  - ensuring a metric exists in metric_registry  (self-healing registry)
  - batch-inserting readings into sensor_telemetry (the hypertable)

Design goal: NEVER take the pipeline down. If PostgreSQL is unreachable the
writer degrades to "disabled" mode and the normalizer keeps running in
print-only mode, exactly mirroring the project's core thesis that the data
path must survive imperfect / missing infrastructure.
"""

import sys

try:
    import psycopg2
    from psycopg2.extras import execute_values
    _PSYCOPG2_AVAILABLE = True
except ImportError:
    _PSYCOPG2_AVAILABLE = False

# ---------------------------------------------------------------------------
# DATABASE CONFIGURATION
# (In production these come from environment variables / a secrets manager.)
# Matches docker-compose.yml so `python START.py` works against the container.
# ---------------------------------------------------------------------------
DB_CONFIG = {
    "dbname": "kendeda_iot_db",
    "user": "pipeline_service_account",
    "password": "secure_password_here",
    "host": "localhost",
    "port": "5432",
}


class DatabaseWriter:
    def __init__(self, config=None):
        self.config = config or DB_CONFIG
        self.conn = None
        self.enabled = False

        # In-memory mirrors so we only hit the DB when something actually
        # changes, instead of on every single reading.
        self._known_assets = set()
        self._known_metrics = {}   # metric_name -> last written (class, status)

    # ---- lifecycle --------------------------------------------------------
    def connect(self):
        if not _PSYCOPG2_AVAILABLE:
            print("  psycopg2 not installed -> running in PRINT-ONLY mode "
                  "(no database writes). `pip install psycopg2-binary` to enable.",
                  flush=True)
            return False
        try:
            self.conn = psycopg2.connect(**self.config)
            self.conn.autocommit = True
            self.enabled = True
            print(" Database connected. Telemetry will be persisted.", flush=True)
            return True
        except Exception as e:
            print(f"  Could not connect to PostgreSQL ({e}). "
                  f"Running in PRINT-ONLY mode.", flush=True)
            self.enabled = False
            return False

    def close(self):
        if self.conn:
            self.conn.close()

    # ---- registry self-healing -------------------------------------------
    def load_verified_registry(self):
        """Pull the human-verified metric -> class map cached at boot."""
        verified = {}
        if not self.enabled:
            return verified
        try:
            with self.conn.cursor() as cur:
                cur.execute("""
                    SELECT metric_name, class_name
                    FROM metric_registry
                    WHERE is_active = TRUE
                      AND COALESCE(classification_status, 'PENDING') = 'VERIFIED';
                """)
                for metric_name, class_name in cur.fetchall():
                    verified[metric_name] = class_name
        except Exception as e:
            print(f"  Registry load failed: {e}", flush=True)
        return verified

    def ensure_asset(self, sensor_id):
        """Register an unknown physical sensor with placeholder location."""
        if not self.enabled or sensor_id in self._known_assets:
            return
        try:
            with self.conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO sensor_assets (sensor_id, building_name, floor_level, zone_name)
                    VALUES (%s, 'Kendeda', 'UNREGISTERED', 'UNREGISTERED')
                    ON CONFLICT (sensor_id) DO NOTHING;
                """, (sensor_id,))
            self._known_assets.add(sensor_id)
        except Exception as e:
            print(f"  ensure_asset({sensor_id}) failed: {e}", flush=True)

    def upsert_metric(self, metric_name, class_name, status):
        """
        Insert/update a metric's inferred class. Only writes when the
        (class, status) pair has changed since we last wrote it, to keep
        DB traffic proportional to *change*, not to data volume.
        """
        if not self.enabled:
            return
        if self._known_metrics.get(metric_name) == (class_name, status):
            return
        auto = status != "VERIFIED"
        try:
            with self.conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO metric_registry
                        (metric_name, class_name, classification_status, auto_registered, is_active)
                    VALUES (%s, %s, %s, %s, TRUE)
                    ON CONFLICT (metric_name) DO UPDATE
                        SET class_name = EXCLUDED.class_name,
                            classification_status = EXCLUDED.classification_status,
                            auto_registered = EXCLUDED.auto_registered
                        WHERE metric_registry.classification_status <> 'VERIFIED';
                """, (metric_name, class_name, status, auto))
            self._known_metrics[metric_name] = (class_name, status)
        except Exception as e:
            print(f"  upsert_metric({metric_name}) failed: {e}", flush=True)

    # ---- telemetry insertion ---------------------------------------------
    def insert_readings(self, rows):
        """
        rows: list of dicts with keys timestamp, sensor_id, measurement_name,
              reading_value. Inserted in a single round-trip via execute_values.
        """
        if not self.enabled or not rows:
            return
        values = [
            (
                r["timestamp"].replace("Z", "+00:00"),  # ISO 'Z' -> tz offset PG accepts
                r["sensor_id"],
                r["measurement_name"],
                r["reading_value"],
            )
            for r in rows
        ]
        try:
            with self.conn.cursor() as cur:
                execute_values(cur, """
                    INSERT INTO sensor_telemetry (time, sensor_id, metric_name, reading_value)
                    VALUES %s;
                """, values)
        except Exception as e:
            print(f"  insert_readings failed ({len(rows)} rows): {e}", flush=True)


if __name__ == "__main__":
    # Quick connectivity smoke test: `python database.py`
    writer = DatabaseWriter()
    ok = writer.connect()
    if ok:
        verified = writer.load_verified_registry()
        print(f"Verified metrics in registry: {len(verified)}")
        writer.close()
    else:
        sys.exit(1)
