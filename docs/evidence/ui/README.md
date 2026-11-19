# Gate 7 UI evidence (prompt 7)

Captured 2026-11-19 with Playwright (Chromium, 1440×900, dark theme).

| File | What it shows |
|---|---|
| `fleet.png` | Fleet tab: three saved robots, the camera car Live, Executive status tagged SIM on each panel, the Add a robot card |
| `robot-page.png` | The camera car's robot page, opened from its Fleet panel, after the W hold and release: "Connected · Stopped" |
| `map.png` | Map tab with the Executive's real reply (`available: false`): the empty state |
| `map-demo.png` | Map tab with the dev-only **Demo data** toggle on. It is a fixture and is watermarked DEMO |

## How the stack was run

The stack was started by hand, not with `npm run hive`. The launcher does not pass `HIVE_ROS_SETUP` through to WSL.

- Car backend, from the repo root in WSL:
  `HIVE_ROS_SETUP=backend/test/mock_ros/setup.bash python3 backend/server.py` (port 8787)
- Executive, from `backend/` in WSL:
  `HIVE_GATEWAY=fake ~/.venvs/hive-exec/bin/python -m hive_exec` (port 8788)
- UI: `npx vite --host 127.0.0.1` (port 5173)

## live-data regression on the mock backend

These steps were run after a full page reload:

1. On Fleet, the camera car's panel showed "Offline". Clicked its Connect button. The panel went Live, with link online and 3 nodes · 7 topics.
2. Clicked the panel. The robot page opened unchanged. The back button reads "Fleet".
3. Held W for 1.2 s, then released.

| Time | Event |
|---|---|
| 15 ms | `POST /api/robots/drive` action `forward`, seq 1 |
| 1200 ms | Page showed "Connected · Driving forward" |
| on release | Page showed "Connected · Stopping…" |
| 1244 ms | `POST /api/robots/drive` action `stop`, seq 2 |
| 1295 ms | Page showed "Connected · Stopped" |

- Confirmation: "Car confirmed 0,1,0,1 · 328 ms ago".
- Console: 0 errors.

The Map tab, with the demo toggle on and then off, also logged no console errors or warnings.
