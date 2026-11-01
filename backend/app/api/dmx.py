# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bench endpoints for DMX bring-up: fixtures, faders, blackout, channel-map sweep.

Gentle range, enforced here: values at or below nominal 10% (26/255) pass
freely; anything brighter needs allow_bright (these fixtures are blinding).
Automated testing stays in the gentle range; the flag exists for the human
operator driving the bench on purpose.
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..core.logging import get_logger
from ..drivers.dmx_usb import (
    GENTLE_DMX_MAX,
    GENTLE_PERCENT_MAX,
    DmxUsbDriver,
    cct_for_dmx,
    display_percent,
    dmx_for_cct,
    dmx_for_percent,
    get_shared_driver,
)

log = get_logger("api")
router = APIRouter()

SWEEP_CHANNEL_MAX = 32
SWEEP_DWELL_MS_MIN = 200
SWEEP_DWELL_MS_MAX = 2000


class PortBody(BaseModel):
    port: str | None = None


class SetBody(PortBody):
    channel: int
    value: int
    allow_bright: bool = False


class BlackoutBody(PortBody):
    on: bool = True


class SweepBody(PortBody):
    start: int = 1
    end: int = 12
    value: int = 13
    dwell_ms: int = 500
    allow_bright: bool = False


class FadeBody(PortBody):
    channels: dict
    seconds: float = 2.0
    units: str = "dmx"
    cct: list = []
    allow_bright: bool = False
    dwell_s: float | None = None


class LightBody(PortBody):
    fixture: str
    power: int | None = None
    cct: int | None = None
    dwell_s: float = 0
    studio: str = "home-studio"
    allow_bright: bool = False


def _driver(body):
    """Shared universe driver: one buffer per (class, port), modes included."""
    return get_shared_driver(body.port, DmxUsbDriver)


def _check_value(value, allow_bright=False):
    if not 0 <= value <= 255:
        raise HTTPException(status_code=422, detail=f"value out of range 0..255: {value}")
    if value > GENTLE_DMX_MAX and not allow_bright:
        raise HTTPException(
            status_code=422,
            detail=f"value {value} is above the gentle range ({GENTLE_DMX_MAX}/255, 10%): "
            "pass allow_bright to confirm",
        )


def _check_dwell(dwell):
    if isinstance(dwell, bool) or not isinstance(dwell, (int, float)) or not 0 <= dwell <= 60:
        raise HTTPException(status_code=422, detail=f"dwell_s must be 0..60: {dwell!r}")


def _check_percent(pct, allow_bright=False):
    if isinstance(pct, bool) or not isinstance(pct, (int, float)) or not float(pct).is_integer():
        raise HTTPException(status_code=422, detail=f"power must be a whole number 0..100: {pct!r}")
    if not 0 <= pct <= 100:
        raise HTTPException(status_code=422, detail=f"power out of range 0..100%: {pct!r}")
    if pct > GENTLE_PERCENT_MAX and not allow_bright:
        raise HTTPException(
            status_code=422,
            detail=f"power {pct}% is above the gentle range ({GENTLE_PERCENT_MAX}%): "
            "pass allow_bright to confirm",
        )


@router.post("/api/testbench/dmx/state")
def dmx_state(body: PortBody):
    """Driver buffer state. Never emits frames."""
    result = _driver(body).get_state()
    return {"ok": result.ok, "state": result.state, "error": result.error}


@router.post("/api/testbench/dmx/set")
def dmx_set(body: SetBody):
    """Set one channel (bright values need allow_bright). Explicit human trial (Bench)."""
    _check_value(body.value, body.allow_bright)
    log.debug("dmx set")
    result = _driver(body).apply({"channel": body.channel, "value": body.value})
    return {"ok": result.ok, "state": result.state, "error": result.error}


@router.post("/api/testbench/dmx/blackout")
def dmx_blackout(body: BlackoutBody):
    """Blackout on/off. Off restores the buffered look."""
    result = _driver(body).apply({"blackout": body.on})
    return {"ok": result.ok, "state": result.state, "error": result.error}


@router.post("/api/testbench/dmx/fade")
def dmx_fade(body: FadeBody):
    """Ramp channels to targets over wall-clock seconds (no blackout dip).

    Pass dwell_s instead to hold every step that long (dmx units walk every
    value, percent units walk every shown %); seconds is ignored then.
    """
    try:
        channels = {int(channel): value for channel, value in (body.channels or {}).items()}
        exempt = {int(channel) for channel in (body.cct or [])}
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=f"bad fade channels: {exc}") from exc
    if body.units == "percent":
        for pct in channels.values():
            _check_percent(pct, body.allow_bright)
    elif body.units == "dmx":
        for channel, value in channels.items():
            if isinstance(value, bool) or not isinstance(value, int):
                raise HTTPException(status_code=422, detail=f"dmx fade values must be integers: {value!r}")
            if channel in exempt:
                if not 0 <= value <= 255:
                    raise HTTPException(status_code=422, detail=f"value out of range 0..255: {value}")
            else:
                _check_value(value, body.allow_bright)
    else:
        raise HTTPException(status_code=422, detail='units must be "dmx"|"percent"')
    if not isinstance(body.seconds, (int, float)) or not 0 <= body.seconds <= 120:
        raise HTTPException(status_code=422, detail="seconds must be 0..120")
    if body.dwell_s is not None:
        _check_dwell(body.dwell_s)
    driver = _driver(body)
    try:
        if body.dwell_s is not None:
            frames = driver.fade(channels, 0, units=body.units, dwell_s=body.dwell_s)
        else:
            frames = driver.fade(channels, body.seconds, units=body.units)
    except Exception as exc:  # noqa: BLE001 - Bench reports, never 500s on hardware
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    state = driver.get_state().state
    state["frames"] = frames
    return {"ok": True, "state": state}


def _fixture_map(request, studio_id):
    """Controllable DMX lights in a studio. Unknown studio 404s."""
    try:
        registry = request.app.state.registries[studio_id]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown studio: {studio_id}") from exc
    fixtures, skipped = {}, []
    for device_id in registry.device_ids:
        device = registry.get_device(device_id)
        if device.get("type") != "light" or device.get("driver") != "dmx":
            continue
        if not device.get("controllable", True):
            skipped.append({"target": device_id, "reason": f"{device_id} is not controllable"})
            continue
        address = (device.get("driverAddress") or {}).get("dmx") or {}
        dimmer = address.get("dimmer")
        cct_ch = address.get("cct")
        for channel in (dimmer,) if cct_ch is None else (dimmer, cct_ch):
            if isinstance(channel, bool) or not isinstance(channel, int) or not 1 <= channel <= 512:
                skipped.append(
                    {"target": device_id, "reason": f"{device_id} has a bad DMX channel map: {address!r}"}
                )
                break
        else:
            spec = registry.effective_device(device_id).get("spec") or {}
            bounds = spec.get("cctRange")
            tunable = cct_ch is not None and isinstance(bounds, list) and len(bounds) == 2
            fixtures[device_id] = {
                "id": device_id,
                "name": device.get("name", device_id),
                "dimmer": dimmer,
                "cct": cct_ch,
                "cct_range": bounds if tunable else None,
                "fixed_cct": spec.get("colorTempK") if not tunable else None,
            }
    return fixtures, skipped


@router.get("/api/testbench/dmx/fixtures")
def dmx_fixtures(request: Request, studio: str = "home-studio"):
    """Controllable DMX lights for the bench light panels."""
    fixtures, skipped = _fixture_map(request, studio)
    return {"fixtures": list(fixtures.values()), "skipped": skipped}


@router.post("/api/testbench/dmx/light")
def dmx_light(body: LightBody, request: Request):
    """Set one light's power and/or color temperature.

    dwell_s=0 jumps; above 0 every step holds that long (power steps whole
    percents, color steps fixture DMX increments). Power fades first, then
    color, so each quantity keeps exact step holds.
    """
    fixtures, skipped = _fixture_map(request, body.studio)
    fixture = fixtures.get(body.fixture)
    if fixture is None:
        detail = next((item["reason"] for item in skipped if item["target"] == body.fixture), None)
        raise HTTPException(status_code=422, detail=detail or f"unknown DMX fixture: {body.fixture}")
    if body.power is None and body.cct is None:
        raise HTTPException(status_code=422, detail="set power and/or cct")
    channels = {}
    if body.power is not None:
        _check_percent(body.power, body.allow_bright)
        channels[fixture["dimmer"]] = dmx_for_percent(body.power)
    if body.cct is not None:
        if isinstance(body.cct, bool) or not isinstance(body.cct, (int, float)):
            raise HTTPException(status_code=422, detail=f"cct must be a number: {body.cct!r}")
        bounds = fixture["cct_range"]
        if bounds is None:
            fixed = fixture["fixed_cct"]
            suffix = f" ({fixed}K)" if fixed else ""
            raise HTTPException(status_code=422, detail=f"{body.fixture} is fixed-CCT{suffix}")
        if not bounds[0] <= body.cct <= bounds[1]:
            raise HTTPException(status_code=422, detail=f"cct {body.cct}K outside {bounds[0]}..{bounds[1]}K")
        channels[fixture["cct"]] = dmx_for_cct(body.cct, bounds[0], bounds[1])
    _check_dwell(body.dwell_s)
    sequence = []
    if body.power is not None:
        sequence.append("power")
    if body.cct is not None:
        sequence.append("cct")
    driver = _driver(body)
    pre = driver.get_state().state.get("channels") or {}
    from_power = None
    from_cct = None
    if body.power is not None:
        from_power = display_percent(pre.get(str(fixture["dimmer"]), 0))
    if body.cct is not None:
        bounds = fixture["cct_range"]
        from_cct = cct_for_dmx(pre.get(str(fixture["cct"]), 0), bounds[0], bounds[1])
    try:
        frames = None
        if body.dwell_s > 0:
            total = 0
            if body.power is not None:
                total += driver.fade(
                    {fixture["dimmer"]: body.power}, 0, units="percent", dwell_s=body.dwell_s
                )
            if body.cct is not None:
                total += driver.fade(
                    {fixture["cct"]: channels[fixture["cct"]]}, 0, units="dmx", dwell_s=body.dwell_s
                )
            frames = total
        else:
            result = driver.apply({"channels": channels})
            if not result.ok:
                raise HTTPException(status_code=502, detail=result.error or "DMX apply failed")
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - Bench reports, never 500s on hardware
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    state = driver.get_state().state
    if frames is not None:
        state["frames"] = frames
    applied = {
        "fixture": body.fixture,
        "power": body.power,
        "cct": body.cct,
        "channels": channels,
        "dwell_s": body.dwell_s,
        "sequence": sequence,
        "from_power": from_power,
        "from_cct": from_cct,
    }
    return {"ok": True, "state": state, "applied": applied}


@router.post("/api/testbench/dmx/sweep")
def dmx_sweep(body: SweepBody):
    """Channel-map finder: hold each channel solo, dark gaps between steps."""
    _check_value(body.value, body.allow_bright)
    span = body.end - body.start + 1
    if not 1 <= body.start <= body.end <= 512:
        raise HTTPException(status_code=422, detail="need 1 <= start <= end <= 512")
    if span > SWEEP_CHANNEL_MAX:
        raise HTTPException(status_code=422, detail=f"sweep covers at most {SWEEP_CHANNEL_MAX} channels")
    if not SWEEP_DWELL_MS_MIN <= body.dwell_ms <= SWEEP_DWELL_MS_MAX:
        raise HTTPException(status_code=422, detail="dwell_ms must be 200..2000")
    driver = _driver(body)
    dwell_s = body.dwell_ms / 1000
    seen = []
    try:
        driver.hold({}, 0.5)
        for channel in range(body.start, body.end + 1):
            driver.hold({}, 0.3)
            driver.hold({channel: body.value}, dwell_s)
            seen.append(channel)
    except Exception as exc:  # noqa: BLE001 - Bench reports, never 500s on hardware
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True, "state": {"channels": seen, "value": body.value}}
