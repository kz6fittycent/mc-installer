#!/usr/bin/env python3

import gi  # in package python3-gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib
import requests
import math

from threading import Thread
import hashlib
import os.path
import subprocess

# QUICK FIX - OLDER LAUNCHER
#DOWNLOAD_LINK = "https://archive.org/download/minecraft-launcher_202103/Minecraft.tar.gz"
DOWNLOAD_LINK = "https://launcher.mojang.com/download/Minecraft.tar.gz"
DOWNLOAD_FILE = "Minecraft.tar.gz"
RESULT_PATH = "minecraft-launcher"


def convert_size(size_bytes):
   if size_bytes == 0:
       return "0B"
   size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
   i = int(math.floor(math.log(size_bytes, 1024)))
   p = math.pow(1024, i)
   s = round(size_bytes / p, 2)
   return "{}{}".format(s, size_name[i])


class SnapUIWindow(Gtk.Window):

    def __init__(self):
        Gtk.Window.__init__(self, title="Downloading Minecraft Launcher")
        self.set_border_width(10)
        # self.set_default_size(640, 480)
        self.set_position(Gtk.WindowPosition.CENTER)

        hb = Gtk.HeaderBar()
        hb.set_show_close_button(True)
        hb.props.title = "Downloading Minecraft Launcher"
        self.set_titlebar(hb)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(vbox)

        self.infolabel = Gtk.Label(justify=Gtk.Justification.CENTER)
        vbox.pack_start(self.infolabel, False, True, 10)

        self.progressbar = Gtk.ProgressBar()
        self.progressbar.set_show_text(True)
        vbox.pack_start(self.progressbar, False, True, 0)

        self.retry_button = Gtk.Button.new_with_mnemonic("Retry Download")
        self.retry_button.connect("clicked", self.retry_onclick)
        vbox.pack_end(self.retry_button, False, True, 00)

    def retry_onclick(self, button):
        self.start_download()

    def start_download(self):
        self.infolabel.set_text("Downloading Minecraft: Java Edition launcher")
        self.progressbar.set_text(DOWNLOAD_LINK)
        self.progressbar.show()
        self.retry_button.hide()

        thread = Thread(target=self.download_thread)
        thread.daemon = True
        thread.start()

    def download_error(self, e):
        self.infolabel.set_text("Download failed!\nPlease check your internet connection and retry.")
        self.progressbar.set_text(str(e))
        self.progressbar.hide()
        self.retry_button.show()

    def download_thread(self):
        from pathlib import Path
        import requests

        try:
            r = requests.get(DOWNLOAD_LINK, allow_redirects=True, timeout=10, stream=True)
            r.raise_for_status()

            totalsize = int(r.headers.get('content-length', 0))
            blocksize = 2048
            blocknum = 0

            with open(DOWNLOAD_FILE, 'wb') as f:
                for chunk in r.iter_content(chunk_size=blocksize):
                    if chunk:
                        f.write(chunk)
                        f.flush()
                    self.report_progress(blocknum, blocksize, totalsize)
                    blocknum = blocknum + 1

        except requests.exceptions.RequestException as e:
            # Ask the main thread to execute this code.
            GLib.idle_add(self.download_error, e)
            return

        subprocess.call(['tar','-xzf', DOWNLOAD_FILE])
        subprocess.call(['rm','-f', DOWNLOAD_FILE])

        with open('Minecraft-Launcher-Last-Modified', 'w+') as f:
            f.write(r.headers.get("Last-Modified", DOWNLOAD_LINK))

        Gtk.main_quit()

    def report_progress(self, blocknum, blocksize, totalsize):
        p = round((blocknum * blocksize) / totalsize, 2)
        # Ask the main thread to execute this code. Because GTK isn't
        # thread safe, you cannot execute `set_fraction` in another thread.
        #   https://pygobject.readthedocs.io/en/latest/guide/threading.html
        GLib.idle_add(self.progressbar.set_fraction, p)
        GLib.idle_add(self.progressbar.set_text, "{}/{}".format(convert_size(blocknum*blocksize), convert_size(totalsize)))


def requires_update():
    """ requires_update returns true
         * If no launcher is present
         * If a launcher is present but a newer version is available online.
    """
    if not os.path.isdir(RESULT_PATH):
        # Launcher isn't present
        return True

    try:
        with open('Minecraft-Launcher-Last-Modified', 'r') as f:
            last_modified = f.read()
    except FileNotFoundError:
        last_modified = "NEVER"
    try:
        response = requests.head(DOWNLOAD_LINK, allow_redirects=True, timeout=10)
        if response.headers.get("Last-Modified", DOWNLOAD_LINK) != last_modified:
            # New Launcher Available
            return True
        else:
            # Launcher is already latest
            return False            
    except requests.exceptions.ConnectionError:
        # No network available
        return False

# Only start the GUI if we actually need to download the launcher.
if requires_update():
    win = SnapUIWindow()
    win.connect("delete-event", Gtk.main_quit)
    win.show_all()
    win.start_download()
    Gtk.main()

# This script returns exit code
#   0 (success) if launcher is present or
#   1 (fail)    if not.
if os.path.isdir(RESULT_PATH):
    exit(0)
else:
    exit(1)
