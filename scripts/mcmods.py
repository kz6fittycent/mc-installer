"""Mod setup for the official Minecraft launcher inside the mc-installer snap.

No GUI code here, so it can be tested on its own. mods.py is the window.

Fabric is installed the way Fabric's own installer does it, without Java:
meta.fabricmc.net serves the version profile JSON, which goes in
.minecraft/versions/<id>/<id>.json, and a launcher profile pointing at it is
added to launcher_profiles.json. The launcher downloads the libraries itself.

Each Fabric profile gets its own game folder (profiles/fabric-<mc>/), so mods
for one Minecraft version never load on another.

Mods only ever come from Modrinth: the download URL must be on
cdn.modrinth.com and the file must match the sha512 Modrinth publishes.
"""

import datetime
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

import requests

FABRIC_META = "https://meta.fabricmc.net/v2"
MODRINTH_API = "https://api.modrinth.com/v2"
MODRINTH_CDN = "https://cdn.modrinth.com/"
HEADERS = {"User-Agent": "mc-installer (github.com/kz6fittycent/mc-installer)"}
TIMEOUT = 30

PROFILE_KEY = "mc-installer-fabric-{mc}"
# Minecraft and loader versions look like 26.2, 1.21.11, 26.4-snapshot-7, 0.20.1.
VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]*$")


class ModsError(Exception):
    """A problem to show the player, already worded for them."""


def _snap_user_data():
    try:
        return Path(os.environ["SNAP_USER_DATA"])
    except KeyError:
        raise ModsError("This only runs inside the mc-installer snap.") from None


def dot_minecraft():
    """The launcher's .minecraft (in this snap revision's data)."""
    return _snap_user_data() / ".minecraft"


def stable_dot_minecraft():
    """The same folder, reached through the 'current' link.

    Paths saved in launcher_profiles.json must use this. SNAP_USER_DATA has
    the revision number in it, and snapd copies the data to a new folder on
    every refresh, so a path with the revision in it breaks after an update.
    """
    return _snap_user_data().parent / "current" / ".minecraft"


def _get_json(url, **params):
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        raise ModsError(f"Couldn't reach {url.split('/')[2]}. Check your internet connection.\n({e})") from e


def _check_version(v):
    if not isinstance(v, str) or not VERSION_RE.match(v):
        raise ModsError(f"Unexpected version name from the server: {v!r}")
    return v


def _write_json(path, data):
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


# --- Fabric ---------------------------------------------------------------

def fabric_game_versions():
    """Minecraft releases Fabric supports, newest first (no snapshots)."""
    return [_check_version(v["version"]) for v in _get_json(f"{FABRIC_META}/versions/game") if v.get("stable")]


def latest_fabric_loader(mc):
    loaders = _get_json(f"{FABRIC_META}/versions/loader/{_check_version(mc)}")
    for entry in loaders:
        if entry["loader"].get("stable"):
            return _check_version(entry["loader"]["version"])
    raise ModsError(f"Fabric doesn't have a stable loader for Minecraft {mc} yet.")


def launcher_running():
    """True if the Minecraft launcher is open.

    The launcher rewrites launcher_profiles.json when it closes, which would
    drop a profile added while it was open.
    """
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                exe = f.read().split(b"\0", 1)[0]
        except OSError:
            continue
        if exe.endswith(b"/minecraft-launcher"):
            return True
    return False


def fabric_profiles():
    """Fabric profiles this app made: list of (minecraft version, mods folder)."""
    try:
        with open(dot_minecraft() / "launcher_profiles.json") as f:
            profiles = json.load(f).get("profiles", {})
    except (OSError, ValueError):
        return []
    found = []
    prefix = PROFILE_KEY.format(mc="")
    for key, p in profiles.items():
        if key.startswith(prefix) and p.get("gameDir"):
            found.append((key[len(prefix):], Path(p["gameDir"]) / "mods"))
    return sorted(found, reverse=True)


def install_fabric(mc, with_fabric_api=True, progress=lambda text: None):
    """Install the newest stable Fabric for Minecraft `mc`.

    Returns (profile name, mods folder). Safe to run again: it updates the
    loader and keeps the profile's mods and settings.
    """
    mc = _check_version(mc)
    dot = dot_minecraft()
    profiles_path = dot / "launcher_profiles.json"
    if not profiles_path.exists():
        raise ModsError("Open Minecraft once and sign in first, then come back here.")
    if launcher_running():
        raise ModsError("Close the Minecraft launcher first, then try again.")

    progress("Finding the newest Fabric loader…")
    loader = latest_fabric_loader(mc)
    profile_json = _get_json(f"{FABRIC_META}/versions/loader/{mc}/{loader}/profile/json")
    vid = profile_json.get("id", "")
    if profile_json.get("inheritsFrom") != mc or vid != f"fabric-loader-{loader}-{mc}":
        raise ModsError("Fabric's server sent an unexpected profile. Nothing was changed.")

    progress(f"Installing Fabric loader {loader}…")
    vdir = dot / "versions" / vid
    vdir.mkdir(parents=True, exist_ok=True)
    _write_json(vdir / f"{vid}.json", profile_json)

    game_dir = stable_dot_minecraft() / "profiles" / f"fabric-{mc}"
    mods_dir = game_dir / "mods"
    mods_dir.mkdir(parents=True, exist_ok=True)

    with open(profiles_path) as f:
        launcher_profiles = json.load(f)
    shutil.copy2(profiles_path, profiles_path.with_name("launcher_profiles.json.bak-mods"))
    profiles = launcher_profiles.setdefault("profiles", {})
    key = PROFILE_KEY.format(mc=mc)
    names = {p.get("name") for k, p in profiles.items() if k != key}
    name = f"Fabric {mc}" if f"Fabric {mc}" not in names else f"Fabric {mc} (Mods app)"
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    entry = profiles.get(key, {"created": now, "icon": "Furnace"})
    entry.update(name=name, type="custom", lastVersionId=vid, gameDir=str(game_dir), lastUsed=now)
    profiles[key] = entry
    _write_json(profiles_path, launcher_profiles)

    if with_fabric_api:
        progress("Installing Fabric API…")
        install_modrinth_project("fabric-api", mc, mods_dir)

    return name, mods_dir


# --- Modrinth -------------------------------------------------------------

def install_modrinth_project(project, mc, mods_dir):
    """Download the newest Fabric build of a Modrinth project for `mc`.

    Replaces older copies of the same project in mods_dir. Returns the file name.
    """
    versions = _get_json(
        f"{MODRINTH_API}/project/{project}/version",
        loaders=json.dumps(["fabric"]),
        game_versions=json.dumps([mc]),
    )
    if not versions:
        raise ModsError(f"{project} has no Fabric version for Minecraft {mc} on Modrinth.")
    version = next((v for v in versions if v.get("version_type") == "release"), versions[0])
    files = version["files"]
    file = next((f for f in files if f.get("primary")), files[0])

    url, filename, sha512 = file["url"], file["filename"], file["hashes"]["sha512"]
    if not url.startswith(MODRINTH_CDN):
        raise ModsError(f"Refusing to download {filename}: it isn't hosted on Modrinth.")
    if "/" in filename or not filename.endswith(".jar"):
        raise ModsError(f"Unexpected file name from Modrinth: {filename!r}")

    digest = hashlib.sha512()
    with tempfile.NamedTemporaryFile(dir=mods_dir, prefix=".download-", delete=False) as tmp:
        try:
            with requests.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True) as r:
                r.raise_for_status()
                for chunk in r.iter_content(65536):
                    digest.update(chunk)
                    tmp.write(chunk)
        except (requests.RequestException, OSError) as e:
            os.unlink(tmp.name)
            raise ModsError(f"Downloading {filename} failed. Check your internet connection.\n({e})") from e
    if digest.hexdigest() != sha512:
        os.unlink(tmp.name)
        raise ModsError(f"{filename} didn't match Modrinth's checksum, so it wasn't installed.")

    # Remove older builds of this project. Modrinth file names start with the
    # project slug, e.g. fabric-api-0.161.0+26.2.jar.
    for old in mods_dir.glob(f"{project}-*.jar"):
        if old.name != filename:
            old.unlink()
    os.replace(tmp.name, mods_dir / filename)
    return filename
