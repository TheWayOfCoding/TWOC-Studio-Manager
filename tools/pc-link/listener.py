# SPDX-License-Identifier: AGPL-3.0-or-later
"""PC-link listener: tone commands off a mic input, allow-listed actions.

Runs on the main PC (user session, e.g. Startup shortcut). Polls one input
device, decodes framed DTMF commands, and runs ONLY the actions named in
local.json — nothing listens on the network at all. With a second cable
(main TX -> hub RX), an optional "ack" block in local.json plays one
reverse ack burst per accepted command (off unless it names a device).

Actions may set "watch_obs": after launching, a background thread polls the
local OBS websocket (stdlib HTTP Upgrade handshake, no password needed) and
reports obs_ready/obs_fail down the ack path. The input stream re-opens
itself if the device vanishes (USB re-enumeration), and "log_file" captures
output when running headless. Frozen builds (PyInstaller exe) resolve the
config next to the exe and import the bundled linkproto.

Usage:
  python listener.py --list-devices
  python listener.py --calibrate
  python listener.py [--config local.json]
"""

import argparse
import collections
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

BASE = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
if not getattr(sys, "frozen", False):
    ROOT = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(ROOT / "backend"))

try:
    from app.linkproto import COMMANDS, decode_command, encode_samples, frame_for  # noqa: E402
except ImportError:  # frozen exe bundles linkproto as a top-level module
    from linkproto import COMMANDS, decode_command, encode_samples, frame_for  # noqa: E402

DEFAULT_RATE = 44100
WINDOW_S = 1.5
RMS_GATE = 0.02
COOLDOWN_S = 3.0
QUIET_S = 0.5
RECONNECT_S = 5.0


def load_config(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return data if isinstance(data, dict) else {}


def resolve_input(name):
    import sounddevice as sd

    if name is None:
        return None
    if isinstance(name, int):
        return name
    lowered = str(name).lower()
    for index, info in enumerate(sd.query_devices()):
        if (info.get("max_input_channels") or 0) > 0 and lowered in str(info.get("name") or "").lower():
            return index
    raise SystemExit(f"unknown input (see --list-devices): {name!r}")


def obs_already_up(spec, obs_watch):
    """True when a watch_obs action would relaunch a live OBS.

    Probes the local websocket handshake (short timeout): the boot tone is
    harmless to repeat, but a second OBS process is not.
    """
    if not isinstance(spec, dict) or not spec.get("watch_obs"):
        return False
    return obs_websocket_ready(obs_watch.get("host", "127.0.0.1"), obs_watch.get("port", 4455), timeout=3)


def run_action(command, spec):
    if not isinstance(spec, dict) or not spec.get("launch"):
        print(f"[{command}] no launch action configured; ignoring", flush=True)
        return
    args = [spec["launch"], *spec.get("args", [])]
    cwd = spec.get("cwd") or None
    subprocess.Popen(args, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # noqa: S603 - allow-listed local config only
    print(f"[{command}] launched: {spec['launch']}" + (f" (cwd {cwd})" if cwd else ""), flush=True)


def resolve_output(name):
    import sounddevice as sd

    if name is None:
        return None
    if isinstance(name, int):
        return name
    lowered = str(name).lower()
    for index, info in enumerate(sd.query_devices()):
        if (info.get("max_output_channels") or 0) > 0 and lowered in str(info.get("name") or "").lower():
            return index
    raise SystemExit(f"unknown ack output (see send.py --list-devices): {name!r}")


def play_ack(command, device, rate):
    """One reverse ack burst down the second cable. Failures log, never raise."""
    import sounddevice as sd

    try:
        samples = encode_samples(frame_for(command), rate)
        sd.play(list(samples), samplerate=rate, device=device, blocking=True)
        print(f"[ack] sent {command} on device={device}", flush=True)
    except Exception as exc:
        print(f"[ack] failed ({command}): {exc}", flush=True)


def obs_websocket_ready(host="127.0.0.1", port=4455, timeout=3.0):
    """True when a local OBS websocket answers the HTTP Upgrade handshake.

    Stdlib only (no websocket package on the main PC): a bare Upgrade
    request gets 101 from the HTTP layer regardless of OBS auth settings,
    which proves the server is up. Auth happens at the protocol layer
    after the handshake, so no password is needed for readiness.
    """
    import socket

    request = (
        f"GET / HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
        "Connection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    ).encode("ascii")
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError:
        return False
    try:
        sock.settimeout(timeout)
        sock.sendall(request)
        data = sock.recv(1024)
    except OSError:
        return False
    finally:
        try:
            sock.close()
        except OSError:
            pass
    status = data.split(b"\r\n", 1)[0].decode("latin-1", "replace")
    return status.startswith("HTTP/") and " 101" in status


def obs_watch_loop(obs_cfg, ack_device, ack_enabled, rate):
    """Poll for the OBS websocket, then report obs_ready/obs_fail (thread)."""
    host = obs_cfg.get("host", "127.0.0.1")
    port = obs_cfg.get("port", 4455)
    deadline = time.monotonic() + obs_cfg.get("timeout_s", 45)
    poll_s = obs_cfg.get("poll_s", 2)
    ready = False
    while time.monotonic() < deadline:
        if obs_websocket_ready(host, port):
            ready = True
            break
        time.sleep(poll_s)
    result = "obs_ready" if ready else "obs_fail"
    print(f"[obs-watch] {result} (OBS websocket {host}:{port})", flush=True)
    if ack_enabled:
        play_ack(result, ack_device, rate)
    else:
        print("[obs-watch] no ack device configured; hub must confirm via its own OBS poll", flush=True)


def _watch_number(obs_cfg, key, default, minimum, maximum):
    try:
        value = float(obs_cfg.get(key, default))
    except (TypeError, ValueError):
        value = default
    if not minimum <= value <= maximum:
        print(f"[config] obs_watch.{key}={value!r} out of range; using {default}", flush=True)
        return default
    return value


def main():
    parser = argparse.ArgumentParser(description="PC-link tone listener")
    parser.add_argument("--config", default=str(BASE / "local.json"))
    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument("--calibrate", action="store_true")
    args = parser.parse_args()

    try:
        import sounddevice as sd
    except ImportError:
        raise SystemExit("pip install -r requirements-link.txt first (sounddevice)")

    if args.list_devices:
        for index, info in enumerate(sd.query_devices()):
            if (info.get("max_input_channels") or 0) > 0:
                print(f"{index}: {info.get('name')} (in {info['max_input_channels']})")
        return

    cfg = load_config(args.config)
    log_path = cfg.get("log_file")
    if log_path:
        try:
            handle = open(log_path, "a", encoding="utf-8", buffering=1)
        except OSError as exc:
            raise SystemExit(f"cannot open log_file {log_path!r}: {exc}")
        sys.stdout = handle
        sys.stderr = handle
    print(f"pc-link listener starting {time.strftime('%Y-%m-%d %H:%M:%S')} (config: {args.config})", flush=True)
    rate = int(cfg.get("rate", DEFAULT_RATE))
    actions = cfg.get("actions", {})
    for key in actions:
        if key not in COMMANDS:
            print(
                f"[config] actions.{key}: unknown command, will never fire (want one of {sorted(COMMANDS)})",
                flush=True,
            )
    ack_cfg = cfg.get("ack") or {}
    ack_command = ack_cfg.get("command", "ack") if isinstance(ack_cfg, dict) else "ack"
    ack_device = resolve_output(ack_cfg.get("device")) if isinstance(ack_cfg, dict) and ack_cfg.get("device") else None
    ack_enabled = bool(isinstance(ack_cfg, dict) and ack_cfg.get("device"))
    if ack_enabled and ack_command not in COMMANDS:
        raise SystemExit(f"unknown ack command {ack_command!r} (want one of {sorted(COMMANDS)})")
    raw_watch = cfg.get("obs_watch") or {}
    if not isinstance(raw_watch, dict):
        print("[config] obs_watch must be an object; using defaults", flush=True)
        raw_watch = {}
    obs_watch = {
        "host": str(raw_watch.get("host", "127.0.0.1")),
        "port": int(_watch_number(raw_watch, "port", 4455, 1, 65535)),
        "timeout_s": _watch_number(raw_watch, "timeout_s", 45, 5, 300),
        "poll_s": _watch_number(raw_watch, "poll_s", 2, 0.5, 10),
    }
    window = collections.deque(maxlen=int(rate * WINDOW_S))
    print(
        f"listening on input={cfg.get('device')!r} rate={rate} (commands: {sorted(COMMANDS)})",
        flush=True,
    )
    if ack_enabled:
        print(f"ack enabled: {ack_command} -> device={ack_device}", flush=True)

    def callback(indata, _frames, _time_info, _status):
        window.extend(float(s) for s in indata[:, 0])

    last_fire, quiet_since = 0.0, time.monotonic()
    while True:
        try:
            live_device = resolve_input(cfg.get("device"))
        except SystemExit as exc:
            print(f"input unavailable ({exc}); retrying in 5s (fix local.json + restart if the name is wrong)", flush=True)
            time.sleep(RECONNECT_S)
            continue
        try:
            with sd.InputStream(device=live_device, channels=1, samplerate=rate, callback=callback):
                window.clear()
                while True:
                    time.sleep(0.25)
                    buf = list(window)
                    if not buf:
                        continue
                    rms = (sum(s * s for s in buf) / len(buf)) ** 0.5
                    now = time.monotonic()
                    if rms < RMS_GATE:
                        quiet_since = now
                        if args.calibrate:
                            print(f"rms={rms:.4f} quiet", flush=True)
                        continue
                    if args.calibrate:
                        heard = decode_command(buf, rate)
                        print(f"rms={rms:.4f} decode={heard}", flush=True)
                        continue
                    if now - last_fire < COOLDOWN_S or now - quiet_since < QUIET_S:
                        continue
                    heard = decode_command(buf, rate)
                    if heard is None:
                        continue
                    last_fire, quiet_since = now, now
                    print(f"heard command: {heard}", flush=True)
                    if heard in actions:
                        spec = actions[heard]
                        if obs_already_up(spec, obs_watch):
                            print(f"[{heard}] OBS already up; skipping launch", flush=True)
                        else:
                            run_action(heard, spec)
                        if ack_enabled:
                            play_ack(ack_command, ack_device, rate)
                        if isinstance(spec, dict) and spec.get("watch_obs"):
                            threading.Thread(
                                target=obs_watch_loop,
                                args=(obs_watch, ack_device, ack_enabled, rate),
                                daemon=True,
                            ).start()
                            print(f"[obs-watch] watching for OBS websocket (port {obs_watch['port']})", flush=True)
                    else:
                        print(f"[{heard}] not in allow-list; ignoring", flush=True)
        except Exception as exc:  # noqa: BLE001 - any stream fault retries; Ctrl+C (BaseException) still exits
            print(f"input stream lost ({exc}); retrying in 5s", flush=True)
            time.sleep(RECONNECT_S)
            continue


if __name__ == "__main__":
    main()
