import asyncio
import datetime
import json
import math
import random
import time
import numpy as np

# ============================================================================
# Kendeda Building Sensor Simulator
# ----------------------------------------------------------------------------
# Stands in for the real hardware fleet. Each coroutine models one physical
# sensor, prints one JSON payload per second to stdout, and is meant to be
# piped into sensor_normalizer.py:
#
#     python src/sensor_simulator.py | python src/sensor_normalizer.py
#
# Every sensor injects realistic FAILURE MODES at random so the normalizer's
# classification + quarantine logic has something to chew on. The whole point
# is that the normalizer does NOT know this schema ahead of time.
# ============================================================================

SIMULATION_SPEED_MULTIPLIER = 3600   # 1 real second == 1 simulated hour
ANOMALY_PROB = 0.01                  # ~1% chance of a fault per reading


def now_iso():
    return datetime.datetime.utcnow().isoformat() + "Z"


def emit(payload):
    # flush=True guarantees the line streams immediately when piped, instead of
    # sitting in a block buffer until the process exits.
    print(json.dumps(payload), flush=True)


def sim_hour(start_time):
    """Returns the simulated hour-of-day (0-24) given a real wall-clock start."""
    elapsed_seconds = (time.time() - start_time) * SIMULATION_SPEED_MULTIPLIER
    return (elapsed_seconds / 3600) % 24


# ============================================================================
# 1. MAIN LINE METER  (existing)  -- net building power, solar back-feed curve
# ============================================================================
async def run_main_line_meter(sensor_id):
    start_time = time.time()
    while True:
        hour = sim_hour(start_time)
        time_phase = (hour - 6) / 24 * 2 * math.pi

        building_load = 35.0 + np.random.normal(0, 2.0)
        if 6 <= hour <= 18:
            solar_generation = max(0.0, math.sin(time_phase) * 100.0 + np.random.normal(0, 4.0))
        else:
            solar_generation = 0.0

        net_active_power = building_load - solar_generation  # negative == exporting

        if random.random() < ANOMALY_PROB:
            # Power spike: large unexplained load step or meter glitch
            net_active_power += random.choice([-100.0, 100.0])

        emit({
            "timestamp": now_iso(),
            "sensor_id": sensor_id,
            "metric_type": "main_line_meter",
            "active_power_high_kw": round(net_active_power, 3),
        })
        await asyncio.sleep(1)


# ============================================================================
# 2. SUBMETER ELECTRICAL  (existing)  -- 3-phase voltages + accumulating energy
# ============================================================================
async def run_submeter_electrical(sensor_id):
    start_time = time.time()
    active_energy_counter = 5678.0
    apparent_energy_counter = 6409.0
    reactive_energy_counter = 44.0

    while True:
        jitter = np.random.normal(0, 0.4)
        v_ca = 208.9 + jitter
        v_ab = 211.7 + jitter
        v_ln = 121.5 + (jitter / math.sqrt(3))

        active_power = max(0.0, 0.033 + np.random.normal(0, 0.05))
        apparent_power = active_power * random.uniform(1.0, 1.1)
        active_power_phase_a = active_power * random.uniform(0.8, 1.0)

        # 1 real second == 1 simulated hour, so each loop adds ~1 hour of energy
        active_energy_counter += active_power
        apparent_energy_counter += apparent_power
        reactive_energy_counter += random.uniform(0.0, 0.01)

        if random.random() < ANOMALY_PROB:
            v_ca -= 25.0  # single-phase voltage sag

        emit({
            "timestamp": now_iso(),
            "sensor_id": sensor_id,
            "metric_type": "submeter_electrical",
            "voltage_phases_ca": round(v_ca, 1),
            "voltage_phases_ab": round(v_ab, 1),
            "average_voltage_line_to_neutral": round(v_ln, 1),
            "active_power_kw": round(active_power, 3),
            "active_power_phase_a_kw": round(active_power_phase_a, 3),
            "apparent_power_kva": round(apparent_power, 3),
            "active_energy_delivered_kwh": round(active_energy_counter, 1),
            "apparent_energy_delivered_received_kvah": round(apparent_energy_counter, 1),
            "reactive_energy_delivered_kvarh": round(reactive_energy_counter, 1),
        })
        await asyncio.sleep(1)


# ============================================================================
# 3. DEDICATED SOLAR STRING  (existing)  -- pure PV generation bell curve
# ============================================================================
async def run_solar_dedicated(sensor_id):
    start_time = time.time()
    while True:
        hour = sim_hour(start_time)
        if 6 <= hour <= 18:
            solar_phase = (hour - 6) / 12 * math.pi
            cloud = max(0.2, 1.0 - abs(np.random.normal(0, 0.15)))
            solar_output = math.sin(solar_phase) * 150.0 * cloud
        else:
            solar_output = 0.0

        if random.random() < ANOMALY_PROB and solar_output > 0:
            solar_output *= 0.1  # cloud shadow: sudden mid-day drop

        emit({
            "timestamp": now_iso(),
            "sensor_id": sensor_id,
            "metric_type": "solar_dedicated",
            "solar_power_output_kw": round(solar_output, 3),
        })
        await asyncio.sleep(1)


# ============================================================================
# 4. AIR HANDLING UNIT (AHU)  (new)
# ============================================================================
async def run_air_handling_unit(sensor_id):
    while True:
        supply_temp = 55.0 + np.random.normal(0, 0.6)
        return_temp = 72.0 + np.random.normal(0, 0.8)
        airflow = 4200.0 + np.random.normal(0, 80.0)
        damper = 78.0 + np.random.normal(0, 1.5)
        fan_rpm = 1450 + int(np.random.normal(0, 20))
        static_pressure = 1.23 + np.random.normal(0, 0.05)

        if random.random() < ANOMALY_PROB:
            mode = random.choice(["stuck_damper", "fan_runaway", "supply_freeze"])
            if mode == "stuck_damper":
                airflow = 210.0 + np.random.normal(0, 15)   # damper frozen, air collapses
            elif mode == "fan_runaway":
                fan_rpm = 2890
                airflow = 7800.0
                static_pressure = 3.81
            elif mode == "supply_freeze":
                supply_temp = 36.1            # coil icing
                airflow = 1840.0
                static_pressure = 2.10

        emit({
            "timestamp": now_iso(),
            "sensor_id": sensor_id,
            "metric_type": "air_handling_unit",
            "supply_air_temp_f": round(supply_temp, 1),
            "return_air_temp_f": round(return_temp, 1),
            "supply_air_flow_cfm": round(airflow, 1),
            "damper_position_pct": round(damper, 1),
            "fan_speed_rpm": fan_rpm,
            "static_pressure_in_wg": round(static_pressure, 2),
        })
        await asyncio.sleep(1)


# ============================================================================
# 5. INDOOR AIR QUALITY  (new)
# ============================================================================
async def run_indoor_air_quality(sensor_id):
    while True:
        co2 = 600.0 + np.random.normal(0, 20.0)
        tvoc = 88.0 + np.random.normal(0, 8.0)
        pm25 = max(0.0, 4.2 + np.random.normal(0, 0.6))
        humidity = 48.0 + np.random.normal(0, 1.5)
        temp = 70.0 + np.random.normal(0, 0.5)

        if random.random() < ANOMALY_PROB:
            mode = random.choice(["crowd_surge", "sensor_drift", "pm_event"])
            if mode == "crowd_surge":
                co2 = 1840.0
                tvoc = 210.0
                humidity = 58.1
                temp = 72.4
            elif mode == "sensor_drift":
                co2 = 412.0          # reads artificially low despite occupancy
            elif mode == "pm_event":
                pm25 = 68.4          # outdoor particulate intrusion

        emit({
            "timestamp": now_iso(),
            "sensor_id": sensor_id,
            "metric_type": "indoor_air_quality",
            "co2_ppm": round(co2, 1),
            "tvoc_ppb": round(tvoc, 1),
            "pm2_5_ug_m3": round(pm25, 1),
            "relative_humidity_pct": round(humidity, 1),
            "temperature_f": round(temp, 1),
        })
        await asyncio.sleep(1)


# ============================================================================
# 6. HYDRONIC LOOP  (new)  -- chilled / hot water with accumulating BTU counter
# ============================================================================
async def run_hydronic_loop(sensor_id, supply_setpoint, return_setpoint):
    thermal_energy_btu = 8820400.0
    while True:
        supply_temp = supply_setpoint + np.random.normal(0, 0.4)
        return_temp = return_setpoint + np.random.normal(0, 0.5)
        flow = 142.0 + np.random.normal(0, 3.0)
        dp = 12.4 + np.random.normal(0, 0.3)
        thermal_energy_btu += abs(supply_temp - return_temp) * flow * 5.0  # rough integration

        if random.random() < ANOMALY_PROB:
            mode = random.choice(["cavitation", "valve_closed", "low_delta_t"])
            if mode == "cavitation":
                flow = random.choice([89.3, 134.0, 71.0, 128.0])  # erratic swing
                dp = 6.1
            elif mode == "valve_closed":
                flow = 0.0
                dp = 0.0
                return_temp = supply_temp + np.random.normal(0, 0.2)  # temps equalize
            elif mode == "low_delta_t":
                return_temp = supply_temp + 1.6   # almost no heat exchange

        emit({
            "timestamp": now_iso(),
            "sensor_id": sensor_id,
            "metric_type": "hydronic_loop",
            "supply_temp_f": round(supply_temp, 1),
            "return_temp_f": round(return_temp, 1),
            "flow_rate_gpm": round(flow, 1),
            "differential_pressure_psi": round(dp, 1),
            "thermal_energy_delivered_btu": round(thermal_energy_btu, 1),
        })
        await asyncio.sleep(1)


# ============================================================================
# 7. RAINWATER CISTERN  (new)
# ============================================================================
async def run_rainwater_cistern(sensor_id):
    level_gal = 8420.0
    while True:
        inflow = max(0.0, 2.1 + np.random.normal(0, 0.4))
        outflow = max(0.0, 0.8 + np.random.normal(0, 0.2))
        level_gal = min(12500.0, max(0.0, level_gal + (inflow - outflow)))
        level_pct = round(level_gal / 12500.0 * 100.0, 1)
        turbidity = max(0.0, 0.4 + np.random.normal(0, 0.1))
        ph = 7.1 + np.random.normal(0, 0.1)

        if random.random() < ANOMALY_PROB:
            mode = random.choice(["dry", "overflow", "ph_spike"])
            if mode == "dry":
                level_gal = 12.0
                level_pct = 0.1
                inflow = 0.0
                outflow = 0.0
                turbidity = 18.4          # sediment stirred up
            elif mode == "overflow":
                level_gal = 12500.0
                level_pct = 100.0
                inflow = 14.8
            elif mode == "ph_spike":
                inflow = 8.4
                turbidity = 2.1
                ph = 4.8                  # acidic first-flush

        emit({
            "timestamp": now_iso(),
            "sensor_id": sensor_id,
            "metric_type": "rainwater_cistern",
            "tank_level_gallons": round(level_gal, 1),
            "tank_level_pct": level_pct,
            "inflow_rate_gpm": round(inflow, 1),
            "outflow_rate_gpm": round(outflow, 1),
            "water_quality_turbidity_ntu": round(turbidity, 1),
            "water_quality_ph": round(ph, 1),
        })
        await asyncio.sleep(1)


# ============================================================================
# 8. WEATHER STATION  (new)  -- can "freeze" (replay last reading) on comms loss
# ============================================================================
async def run_weather_station(sensor_id):
    start_time = time.time()
    last_payload = None
    while True:
        hour = sim_hour(start_time)
        if 6 <= hour <= 18:
            irradiance = max(0.0, math.sin((hour - 6) / 12 * math.pi) * 950.0 + np.random.normal(0, 30))
        else:
            irradiance = 0.0

        payload = {
            "timestamp": now_iso(),
            "sensor_id": sensor_id,
            "metric_type": "weather_station",
            "outdoor_temp_f": round(84.0 + np.random.normal(0, 1.5), 1),
            "outdoor_humidity_pct": round(61.0 + np.random.normal(0, 2.0), 1),
            "wind_speed_mph": round(max(0.0, 7.3 + np.random.normal(0, 1.5)), 1),
            "wind_direction_deg": round((215.0 + np.random.normal(0, 8)) % 360, 1),
            "solar_irradiance_w_m2": round(irradiance, 1),
            "rainfall_rate_in_hr": 0.0,
        }

        if random.random() < ANOMALY_PROB and last_payload is not None:
            mode = random.choice(["freeze", "gust", "shadow"])
            if mode == "freeze":
                # Comms loss: replay the previous reading verbatim but with a new timestamp
                payload = dict(last_payload)
                payload["timestamp"] = now_iso()
            elif mode == "gust":
                payload["wind_speed_mph"] = 47.8
                payload["wind_direction_deg"] = 198.0
            elif mode == "shadow":
                payload["solar_irradiance_w_m2"] = 43.0

        emit(payload)
        last_payload = payload
        await asyncio.sleep(1)


# ============================================================================
# 9. LIGHTING CONTROL  (new)  -- daylight harvesting node
# ============================================================================
async def run_lighting_control(sensor_id):
    start_time = time.time()
    while True:
        hour = sim_hour(start_time)
        daylight = max(0.0, math.sin((hour - 6) / 12 * math.pi) * 500.0) if 6 <= hour <= 18 else 0.0
        dim = max(0.0, min(100.0, 80.0 - daylight / 10.0))   # more daylight -> dimmer fixtures
        power_w = dim * 4.8
        override = 0

        if random.random() < ANOMALY_PROB:
            mode = random.choice(["phantom_load", "sensor_fail"])
            if mode == "phantom_load":
                daylight = 0.0
                dim = 0.0
                power_w = 18.4            # parasitic draw with lights "off"
            elif mode == "sensor_fail":
                daylight = 0.0           # lux sensor reads dark mid-day
                dim = 100.0
                power_w = 480.0

        emit({
            "timestamp": now_iso(),
            "sensor_id": sensor_id,
            "metric_type": "lighting_control",
            "daylight_lux": round(daylight, 1),
            "fixture_dim_level_pct": round(dim, 1),
            "lighting_power_w": round(power_w, 1),
            "occupancy_override": override,
        })
        await asyncio.sleep(1)


# ============================================================================
# 10. SOLAR INVERTER  (new)  -- DC/AC sides + lifetime energy counter
# ============================================================================
async def run_solar_inverter(sensor_id):
    start_time = time.time()
    lifetime_kwh = 41820.0
    while True:
        hour = sim_hour(start_time)
        if 6 <= hour <= 18:
            ac_out = max(0.0, math.sin((hour - 6) / 12 * math.pi) * 7.3 + np.random.normal(0, 0.2))
        else:
            ac_out = 0.0

        efficiency = 96.9 if ac_out > 0.1 else 0.0
        dc_in = ac_out / (efficiency / 100.0) if efficiency > 0 else 0.0
        dc_voltage = 412.0 + np.random.normal(0, 3.0) if ac_out > 0 else 0.0
        dc_current = (dc_in * 1000 / dc_voltage) if dc_voltage > 0 else 0.0
        inv_temp = 104.0 + ac_out * 2.0 + np.random.normal(0, 1.0)
        lifetime_kwh += ac_out

        if random.random() < ANOMALY_PROB and ac_out > 0:
            mode = random.choice(["fault", "clipping", "overtemp"])
            if mode == "fault":
                ac_out = 0.0
                efficiency = 0.0
                lifetime_kwh -= 0  # counter freezes (no production)
            elif mode == "clipping":
                dc_in = 10.94
                dc_voltage = 441.2
                dc_current = 24.8
                ac_out = 7.6
                efficiency = round(ac_out / dc_in * 100, 1)
                inv_temp = 118.6
            elif mode == "overtemp":
                dc_in = 4.61
                ac_out = 4.38
                efficiency = 95.0
                inv_temp = 149.8

        emit({
            "timestamp": now_iso(),
            "sensor_id": sensor_id,
            "metric_type": "solar_inverter",
            "dc_voltage_v": round(dc_voltage, 1),
            "dc_current_a": round(dc_current, 1),
            "dc_power_input_kw": round(dc_in, 2),
            "ac_power_output_kw": round(ac_out, 2),
            "inverter_efficiency_pct": round(efficiency, 1),
            "inverter_temp_f": round(inv_temp, 1),
            "lifetime_energy_kwh": round(lifetime_kwh, 1),
        })
        await asyncio.sleep(1)


# ============================================================================
# 11. BATTERY ENERGY STORAGE  (new)  -- SOC + cycle / throughput counters
# ============================================================================
async def run_battery_storage(sensor_id):
    soc = 72.4
    cycles = 412
    throughput_kwh = 98340.0
    while True:
        charge_rate = 0.0
        discharge_rate = max(0.0, 14.2 + np.random.normal(0, 1.0))
        soc = max(0.0, min(100.0, soc - discharge_rate * 0.02))
        temp = 77.8 + np.random.normal(0, 1.0)
        throughput_kwh += discharge_rate * 0.02

        if random.random() < ANOMALY_PROB:
            mode = random.choice(["cell_imbalance", "thermal_runaway", "charge_refusal"])
            if mode == "cell_imbalance":
                soc = random.choice([43.0, 68.0, 51.0])   # jumps instead of smooth decay
            elif mode == "thermal_runaway":
                temp = 168.4                                # critical safety event
            elif mode == "charge_refusal":
                charge_rate = 18.0
                discharge_rate = 0.0
                soc = 84.2                                  # stuck despite charging

        emit({
            "timestamp": now_iso(),
            "sensor_id": sensor_id,
            "metric_type": "battery_storage",
            "state_of_charge_pct": round(soc, 1),
            "charge_rate_kw": round(charge_rate, 1),
            "discharge_rate_kw": round(discharge_rate, 1),
            "battery_temp_f": round(temp, 1),
            "total_cycles": cycles,
            "lifetime_energy_throughput_kwh": round(throughput_kwh, 1),
        })
        await asyncio.sleep(1)


# ============================================================================
# 12. PLUG LOAD CIRCUIT  (new)  -- branch outlet metering + Wh counter
# ============================================================================
async def run_plug_load_circuit(sensor_id):
    energy_wh = 18430.0
    while True:
        active_power = max(0.0, 340.0 + np.random.normal(0, 30.0))
        voltage = 120.3 + np.random.normal(0, 0.3)
        power_factor = min(1.0, 0.997 + np.random.normal(0, 0.002))
        current = active_power / (voltage * power_factor) if power_factor > 0 else 0.0
        energy_wh += active_power / 1000.0

        if random.random() < ANOMALY_PROB:
            mode = random.choice(["phantom_load", "pf_degradation", "circuit_trip"])
            if mode == "phantom_load":
                active_power = 18.4
                current = 0.15
            elif mode == "pf_degradation":
                power_factor = 0.704
                current = active_power / (voltage * power_factor)
            elif mode == "circuit_trip":
                active_power = 0.0
                voltage = 0.0
                current = 0.0
                power_factor = 0.0

        emit({
            "timestamp": now_iso(),
            "sensor_id": sensor_id,
            "metric_type": "plug_load_circuit",
            "active_power_w": round(active_power, 1),
            "voltage_v": round(voltage, 1),
            "current_a": round(current, 2),
            "power_factor": round(power_factor, 3),
            "energy_consumed_wh": round(energy_wh, 1),
        })
        await asyncio.sleep(1)


# ============================================================================
# FLEET ASSEMBLY
# ============================================================================
async def main():
    tasks = []

    # Main net meter
    tasks.append(run_main_line_meter("KEN-METER-MAIN-01"))

    # Branch electrical submeters (U7 / U9 structures)
    for i in range(1, 5):
        tasks.append(run_submeter_electrical(f"KEN-SUB-U7U1-{i:02d}"))
        tasks.append(run_submeter_electrical(f"KEN-SUB-U9U1-{i:02d}"))

    # Dedicated PV strings
    for i in range(1, 3):
        tasks.append(run_solar_dedicated(f"KEN-SOLAR-STRING-{i:02d}"))

    # HVAC air handling units
    for i in range(1, 3):
        tasks.append(run_air_handling_unit(f"KEN-AHU-{i:02d}"))

    # Indoor air quality nodes
    for room in ["ROOM204", "ROOM118", "ATRIUM"]:
        tasks.append(run_indoor_air_quality(f"KEN-IAQ-{room}"))

    # Hydronic loops: chilled water (44/57F) and hot water (120/140F)
    tasks.append(run_hydronic_loop("KEN-CHW-LOOP-01", 44.2, 56.8))
    tasks.append(run_hydronic_loop("KEN-HW-LOOP-01", 120.0, 140.0))

    # Rainwater cistern
    tasks.append(run_rainwater_cistern("KEN-WATER-CISTERN-01"))

    # Rooftop weather station
    tasks.append(run_weather_station("KEN-WEATHER-ROOF"))

    # Lighting control zones
    for zone in ["ZONE3B", "ZONE1A"]:
        tasks.append(run_lighting_control(f"KEN-LIGHT-{zone}"))

    # Solar inverters
    for i in range(1, 4):
        tasks.append(run_solar_inverter(f"KEN-INV-{i:02d}"))

    # Battery energy storage
    tasks.append(run_battery_storage("KEN-BESS-01"))

    # Plug load circuits
    for ckt in ["ZONE2-CKT08", "ZONE2-CKT09", "ZONE5-CKT03"]:
        tasks.append(run_plug_load_circuit(f"KEN-PLUG-{ckt}"))

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nSimulation stopped safely.", flush=True)
