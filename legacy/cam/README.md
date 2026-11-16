# ESP32-CAM snapshot node

Separate PlatformIO project (board: AI-Thinker ESP32-CAM). It does one job:
when the backend raises an alert it publishes a `capture` command; this board
takes a photo and uploads it to `POST /face/snapshot`, where it is attached to
the alert on the dashboard.

```
backend ──MQTT──► <prefix>/<CAM_ID>/commands   {"cmd":"capture","locker":3,"alert":42}
camera  ──HTTP──► http://<BACKEND_HOST>:8000/face/snapshot   (multipart, X-Device-Key)
camera  ──MQTT──► <prefix>/<CAM_ID>/status     "online" / "offline" (retained)
```

> **Status: compiles, and the upload format was replayed byte for byte against
> the real backend, but it has not run on a physical board yet.** Expect to
> adjust things on first power-up (see Troubleshooting).

## 1. Configure

```bash
cd cam
cp include/cam_config.example.h include/cam_config.h   # already done once
```

Edit `include/cam_config.h` (git-ignored, it holds your WiFi password):

| setting | must match |
|---|---|
| `WIFI_SSID` / `WIFI_PASSWORD` | the 2.4 GHz network (ESP32 has no 5 GHz) |
| `MQTT_HOST`, `MQTT_PORT`, `MQTT_TOPIC_PREFIX` | the locker firmware and `backend/.env` |
| `CAM_ID` | `CAMERA_DEVICE_ID` in `backend/.env` |
| `BACKEND_HOST` | the **LAN IP of the PC** running the backend, not `localhost` |
| `DEVICE_API_KEY` | `DEVICE_API_KEY` in `backend/.env` |

`backend/.env`:

```
CAMERA_ENABLED=true
CAMERA_DEVICE_ID=cam-01
DEVICE_API_KEY=<a long random string, same as in cam_config.h>
```

## 2. Let the camera reach the backend

By default uvicorn only listens on this PC (`127.0.0.1`), so the board cannot
connect. Start it on all interfaces and allow the port in Windows Firewall:

```bash
.venv/Scripts/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Be aware that this also exposes the dashboard (it has no login) to everyone on
that network. Only do it on a network you trust, and set `DEVICE_API_KEY`.
The upload is plain HTTP; do not cross the internet with it.

## 3. Flash

AI-Thinker boards have no USB port. With a USB-serial adapter (3.3 V logic):

| adapter | ESP32-CAM |
|---|---|
| 5V | 5V |
| GND | GND |
| TX | U0R |
| RX | U0T |
| | **IO0 → GND** while flashing |

```bash
~/.platformio/penv/Scripts/pio.exe run -t upload
```

Then unplug IO0 from GND, press the reset button, and open the monitor
(`pio device monitor`). You should see `Camera ready.` then `MQTT connected.`

Power matters: the camera plus WiFi draws bursts above 300 mA. A weak USB port
or a long thin cable causes brownout resets; use a good 5 V supply.

## 4. Test without waiting for an alert

With the backend running, ask the camera for a photo on demand:

```bash
cd ../backend
.venv/Scripts/python.exe -m app.commands --locker 1
```

The monitor shows `Capture requested`, `Photo: ... bytes`, `Upload: HTTP/1.1 201`.
The photo appears in `GET /face/snapshots`. To see it on an alert, trigger one
(for example 5 wrong PINs at one locker) with `CAMERA_ENABLED=true`.

## Troubleshooting

| symptom | likely cause |
|---|---|
| `Camera init failed: 0x105` | ribbon cable loose, or not enough power |
| `Camera init failed: 0x20001` | wrong pin map (this project is AI-Thinker only) |
| `MQTT connect failed, rc=-2` | no internet from the board, or wrong `MQTT_HOST` |
| `Upload: cannot reach backend` | `BACKEND_HOST` wrong, uvicorn not on `0.0.0.0`, or firewall |
| `Upload: HTTP/1.1 401` | `DEVICE_API_KEY` differs from the backend's |
| photos too dark or washed out | first frames after idle are discarded; for a dark room add light, or use the board's flash LED (GPIO 4) |
| two boards, one stops working | give each its own `CAM_ID` |
