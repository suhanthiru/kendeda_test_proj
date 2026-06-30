-- ==============================================================================
-- 002_seed.sql  —  Operational seed + tracking columns
-- Runs automatically AFTER 001_schema.sql when the Docker container first boots
-- (files in /docker-entrypoint-initdb.d execute in alphabetical order).
-- ==============================================================================

-- ------------------------------------------------------------------------------
-- Tracking columns on the metric registry.
-- The pipeline auto-registers metrics it has never seen, so we record HOW each
-- entry got its class and whether a human still needs to review it.
--   classification_status: VERIFIED | CONFIRMED | NAME_INFERRED |
--                          BEHAVIOR_INFERRED | PENDING | CONFLICT
--   auto_registered: TRUE if the machine created it, FALSE if a human did.
-- ------------------------------------------------------------------------------
ALTER TABLE metric_registry
    ADD COLUMN IF NOT EXISTS classification_status VARCHAR(30) DEFAULT 'PENDING';
ALTER TABLE metric_registry
    ADD COLUMN IF NOT EXISTS auto_registered BOOLEAN DEFAULT FALSE;

-- ------------------------------------------------------------------------------
-- The telemetry class umbrella. This short list is the ONE thing humans curate;
-- everything else (the per-metric registry) is filled in by the normalizer.
-- ------------------------------------------------------------------------------
INSERT INTO telemetry_classes (class_name, handling_logic) VALUES
    ('ANALOG_SIGNED',   'Continuous float, may be negative. Net power, temperatures, deltas.'),
    ('ANALOG_UNSIGNED', 'Continuous float, always >= 0. Voltage, flow, irradiance, solar output.'),
    ('COUNTER',         'Monotonically increasing accumulator, never resets. kWh, BTU, cycles.'),
    ('BINARY',          'Strictly 0 or 1. Override flags, on/off status.'),
    ('CONFLICT',        'Name heuristic and observed behavior disagree. Needs engineer review: either extend the keyword list or rename the sensor field.'),
    ('UNREGISTERED',    'Provisional bucket. Not enough evidence yet to classify.')
ON CONFLICT (class_name) DO NOTHING;
