CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;


-- ==============================================================================
-- PART 1: THE METADATA DICTIONARIES (The Configuration Layer)
-- ==============================================================================

-- 1. The Telemetry Classes (Solves: "Easily add telemetry types")
-- Instead of hardcoding 'ANALOG_SIGNED' or 'COUNTER' in Python, they live here.
CREATE TABLE telemetry_classes (
    class_name VARCHAR(50) PRIMARY KEY,
    handling_logic TEXT,          -- Instructions for how ML/Analytics should treat this
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. The Metric Registry (Solves: "Break apart or merge metrics")
-- Maps a specific metric name from the hardware to its class and analytical domain.
CREATE TABLE metric_registry (
    metric_name VARCHAR(100) PRIMARY KEY,
    class_name VARCHAR(50) REFERENCES telemetry_classes(class_name),
    physical_unit VARCHAR(20),
    analytical_domain VARCHAR(50), -- e.g., 'Electrical', 'Thermodynamics'
    is_active BOOLEAN DEFAULT TRUE
);

-- 3. The Physical Asset Registry (The Hardware Locations)
-- Tracks where the physical box actually sits in the real world.
CREATE TABLE sensor_assets (
    sensor_id VARCHAR(100) PRIMARY KEY,
    building_name VARCHAR(100),
    floor_level VARCHAR(50),
    zone_name VARCHAR(100),
    install_date DATE
);

-- ==============================================================================
-- PART 2: THE TIMESCALE DB HYPERTABLE (The Storage Layer)
-- ==============================================================================

-- 4. The Telemetry Storage
-- This table only holds the raw numbers and Foreign Keys linking to the dictionaries above.
CREATE TABLE sensor_telemetry (
    time TIMESTAMPTZ NOT NULL,
    sensor_id VARCHAR(100) REFERENCES sensor_assets(sensor_id),
    metric_name VARCHAR(100) REFERENCES metric_registry(metric_name),
    reading_value DOUBLE PRECISION NOT NULL
);

-- 5. TimescaleDB Magic Command
-- Converts the standard Postgres table into a highly-optimized Time-Series Hypertable
SELECT create_hypertable('sensor_telemetry', 'time');