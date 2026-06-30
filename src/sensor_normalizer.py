import sys
import json
import psycopg2 # The industry-standard PostgreSQL adapter for Python

# ---------------------------------------------------------
# DATABASE CONFIGURATION
# (In production, these are loaded securely via Environment Variables)
# ---------------------------------------------------------
DB_CONFIG = {
    "dbname": "kendeda_iot_db",
    "user": "pipeline_service_account",
    "password": "secure_password_here",
    "host": "localhost",
    "port": "5432"
}

# The empty memory cache that will be filled at boot
DYNAMIC_METRIC_MAP = {}

def boot_sequence_load_registry():
    """
    Connects to the database at startup and caches the metric_registry.
    This prevents the script from having to query the DB 10,000 times a second.
    """
    print("Booting up: Connecting to PostgreSQL to fetch the Sensor Registry...")
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()
        
        # Pull all active metrics and their official Data Class classifications
        cursor.execute("SELECT metric_name, class_name FROM metric_registry WHERE is_active = TRUE;")
        rows = cursor.fetchall()
        
        # Populate our local memory cache
        for metric_name, class_name in rows:
            DYNAMIC_METRIC_MAP[metric_name] = class_name
            
        print(f"Boot successful. Cached {len(DYNAMIC_METRIC_MAP)} registered metrics.")
        
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"CRITICAL BOOT FAILURE: Could not connect to database. {e}")
        sys.exit(1)

def normalize_payload(raw_json_string):
    """
    Flattens the JSON and maps it using the dynamically loaded DB cache.
    """
    try:
        data = json.loads(raw_json_string)
    except json.JSONDecodeError:
        return []

    timestamp = data.pop("timestamp", None)
    sensor_id = data.pop("sensor_id", None)
    data.pop("metric_type", None)

    normalized_rows = []

    for metric_name, raw_value in data.items():
        
        # 1. Lookup the class from our downloaded database cache
        data_class = DYNAMIC_METRIC_MAP.get(metric_name)
        is_quarantined = False
        
        # 2. INFERENCE & QUARANTINE: What if the DB didn't know this metric?
        if data_class is None:
            is_quarantined = True
            numeric_val = float(raw_value)
            
            # Heuristic Guessing: If it's strictly 0.0 or 1.0, guess BINARY
            if numeric_val == 0.0 or numeric_val == 1.0:
                data_class = "BINARY_UNREGISTERED"
            else:
                data_class = "ANALOG_UNREGISTERED"

        # 3. Create the flattened database row
        row = {
            "timestamp": timestamp,
            "sensor_id": sensor_id,
            "measurement_name": metric_name,
            "data_class": data_class,
            "reading_value": float(raw_value),
            "is_quarantined": is_quarantined
        }
        normalized_rows.append(row)
        
    return normalized_rows

if __name__ == "__main__":
    # Step 1: Execute the DB pull before any data starts flowing
    boot_sequence_load_registry()
    
    print("Normalizer Engine online. Listening to data stream...\n")
    try:
        # Step 2: Continuously listen to the live stream
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            
            db_ready_rows = normalize_payload(line)
            
            # Print output (or pipe to the next database insertion script)
            for row in db_ready_rows:
                # Add a bright visual warning flag if the data had to be quarantined
                q_flag = "⚠️ QUARANTINE" if row['is_quarantined'] else "VERIFIED "
                
                print(f"{q_flag} -> [{row['timestamp']}] | ID: {row['sensor_id']:<18} | TYPE: {row['data_class']:<20} | METRIC: {row['measurement_name']:<35} | VAL: {row['reading_value']}")
                
    except KeyboardInterrupt:
        print("\n Normalizer shut down safely.")