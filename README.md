# Display Scaling

A local GNOME Shell 51 extension that adds a native split-button tile to the
top-right Quick Settings menu.

- Click the left part to switch between **100%** and the remembered on-scale
  (**200%** by default).
- Click the right-hand arrow to choose an on-scale supported by the current
  display mode. Choosing while off only remembers the value; choosing while on
  immediately applies it.
- The tile reflects actual display scaling, including changes made in Settings.
  Resolution, refresh rate, rotation, and color mode are preserved.

The selected on-scale survives logins. Display changes are session-only, avoiding
a confirmation dialog on every click; this does not overwrite the monitor
configuration saved by GNOME Settings. Disabling the extension removes the tile
without changing display settings, including while the screen is locked.

This version supports a single active display or a mirrored display group.
Extended multi-monitor layouts are rejected rather than rearranged.

## Install

```sh
git clone https://github.com/sunshinedaniel/gnome-display-scaling.git
cd gnome-display-scaling
glib-compile-schemas --strict schemas
gnome-extensions pack --force --extra-source=display-config.mjs --extra-source=README.md .
gnome-extensions install scale-toggle@local.daniel.shell-extension.zip
```

New local extensions are discovered at the next GNOME Shell login on Wayland.
Enable **Display Scaling** in Extensions, or run:

```sh
gnome-extensions enable scale-toggle@local.daniel
```

## Remove

```sh
gnome-extensions disable scale-toggle@local.daniel
gnome-extensions uninstall scale-toggle@local.daniel
```

## Development

Run the pure display-configuration checks without a desktop:

```sh
node --test tests/display-config.test.mjs
```

The optional integration check launches a disposable, headless GNOME Shell on a
private D-Bus session. It exercises the actual split button and compositor scale
changes without changing the running desktop:

```sh
glib-compile-schemas --strict schemas
dbus-run-session -- env SCALE_TOGGLE_PRIVATE_TEST_BUS=1 /usr/bin/python3 tests/shell-smoke.py
```

This check needs GNOME Shell 51, Python with PyGObject, and a working headless
Mutter renderer. Its automation bootstrap is test-only and is not included in
the installed extension.
