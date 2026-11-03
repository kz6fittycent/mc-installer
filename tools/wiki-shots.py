#!/usr/bin/env python3
"""Guided screenshots for the wiki.

Run it in a normal terminal (not inside the snap):

    python3 tools/wiki-shots.py            # every step
    python3 tools/wiki-shots.py game-title # just these steps

Each step says which screen to open and which key to press. GNOME saves
the screenshot to ~/Pictures/Screenshots; this script notices the new file
and moves it to ~/Pictures/mc-installer-wiki-raw/<name>.png, then goes on.
They stay out of the wiki on purpose: names, avatars and anything private
get cropped or blurred before an image goes in the wiki's images/.

  Alt+Print    the window in front (most steps)
  Shift+Print  the whole screen (menus; it gets cropped afterwards)

Press Enter to skip a step, or type b and Enter to redo the previous one.
"""

import select
import shutil
import subprocess
import sys
import time
from pathlib import Path

PICTURES = Path(subprocess.run(["xdg-user-dir", "PICTURES"], capture_output=True, text=True).stdout.strip() or Path.home() / "Pictures")
SCREENSHOTS = PICTURES / "Screenshots"
RAW = PICTURES / "mc-installer-wiki-raw"

WINDOW, SCREEN = "Alt+Print", "Shift+Print"

# (name, key, what to do). Names are the wiki's image file names.
STEPS = [
    # Installing
    ("appcenter-search", WINDOW, "Open App Center, search for  mc-installer  and leave the results showing."),
    ("appcenter-page", WINDOW, "Open the Minecraft Installer page in App Center (with its Install/Open button showing)."),
    # Starting
    ("app-grid", SCREEN, "Open the app grid (Super key, then Show Apps) and search  minecraft  so both Minecraft and MC Mods show."),
    ("app-menu-set-up-mods", SCREEN, "Right-click the Minecraft icon (in the app grid or the dock) so the menu with  Set Up Mods  shows."),
    # The launcher
    ("launcher-signin", WINDOW, "OPTIONAL (needs signing out): the launcher's sign-in screen. Press Enter to skip."),
    ("launcher-version-menu", WINDOW, "Click the version picker next to Play so the list of profiles (Latest release, Fabric…) is open."),
    ("launcher-installations", WINDOW, "The launcher's Installations tab."),
    ("launcher-new-installation", WINDOW, "Installations > New installation, with the version list showing (then cancel it)."),
    # The game
    ("game-title", WINDOW, "Start Latest release. The game's title screen."),
    ("game-create-world", WINDOW, "Singleplayer > Create New World: the create-world screen (you can cancel after)."),
    ("game-multiplayer", WINDOW, "Multiplayer: the server list."),
    ("game-add-server", WINDOW, "Multiplayer > Add Server, with a name and address typed in (e.g. ironhide's)."),
    ("game-connection-lost", WINDOW, "OPTIONAL: join ironhide from the plain 26.2 profile (no mods) to get the Connection Lost / registry error. Enter to skip."),
    # Mods
    ("files-mods-folder", WINDOW, "In MC Mods, press Open mods folder. The Files window that opens."),
    ("game-fabric-title", WINDOW, "Start the Fabric profile. The title screen (Fabric shows in the corner)."),
    ("game-mods-incompatible", WINDOW, "OPTIONAL: Fabric's 'Incompatible mods' screen, e.g. with a mod for the wrong version in the mods folder. Enter to skip."),
    # Microphone
    ("settings-app-permissions", WINDOW, "Settings > Apps > Minecraft Installer, showing its permissions (Microphone)."),
]


def newest_png(after):
    if not SCREENSHOTS.is_dir():
        return None
    files = [p for p in SCREENSHOTS.glob("*.png") if p.stat().st_mtime > after]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def wait_for_shot(after):
    """Return a new screenshot path, 'skip' or 'back'."""
    while True:
        ready, _, _ = select.select([sys.stdin], [], [], 0.5)
        if ready:
            answer = sys.stdin.readline().strip().lower()
            return "back" if answer == "b" else "skip"
        shot = newest_png(after)
        if shot:
            size = -1
            while size != shot.stat().st_size:  # wait until GNOME has finished writing it
                size = shot.stat().st_size
                time.sleep(0.3)
            return shot


def main():
    wanted = set(sys.argv[1:])
    steps = [s for s in STEPS if not wanted or s[0] in wanted]
    RAW.mkdir(parents=True, exist_ok=True)
    print(f"Saving to {RAW}\nEnter = skip, b + Enter = redo the previous step.\n")
    i = 0
    while i < len(steps):
        name, key, what = steps[i]
        print(f"[{i + 1}/{len(steps)}] {what}\n        then press {key}")
        result = wait_for_shot(time.time())
        if result == "back":
            i = max(0, i - 1)
            continue
        if result == "skip":
            print("        skipped\n")
        else:
            shutil.move(str(result), RAW / f"{name}.png")
            print(f"        saved {name}.png\n")
        i += 1
    print("Done.")


if __name__ == "__main__":
    main()
