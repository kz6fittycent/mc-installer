# MC-INSTALLER

[![Minecraft Installer](https://snapcraft.io/mc-installer/badge.svg)](https://snapcraft.io/mc-installer)

## Installation
`sudo snap install mc-installer`

### Microphone (voice chat mods)
Microphone access is not connected automatically. To use voice chat mods such as Simple Voice Chat, run:

`sudo snap connect mc-installer:audio-record`

### Mods (Fabric)
Mods need a mod loader; putting `.jar` files in the mods folder isn't enough on its own. The snap includes a **Minecraft Mods** app (also in the right-click menu of the Minecraft icon, as *Set Up Mods*), or run:

`mc-installer.mods`

1. Close the Minecraft launcher.
2. Pick the Minecraft version your mods (or your server) use, and press **Install Fabric**. Fabric API, which most mods need, is installed too.
3. Press **Open mods folder** and put mods for that version there. Only download mods from sites you trust, such as [Modrinth](https://modrinth.com).
4. In the launcher, pick the **Fabric** profile and press Play.

Each Fabric profile has its own game folder (`~/snap/mc-installer/current/.minecraft/profiles/fabric-<version>/`), with its own mods, settings and worlds, so mods for one Minecraft version never load on another.

## About
A fork of the Minecraft-NSG installer snap. Omits the Oracle version of Java and just runs with openjdk. Skips the dialog and just downloads the .jar and runs the installer.

This does not come WITH Minecraft pre-installed. It simply downloads the `.jar` and allows you to play it on your Linux box.

Enjoy!

#### If you like what I'm doing, please consider supporting me on Patreon!
[![Patreon](https://img.shields.io/badge/Patreon-F96854?style=for-the-badge&logo=patreon&logoColor=white)](https://www.patreon.com/kz6fittycent)

### SPECIAL THANKS TO:

* [galgalesh](https://github.com/galgalesh)
* [popey](https://github.com/popey)
* [tommytran732](https://github.com/tommytran732)
* [kenvandine](https://github.com/kenvandine)

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=kz6fittycent/mc-installer&type=Date)](https://star-history.com/#kz6fittycent/mc-installer&Date)
