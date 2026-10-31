# SPDX-License-Identifier: AGPL-3.0-or-later
"""Safety gate: single choke point for automatic power actions.

Bench trials are explicit human presses and bypass this gate; everything
automatic (modes, AI, redeems, All Off) must pass through it.
"""

MANUAL_POLICIES = ("manual-only", "never")
STREAM_ACTIONS = ("start", "stop")


class SafetyViolation(Exception):
    """An automatic action was refused by the safety gate."""


def check_power(device, turn_on):
    """Raise SafetyViolation if auto-switching this device violates its policy.

    Power-on is always allowed. Power-off is refused for `manual-only` and
    `never` devices (the main PC plug, anything the Pi itself needs).
    """
    if turn_on:
        return
    policy = device.get("powerOffPolicy", "auto")
    if policy in MANUAL_POLICIES:
        raise SafetyViolation(f"refusing auto power-off of {device['id']} (policy={policy})")


def check_stream(action, confirmed):
    """Raise SafetyViolation unless a stream start/stop is explicitly confirmed.

    Scene switches flow freely; changing broadcast state never happens on an
    unconfirmed automatic action. Bench passes confirmed=True from its dialog.
    """
    if action in STREAM_ACTIONS and not confirmed:
        raise SafetyViolation(f"refusing unconfirmed stream {action} (explicit confirmation required)")
