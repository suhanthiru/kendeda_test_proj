import sys
import os
import json

# Windows consoles default to cp1252 and crash on the status emoji. Force UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# database.py lives alongside this file in src/. Make that explicit instead of
# relying on Python's implicit "script's own directory" sys.path behavior,
# which only holds when this file is launched directly (not imported).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from database import DatabaseWriter

# ============================================================================
# Kendeda Stream Normalizer
# ----------------------------------------------------------------------------
# Reads raw sensor JSON from stdin, flattens each multi-metric payload into one
# row per metric, and CLASSIFIES every metric into a telemetry class WITHOUT
# being told the schema in advance. Three layers, in priority order:
#
#   0. VERIFIED        -- a human entered it in metric_registry. Ground truth.
#   1. NAME_INFERRED   -- Option 1: the metric NAME matches a known keyword.
#   2. BEHAVIOR_INFERRED / CONFIRMED / CONFLICT
#                      -- Option 2: watch how the VALUES behave over time and
#                         either confirm the name guess, resolve an unnamed
#                         metric, or flag a contradiction.
#
# Anything not human-verified is is_quarantined = True, but quarantine now has
# grades (PENDING < NAME_INFERRED < CONFIRMED, vs the red-flag CONFLICT).
# ============================================================================

# Telemetry classes (must exist in telemetry_classes table — see sql/002_seed.sql)
SIGNED, UNSIGNED, COUNTER, BINARY = (
    "ANALOG_SIGNED", "ANALOG_UNSIGNED", "COUNTER", "BINARY",
)
CONFLICT = "CONFLICT"
UNREGISTERED = "UNREGISTERED"   # provisional bucket while we gather evidence

CONFIRM_THRESHOLD = 20          # readings to watch before confirming a guess

# ---------------------------------------------------------------------------
# OPTION 1 — name keyword maps. Checked in this precedence order so the most
# specific category wins (an energy COUNTER must beat a generic power match).
# ---------------------------------------------------------------------------
COUNTER_KEYWORDS = ["_kwh", "_kvarh", "_kvah", "_wh", "_btu", "lifetime_",
                    "total_", "_cycles", "_throughput", "_delivered",
                    "_consumed", "_received"]
SIGNED_KEYWORDS = ["active_power", "net_", "_power_high", "_temp_",
                   "_temperature", "static_pressure", "differential_"]
UNSIGNED_KEYWORDS = ["voltage_", "_voltage", "_rpm", "_cfm", "_gpm", "_ppm",
                     "_ppb", "_w_m2", "solar_", "apparent_power", "_kva",
                     "_flow", "_rate", "_lux", "_current_a", "_irradiance",
                     "_speed"]
BINARY_KEYWORDS = ["is_", "_override", "_status", "_flag", "_enabled"]


def infer_from_name(metric_name):
    """Option 1: return a class from the metric name, or None if no keyword hits."""
    name = metric_name.lower()
    for kw in COUNTER_KEYWORDS:
        if kw in name:
            return COUNTER
    for kw in SIGNED_KEYWORDS:
        if kw in name:
            return SIGNED
    for kw in UNSIGNED_KEYWORDS:
        if kw in name:
            return UNSIGNED
    for kw in BINARY_KEYWORDS:
        if kw in name:
            return BINARY
    return None


# ---------------------------------------------------------------------------
# OPTION 2 — behavioral observation buffer, one running record per PHYSICAL
# STREAM, keyed (sensor_id, metric_name). This matters for COUNTER detection:
# 8 submeters all emit "active_energy_delivered_kwh" with independent counter
# values, so a shared per-name buffer would see one sensor's 5678 followed by
# another's 5670 and wrongly record a "decrease". Each stream is tracked alone;
# the final CLASS decision is still global per metric name (registry PK).
# ---------------------------------------------------------------------------
BEHAVIORAL_BUFFER = {}


def update_behavior(stream_key, value):
    b = BEHAVIORAL_BUFFER.get(stream_key)
    if b is None:
        b = {"count": 0, "last": None, "saw_negative": False,
             "saw_decrease": False, "all_binary": True, "increased": False}
        BEHAVIORAL_BUFFER[stream_key] = b
    b["count"] += 1
    if value < 0:
        b["saw_negative"] = True
    if value not in (0.0, 1.0):
        b["all_binary"] = False
    if b["last"] is not None:
        if value < b["last"]:
            b["saw_decrease"] = True
        elif value > b["last"]:
            b["increased"] = True
    b["last"] = value
    return b


def behavioral_class(b):
    """The class the VALUES alone imply (used when the name told us nothing)."""
    if b["saw_negative"]:
        return SIGNED
    if b["all_binary"]:
        return BINARY
    if b["increased"] and not b["saw_decrease"]:
        return COUNTER
    return UNSIGNED


def violates(name_class, b):
    """
    True if observed behavior breaks the constraint implied by the name class.
    Classes are nested constraints; ANALOG_SIGNED is the most permissive and
    can never be violated, so an always-positive SIGNED metric is NOT a conflict.
    """
    if name_class == BINARY and not b["all_binary"]:
        return True
    if name_class == COUNTER and b["saw_decrease"]:
        return True
    if name_class == UNSIGNED and b["saw_negative"]:
        return True
    return False


# ---------------------------------------------------------------------------
# Classification state. Each metric resolves to (class, status). Terminal
# statuses are never recomputed; provisional ones are re-evaluated each reading.
# ---------------------------------------------------------------------------
TERMINAL = {"VERIFIED", "CONFIRMED", "CONFLICT", "BEHAVIOR_INFERRED"}
DYNAMIC_METRIC_MAP = {}   # metric_name -> (class, status)


def classify(sensor_id, metric_name, value):
    """Return (data_class, status, is_quarantined) for one reading."""
    existing = DYNAMIC_METRIC_MAP.get(metric_name)
    if existing and existing[1] in TERMINAL:
        cls, status = existing
        return cls, status, (status != "VERIFIED")

    # Behavior is tracked per physical stream so independent counters on
    # different sensors aren't mistaken for one non-monotonic series.
    b = update_behavior((sensor_id, metric_name), value)
    name_class = infer_from_name(metric_name)

    if name_class is not None:
        # Option 1 gave us a candidate. Let Option 2 confirm or contradict it.
        if violates(name_class, b):
            decision = (CONFLICT, "CONFLICT")
        elif b["count"] >= CONFIRM_THRESHOLD:
            decision = (name_class, "CONFIRMED")
        else:
            decision = (name_class, "NAME_INFERRED")   # provisional, keep watching
    else:
        # Name told us nothing. Resolve purely from behavior.
        if b["saw_negative"]:
            decision = (SIGNED, "BEHAVIOR_INFERRED")    # one negative is definitive
        elif b["count"] >= CONFIRM_THRESHOLD:
            decision = (behavioral_class(b), "BEHAVIOR_INFERRED")
        else:
            decision = (UNREGISTERED, "PENDING")        # not enough evidence yet

    DYNAMIC_METRIC_MAP[metric_name] = decision
    return decision[0], decision[1], True


def normalize_payload(raw_json_string):
    """Flatten one JSON payload into a list of classified per-metric rows."""
    try:
        data = json.loads(raw_json_string)
    except json.JSONDecodeError:
        return []

    timestamp = data.pop("timestamp", None)
    sensor_id = data.pop("sensor_id", None)
    data.pop("metric_type", None)

    rows = []
    for metric_name, raw_value in data.items():
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        data_class, status, quarantined = classify(sensor_id, metric_name, value)
        rows.append({
            "timestamp": timestamp,
            "sensor_id": sensor_id,
            "measurement_name": metric_name,
            "data_class": data_class,
            "classification_status": status,
            "reading_value": value,
            "is_quarantined": quarantined,
        })
    return rows


# Console flag per status, so the live stream is readable at a glance.
STATUS_FLAG = {
    "VERIFIED":          " VERIFIED  ",
    "CONFIRMED":         " CONFIRMED ",
    "NAME_INFERRED":     " NAME-GUESS",
    "BEHAVIOR_INFERRED": " BEHAVIOR  ",
    "PENDING":           " PENDING   ",
    "CONFLICT":          " CONFLICT  ",
}


def main():
    db = DatabaseWriter()
    db.connect()

    # Boot phase: seed the cache with human-verified ground truth.
    for metric_name, class_name in db.load_verified_registry().items():
        DYNAMIC_METRIC_MAP[metric_name] = (class_name, "VERIFIED")
    print(f" Boot complete. {len(DYNAMIC_METRIC_MAP)} human-verified metrics cached.",
          flush=True)
    print(" Normalizer online. Listening to data stream...\n", flush=True)

    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue

            rows = normalize_payload(line)
            if not rows:
                continue

            # --- persist (self-healing registry + hypertable insert) -------
            for row in rows:
                db.ensure_asset(row["sensor_id"])
                db.upsert_metric(row["measurement_name"],
                                 row["data_class"],
                                 row["classification_status"])
            db.insert_readings(rows)

            # --- live console view -----------------------------------------
            for row in rows:
                flag = STATUS_FLAG.get(row["classification_status"], "          ")
                print(f"{flag} -> [{row['timestamp']}] | "
                      f"ID: {row['sensor_id']:<22} | "
                      f"CLASS: {row['data_class']:<18} | "
                      f"METRIC: {row['measurement_name']:<40} | "
                      f"VAL: {row['reading_value']}", flush=True)

    except KeyboardInterrupt:
        print("\n Normalizer shut down safely.", flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
