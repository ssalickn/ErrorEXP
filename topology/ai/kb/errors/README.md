# Knowledge base: error / resolution docs

Drop one Markdown or text file per device class / error code here.

Suggested naming: `<VENDOR>_<DEVICE>_<SYMPTOM>.md`, e.g.

- `CISCO_AP_OFFLINE.md`
- `NVR_DISK_FULL.md`
- `CAMERA_AUTH_FAILED.md`
- `HONEYWELL_PANEL_LOST.md`

Each file should have:

- **Symptoms** — what shows up in `iot.device_logs` and the dashboard.
- **Likely causes** — ranked.
- **Resolution** — concrete steps, commands, GUI paths.

The retriever scores files by keyword overlap with the event signature
(`status_code + status + severity + device_type + message`), so the
title and a few keywords near the top of the file matter most.
