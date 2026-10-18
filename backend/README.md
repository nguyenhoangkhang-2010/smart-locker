# Smart Locker backend

FastAPI + PostgreSQL. Lockers publish authentication events over MQTT; the
backend stores them in `access_logs`.

## Setup (once)

```bash
py -3.11 -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
cp .env.example .env        # then fill in DB_PASSWORD
.venv/Scripts/python.exe -m app.init_db      # schema + demo users/lockers
```

## Run

```bash
.venv/Scripts/python.exe -m uvicorn app.main:app --reload
```

- `GET  /health`  database + MQTT status
- `GET  /events`  recent events (`?user=1&locker=1&success=false&limit=50`)
- `POST /events`  insert an event over HTTP (same JSON as MQTT)
- MQTT topic: `<MQTT_TOPIC_PREFIX>/<device_id>/events`

Event JSON (what the firmware sends):

```json
{"locker":1,"user":1,"method":"PIN","ok":true,"door":"open"}
{"locker":1,"method":"RFID","ok":false,"reason":"BAD_CREDENTIAL"}
```

## Synthetic data for the anomaly detector

```bash
.venv/Scripts/python.exe -m simulator.generate --days 45 --reset
```

Writes normal usage plus labelled attacks (`access_logs.sim_label`).
`--dry-run` only prints the event mix.
