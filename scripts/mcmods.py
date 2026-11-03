"""Mod setup for the official Minecraft launcher inside the mc-installer snap.

No GUI code here, so it can be tested on its own. mods.py is the window.

Fabric is installed the way Fabric's own installer does it, without Java:
meta.fabricmc.net serves the version profile JSON, which goes in
.minecraft/versions/<id>/<id>.json, and a launcher profile pointing at it is
added to launcher_profiles.json. The launcher downloads the libraries itself.

Each Fabric profile gets its own game folder (profiles/fabric-<mc>/), so mods
for one Minecraft version never load on another.

Mods, resource packs and modpacks (.mrpack) only ever come from Modrinth:
every download URL must be on cdn.modrinth.com and every file must match the
sha512 Modrinth publishes. Installed files are told apart by their sha1,
which Modrinth looks up, so files from anywhere get their real names.
"""

import datetime
import hashlib
import json
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

import requests

FABRIC_META = "https://meta.fabricmc.net/v2"
MODRINTH_API = "https://api.modrinth.com/v2"
MODRINTH_CDN = "https://cdn.modrinth.com/"
MOJANG_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
SEARCH_RESULTS = 25
HEADERS = {"User-Agent": "mc-installer (github.com/kz6fittycent/mc-installer)"}
TIMEOUT = 30

PROFILE_KEY = "mc-installer-fabric-{mc}"
PACK_KEY = "mc-installer-pack-{slug}"
PACK_RECORD = ".mc-installer-pack.json"  # in a modpack's game folder: what the last import added

# What can be searched for and where it goes in a game folder.
KINDS = {
    "mod": {"folder": "mods", "ext": ".jar", "noun": "mod"},
    "resourcepack": {"folder": "resourcepacks", "ext": ".zip", "noun": "resource pack"},
}
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


def _post_json(url, body):
    try:
        r = requests.post(url, json=body, headers=HEADERS, timeout=TIMEOUT)
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


FABRIC_VERSION_RE = re.compile(r"^fabric-loader-([0-9A-Za-z.+_-]+?)-([0-9][0-9A-Za-z.+_-]*)$")


class GameFolder:
    """Where a launcher profile keeps its mods, resource packs and worlds."""

    def __init__(self, name, mc, game_dir, loader=None, key=None):
        self.name, self.mc, self.game_dir, self.loader, self.key = name, mc, game_dir, loader, key
        self.mods_dir = game_dir / "mods"

    def folder(self, kind):
        return self.game_dir / KINDS[kind]["folder"]

    def label(self):
        loader = f" · Fabric {self.loader}" if self.loader else ""
        return f"{self.name} (Minecraft {self.mc}{loader})" if self.mc else self.name


class FabricProfile(GameFolder):
    """A launcher profile that runs Fabric, whoever made it.

    Fabric's own installer names its versions fabric-loader-<loader>-<mc>,
    the same as this app, so that is how they're recognized.
    """

    def __init__(self, key, entry):
        match = FABRIC_VERSION_RE.match(entry.get("lastVersionId", ""))
        game_dir = entry.get("gameDir")
        if not game_dir:  # no game folder of its own: the shared .minecraft
            game_dir = stable_dot_minecraft()
        elif os.path.isabs(game_dir):
            game_dir = Path(game_dir)
        else:
            game_dir = stable_dot_minecraft() / game_dir
        super().__init__(entry.get("name") or f"Fabric {match.group(2)}", match.group(2), game_dir,
                         loader=match.group(1), key=key)
        self.ours = key.startswith(PROFILE_KEY.format(mc="")) or key.startswith(PACK_KEY.format(slug=""))


def fabric_profiles():
    """Every Fabric profile in the launcher, newest Minecraft first."""
    try:
        with open(dot_minecraft() / "launcher_profiles.json") as f:
            profiles = json.load(f).get("profiles", {})
    except (OSError, ValueError):
        return []
    found = [FabricProfile(k, p) for k, p in profiles.items()
             if isinstance(p, dict) and FABRIC_VERSION_RE.match(p.get("lastVersionId", ""))]

    def version_key(fp):
        return [int(x) if x.isdigit() else -1 for x in re.split(r"[.-]", fp.mc)]
    return sorted(found, key=lambda fp: (version_key(fp), fp.ours), reverse=True)


def fabric_profile_for(mc):
    """The profile to update for Minecraft `mc`: this app's, else any other.

    Modpacks have their own profiles and are updated by importing them again.
    """
    matches = [fp for fp in fabric_profiles() if fp.mc == mc and not fp.key.startswith(PACK_KEY.format(slug=""))]
    return next((fp for fp in matches if fp.ours), matches[0] if matches else None)


def latest_release():
    """The newest Minecraft release, which the launcher's "Latest release" plays."""
    return _check_version(_get_json(MOJANG_MANIFEST)["latest"]["release"])


def game_folders():
    """Every Fabric profile, then the launcher's shared .minecraft (the
    "Latest release" profile) unless a Fabric profile already uses it."""
    folders = fabric_profiles()
    shared = stable_dot_minecraft()
    if not any(_same_path(f.game_dir, shared) for f in folders):
        try:
            mc = latest_release()
        except ModsError:
            mc = None  # offline: search without a version filter
        folders.append(GameFolder("Minecraft (no mods)", mc, shared))
    return folders


def _same_path(a, b):
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return a == b


def _launcher_profiles_path():
    """launcher_profiles.json, once it's safe to change it."""
    path = dot_minecraft() / "launcher_profiles.json"
    if not path.exists():
        raise ModsError("Open Minecraft once and sign in first, then come back here.")
    if launcher_running():
        raise ModsError("Close the Minecraft launcher first, then try again.")
    return path


def _install_fabric_version(mc, loader):
    """Put Fabric's version profile for `mc` + `loader` where the launcher finds it."""
    mc, loader = _check_version(mc), _check_version(loader)
    profile_json = _get_json(f"{FABRIC_META}/versions/loader/{mc}/{loader}/profile/json")
    vid = profile_json.get("id", "")
    if profile_json.get("inheritsFrom") != mc or vid != f"fabric-loader-{loader}-{mc}":
        raise ModsError("Fabric's server sent an unexpected profile. Nothing was changed.")
    vdir = dot_minecraft() / "versions" / vid
    vdir.mkdir(parents=True, exist_ok=True)
    _write_json(vdir / f"{vid}.json", profile_json)
    return vid


def install_fabric(mc, with_fabric_api=True, progress=lambda text: None):
    """Install the newest stable Fabric for Minecraft `mc`.

    If a Fabric profile for `mc` already exists (made by this app or by
    Fabric's own installer), that profile is updated instead: same name,
    same game folder, same mods. Otherwise a new profile gets its own game
    folder. Returns (profile name, mods folder, updated: bool).
    """
    mc = _check_version(mc)
    profiles_path = _launcher_profiles_path()

    progress("Finding the newest Fabric loader…")
    loader = latest_fabric_loader(mc)
    progress(f"Installing Fabric loader {loader}…")
    vid = _install_fabric_version(mc, loader)

    with open(profiles_path) as f:
        launcher_profiles = json.load(f)
    shutil.copy2(profiles_path, profiles_path.with_name("launcher_profiles.json.bak-mods"))
    profiles = launcher_profiles.setdefault("profiles", {})
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    existing = fabric_profile_for(mc)
    if existing:
        # Only the loader changes; name, game folder and mods stay as they are.
        profiles[existing.key]["lastVersionId"] = vid
        name, mods_dir = existing.name, existing.mods_dir
    else:
        game_dir = stable_dot_minecraft() / "profiles" / f"fabric-{mc}"
        used = {fp.game_dir for fp in fabric_profiles()}
        n = 2
        while any(_same_path(game_dir, u) for u in used):  # say, a profile upgraded from this version
            game_dir = stable_dot_minecraft() / "profiles" / f"fabric-{mc}-{n}"
            n += 1
        mods_dir = game_dir / "mods"
        names = {p.get("name") for p in profiles.values() if isinstance(p, dict)}
        name = f"Fabric {mc}" if f"Fabric {mc}" not in names else f"Fabric {mc} (Mods app)"
        key, n = PROFILE_KEY.format(mc=mc), 2
        while key in profiles:  # left behind by a profile upgraded to another version
            key, n = f"{PROFILE_KEY.format(mc=mc)}-{n}", n + 1
        profiles[key] = {
            "created": now, "icon": "Furnace", "name": name, "type": "custom",
            "lastVersionId": vid, "gameDir": str(game_dir), "lastUsed": now,
        }
    mods_dir.mkdir(parents=True, exist_ok=True)
    _write_json(profiles_path, launcher_profiles)

    if with_fabric_api:
        progress("Installing Fabric API…")
        install_project("fabric-api", "mod", GameFolder(name, mc, mods_dir.parent, loader="fabric"))

    return name, mods_dir, bool(existing)


# --- Modrinth -------------------------------------------------------------

class Hit:
    """One search result."""

    def __init__(self, h):
        self.id, self.slug, self.title = h["project_id"], h["slug"], h["title"]
        self.description = h.get("description") or ""
        self.author = h.get("author") or ""
        self.downloads = h.get("downloads", 0)


def search(query, kind, game):
    """Modrinth projects of `kind` that have a build for `game`'s Minecraft.

    Mods must be Fabric mods that run on the player's side. With no query,
    the most downloaded come first.
    """
    facets = [[f"project_type:{kind}"]]
    if game.mc:
        facets.append([f"versions:{game.mc}"])
    if kind == "mod":
        facets += [["categories:fabric"], ["client_side:required", "client_side:optional"]]
    found = _get_json(f"{MODRINTH_API}/search", query=query.strip(), facets=json.dumps(facets),
                      index="relevance" if query.strip() else "downloads", limit=SEARCH_RESULTS)
    return [Hit(h) for h in found.get("hits", [])]


def _newest_build(project, kind, game):
    """A project's newest build for `game` (a release if there is one), or None."""
    params = {"game_versions": json.dumps([game.mc])} if game.mc else {}
    if kind == "mod":
        params["loaders"] = json.dumps(["fabric"])
    try:
        builds = _get_json(f"{MODRINTH_API}/project/{project}/version", **params)
    except ModsError as e:
        if isinstance(e.__cause__, requests.HTTPError) and e.__cause__.response.status_code == 404:
            raise ModsError(f"There's no {project} on Modrinth.") from None
        raise
    builds.sort(key=lambda b: b.get("date_published", ""), reverse=True)
    return next((b for b in builds if b.get("version_type") == "release"), builds[0] if builds else None)


def _sha1(path):
    digest = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Installed:
    """A file in a game folder; title and project come from Modrinth if it knows the file."""

    def __init__(self, path, project=None, title=None):
        self.path, self.project = path, project
        self.title = title or path.stem


def installed(kind, game, online=True):
    """The mods (or resource packs) in a game folder, by title.

    Modrinth recognizes files by their sha1, wherever they were downloaded.
    Offline, or for files Modrinth doesn't know, the file name is used.
    """
    folder = game.folder(kind)
    paths = sorted(p for p in folder.glob("*" + KINDS[kind]["ext"]) if p.is_file()) if folder.is_dir() else []
    if not paths or not online:
        return [Installed(p) for p in paths]
    hashes = {_sha1(p): p for p in paths}
    try:
        versions = _post_json(f"{MODRINTH_API}/version_files", {"hashes": list(hashes), "algorithm": "sha1"})
        ids = sorted({v["project_id"] for v in versions.values()})
        titles = {p["id"]: p["title"] for p in
                  _get_json(f"{MODRINTH_API}/projects", ids=json.dumps(ids))} if ids else {}
    except ModsError:
        return [Installed(p) for p in paths]
    items = []
    for sha, path in hashes.items():
        project = versions.get(sha, {}).get("project_id")
        items.append(Installed(path, project, titles.get(project)))
    return sorted(items, key=lambda i: i.title.lower())


def _download(file, folder, ext):
    """Download a Modrinth file into `folder`, checking where it's from and its sha512.

    Returns the temporary path; the caller moves it into place.
    """
    url, filename = file["url"], file["filename"]
    sha512 = file.get("hashes", {}).get("sha512")
    if not url.startswith(MODRINTH_CDN):
        raise ModsError(f"Refusing to download {filename}: it isn't hosted on Modrinth.")
    if "/" in filename or filename.startswith(".") or not filename.endswith(ext) or not sha512:
        raise ModsError(f"Unexpected file from Modrinth: {filename!r}")
    folder.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha512()
    with tempfile.NamedTemporaryFile(dir=folder, prefix=".download-", delete=False) as tmp:
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
    return Path(tmp.name)


def _resolve(project, kind, game, have=(), cache=None):
    """The newest builds of a project and (for mods) the mods it requires, for `game`.

    Required mods whose project is in `have` are left out. Raises ModsError,
    worded for the player, if any of them has no build for that Minecraft.
    """
    cache = {} if cache is None else cache
    mc = f"Minecraft {game.mc}" if game.mc else "this Minecraft"
    plan, seen, queue = [], set(), [(project, None)]
    while queue:
        wanted, needed_by = queue.pop(0)
        if wanted not in cache:
            cache[wanted] = _newest_build(wanted, kind, game)
        build = cache[wanted]
        if build is None:
            loader = " Fabric" if kind == "mod" else ""
            if needed_by:
                names = {p["id"]: p["title"] for p in
                         _get_json(f"{MODRINTH_API}/projects", ids=json.dumps([needed_by, wanted]))}
                raise ModsError(f"{names.get(needed_by, needed_by)} needs {names.get(wanted, wanted)}, "
                                f"which has no{loader} version for {mc} yet.")
            raise ModsError(f"There's no{loader} version of this {KINDS[kind]['noun']} for {mc} yet.")
        pid = build["project_id"]
        if pid in seen:
            continue
        seen.add(pid)
        if needed_by and pid in have:
            continue  # already there; the caller deals with it
        plan.append(build)
        for dep in build.get("dependencies") or []:
            if dep.get("dependency_type") != "required":
                continue
            dep_id = dep.get("project_id") or _get_json(f"{MODRINTH_API}/version/{dep['version_id']}")["project_id"]
            if dep_id not in seen:
                queue.append((dep_id, pid))
    return plan


def _titles(ids):
    ids = sorted(set(ids))
    return {p["id"]: p["title"] for p in _get_json(f"{MODRINTH_API}/projects", ids=json.dumps(ids))} if ids else {}


def _primary(build):
    files = build["files"]
    return next((f for f in files if f.get("primary")), files[0])


def _rename_in_options(game_dir, old, new):
    """Keep a resource pack turned on when its file name changes.

    options.txt lists the packs that are on as "file/<name>". The game
    rewrites options.txt when it closes, so this only sticks while it's closed.
    """
    options = game_dir / "options.txt"
    try:
        text = options.read_text()
    except OSError:
        return
    changed = text.replace(json.dumps(f"file/{old}"), json.dumps(f"file/{new}"))
    if changed != text:
        tmp = options.with_name("options.txt.tmp")
        tmp.write_text(changed)
        os.replace(tmp, options)


def install_project(project, kind, game, progress=lambda text: None):
    """Install the newest build of a Modrinth project (slug or id) for `game`.

    For mods, the mods it requires come too, unless they're already there.
    Older builds of the same projects are replaced. Returns the titles of
    what was installed, the asked-for project first.
    """
    spec = KINDS[kind]
    folder = game.folder(kind)
    have = {i.project: i for i in installed(kind, game) if i.project}
    plan = _resolve(project, kind, game, have)
    titles = _titles(b["project_id"] for b in plan)

    done = []
    for build in plan:
        pid = build["project_id"]
        title = titles.get(pid, build.get("name", pid))
        progress(f"Downloading {title}…")
        file = _primary(build)
        tmp = _download(file, folder, spec["ext"])
        old = have.get(pid)
        if old and old.path.name != file["filename"]:
            old.path.unlink(missing_ok=True)
            if kind == "resourcepack":
                _rename_in_options(game.game_dir, old.path.name, file["filename"])
        os.replace(tmp, folder / file["filename"])
        done.append(title)
    return done


# --- Upgrading a profile to another Minecraft version -------------------------

class UpgradePlan:
    """What upgrading a Fabric profile to Minecraft `mc` would do.

    mods: builds to install (the profile's mods and what they need).
    stuck: (Installed, reason) for mods that can't come along (yet).
    packs: (Installed, build) for resource packs with a newer build.
    """

    def __init__(self, profile, mc, loader):
        self.profile, self.mc, self.loader = profile, mc, loader
        self.mods, self.stuck, self.packs = [], [], []
        self.had = []  # installed mods Modrinth knows
        self.unchanged = set()  # paths of mods already on the right build
        self.titles = {}

    @property
    def same_version(self):
        return self.mc == self.profile.mc

    def nothing_to_do(self):
        return self.same_version and self.loader == self.profile.loader and not self.mods and not self.packs

    def new_mods(self):
        """Titles of mods that weren't in the profile before (newly required)."""
        had = {i.project for i in self.had}
        return sorted(self.titles.get(b["project_id"], b["name"]) for b in self.mods if b["project_id"] not in had)


def plan_upgrade(profile, mc, progress=lambda text: None):
    """Work out how to move a Fabric profile and its mods to Minecraft `mc`
    (or, for its own version, how to update all its mods). Changes nothing."""
    mc = _check_version(mc)
    if profile.key and profile.key.startswith(PACK_KEY.format(slug="")):
        raise ModsError(f"“{profile.name}” is a modpack. To update it, import the newer "
                        "version of the pack instead.")
    progress("Finding the newest Fabric loader…")
    loader = latest_fabric_loader(mc)
    target = GameFolder(profile.name, mc, profile.game_dir, loader=loader)
    plan = UpgradePlan(profile, mc, loader)

    progress("Looking up your mods on Modrinth…")
    mods = installed("mod", profile)
    plan.had = [i for i in mods if i.project]
    chosen, cache = {}, {}
    for n, item in enumerate(mods, 1):
        if not item.project:
            plan.stuck.append((item, "isn't on Modrinth, so it can't be updated here"))
            continue
        progress(f"Checking {n} of {len(mods)}: {item.title}")
        try:
            for build in _resolve(item.project, "mod", target, cache=cache):
                chosen.setdefault(build["project_id"], build)
        except ModsError as e:
            plan.stuck.append((item, str(e)))
    # A mod that's stuck can't be required by one that isn't (_resolve would
    # have failed), but its own required mods may have been picked: that's fine.
    # Builds that are already there (same file) stay as they are.
    present = {i.path.name: i.path for i in mods}
    for build in chosen.values():
        name = _primary(build)["filename"]
        if name in present:
            plan.unchanged.add(present[name])
        else:
            plan.mods.append(build)

    progress("Checking resource packs…")
    for item in installed("resourcepack", profile):
        if not item.project:
            continue
        try:
            build = _newest_build(item.project, "resourcepack", target)
        except ModsError:
            continue
        if build and _primary(build)["filename"] != item.path.name:
            plan.packs.append((item, build))
    plan.titles = _titles([b["project_id"] for b in plan.mods] + [b["project_id"] for _i, b in plan.packs])
    return plan


def apply_upgrade(plan, progress=lambda text: None):
    """Do what plan_upgrade() worked out. Everything is downloaded before
    anything is changed. Replaced files go to a backup folder in the profile's
    game folder, whose path is returned along with the profile's name."""
    profile, mc = plan.profile, plan.mc
    profiles_path = _launcher_profiles_path()
    mods_dir, packs_dir = profile.folder("mod"), profile.folder("resourcepack")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = profile.game_dir / f"backup-{profile.mc}-{stamp}"
    staging = profile.game_dir / f".upgrade-{stamp}"
    staging.mkdir(parents=True)
    try:
        staged = []
        downloads = [(b, mods_dir, ".jar") for b in plan.mods] + [(b, packs_dir, ".zip") for _i, b in plan.packs]
        for n, (build, folder, ext) in enumerate(downloads, 1):
            title = plan.titles.get(build["project_id"], build.get("name", ""))
            progress(f"Downloading {n} of {len(downloads)}: {title}")
            file = _primary(build)
            staged.append((_download(file, staging, ext), folder / file["filename"]))

        progress(f"Installing Fabric loader {plan.loader} for Minecraft {mc}…")
        vid = _install_fabric_version(mc, plan.loader)

        # Out with the old: every mod jar, except (when staying on the same
        # Minecraft) the ones that couldn't be updated, which still work.
        keep = set(plan.unchanged) | ({i.path for i, _r in plan.stuck} if plan.same_version else set())
        if mods_dir.is_dir():
            for jar in mods_dir.glob("*.jar"):
                if jar not in keep:
                    (backup / "mods").mkdir(parents=True, exist_ok=True)
                    os.replace(jar, backup / "mods" / jar.name)
        for item, build in plan.packs:
            (backup / "resourcepacks").mkdir(parents=True, exist_ok=True)
            os.replace(item.path, backup / "resourcepacks" / item.path.name)
            _rename_in_options(profile.game_dir, item.path.name, _primary(build)["filename"])
        mods_dir.mkdir(parents=True, exist_ok=True)
        for tmp, target in staged:
            os.replace(tmp, target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    with open(profiles_path) as f:
        launcher_profiles = json.load(f)
    shutil.copy2(profiles_path, profiles_path.with_name("launcher_profiles.json.bak-mods"))
    profiles = launcher_profiles.setdefault("profiles", {})
    key = profile.key
    entry = profiles.get(key)
    if entry is None:
        raise ModsError(f"“{profile.name}” is no longer in the launcher. Its mods were updated, "
                        "but install Fabric again to get a profile for them.")
    entry["lastVersionId"] = vid
    name = profile.name
    names = {p.get("name") for p in profiles.values() if isinstance(p, dict)}
    if name == f"Fabric {profile.mc}" and f"Fabric {mc}" not in names:
        name = entry["name"] = f"Fabric {mc}"  # still named after its version
    # This app's profiles are keyed by version; move the key along if it's free,
    # so installing Fabric for the old version later makes a new profile.
    new_key = PROFILE_KEY.format(mc=mc)
    if key == PROFILE_KEY.format(mc=profile.mc) and new_key not in profiles:
        profiles[new_key] = profiles.pop(key)
    _write_json(profiles_path, launcher_profiles)
    return name, (backup if backup.exists() else None)


def remove(item):
    """Delete an installed mod or resource pack."""
    try:
        item.path.unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        raise ModsError(f"Couldn't remove {item.path.name}: {e.strerror}") from e


# --- Modpacks (.mrpack) -----------------------------------------------------

def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "modpack"


def _inside(base, rel):
    """base/rel, refusing paths that would land outside base."""
    if not rel or rel.startswith(("/", "\\")) or "\\" in rel:
        raise ModsError(f"The modpack has a file with an unsafe path: {rel!r}. Nothing was changed.")
    target = (base / rel).resolve()
    if not target.is_relative_to(base.resolve()) or target == base.resolve():
        raise ModsError(f"The modpack has a file with an unsafe path: {rel!r}. Nothing was changed.")
    return target


def read_mrpack(path):
    """What a Modrinth modpack (.mrpack) holds, checked before anything is changed."""
    try:
        pack = zipfile.ZipFile(path)
        index = json.loads(pack.read("modrinth.index.json"))
    except (OSError, zipfile.BadZipFile, KeyError, ValueError):
        raise ModsError("That isn't a Modrinth modpack (.mrpack) file.") from None
    if index.get("game") != "minecraft" or index.get("formatVersion") != 1:
        raise ModsError("This modpack is for something other than Minecraft: Java Edition.")
    deps = index.get("dependencies") or {}
    others = set(deps) - {"minecraft", "fabric-loader"}
    if others:
        raise ModsError(f"This modpack needs {', '.join(sorted(others))}. Only Fabric modpacks are supported.")
    if "minecraft" not in deps:
        raise ModsError("The modpack doesn't say which Minecraft version it's for.")
    _check_version(deps["minecraft"])
    if "fabric-loader" in deps:
        _check_version(deps["fabric-loader"])
    files = []
    for f in index.get("files") or []:
        if (f.get("env") or {}).get("client") == "unsupported":
            continue  # server-only
        url = next((u for u in f.get("downloads") or [] if u.startswith(MODRINTH_CDN)), None)
        if not url:
            raise ModsError(f"{f.get('path')} isn't hosted on Modrinth, so this modpack can't be installed.")
        if not (f.get("hashes") or {}).get("sha512"):
            raise ModsError(f"{f.get('path')} has no checksum, so this modpack can't be installed.")
        files.append({"path": f["path"], "url": url, "sha512": f["hashes"]["sha512"]})
    return pack, index, files


def import_mrpack(path, progress=lambda text: None):
    """Install a Modrinth modpack as its own launcher profile and game folder.

    Importing the same pack again (say, a newer version) updates that profile:
    files the old version added and the new one doesn't have are removed,
    and worlds, settings and anything you added yourself stay.
    Returns the profile name.
    """
    pack, index, files = read_mrpack(path)
    profiles_path = _launcher_profiles_path()
    name = (index.get("name") or "Modpack").strip()[:64]
    slug = _slug(name)
    deps = index["dependencies"]
    mc, loader = deps["minecraft"], deps.get("fabric-loader")
    game_dir = stable_dot_minecraft() / "profiles" / f"pack-{slug}"
    game_dir.mkdir(parents=True, exist_ok=True)
    targets = [(f, _inside(game_dir, f["path"])) for f in files]
    base = game_dir.resolve()  # _inside() gives resolved paths
    overrides = []
    for prefix in ("overrides/", "client-overrides/"):  # client-overrides win
        for info in pack.infolist():
            if info.filename.startswith(prefix) and not info.is_dir() and info.filename != prefix:
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    continue  # no symlinks
                overrides.append((info, _inside(game_dir, info.filename[len(prefix):])))

    if loader:
        progress(f"Installing Fabric loader {loader} for Minecraft {mc}…")
        vid = _install_fabric_version(mc, loader)
    else:
        vid = mc

    new_files = set()
    for i, (f, target) in enumerate(targets, 1):
        progress(f"Downloading {i} of {len(targets)}: {target.name}")
        new_files.add(str(target.relative_to(base)))
        if target.is_file() and _sha512(target) == f["sha512"]:
            continue
        tmp = _download({"url": f["url"], "filename": target.name, "hashes": {"sha512": f["sha512"]}},
                        target.parent, target.suffix)
        os.replace(tmp, target)
    progress("Copying the modpack's settings…")
    for info, target in overrides:
        target.parent.mkdir(parents=True, exist_ok=True)
        with pack.open(info) as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)
        new_files.add(str(target.relative_to(base)))

    # Remove what the previous version of the pack added and this one doesn't.
    record = game_dir / PACK_RECORD
    try:
        old_files = set(json.loads(record.read_text()).get("files", []))
    except (OSError, ValueError):
        old_files = set()
    for rel in old_files - new_files:
        try:
            _inside(game_dir, rel).unlink(missing_ok=True)
        except (ModsError, OSError):
            pass
    _write_json(record, {"name": name, "version": index.get("versionId"), "files": sorted(new_files)})

    with open(profiles_path) as f:
        launcher_profiles = json.load(f)
    shutil.copy2(profiles_path, profiles_path.with_name("launcher_profiles.json.bak-mods"))
    profiles = launcher_profiles.setdefault("profiles", {})
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    key = PACK_KEY.format(slug=slug)
    entry = profiles.setdefault(key, {"created": now, "icon": "Crafting_Table", "type": "custom"})
    entry.update(name=name, lastVersionId=vid, gameDir=str(game_dir), lastUsed=now)
    _write_json(profiles_path, launcher_profiles)
    return name


def _sha512(path):
    digest = hashlib.sha512()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()
