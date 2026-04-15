import json
import os
import time
from typing import Any, Dict, List, Optional

import requests
from influxdb import InfluxDBClient

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "60"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "15"))
SHELLY_USERNAME = os.getenv("SHELLY_USERNAME", "")
SHELLY_PASSWORD = os.getenv("SHELLY_PASSWORD", "")

INFLUX_HOST = os.getenv("INFLUX_HOST", "influxdb")
INFLUX_PORT = int(os.getenv("INFLUX_PORT", "8086"))
INFLUX_DB = os.getenv("INFLUX_DB", "shellydb")
INFLUX_USER = os.getenv("INFLUX_USER", "admin")
INFLUX_PASSWORD = os.getenv("INFLUX_PASSWORD", "adminpassword")
MEASUREMENT = os.getenv("INFLUX_MEASUREMENT", "shelly_power")


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


def auth_tuple() -> Optional[tuple[str, str]]:
    if SHELLY_USERNAME and SHELLY_PASSWORD:
        return (SHELLY_USERNAME, SHELLY_PASSWORD)
    return None


def normalize_url(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Device URL/IP cannot be empty")
    if not value.startswith(("http://", "https://")):
        value = f"http://{value}"
    return value.rstrip("/")



def parse_devices() -> List[Dict[str, Any]]:
    raw_json = os.getenv("SHELLY_DEVICES_JSON", "").strip()
    raw_list = os.getenv("SHELLY_DEVICES", "").strip()
    devices: List[Dict[str, Any]] = []

    if raw_json:
        parsed = json.loads(raw_json)
        if not isinstance(parsed, list):
            raise ValueError("SHELLY_DEVICES_JSON must be a JSON array")
        for idx, item in enumerate(parsed):
            if not isinstance(item, dict):
                raise ValueError(f"Device entry at index {idx} must be an object")
            url = normalize_url(str(item.get("url") or item.get("ip") or ""))
            name = str(item.get("name") or f"shelly_{idx + 1}")
            generation = str(item.get("generation") or item.get("gen") or "auto").lower()
            switch_id = int(item.get("switch_id", item.get("id", 0)))
            devices.append(
                {
                    "name": name,
                    "url": url,
                    "generation": generation,
                    "switch_id": switch_id,
                }
            )

    elif raw_list:
        # Format:
        # SHELLY_DEVICES=plug1@192.168.1.10,plug2@http://192.168.1.11
        for idx, part in enumerate(raw_list.split(",")):
            part = part.strip()
            if not part:
                continue
            if "@" in part:
                name, target = part.split("@", 1)
            else:
                name = f"shelly_{idx + 1}"
                target = part
            devices.append(
                {
                    "name": name.strip(),
                    "url": normalize_url(target),
                    "generation": "auto",
                    "switch_id": 0,
                }
            )

    if not devices:
        raise ValueError(
            "No Shelly devices configured. Set SHELLY_DEVICES or SHELLY_DEVICES_JSON."
        )

    return devices



def extract_gen2_fields(payload: Dict[str, Any]) -> Dict[str, float]:
    fields: Dict[str, float] = {}

    if isinstance(payload.get("apower"), (int, float)):
        fields["power"] = float(payload["apower"])
    if isinstance(payload.get("voltage"), (int, float)):
        fields["voltage"] = float(payload["voltage"])
    if isinstance(payload.get("current"), (int, float)):
        fields["current"] = float(payload["current"])
    if isinstance(payload.get("temperature", {}).get("tC"), (int, float)):
        fields["temperature_c"] = float(payload["temperature"]["tC"])

    aenergy = payload.get("aenergy", {})
    if isinstance(aenergy.get("total"), (int, float)):
        fields["energy_total_wh"] = float(aenergy["total"])
    if isinstance(aenergy.get("by_minute"), list):
        values = [v for v in aenergy["by_minute"] if isinstance(v, (int, float))]
        if values:
            fields["energy_last_minutes_wh"] = float(sum(values))

    output = payload.get("output")
    if isinstance(output, bool):
        fields["is_on"] = 1.0 if output else 0.0

    return fields



def extract_gen1_fields(payload: Dict[str, Any], switch_id: int) -> Dict[str, float]:
    fields: Dict[str, float] = {}

    meters = payload.get("meters")
    if isinstance(meters, list) and len(meters) > switch_id and isinstance(meters[switch_id], dict):
        meter = meters[switch_id]
        if isinstance(meter.get("power"), (int, float)):
            fields["power"] = float(meter["power"])
        if isinstance(meter.get("total"), (int, float)):
            # Gen1 total is reported in Wh/minutes-dependent accumulated form depending on model.
            fields["energy_total_wh"] = float(meter["total"])

    relays = payload.get("relays")
    if isinstance(relays, list) and len(relays) > switch_id and isinstance(relays[switch_id], dict):
        relay = relays[switch_id]
        if isinstance(relay.get("ison"), bool):
            fields["is_on"] = 1.0 if relay["ison"] else 0.0
        if isinstance(relay.get("power"), (int, float)):
            fields["power"] = float(relay["power"])

    return fields



def fetch_device(device: Dict[str, Any]) -> tuple[str, Dict[str, Any], Dict[str, float]]:
    name = device["name"]
    base_url = device["url"]
    switch_id = device.get("switch_id", 0)
    generation = device.get("generation", "auto")

    if generation in {"gen2", "gen3", "plus", "new", "auto"}:
        url = f"{base_url}/rpc/Switch.GetStatus?id={switch_id}"
        try:
            response = requests.get(url, timeout=REQUEST_TIMEOUT, auth=auth_tuple())
            response.raise_for_status()
            payload = response.json()
            fields = extract_gen2_fields(payload)
            if fields:
                return "gen2", payload, fields
        except Exception:
            if generation != "auto":
                raise

    url = f"{base_url}/status"
    response = requests.get(url, timeout=REQUEST_TIMEOUT, auth=auth_tuple())
    response.raise_for_status()
    payload = response.json()
    fields = extract_gen1_fields(payload, switch_id)
    return "gen1", payload, fields



def to_point(device: Dict[str, Any], generation: str, fields: Dict[str, float]) -> Dict[str, Any]:
    if not fields:
        fields = {"reachable": 1.0}

    return {
        "measurement": MEASUREMENT,
        "tags": {
            "source": "shelly_reader",
            "device": device["name"],
            "generation": generation,
            "switch_id": str(device.get("switch_id", 0)),
            "host": device["url"].replace("http://", "").replace("https://", ""),
        },
        "fields": fields,
    }



def wait_for_influx(client: InfluxDBClient, retries: int = 30) -> None:
    for _ in range(retries):
        try:
            client.ping()
            return
        except Exception:
            time.sleep(1)
    raise RuntimeError("InfluxDB not reachable")



def ensure_database(client: InfluxDBClient) -> None:
    databases = client.get_list_database()
    if not any(db.get("name") == INFLUX_DB for db in databases):
        client.create_database(INFLUX_DB)



def main() -> None:
    print("Shelly reader starting...", flush=True)
    devices = parse_devices()
    print(f"Configured devices: {[d['name'] for d in devices]}", flush=True)

    client = influx_client()
    wait_for_influx(client)
    ensure_database(client)
    client.switch_database(INFLUX_DB)

    while True:
        points: List[Dict[str, Any]] = []
        for device in devices:
            try:
                generation, _payload, fields = fetch_device(device)
                point = to_point(device, generation, fields)
                points.append(point)
                print(
                    f"Prepared {device['name']} gen={generation} fields={point['fields']}",
                    flush=True,
                )
            except Exception as exc:
                print(f"Error reading {device['name']}: {exc!r}", flush=True)

        if points:
            try:
                ok = client.write_points(points, time_precision="s")
                print(f"Wrote {len(points)} point(s) ok={ok}", flush=True)
            except Exception as exc:
                print(f"Error writing to InfluxDB: {exc!r}", flush=True)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
