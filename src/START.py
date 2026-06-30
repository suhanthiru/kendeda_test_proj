"""
START.py  —  one-command boot for the whole pipeline.

    python START.py

Launches the simulator, pipes its stdout straight into the normalizer's stdin
(the same `simulator | normalizer` shell pattern, but cross-platform and with
clean shutdown), and lets the normalizer fill the TimescaleDB hypertable.

If PostgreSQL isn't up, the normalizer automatically falls back to print-only
mode — so this still runs and you can watch classification happen even without
the database. To get persistence: `docker compose up -d` first.
"""

import os
import subprocess
import sys

# Windows consoles default to cp1252 and crash on the status emoji. Force UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, "sensor_simulator.py")
NORM = os.path.join(HERE, "sensor_normalizer.py")


def main():
    # Force unbuffered child output so lines stream through the pipe in real
    # time, and UTF-8 so the normalizer's status emoji render on Windows.
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONUTF8="1", PYTHONIOENCODING="utf-8")

    print(" Starting Kendeda pipeline:  simulator  ->  normalizer  ->  TimescaleDB")
    print("   (Ctrl+C to stop)\n")

    simulator = subprocess.Popen(
        [sys.executable, SIM], stdout=subprocess.PIPE, env=env,
    )
    normalizer = subprocess.Popen(
        [sys.executable, NORM], stdin=simulator.stdout, env=env,
    )
    # Allow the simulator to receive SIGPIPE if the normalizer dies.
    simulator.stdout.close()

    try:
        normalizer.wait()
    except KeyboardInterrupt:
        print("\n Shutting down pipeline...")
    finally:
        for proc in (normalizer, simulator):
            if proc.poll() is None:
                proc.terminate()
        for proc in (normalizer, simulator):
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    main()
