# SPDX-License-Identifier: AGPL-3.0-or-later
"""Named OBS WebSocket targets: hub-local store with encrypted passwords.

File shape (config_root/studios.local/obs.json, git-ignored like the rest
of studios.local)::

    {"targets": {"main": {"host": ..., "port": 4455, "password": "enc:..."}},
     "studios": {"home-studio": {"target": "main"}}}

Passwords rest encrypted (Fernet, key in studios.local/.obs-key, created
on first use and shipped to the hub with the normal studios.local deploy).
Threat model, stated plainly: this defeats file-content exposure (repo
leaks, backups, shoulder-surfed files, accidental pastes) — not a
full-disk compromise, since the key lives on the same box. Nothing in
this module ever logs or echoes a password; list views carry only
``has_password``.

Legacy shape (``{"studios": {id: {"host", "port"?, "password"?}}}`` with a
plaintext password) still resolves, and is migrated to a named target
with an encrypted password on the first admin write.
"""

import json
import os
import re
from pathlib import Path

STORE_FILENAME = "obs.json"
KEY_FILENAME = ".obs-key"
ENC_PREFIX = "enc:"
DEFAULT_PORT = 4455

TARGET_NAME_RE = re.compile(r"[A-Za-z0-9]([A-Za-z0-9 _.-]{0,63})")

UNSET = object()  # update() "field omitted" marker (imported by the API layer)


class ObsStoreError(Exception):
    """Unreadable store (missing file reads as empty, never raises)."""


class ObsCryptoUnavailable(ObsStoreError):
    """The cryptography package is not installed (pip install it)."""


class ObsCryptoError(ObsStoreError):
    """A stored password cannot be decrypted (key lost or replaced)."""


class ObsTargetError(ObsStoreError):
    """Bad target data or selection (maps to 422)."""


class UnknownTargetError(ObsTargetError):
    """No such target (maps to 404)."""


class TargetConflictError(ObsTargetError):
    """Name already taken (maps to 409)."""


class TargetReferencedError(ObsTargetError):
    """Target is some studio's default (maps to 409)."""


def _fernet_cls():
    """Fernet class, imported lazily so reads work without the dependency."""
    from cryptography.fernet import Fernet

    return Fernet


def _crypto():
    """Fernet class, or an honest unavailable error (maps ImportError too)."""
    try:
        return _fernet_cls()
    except ImportError as exc:
        raise ObsCryptoUnavailable(
            "password encryption needs the cryptography package (pip install -r backend/requirements.txt)"
        ) from exc


def _paths(config_root):
    local = Path(config_root) / "studios.local"
    return local / STORE_FILENAME, local / KEY_FILENAME


def load_doc(config_root):
    """Parse the store file. Missing file reads as an empty store."""
    store_path, _key_path = _paths(config_root)
    try:
        raw = store_path.read_text(encoding="utf-8")
    except OSError:
        return {"targets": {}, "studios": {}}
    try:
        doc = json.loads(raw)
    except ValueError as exc:
        raise ObsStoreError(f"obs store malformed ({store_path}): {exc}") from exc
    if not isinstance(doc, dict):
        raise ObsStoreError(f"obs store malformed ({store_path}): top level must be an object")
    for key in ("targets", "studios"):
        if key in doc and not isinstance(doc[key], dict):
            raise ObsStoreError(f"obs store malformed ({store_path}): {key!r} must be an object")
    return {"targets": doc.get("targets") or {}, "studios": doc.get("studios") or {}}


def save_doc(config_root, doc):
    """Atomic write (tmp file + replace), creating studios.local as needed."""
    store_path, _key_path = _paths(config_root)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = store_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    os.replace(tmp_path, store_path)


def _load_key(config_root, create=False):
    """Raw Fernet key bytes. Created (0600, best effort) on first use."""
    _store_path, key_path = _paths(config_root)
    try:
        return key_path.read_bytes()
    except OSError:
        if not create:
            raise ObsCryptoError(
                "obs key file is missing (studios.local/.obs-key); re-enter passwords on the OBS targets page"
            )
    key = _crypto().generate_key()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(key)
    try:
        os.chmod(key_path, 0o600)
    except OSError:  # noqa: BLE001 - best effort (no-op on Windows ACLs)
        pass
    return key


def encrypt_secret(config_root, plaintext):
    """Encrypt one password for storage. Empty values are rejected."""
    if not isinstance(plaintext, str) or not plaintext:
        raise ObsTargetError("password must be a non-empty string (or null for none)")
    token = _crypto()(_load_key(config_root, create=True)).encrypt(plaintext.encode()).decode()
    return ENC_PREFIX + token


def decrypt_secret(config_root, stored):
    """Stored password back to plaintext. None stays None (no password).

    Values without the ``enc:`` prefix are legacy plaintext (old inline
    entries) and pass through; they get encrypted on the next admin save.
    """
    if stored is None:
        return None
    if not isinstance(stored, str) or not stored.startswith(ENC_PREFIX):
        return stored
    try:
        return _crypto()(_load_key(config_root)).decrypt(stored[len(ENC_PREFIX):].encode()).decode()
    except ObsStoreError:
        raise
    except Exception as exc:
        raise ObsCryptoError(
            "a stored obs password cannot be decrypted (key file lost or replaced); "
            "re-enter passwords on the OBS targets page"
        ) from exc


def check_name(name):
    """Validate a target name. Returns the stripped name."""
    if not isinstance(name, str) or not TARGET_NAME_RE.fullmatch(name.strip()):
        raise ObsTargetError(
            f"target name must match {TARGET_NAME_RE.pattern}: {name!r}"
        )
    return name.strip()


def check_host(host):
    """Validate a target host. Returns the stripped host."""
    if not isinstance(host, str) or not host.strip() or len(host.strip()) > 253:
        raise ObsTargetError(f"host must be a non-empty string (<=253 chars): {host!r}")
    return host.strip()


def check_port(port):
    """Validate a target port. Returns the int."""
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ObsTargetError(f"port must be 1..65535: {port!r}")
    return port


def _redacted(name, entry):
    return {
        "name": name,
        "host": entry.get("host"),
        "port": entry.get("port", DEFAULT_PORT),
        "has_password": entry.get("password") is not None,
    }


def _migrate_doc(config_root, doc):
    """Fold legacy inline studio entries into named targets. Returns changed names.

    Legacy: ``studios: {id: {"host", ...}}`` -> target ``{id}-obs`` holding
    the endpoint (password encrypted) + ``studios: {id: {"target": ...}}``.
    Runs on every admin write; read paths never mutate the file.
    """
    changed = []
    targets, studios = doc["targets"], doc["studios"]
    for studio_id, entry in list(studios.items()):
        if not isinstance(entry, dict) or "host" not in entry or "target" in entry:
            continue
        host = check_host(entry.get("host"))
        port = check_port(entry.get("port", DEFAULT_PORT))
        password = entry.get("password")
        stored = encrypt_secret(config_root, password) if password is not None else None
        base = f"{studio_id}-obs"
        name, suffix = base, 2
        while name in targets:
            name, suffix = f"{base}-{suffix}", suffix + 1
        targets[name] = {"host": host, "port": port, "password": stored}
        studios[studio_id] = {"target": name}
        changed.append(name)
    return changed


def list_targets(config_root):
    """Redacted targets, sorted by name. Passwords never leave the store."""
    doc = load_doc(config_root)
    return [_redacted(name, doc["targets"][name]) for name in sorted(doc["targets"])]


def create_target(config_root, name, host, port=DEFAULT_PORT, password=None):
    """Add a target (migrating legacy entries first). Returns the redacted row."""
    name, host, port = check_name(name), check_host(host), check_port(port)
    if password is not None and (not isinstance(password, str) or not password):
        raise ObsTargetError("password must be a non-empty string (or null for none)")
    doc = load_doc(config_root)
    _migrate_doc(config_root, doc)
    if name in doc["targets"]:
        raise TargetConflictError(f"obs target {name!r} already exists")
    stored = encrypt_secret(config_root, password) if password is not None else None
    doc["targets"][name] = {"host": host, "port": port, "password": stored}
    save_doc(config_root, doc)
    return _redacted(name, doc["targets"][name])


def update_target(config_root, name, host=UNSET, port=UNSET, password=UNSET):
    """Patch a target. Omitted password keeps; explicit null clears to none."""
    doc = load_doc(config_root)
    _migrate_doc(config_root, doc)
    if name not in doc["targets"]:
        raise UnknownTargetError(f"unknown obs target: {name!r}")
    entry = doc["targets"][name]
    if host is not UNSET:
        entry["host"] = check_host(host)
    if port is not UNSET:
        entry["port"] = check_port(port)
    if password is not UNSET:
        if password is not None and (not isinstance(password, str) or not password):
            raise ObsTargetError("password must be a non-empty string (or null for none)")
        entry["password"] = encrypt_secret(config_root, password) if password is not None else None
    save_doc(config_root, doc)
    return _redacted(name, entry)


def delete_target(config_root, name):
    """Remove a target. Refuses while any studio defaults to it."""
    doc = load_doc(config_root)
    if name not in doc["targets"]:
        raise UnknownTargetError(f"unknown obs target: {name!r}")
    users = sorted(
        studio_id
        for studio_id, entry in doc["studios"].items()
        if isinstance(entry, dict) and entry.get("target") == name
    )
    if users:
        raise TargetReferencedError(
            f"obs target {name!r} is the default for {', '.join(users)}; move them first"
        )
    del doc["targets"][name]
    save_doc(config_root, doc)
    return {"name": name, "deleted": True}


def studio_target(config_root, studio_id):
    """A studio's selected target name, or None when unconfigured."""
    entry = load_doc(config_root)["studios"].get(studio_id)
    if not isinstance(entry, dict):
        return None
    return entry.get("target")


def set_studio_target(config_root, studio_id, name):
    """Point a studio at a target (null clears it back to unconfigured)."""
    doc = load_doc(config_root)
    _migrate_doc(config_root, doc)
    if name is None:
        doc["studios"].pop(studio_id, None)
    else:
        if name not in doc["targets"]:
            raise UnknownTargetError(f"unknown obs target: {name!r}")
        doc["studios"][studio_id] = {"target": name}
    save_doc(config_root, doc)
    return {"studio": studio_id, "target": name}


def resolve_target(config_root, name):
    """One target's live credentials, or None when unknown."""
    target = load_doc(config_root)["targets"].get(name)
    if not isinstance(target, dict) or not target.get("host"):
        return None
    return {
        "host": target["host"],
        "port": target.get("port", DEFAULT_PORT),
        "password": decrypt_secret(config_root, target.get("password")),
    }


def resolve_studio(config_root, studio_id):
    """A studio's live credentials, or None when unconfigured.

    Follows ``{"target": name}`` refs (decrypting) and legacy inline
    ``{"host", ...}`` entries (plaintext passthrough). Dangling refs read
    as unconfigured; undecryptable passwords raise ObsCryptoError.
    """
    doc = load_doc(config_root)
    entry = doc["studios"].get(studio_id)
    if not isinstance(entry, dict):
        return None
    if "target" in entry:
        target = doc["targets"].get(entry["target"])
        if not isinstance(target, dict) or not target.get("host"):
            return None
        return {
            "host": target["host"],
            "port": target.get("port", DEFAULT_PORT),
            "password": decrypt_secret(config_root, target.get("password")),
        }
    if not entry.get("host"):
        return None
    return {
        "host": entry["host"],
        "port": entry.get("port", DEFAULT_PORT),
        "password": entry.get("password"),
    }
