# GreenHouse Shelly Power Collector

This project now supports an arbitrary number of Shelly plugs.

## Configure devices

Use the simple environment variable format in `.env`:

```env
SHELLY_DEVICES=plug_kitchen@192.168.1.101,plug_office@192.168.1.102,plug_lab@192.168.1.103
```

You can add as many devices as you want.

If you need more control, use JSON instead:

```env
SHELLY_DEVICES_JSON=[
  {"name":"plug1","url":"http://192.168.1.101","generation":"auto","switch_id":0},
  {"name":"plug2","url":"http://192.168.1.102","generation":"gen2","switch_id":0}
]
```

## Supported Shelly APIs

- Gen2 / Gen3 / Plus devices: `/rpc/Switch.GetStatus?id=0`
- Gen1 devices: `/status`
- `generation=auto` tries the newer RPC endpoint first and falls back to Gen1.

## Stored InfluxDB data

Measurement name:

```text
shelly_power
```

Tags:

- `device`
- `generation`
- `switch_id`
- `host`
- `source=shelly_reader`

Fields, when available:

- `power`
- `voltage`
- `current`
- `energy_total_wh`
- `energy_last_minutes_wh`
- `temperature_c`
- `is_on`

## Start

```bash
docker compose up -d --build
```

## Example Grafana query ideas

- current power by device
- mean power over time grouped by `device`
- total energy per device
- relay on/off state
