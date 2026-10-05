# Arch package for the GNOME frontend

This recipe builds `rog-control-center-gnome` from a pinned, checksum-verified
commit of this fork. It installs only the native Python/GTK4/libadwaita GUI,
desktop integration, documentation and licence. The package is architecture
independent and uses the installed `asusctl` daemon.

From this directory, with Arch's `base-devel` installed:

```sh
makepkg -s
sudo pacman -U ./rog-control-center-gnome-*.pkg.tar.zst
```

Accept the removal of `rog-control-center` when pacman asks. The replacement
provides `rog-control-center=6.5.0`; `asusctl` remains installed. The separate
package name keeps updates to the distribution's original GUI from replacing
the GNOME fork. No system services or configuration files are replaced.

The launcher remains `/usr/bin/rog-control-center`, so an existing GNOME custom
shortcut with that command continues working. Launch the app once to set up
the ROG key if no suitable shortcut exists. Both launch methods activate the
same GTK application, including when its window is already open.

Standard pacman hooks update desktop and icon caches; there is no custom
post-install script. User preferences and the legacy RON configuration are
retained. GTK smoke checks need a graphical session and can be run from the
source checkout as described in the frontend README; package checks run the
unit suite and validate desktop/AppStream metadata without modifying hardware
or desktop settings.

For a new frontend release, update `pkgver`, `_commit` and `sha256sums` together,
reset `pkgrel` to 1, then regenerate `.SRCINFO` with `makepkg --printsrcinfo`.
