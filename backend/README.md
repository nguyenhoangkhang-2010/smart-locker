# Smart Locker backend

FastAPI + PostgreSQL. Lockers publish authentication events over MQTT; the
backend stores them in the existing `access_logs` table.

## Setup (once)

```bash
py -3.11 -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
cp .env.example .env        # then fill in DB_PASSWORD
.venv/Scripts/python.exe -m app.init_db      # adds sim_labels + indexes
```

`init_db` only adds a `sim_labels` table and two indexes; it never touches the
existing tables (`users`, `lockers`, `access_logs`, ...).

## Run

```bash
.venv/Scripts/python.exe -m uvicorn app.main:app --reload
```

- `GET  /health`  database + MQTT status
- `GET  /events`  recent events (`?user=1&locker=1&success=false&limit=50`)
- `POST /events`  insert an event over HTTP (same JSON as MQTT)
- `GET  /alerts`  detected alerts (`?acknowledged=false&type=BRUTE_FORCE`)
- `POST /alerts/{id}/ack`  mark an alert as seen
- MQTT topic: `<MQTT_TOPIC_PREFIX>/<device_id>/events`

Event JSON (what the firmware sends) and how it is stored:

```json
{"locker":1,"user":1,"method":"PIN","ok":true}                          -> SUCCESS
{"locker":1,"user":2,"method":"RFID","ok":false,"reason":"NO_PERMISSION"} -> DENIED
{"locker":1,"method":"RFID","ok":false,"reason":"BAD_CREDENTIAL"}        -> FAILED
{"locker":1,"method":"PIN","ok":false,"reason":"LOCKOUT"}                 -> LOCKOUT
```

`locker` and `user` are the numeric ids from the `lockers` / `users` tables.
`LOCKOUT` is sent once, right after the failed attempt that froze the keypad.
It is a marker, not an attempt: the rules and the model do not count it as a
wrong try and the model does not score it.

`MQTT_TOPIC_PREFIX` is required and must be a long random string kept out of
git (the backend refuses to start MQTT with an empty, short, placeholder or
previously leaked value). Run a second backend (e.g. for testing) with its own
`MQTT_CLIENT_ID`, otherwise the two kick each other off the broker.

## Synthetic data for the anomaly detector

```bash
.venv/Scripts/python.exe -m simulator.generate --days 60 --reset
```

Writes normal usage plus labelled attacks into `access_logs`; the ground truth
goes to `sim_labels`. `--reset` removes only previous simulator rows.
`--dry-run` only prints the event mix.

## Anomaly detection (Phase 2)

Every stored event goes through `app/detection/pipeline.py`:

1. **Rules** (instant, explainable): `BRUTE_FORCE` (5+ wrong attempts at one
   locker in 5 min), `NO_PERMISSION_PROBE` (3+ denials for one user in
   10 min), `NIGHT_ACCESS` (successful open 23:00-05:00).
2. **Isolation Forest** (only when no rule fired): scores the event against
   the usual behaviour (hour vs the user's habit, rare method, rare locker,
   recent failures, burst speed) and explains the odd features in words.
   Raises `ANOMALY`.
3. Alerts go to the `alerts` table (same alert is not repeated within
   `ALERT_COOLDOWN_MINUTES`) and to Telegram when `TELEGRAM_BOT_TOKEN` and
   `TELEGRAM_CHAT_ID` are set. Telegram messages go through their own queue and
   worker thread, so a slow or down Telegram never delays event ingestion; if
   the queue (100) is full the message is dropped but the alert stays in the
   database and on the dashboard.

```bash
.venv/Scripts/python.exe -m app.init_db                    # adds the alerts table
.venv/Scripts/python.exe -m app.detection.train            # fit on access_logs -> models/anomaly.joblib
.venv/Scripts/python.exe -m app.detection.backfill --reset # fill alerts for stored events
.venv/Scripts/python.exe -m app.detection.evaluate         # score against simulator labels
```

Without a trained model only the rules run. Retrain when there is
meaningfully more data; the API picks up the new file on the next event.
`evaluate` needs no database: it simulates a fresh dataset, trains on the
first 60 %, and judges the rest event by event.

## Dashboard (Phase 4)

Open `http://localhost:8000/` while the backend is running. One page, no
build step, no external libraries (works offline):

- filters (range, locker, result) scope every number, chart and table below
- KPI tiles, events per day, events by hour of day (each has a table view)
- alerts with severity, reason and an "Đã xem" button
- users with their locker permissions and credential counts (read-only)
- access log, refreshed every 10 s

Extra endpoints behind it: `/lockers`, `/stats/summary|daily|hourly`
(`?days=7&locker=1`), `/users` (never returns PIN hashes or card UIDs).

The page has no login. Keep uvicorn bound to localhost (the default) or put
it behind authentication before exposing it on a network.

## Face recognition (Phase 3)

OpenCV only (YuNet detector + SFace recognizer, CPU). Put the two model
files in `backend/models/face/` (not committed; ~37 MB):

| file | from |
|---|---|
| `face_detection_yunet_2023mar.onnx` (227 KB) | `github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/` |
| `face_recognition_sface_2021dec.onnx` (37 MB) | `github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/` |

```bash
# enrol 3-5 photos per user (front, slightly turned, real camera lighting)
.venv/Scripts/python.exe -m app.face.enroll --user 1 a.jpg b.jpg c.jpg
.venv/Scripts/python.exe -m app.face.enroll --user 1 --clear
```

Endpoints (the camera sends `X-Device-Key: <DEVICE_API_KEY>`):

- `POST /face/verify`  multipart `image`, optional `user` (1:1 check, use as
  a third factor after PIN/card/finger) and `locker` (also checks permission
  and logs a `FACE` attempt, so alerts and the dashboard see it). Returns
  `{match, granted, user_id, similarity, threshold, reason}`.
- `POST /face/snapshot`  multipart `image`, `locker`, `alert`: stores a photo
  as evidence for an alert; shown on the dashboard with a "looks like ..." hint.
- `GET /face/snapshots`, `GET /face/snapshots/{id}/image`

With `CAMERA_ENABLED=true`, every new alert publishes
`{"cmd":"capture","locker":L,"alert":ID}` to
`<MQTT_TOPIC_PREFIX>/<CAMERA_DEVICE_ID>/commands`; the camera should answer
with a `POST /face/snapshot`.

Things to know:

- **No liveness check.** A printed photo or a phone screen can pass. Use the
  face as an extra factor, never as the only one.
- `FACE_MATCH_THRESHOLD` (cosine, default 0.363) is the model's published
  value. Tune it on photos of your own users and strangers: lower = fewer
  wrongly rejected, higher = fewer wrongly accepted.
- Face vectors and camera photos are personal data. They stay on this
  machine (`data/snapshots/`, table `face_embeddings`); there is no automatic
  deletion yet. Tell the people who are enrolled or photographed.
- Set `DEVICE_API_KEY` before the backend is reachable from a network.
