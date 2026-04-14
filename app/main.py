import os
import time
import requests
from influxdb import InfluxDBClient

API_BASE_URL = os.getenv("API_BASE_URL", "https://api.example.com").rstrip("/")
API_TOKEN = os.getenv("API_TOKEN", "")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "60"))

INFLUX_HOST = os.getenv("INFLUX_HOST", "influxdb")
INFLUX_PORT = int(os.getenv("INFLUX_PORT", "8086"))
INFLUX_DB = os.getenv("INFLUX_DB", "apidb")
INFLUX_USER = os.getenv("INFLUX_USER", "admin")
INFLUX_PASSWORD = os.getenv("INFLUX_PASSWORD", "adminpassword")
MEASUREMENT = os.getenv("INFLUX_MEASUREMENT", "api_metrics")

def influx_client() -> InfluxDBClient:
    return InfluxDBClient(
        host=INFLUX_HOST,
        port=INFLUX_PORT,
        username=INFLUX_USER,
        password=INFLUX_PASSWORD,
        database=INFLUX_DB,
        timeout=30,
        retries=3,
    )

def fetch_api() -> dict:
    headers = {}
    if API_TOKEN:
        headers["Authorization"] = f"Bearer {API_TOKEN}"
    # change /endpoint to your real endpoint
    r = requests.get(f"{API_BASE_URL}/endpoint", headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()

def to_point(payload: dict) -> dict:
    """
    Map your API response -> Influx point.
    Adjust fields/tags to match your data.
    """
    # Example: assume payload contains numeric values like {"temp": 21.3, "pressure": 1012}
    fields = {}
    for k, v in payload.items():
        if isinstance(v, (int, float)) and v is not True and v is not False:
            fields[k] = float(v)

    # If nothing numeric, store a simple counter so you still see writes
    if not fields:
        fields = {"value": 1.0}

    return {
        "measurement": MEASUREMENT,
        "tags": {
            "source": "api_reader",
        },
        "fields": fields,
        # time omitted -> server time
    }

def wait_for_influx(client: InfluxDBClient, retries: int = 30) -> None:
    for i in range(retries):
        try:
            client.ping()
            return
        except Exception:
            time.sleep(1)
    raise RuntimeError("InfluxDB not reachable")

def main():
    print("API reader starting...", flush=True)
    client = influx_client()
    wait_for_influx(client)

    while True:
        try:
            payload = fetch_api()
            point = to_point(payload)
            ok = client.write_points([point], time_precision="s")
            print(f"Wrote point ok={ok} fields={list(point['fields'].keys())}", flush=True)
        except Exception as e:
            print(f"Error: {e!r}", flush=True)
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
