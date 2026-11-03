#!/usr/bin/env python3
"""Minecraft Mods: a small window for setting up Fabric (and later, mods).

The logic lives in mcmods.py; this file is only the GTK window.
"""

import sys
from threading import Thread

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

import mcmods

APP_ID = "io.snapcraft.mc-installer.mods"
_accent_fixed = False


def fix_accent_colors():
    """Set libadwaita's accent CSS variables from the desktop's accent color.

    Inside the snap they come out empty, so suggested buttons have no fill
    and switches look off even when on. Setting them ourselves fixes that
    and still follows the desktop's accent and dark/light style. It needs
    USER priority: libadwaita's own accent rules beat APPLICATION.
    """
    global _accent_fixed
    if _accent_fixed:
        return
    _accent_fixed = True
    style = Adw.StyleManager.get_default()
    provider = Gtk.CssProvider()

    def update(*_args):
        bg = style.get_accent_color_rgba().to_string()
        fg = style.get_accent_color().to_standalone_rgba(style.get_dark()).to_string()
        provider.load_from_string(
            f":root {{ --accent-bg-color: {bg}; --accent-color: {fg}; --accent-fg-color: white; }}")

    update()
    style.connect("notify::accent-color-rgba", update)
    style.connect("notify::dark", update)
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)


class ModsWindow(Adw.ApplicationWindow):

    def __init__(self, app):
        super().__init__(application=app, title="Minecraft Mods", default_width=520, default_height=600)
        fix_accent_colors()
        self.versions = []
        self.toast = None
        self.busy = False

        self.toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        self.banner = Adw.Banner(title="Close the Minecraft launcher before installing.")
        view.add_top_bar(self.banner)
        self.toasts.set_child(view)
        self.set_content(self.toasts)

        page = Adw.PreferencesPage()
        view.set_content(page)

        # Install Fabric
        install = Adw.PreferencesGroup(
            title="Install Fabric",
            description="Fabric loads mods. Install it for the Minecraft version your "
                        "mods (or your server) use. It adds a “Fabric” profile to the launcher.",
        )
        page.add(install)

        self.version_row = Adw.ComboRow(title="Minecraft version", sensitive=False)
        self.version_row.set_model(Gtk.StringList.new(["Loading…"]))
        install.add(self.version_row)

        self.api_row = Adw.SwitchRow(title="Also install Fabric API", subtitle="Most mods need it", active=True)
        install.add(self.api_row)

        self.install_button = Gtk.Button(label="Install Fabric", sensitive=False, margin_top=12,
                                         halign=Gtk.Align.CENTER, css_classes=["pill", "suggested-action"])
        self.install_button.connect("clicked", self.on_install)
        self.status = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, margin_top=6,
                                css_classes=["dim-label"])
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self.install_button)
        box.append(self.status)
        install.add(box)

        # Installed Fabric profiles
        self.installed = Adw.PreferencesGroup(
            title="Your Fabric profiles",
            description="Each one has its own mods folder. Put mod .jar files for that "
                        "Minecraft version there, then pick the profile in the launcher.",
        )
        page.add(self.installed)
        self.installed_rows = []
        self.refresh_installed()

        GLib.timeout_add_seconds(2, self.check_launcher)
        self.check_launcher()
        self.run_in_thread(mcmods.fabric_game_versions, self.got_versions)

    # --- helpers ------------------------------------------------------

    def run_in_thread(self, func, done, *args):
        """Run func(*args) off the main thread; call done(result, error) on it."""
        def work():
            try:
                result, error = func(*args), None
            except mcmods.ModsError as e:
                result, error = None, str(e)
            except Exception as e:  # show anything unexpected instead of hanging
                result, error = None, f"Something went wrong: {e}"
            GLib.idle_add(done, result, error)
        Thread(target=work, daemon=True).start()

    def say(self, text):
        GLib.idle_add(self.status.set_text, text)

    def check_launcher(self):
        running = mcmods.launcher_running()
        self.banner.set_revealed(running)
        self.install_button.set_sensitive(bool(self.versions) and not running and not self.busy)
        return GLib.SOURCE_CONTINUE

    # --- versions -----------------------------------------------------

    def got_versions(self, versions, error):
        if error:
            self.status.set_text(error)
            self.version_row.set_model(Gtk.StringList.new(["Couldn't load versions"]))
            return
        self.versions = versions
        self.version_row.set_model(Gtk.StringList.new(versions))
        self.version_row.set_sensitive(True)
        self.check_launcher()

    # --- install ------------------------------------------------------

    def on_install(self, _button):
        mc = self.versions[self.version_row.get_selected()]
        self.busy = True
        self.install_button.set_sensitive(False)
        self.version_row.set_sensitive(False)
        self.status.set_text(f"Installing Fabric for Minecraft {mc}…")
        self.run_in_thread(mcmods.install_fabric, self.installed_done, mc, self.api_row.get_active(), self.say)

    def installed_done(self, result, error):
        self.busy = False
        self.version_row.set_sensitive(True)
        self.check_launcher()
        if error:
            self.status.set_text(error)
            return
        name, _mods_dir = result
        self.status.set_text(f"Done. In the Minecraft launcher, pick “{name}” and press Play.")
        self.toast = Adw.Toast(title=f"{name} is ready")
        self.toasts.add_toast(self.toast)
        self.refresh_installed()

    # --- installed profiles -------------------------------------------

    def refresh_installed(self):
        for row in self.installed_rows:
            self.installed.remove(row)
        self.installed_rows = []
        profiles = mcmods.fabric_profiles()
        if not profiles:
            row = Adw.ActionRow(title="None yet")
            self.installed.add(row)
            self.installed_rows.append(row)
        for mc, mods_dir in profiles:
            jars = sorted(p.name for p in mods_dir.glob("*.jar")) if mods_dir.is_dir() else []
            names = ", ".join(j[:-4] for j in jars)
            row = Adw.ActionRow(title=f"Fabric {mc}",
                                subtitle=f"{len(jars)} mod{'s' if len(jars) != 1 else ''}: {names}" if jars else "No mods yet")
            button = Gtk.Button(label="Open mods folder", valign=Gtk.Align.CENTER)
            button.connect("clicked", self.open_folder, mods_dir)
            row.add_suffix(button)
            self.installed.add(row)
            self.installed_rows.append(row)

    def open_folder(self, _button, path):
        path.mkdir(parents=True, exist_ok=True)
        launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(str(path)))

        def done(launcher, result):
            try:
                launcher.launch_finish(result)
            except GLib.Error as e:
                self.toasts.add_toast(Adw.Toast(title=f"Couldn't open the folder: {e.message}"))
        launcher.launch(self, None, done)


class ModsApp(Adw.Application):

    def __init__(self):
        # NON_UNIQUE: strict confinement doesn't let the snap own a D-Bus name,
        # so don't try to register one (that fails with AccessDenied).
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)

    def do_activate(self):
        (self.props.active_window or ModsWindow(self)).present()


if __name__ == "__main__":
    sys.exit(ModsApp().run(sys.argv))
