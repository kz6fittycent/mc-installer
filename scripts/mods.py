#!/usr/bin/env python3
"""MC Mods (Minecraft mods): set up Fabric, find mods and resource packs on Modrinth,
and import modpacks.

The logic lives in mcmods.py; this file is only the GTK window.
"""

import sys
from pathlib import Path
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


def run_in_thread(func, done, *args):
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


def esc(text):
    return GLib.markup_escape_text(text)


def short_number(n):
    for size, unit in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if n >= size:
            return f"{n / size:.1f}".rstrip("0").rstrip(".") + unit
    return str(n)


class RowGroup(Adw.PreferencesGroup):
    """A preferences group whose rows are replaced all at once."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.rows = []

    def set_rows(self, rows):
        for row in self.rows:
            self.remove(row)
        self.rows = rows
        for row in rows:
            self.add(row)

    def message(self, text):
        self.set_rows([Adw.ActionRow(title=esc(text), css_classes=["dim-label"])])


class FabricPage(Adw.PreferencesPage):
    """Install or update Fabric, import modpacks, list the Fabric profiles."""

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.versions = []
        self.profiles = []
        self.upgrade_buttons = []
        self.toast = None
        self.busy = False
        self.hinting = False

        install = Adw.PreferencesGroup(
            title="Install Fabric",
            description="Fabric loads mods. Install it for the Minecraft version your "
                        "mods (or your server) use. It adds a “Fabric” profile to the launcher, "
                        "or updates the one you already have.",
        )
        self.add(install)

        self.version_row = Adw.ComboRow(title="Minecraft version", sensitive=False)
        self.version_row.set_model(Gtk.StringList.new(["Loading…"]))
        self.version_row.connect("notify::selected", self.update_button_label)
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

        # Modpacks
        packs = Adw.PreferencesGroup(
            title="Modpacks",
            description="Got a Modrinth modpack (.mrpack) from Modrinth or a server owner? "
                        "It gets its own profile in the launcher. Import a newer version "
                        "of the same pack to update it.",
        )
        self.add(packs)
        self.import_row = Adw.ButtonRow(title="Import a Modpack…", start_icon_name="document-open-symbolic") \
            if hasattr(Adw, "ButtonRow") else None
        if self.import_row:
            self.import_row.connect("activated", self.on_import)
            packs.add(self.import_row)
        else:
            self.import_row = Gtk.Button(label="Import a Modpack…", halign=Gtk.Align.CENTER, css_classes=["pill"])
            self.import_row.connect("clicked", self.on_import)
            packs.add(self.import_row)

        # Installed Fabric profiles
        self.installed = RowGroup(
            title="Your Fabric profiles",
            description="Add mods on the Mods tab, or put mod .jar files made for that "
                        "Minecraft version in its mods folder. Then pick the profile in the launcher.",
        )
        self.add(self.installed)
        self.refresh_installed()
        run_in_thread(mcmods.fabric_game_versions, self.got_versions)

    def check_launcher(self, running):
        idle = bool(self.versions) and not running and not self.busy
        self.install_button.set_sensitive(idle)
        self.import_row.set_sensitive(not running and not self.busy)
        for button in self.upgrade_buttons:
            button.set_sensitive(idle)

    # --- versions -----------------------------------------------------

    def got_versions(self, versions, error):
        if error:
            self.status.set_text(error)
            self.version_row.set_model(Gtk.StringList.new(["Couldn't load versions"]))
            return
        self.versions = versions
        self.version_row.set_model(Gtk.StringList.new(versions))
        # Start on the version of an existing Fabric profile, if there is one.
        have = next((fp.mc for fp in self.profiles if fp.mc in versions), None)
        if have:
            self.version_row.set_selected(versions.index(have))
        self.version_row.set_sensitive(True)
        self.update_button_label()
        self.window.check_launcher()

    def selected_version(self):
        i = self.version_row.get_selected()
        return self.versions[i] if self.versions and 0 <= i < len(self.versions) else None

    def update_button_label(self, *_args):
        mc = self.selected_version()
        pack = mcmods.PACK_KEY.format(slug="")
        mine = [fp for fp in self.profiles if not fp.key.startswith(pack)]
        existing = mc and any(fp.mc == mc for fp in mine)
        self.install_button.set_label("Update Fabric" if existing else "Install Fabric")
        # Point at Upgrade when they might mean "move my mods to this version".
        if not self.busy and (not self.status.get_text() or self.hinting):
            older = mc and not existing and mine and mc in self.versions and \
                any(fp.mc in self.versions and self.versions.index(fp.mc) > self.versions.index(mc) for fp in mine)
            self.hinting = bool(older)
            self.status.set_text(f"To move a profile and its mods to {mc}, use Upgrade… on it below."
                                 if older else "")

    # --- install ------------------------------------------------------

    def start(self, text):
        self.busy = True
        self.hinting = False
        self.version_row.set_sensitive(False)
        self.status.set_text(text)
        self.window.check_launcher()

    def say(self, text):
        GLib.idle_add(self.status.set_text, text)

    def finish(self, error):
        self.busy = False
        self.version_row.set_sensitive(bool(self.versions))
        self.window.check_launcher()
        if error:
            self.status.set_text(error)
            return False
        return True

    def on_install(self, _button):
        mc = self.selected_version()
        self.start(f"Installing Fabric for Minecraft {mc}…")
        run_in_thread(mcmods.install_fabric, self.installed_done, mc, self.api_row.get_active(), self.say)

    def installed_done(self, result, error):
        if not self.finish(error):
            return
        name, _mods_dir, updated = result
        if updated:
            self.status.set_text(f"Updated “{name}”. Your mods and worlds are still there.\n"
                                 "In the Minecraft launcher, pick it and press Play.")
            self.toast = Adw.Toast(title=f"{name} is up to date")
        else:
            self.status.set_text(f"Done. In the Minecraft launcher, pick “{name}” and press Play.")
            self.toast = Adw.Toast(title=f"{name} is ready")
        self.window.toasts.add_toast(self.toast)
        self.window.refresh_all()

    def on_import(self, *_args):
        dialog = Gtk.FileDialog(title="Import a Modpack")
        mrpack = Gtk.FileFilter(name="Modrinth modpacks (.mrpack)")
        mrpack.add_pattern("*.mrpack")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(mrpack)
        dialog.set_filters(filters)
        dialog.set_default_filter(mrpack)

        def picked(dialog, result):
            try:
                file = dialog.open_finish(result)
            except GLib.Error:
                return  # cancelled
            path = file.get_path()
            if not path:
                self.status.set_text("Couldn't open that file.")
                return
            self.start(f"Importing {Path(path).name}…")
            run_in_thread(mcmods.import_mrpack, self.imported, path, self.say)
        dialog.open(self.window, None, picked)

    def imported(self, name, error):
        if not self.finish(error):
            return
        self.status.set_text(f"Done. In the Minecraft launcher, pick “{name}” and press Play.")
        self.toast = Adw.Toast(title=f"{name} is ready")
        self.window.toasts.add_toast(self.toast)
        self.window.refresh_all()

    # --- upgrade a profile and its mods ----------------------------------

    def on_upgrade(self, _button, fp):
        # Newest first; the profile's own version last ("update mods only").
        choices = self.versions[:self.versions.index(fp.mc) + 1] if fp.mc in self.versions else list(self.versions)
        labels = [f"{v} (just update the mods)" if v == fp.mc else v for v in choices]
        drop = Gtk.DropDown.new_from_strings(labels)
        dialog = Adw.AlertDialog(
            heading=f"Upgrade “{fp.name}”",
            body="Pick the Minecraft version to move this profile and its mods to. "
                 "Nothing changes until you've seen which mods can come along.")
        dialog.set_extra_child(drop)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("check", "Check Mods")
        dialog.set_response_appearance("check", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("check")

        def answered(dialog, result):
            if dialog.choose_finish(result) != "check":
                return
            mc = choices[drop.get_selected()]
            self.start(f"Checking {fp.name}'s mods for Minecraft {mc}…")
            run_in_thread(mcmods.plan_upgrade, self.planned, fp, mc, self.say)
        dialog.choose(self.window, None, answered)

    def planned(self, plan, error):
        if not self.finish(error):
            return
        self.status.set_text("")
        fp, mc = plan.profile, plan.mc
        if plan.nothing_to_do():
            self.status.set_text(f"Everything in “{fp.name}” is already up to date.")
            return
        lines = []
        if not plan.same_version:
            lines.append(f"It gets Fabric {plan.loader} for Minecraft {mc}.")
        elif plan.loader != fp.loader:
            lines.append(f"Fabric is updated to {plan.loader}.")
        if plan.mods:
            n = len(plan.mods)
            lines.append(f"{n} mod{'s are' if n != 1 else ' is'} downloaded for Minecraft {mc}.")
        if plan.new_mods():
            lines.append("Newly needed: " + ", ".join(plan.new_mods()) + ".")
        if plan.packs:
            n = len(plan.packs)
            lines.append(f"{n} resource pack{'s are' if n != 1 else ' is'} updated.")
        if plan.stuck:
            head = "These stay as they are:" if plan.same_version else \
                "These can't come along yet and go to the backup folder:"
            lines.append(head + "".join(f"\n• {i.title}: {r}" for i, r in plan.stuck))
        lines.append("Replaced files are kept in a backup folder in the profile's game folder.")
        if not plan.same_version:
            lines.append(f"Worlds opened in {mc} can't go back to {fp.mc}. "
                         "Copy any world you care about first.")
        dialog = Adw.AlertDialog(heading=f"Move “{fp.name}” to {mc}?" if not plan.same_version
                                 else f"Update “{fp.name}”?", body="\n\n".join(lines))
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("go", "Upgrade" if not plan.same_version else "Update")
        dialog.set_response_appearance("go", Adw.ResponseAppearance.SUGGESTED)

        def answered(dialog, result):
            if dialog.choose_finish(result) != "go":
                return
            if mcmods.launcher_running():
                self.status.set_text("Close the Minecraft launcher first, then try again.")
                return
            self.start(f"Upgrading “{fp.name}”…")
            run_in_thread(mcmods.apply_upgrade, self.upgraded, plan, self.say)
        dialog.choose(self.window, None, answered)

    def upgraded(self, result, error):
        if not self.finish(error):
            return
        name, backup = result
        text = f"Done. In the Minecraft launcher, pick “{name}” and press Play."
        if backup:
            text += f"\nOld files are in {backup.name}, next to its mods folder."
        self.status.set_text(text)
        self.toast = Adw.Toast(title=esc(f"{name} is upgraded"))
        self.window.toasts.add_toast(self.toast)
        self.window.refresh_all()

    # --- installed profiles -------------------------------------------

    def refresh_installed(self):
        self.upgrade_buttons = []
        self.profiles = profiles = mcmods.fabric_profiles()
        if not profiles:
            self.installed.message("None yet")
            return
        rows = []
        for fp in profiles:
            jars = sorted(p.name for p in fp.mods_dir.glob("*.jar")) if fp.mods_dir.is_dir() else []
            mods = f"{len(jars)} mod{'s' if len(jars) != 1 else ''}" if jars else "no mods yet"
            row = Adw.ActionRow(title=esc(fp.name),
                                subtitle=esc(f"Minecraft {fp.mc} · Fabric {fp.loader} · {mods}"))
            if not fp.key.startswith(mcmods.PACK_KEY.format(slug="")):  # packs update by import
                upgrade = Gtk.Button(label="Upgrade…", valign=Gtk.Align.CENTER,
                                     tooltip_text="Move to a newer Minecraft, or update the mods")
                upgrade.connect("clicked", self.on_upgrade, fp)
                row.add_suffix(upgrade)
                self.upgrade_buttons.append(upgrade)
            button = Gtk.Button(label="Open mods folder", valign=Gtk.Align.CENTER)
            button.connect("clicked", lambda _b, path=fp.mods_dir: self.window.open_folder(path))
            row.add_suffix(button)
            rows.append(row)
        self.installed.set_rows(rows)
        if getattr(self.window, "fabric", None):  # not while the window is being built
            self.window.check_launcher()
        self.update_button_label()


class BrowsePage(Adw.PreferencesPage):
    """Search Modrinth for mods or resource packs, install them into a
    profile's game folder, and list and remove what's there."""

    def __init__(self, window, kind):
        super().__init__()
        self.window, self.kind = window, kind
        self.noun = mcmods.KINDS[kind]["noun"]
        self.folders = []
        self.have = {}          # project id -> Installed, for the selected folder
        self.search_gen = 0     # drops results of searches that were overtaken
        self.list_gen = 0
        self.working = set()    # project ids being installed
        self.hits = []

        where = Adw.PreferencesGroup()
        self.add(where)
        self.folder_row = Adw.ComboRow(
            title="Install into",
            subtitle="Fabric profiles have their own mods" if kind == "mod"
            else "Each profile has its own resource packs")
        self.folder_row.set_model(Gtk.StringList.new(["Loading…"]))
        self.folder_row.set_sensitive(False)
        self.folder_row.connect("notify::selected", self.folder_changed)
        where.add(self.folder_row)

        find = Adw.PreferencesGroup(
            title="Find on Modrinth",
            description="Fabric mods that run on your side of the game. Mods they need "
                        "are installed with them." if kind == "mod"
            else "Turn packs on in the game: Options › Resource Packs.")
        self.search = Gtk.SearchEntry(placeholder_text=f"Search {self.noun}s", search_delay=400)
        self.search.connect("search-changed", lambda *_a: self.run_search())
        find.add(self.search)
        self.add(find)
        self.results = RowGroup()
        self.add(self.results)

        self.installed = RowGroup(title="Installed")
        folder_button = Gtk.Button(label="Open folder", valign=Gtk.Align.CENTER, css_classes=["flat"])
        folder_button.connect("clicked", self.open_folder)
        self.installed.set_header_suffix(folder_button)
        self.add(self.installed)

    # --- folders ------------------------------------------------------

    def load_folders(self):
        run_in_thread(mcmods.game_folders, self.got_folders)

    def got_folders(self, folders, error):
        if error:
            folders = []
        if self.kind == "mod":
            folders = [f for f in folders if f.loader]
        old = self.selected_folder()
        self.folders = folders
        if not folders:
            self.folder_row.set_model(Gtk.StringList.new(
                ["Install Fabric first (Fabric tab)" if self.kind == "mod" else "Couldn't find Minecraft"]))
            self.folder_row.set_sensitive(False)
            self.search.set_sensitive(False)
            self.results.message("Mods need Fabric. Install it on the Fabric tab first."
                                 if self.kind == "mod" else error or "Open Minecraft once first.")
            self.installed.set_rows([])
            return
        self.folder_row.set_model(Gtk.StringList.new([f.name for f in folders]))
        keep = next((i for i, f in enumerate(folders) if old and f.game_dir == old.game_dir), 0)
        self.folder_row.set_selected(keep)
        self.folder_row.set_sensitive(True)
        self.search.set_sensitive(True)
        self.folder_changed()

    def selected_folder(self):
        i = self.folder_row.get_selected()
        return self.folders[i] if 0 <= i < len(self.folders) else None

    def folder_changed(self, *_args):
        folder = self.selected_folder()
        if folder:
            self.folder_row.set_subtitle(esc(folder.label().removeprefix(folder.name).strip(" ()")
                                             or "Any Minecraft version"))
            self.refresh_installed()
            self.run_search()

    def open_folder(self, _button):
        folder = self.selected_folder()
        if folder:
            self.window.open_folder(folder.folder(self.kind))

    # --- search -------------------------------------------------------

    def run_search(self):
        folder = self.selected_folder()
        if not folder:
            return
        self.search_gen += 1
        gen = self.search_gen
        run_in_thread(mcmods.search, lambda hits, error: self.got_hits(gen, hits, error),
                      self.search.get_text(), self.kind, folder)

    def got_hits(self, gen, hits, error):
        if gen != self.search_gen:
            return
        self.hits = hits or []
        if error:
            self.results.message(error)
        elif not hits:
            mc = self.selected_folder().mc
            self.results.message(f"Nothing found for Minecraft {mc}." if mc else "Nothing found.")
        else:
            self.show_hits()

    def show_hits(self):
        rows = []
        for hit in self.hits:
            by = f"by {hit.author} · " if hit.author else ""
            row = Adw.ActionRow(title=esc(hit.title),
                                subtitle=esc(f"{hit.description}\n{by}{short_number(hit.downloads)} downloads"),
                                subtitle_lines=3)
            if hit.id in self.working:
                row.add_suffix(Adw.Spinner() if hasattr(Adw, "Spinner") else Gtk.Spinner(spinning=True))
            elif hit.id in self.have:
                row.add_suffix(Gtk.Label(label="Installed", css_classes=["dim-label"]))
            else:
                button = Gtk.Button(label="Install", valign=Gtk.Align.CENTER)
                button.connect("clicked", self.on_install, hit)
                row.add_suffix(button)
            rows.append(row)
        self.results.set_rows(rows)  # the search box stays above them

    # --- install / remove ---------------------------------------------

    def on_install(self, _button, hit):
        self.working.add(hit.id)
        self.show_hits()
        run_in_thread(mcmods.install_project, lambda titles, error: self.install_done(hit, titles, error),
                      hit.id, self.kind, self.selected_folder())

    def install_done(self, hit, titles, error):
        self.working.discard(hit.id)
        if error:
            self.window.toasts.add_toast(Adw.Toast(title=esc(error), timeout=8))
        else:
            extra = f" (with {', '.join(titles[1:])})" if len(titles) > 1 else ""
            self.window.toasts.add_toast(Adw.Toast(title=esc(f"Installed {titles[0]}{extra}")))
        self.refresh_installed()
        self.window.fabric.refresh_installed()

    def on_remove(self, _button, item):
        dialog = Adw.AlertDialog(heading=f"Remove {item.title}?",
                                 body=f"This deletes {item.path.name}. You can install it again later.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)

        def answered(dialog, result):
            if dialog.choose_finish(result) != "remove":
                return
            try:
                mcmods.remove(item)
            except mcmods.ModsError as e:
                self.window.toasts.add_toast(Adw.Toast(title=esc(str(e))))
            self.refresh_installed()
            self.window.fabric.refresh_installed()
        dialog.choose(self.window, None, answered)

    def refresh_installed(self):
        folder = self.selected_folder()
        if not folder:
            return
        self.list_gen += 1
        gen = self.list_gen
        # The file names straight away, then the real names from Modrinth.
        self.show_installed(mcmods.installed(self.kind, folder, online=False))
        run_in_thread(mcmods.installed, lambda items, error: self.got_installed(gen, items, error),
                      self.kind, folder)

    def got_installed(self, gen, items, error):
        if gen == self.list_gen and not error:
            self.show_installed(items)
            if self.hits:
                self.show_hits()

    def show_installed(self, items):
        self.have = {i.project: i for i in items if i.project}
        if not items:
            self.installed.message(f"No {self.noun}s yet")
            return
        rows = []
        for item in items:
            row = Adw.ActionRow(title=esc(item.title), subtitle=esc(item.path.name))
            button = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Remove",
                                valign=Gtk.Align.CENTER, css_classes=["flat"])
            button.connect("clicked", self.on_remove, item)
            row.add_suffix(button)
            rows.append(row)
        self.installed.set_rows(rows)


class ModsWindow(Adw.ApplicationWindow):

    def __init__(self, app):
        super().__init__(application=app, title="MC Mods", default_width=600, default_height=720)
        fix_accent_colors()
        self.toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        view.add_top_bar(header)
        self.banner = Adw.Banner(title="Close the Minecraft launcher to install Fabric or a modpack.")
        view.add_top_bar(self.banner)
        self.toasts.set_child(view)
        self.set_content(self.toasts)

        self.stack = Adw.ViewStack()
        view.set_content(self.stack)
        header.set_title_widget(Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE))

        self.fabric = FabricPage(self)
        self.mods = BrowsePage(self, "mod")
        self.packs = BrowsePage(self, "resourcepack")
        # Inside the snap only a small set of icons exists (not the desktop's
        # theme), so these are picked from what the snap itself has.
        self.stack.add_titled_with_icon(self.fabric, "fabric", "Fabric", "emblem-system-symbolic")
        self.stack.add_titled_with_icon(self.mods, "mods", "Mods", "insert-object-symbolic")
        self.stack.add_titled_with_icon(self.packs, "packs", "Resource Packs", "color-select-symbolic")

        GLib.timeout_add_seconds(2, self.check_launcher)
        self.check_launcher()
        self.mods.load_folders()
        self.packs.load_folders()

    # The first page's widgets, as the screenshot tool knows them.
    @property
    def versions(self):
        return self.fabric.versions

    def check_launcher(self):
        running = mcmods.launcher_running()
        self.banner.set_revealed(running)
        self.fabric.check_launcher(running)
        return GLib.SOURCE_CONTINUE

    def refresh_all(self):
        self.fabric.refresh_installed()
        self.mods.load_folders()
        self.packs.load_folders()

    def open_folder(self, path):
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
