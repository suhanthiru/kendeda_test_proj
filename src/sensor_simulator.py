import asyncio
import datetime
import json
import math
import random
import time
import numpy as np

# Configuration
SIMULATION_SPEED_MULTIPLIER = 3600  # 1 real second = 1 hour of simulated time

async def run_main_line_meter(sensor_id):
    """
    Simulates Kendeda Main Line 1 (ML1).
    Models net active power displaying the classic solar back-feeding curve.
    """
    start_time = time.time()
    
    while True:
        elapsed_seconds = (time.time() - start_time) * SIMULATION_SPEED_MULTIPLIER
        # Align sine wave phase so solar peak occurs at noon (hour 12)
        hour = (elapsed_seconds / 3600) % 24
        time_phase = (hour - 6) / 24 * 2 * math.pi
        
        # Baseline building consumption draw (kW)
        building_load = 35.0 + np.random.normal(0, 2.0)
        
        # Solar production curve (bell-curve behavior matching sunlight hours)
        if 6 <= hour <= 18:
            solar_generation = math.sin(time_phase) * 100.0 + np.random.normal(0, 4.0)
            solar_generation = max(0.0, solar_generation)
        else:
            solar_generation = 0.0
            
        # Net Active Power = Consumption - Generation
        net_active_power = building_load - solar_generation
        
        # Inject an anomaly (0.5% chance of an unnatural drop/spike)
        if random.random() < 0.005:
            net_active_power += random.choice([-100.0, 100.0])
            
        payload = {
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "sensor_id": sensor_id,
            "metric_type": "main_line_meter",
            "active_power_high_kw": round(net_active_power, 3)
        }
        
        print(json.dumps(payload))
        await asyncio.sleep(1)

async def run_submeter_electrical(sensor_id):
    """
    Simulates a highly detailed submeter loop (like U9U1).
    Tracks cross-correlated voltages, phase loads, and integrates energy state counters over time.
    """
    start_time = time.time()
    
    # Initialize stateful accumulating counters derived from real CSV baselines
    active_energy_counter = 5678.0
    apparent_energy_counter = 6409.0
    reactive_energy_counter = 44.0
    
    while True:
        elapsed_seconds = (time.time() - start_time) * SIMULATION_SPEED_MULTIPLIER
        
        # 1. Simulate base grid voltage jitter
        voltage_grid_fluctuation = np.random.normal(0, 0.4)
        v_ca = 208.9 + voltage_grid_fluctuation
        v_ab = 211.7 + voltage_grid_fluctuation
        v_ln = 121.5 + (voltage_grid_fluctuation / math.sqrt(3)) # Physically derived relationship
        
        # 2. Simulate tiny active/apparent branch draws seen in U9 submeters
        active_power = max(0.0, 0.033 + np.random.normal(0, 0.05))
        apparent_power = active_power * random.uniform(1.0, 1.1)
        active_power_phase_a = active_power * random.uniform(0.8, 1.0)
        
        # 3. Mathematical Integration: Accumulate Energy counters over time
        # Since 1 real second = 1 simulated hour, hours_elapsed = 1.0 per loop iteration
        hours_elapsed = 1.0 
        active_energy_counter += active_power * hours_elapsed
        apparent_energy_counter += apparent_power * hours_elapsed
        reactive_energy_counter += random.uniform(0.0, 0.01) * hours_elapsed
        
        # Anomaly Injection
        if random.random() < 0.005:
            v_ca -= 25.0  # Simulate a severe voltage sag event

        payload = {
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
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
            "reactive_energy_delivered_kvarh": round(reactive_energy_counter, 1)
        }
        
        print(json.dumps(payload))
        await asyncio.sleep(1)

async def run_solar_dedicated(sensor_id):
    """
    Simulates a dedicated solar panel string meter.
    Outputs pure generation capacity tracking clear atmospheric bell-shaped parameters.
    """
    start_time = time.time()
    
    while True:
        elapsed_seconds = (time.time() - start_time) * SIMULATION_SPEED_MULTIPLIER
        hour = (elapsed_seconds / 3600) % 24
        
        if 6 <= hour <= 18:
            # Map daylight hours to a clean 0 to pi radian arch
            solar_phase = (hour - 6) / 12 * math.pi
            # Model clear sky solar radiation profile injected with cloud-cover noise drops
            cloud_shading_factor = max(0.2, 1.0 - abs(np.random.normal(0, 0.15)))
            solar_output = math.sin(solar_phase) * 150.0 * cloud_shading_factor
        else:
            solar_output = 0.0
            
        payload = {
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "sensor_id": sensor_id,
            "metric_type": "solar_dedicated",
            "solar_power_output_kw": round(solar_output, 3)
        }
        
        print(json.dumps(payload))
        await asyncio.sleep(1)

async def main():
    tasks = []
    
    # 1. Main Net Meter
    tasks.append(run_main_line_meter("KEN-METER-MAIN-01"))
    
    # 2. Branch Electrical Submeters (MAPPED FROM U7/U9 STRUCTURES)
    for i in range(1, 5):
        tasks.append(run_submeter_electrical(f"KEN-SUB-U7U1-{i:02d}"))
        tasks.append(run_submeter_electrical(f"KEN-SUB-U9U1-{i:02d}"))
        
    # 3. Dedicated Photovoltaic Solar Strings
    for i in range(1, 3):
        tasks.append(run_solar_dedicated(f"KEN-SOLAR-STRING-{i:02d}"))
        
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nSimulation stopped safely.")