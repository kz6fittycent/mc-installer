#!/usr/bin/env python3
"""Make the MC Mods app screenshots in docs/screenshots/.

Runs inside the installed snap, against a throwaway .minecraft (your real one
isn't touched), and renders the window with GTK itself, so no screenshot
permission is needed:

    snap run --shell mc-installer -c 'python3 tools/mods-shots.py'

It installs Fabric API, Sodium, Iris and a resource pack from Modrinth into
the throwaway folder so the lists look real, and shows the Upgrade dialog.
"""

import json
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "docs" / "screenshots"
MC = "26.2"

# A throwaway SNAP_USER_DATA: <tmp>/1/.minecraft, plus the "current" link.
fake = Path(os.environ["SNAP_USER_COMMON"]) / "mods-shots"
shutil.rmtree(fake, ignore_errors=True)
(fake / "1" / ".minecraft").mkdir(parents=True)
(fake / "current").symlink_to("1")
(fake / "1" / ".minecraft" / "launcher_profiles.json").write_text(json.dumps({"profiles": {}, "settings": {}}))
os.environ["SNAP_USER_DATA"] = str(fake / "1")

sys.path.insert(0, str(REPO / "scripts"))
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Graphene, Gtk

import mcmods
import mods

launcher_open = False
mcmods.launcher_running = lambda: launcher_open


def save(win, name):
    w, h = win.get_width(), win.get_height()
    snapshot = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(win).snapshot(snapshot, w, h)
    rect = Graphene.Rect().init(0, 0, w, h)
    texture = win.get_renderer().render_texture(snapshot.to_node(), rect)
    texture.save_to_png(str(OUT / name))
    print("saved", OUT / name)


def wait_for(cond, then, timeout_s=120):
    ticks = [0]

    def tick():
        ticks[0] += 1
        if cond():
            GLib.timeout_add(600, then)  # let the frame settle
            return GLib.SOURCE_REMOVE
        if ticks[0] > timeout_s * 5:
            print("timed out", file=sys.stderr)
            app.quit()
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE
    GLib.timeout_add(200, tick)


def run(app):
    global launcher_open
    Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
    win = mods.ModsWindow(app)
    # Default Adwaita blue, whatever accent this desktop uses.
    blue = Gtk.CssProvider()
    blue.load_from_string(":root { --accent-bg-color: #3584e4; --accent-color: #1c71d8; }")
    Gtk.StyleContext.add_provider_for_display(win.get_display(), blue, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
    win.set_default_size(620, 760)
    win.present()
    OUT.mkdir(parents=True, exist_ok=True)

    fabric = win.fabric

    def shot_start():
        fabric.version_row.set_selected(win.versions.index(MC))
        GLib.timeout_add(400, lambda: save(win, "mods-app.png") or shot_install())

    def shot_install():
        fabric.on_install(None)
        wait_for(lambda: fabric.status.get_text().startswith("Done"), shot_done)

    def shot_done():
        profile = mcmods.fabric_profiles()[0]
        mcmods.install_project("iris", "mod", profile)  # brings Sodium
        win.refresh_all()
        GLib.timeout_add(1500, lambda: save(win, "mods-app-installed.png") or shot_mods())

    def shot_mods():
        if fabric.toast:
            fabric.toast.dismiss()
        win.stack.set_visible_child_name("mods")
        win.mods.search.set_text("minimap")
        wait_for(lambda: win.mods.hits and win.mods.search_gen and len(win.mods.installed.rows) > 1,
                 lambda: save(win, "mods-app-search.png") or shot_packs())

    def shot_packs():
        packs = win.packs
        packs.folder_row.set_selected(len(packs.folders) - 1)  # Minecraft without mods
        hits = mcmods.search("", "resourcepack", packs.selected_folder())
        mcmods.install_project(hits[0].id, "resourcepack", packs.selected_folder())
        packs.refresh_installed()
        win.stack.set_visible_child_name("packs")
        wait_for(lambda: packs.hits and packs.have, lambda: save(win, "mods-app-resourcepacks.png") or shot_upgrade())

    def shot_upgrade():
        # The confirmation for moving the 26.2 profile to the newest Minecraft.
        win.stack.set_visible_child_name("fabric")
        profile = mcmods.fabric_profile_for(MC)
        fabric.planned(mcmods.plan_upgrade(profile, win.versions[0]), None)

        def shot():
            save(win, "mods-app-upgrade.png")
            win.get_visible_dialog().force_close()
            shot_banner()
        GLib.timeout_add(800, shot)

    def shot_banner():
        global launcher_open
        launcher_open = True
        win.stack.set_visible_child_name("fabric")
        win.check_launcher()
        fabric.status.set_text("")
        GLib.timeout_add(800, lambda: save(win, "mods-app-launcher-open.png") or finish())

    def finish():
        shutil.rmtree(fake, ignore_errors=True)
        app.quit()

    wait_for(lambda: bool(win.versions), shot_start)


app = Adw.Application(application_id="io.snapcraft.mc-installer.shots", flags=mods.Gio.ApplicationFlags.NON_UNIQUE)
app.connect("activate", run)
app.run([])
