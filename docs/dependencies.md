# Dependencies: software, drivers, and tools this project relies on

The complete inventory, in one place. Versions are the floors declared
in `backend/requirements.txt`, `backend/requirements-dev.txt`, and
`tools/pc-link/requirements-link.txt` (`>=` — the hub runs whatever pip
resolved at bootstrap). Platform markers matter: several entries are
Windows-only or hub-only and never install elsewhere.

## Python runtime (hub service)

| Package | Floor | Used by | Notes |
|---|---|---|---|
| fastapi | >=0.110 | `app/main.py`, all `app/api/` | HTTP API + static pages. Pydantic models ride along as its dependency. |
| uvicorn | >=0.29 | `tools/hub-serve.py`, dev server | ASGI server. Prod serves HTTPS 8000 + HTTP 8001 from one app. |
| jsonschema | >=4.21 | `app/config/loader.py` | Validates every studio JSON file against `app/config/schemas/`. |
| python-kasa | >=0.12 | `app/drivers/kasa.py` | LAN control/discovery of the six HS103 plugs. No login on current firmware; per-plug creds supported for AES models. |
| pyserial | >=3.5 | `app/drivers/dmx_usb.py`, `app/drivers/ir_usb_serial.py` | USB serial to the FTDI DMX dongle and the YS-IRTM IR blaster. |
| bleak | >=1.0 | `app/drivers/gvm_ble.py` | BLE GATT client for GVM panels (write characteristic, no pairing UI). |
| winsdk | >=1.0.0b10 (win32 only) | `app/drivers/ninja_ble.py` | WinRT BLE advertiser: the hub broadcasts Ninja frames, it is not a GATT client. Falls back extended→legacy advertising. |
| websocket-client | >=1.8 | `app/drivers/obs_ws.py` | OBS WebSocket v5 (handshake, scenes, collections, stream state). Main-PC listener deliberately uses a stdlib Upgrade probe instead. |
| sounddevice | >=0.4 | `app/drivers/link_audio.py`, PC-link listener/sender | PortAudio bindings for the 3.5mm DTMF wire, both directions. |
| numpy | >=1.26 | sounddevice runtime | Sample buffers for tone play/capture; sounddevice needs it installed. |

Standard library does heavy lifting on purpose: `asyncio` (Kasa wrapper),
`subprocess` (scheduled-task helpers, listener actions), `socket`
(WoL + OBS readiness probe), `wave`/`struct`/`math` (DTMF codec in
`app/linkproto.py`, shared stdlib-only with the PC-link scripts so the
workstation needs no project package installed).

## Driver → transport → library map

| Driver file | Hardware | Transport | Library |
|---|---|---|---|
| `kasa.py` | Kasa HS103 plugs x6 | LAN broadcast + TCP | python-kasa |
| `gvm_ble.py` | GVM 1500D panels x2 | BLE GATT write | bleak |
| `ninja_ble.py` | Viltrox Ninja 10/20 | BLE broadcast advertising | winsdk (WinRT) |
| `dmx_usb.py` + `dmx_light.py` | Ninja 400 (DMX-only) | USB-DMX (FTDI) | pyserial |
| `ir_usb_serial.py` | LED strip via YS-IRTM | USB serial NEC replay | pyserial |
| `obs_ws.py` | OBS Studio | WebSocket v5 | websocket-client |
| `link_audio.py` + `wol.py` | 9900x workstation | 3.5mm DTMF + UDP magic packet | sounddevice + stdlib socket |
| `screen.py` | KQC kiosk | in-process state + HTTP page | none (hub state) |
| `mock.py` | tests/demos | none | none |

## Workstation (9900x, user `creative`)

- Python 3.11+ with `tools/pc-link/requirements-link.txt`
  (sounddevice + numpy only) for `listener.py` / `send.py`.
- Portable OBS in the user profile (`--portable`), launched by the
  listener; OBS WebSocket enabled there. Per-user install steps:
  `tools/pc-link/README.md`.
- No network listener of any kind: the audio wire + WoL are the whole
  control channel.

## Kiosk shell

- Tauri 2 (`kiosk-shell/src-tauri/Cargo.toml`: tauri, tauri-build,
  serde_json, url) on the system WebView2 (Windows) / WebKitGTK
  (Linux). Rust toolchain only needed to rebuild the exe; the hub
  runs the shipped binary via `tools/hub-kiosk.py`.

## System and OS tools

| Tool | Where | Job |
|---|---|---|
| Windows Task Scheduler (`schtasks`) | hub | `StudioHub` service, watchdog, one-shot restart/shutdown helpers, kiosk logon. |
| OpenSSH server + `scp`/`ssh` | hub + dev PC | passwordless deploys (`tools/deploy-win.ps1`). |
| `curl.exe` | hub + dev PC | all health probes (Framework TLS cannot handshake the hub's OpenSSL 3). |
| PowerShell 5.1 | hub + dev PC | bootstrap, deploy, TLS (`hub-tls.ps1`), watchdog, icons. |
| `shutdown.exe` | hub + workstation | hub full power-down; listener DTMF shutdown (abort with `shutdown /a`). |
| Python 3.14 | hub + dev PC | service and suite runtime. |

## Dev and test tools

| Tool | Floor | Job |
|---|---|---|
| pytest | >=8 | `tests/unit` (mocked hardware), `tests/regression` (golden), `tests/hardware` (`STUDIO_HW=1` only). |
| pytest-cov | >=5 | coverage (`pytest --cov=backend/app`, currently ~85%). |
| httpx | >=0.27 | FastAPI TestClient transport. |
| ruff | >=0.6 | lint gate, config in `pyproject.toml` (`ruff check backend tools tests`). |
| node | any recent | `node --check` over extracted page scripts (no JS build step anywhere). |
| git | — | the public remote is the only backup (Pi hardware dead, `pi` remote retired — push `origin` only). |

## Reverse-engineering kit (one-off, per protocol)

Android phone with vendor apps (GVM LED, Weeylite Pro), `adb` +
`btsnoop_net` live capture, scripted tap walkers under `captures/`
(git-ignored). How each protocol fell out: `docs/gvm-ble.md`,
`docs/ninja-ble.md`, `docs/ir-capture.md`, `docs/dmx-wiring.md`.

## Dormant paths

Raspberry Pi: `tools/pi-bootstrap.sh`, `tools/studio-hub.service`,
`tools/deploy.ps1` (systemd + bash) — kept for reference, untested
since the hub migrated to Windows and the Pi hardware died.
