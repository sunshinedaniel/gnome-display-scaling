#!/usr/bin/python3
"""Run with dbus-run-session on an isolated bus, never the desktop bus."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from gi.repository import Gio, GLib


UUID = "scale-toggle@local.daniel"
SOURCE = Path(__file__).resolve().parents[1]


def call(destination, path, interface, method, parameters=None):
    return connection.call_sync(
        destination, path, interface, method, parameters, None,
        Gio.DBusCallFlags.NONE, 5000, None,
    ).unpack()


def wait_for(predicate, description, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.15)
    raise AssertionError(f"Timed out: {description}")


def evaluate(code):
    success, result = call(
        "org.gnome.Shell", "/org/gnome/Shell", "org.gnome.Shell", "Eval",
        GLib.Variant("(s)", (code,)),
    )
    if not success:
        raise AssertionError(f"Shell evaluation failed: {result}")
    return json.loads(result) if result else None


if os.environ.get("SCALE_TOGGLE_PRIVATE_TEST_BUS") != "1":
    raise SystemExit("Use dbus-run-session -- env SCALE_TOGGLE_PRIVATE_TEST_BUS=1 /usr/bin/python3 tests/shell-smoke.py")

connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)

with tempfile.TemporaryDirectory(prefix="scale-toggle-smoke-") as temporary:
    root = Path(temporary)
    data = root / "data"
    extensions = data / "gnome-shell/extensions"
    extensions.mkdir(parents=True)
    (extensions / UUID).symlink_to(SOURCE, target_is_directory=True)
    environment = os.environ.copy()
    environment.pop("GI_TYPELIB_PATH", None)
    environment.update({
        "XDG_DATA_HOME": str(data),
        "XDG_CONFIG_HOME": str(root / "config"),
        "XDG_CACHE_HOME": str(root / "cache"),
        "XDG_STATE_HOME": str(root / "state"),
        "GSETTINGS_BACKEND": "memory",
        "GNOME_SHELL_SESSION_MODE": "user",
    })
    log_path = SOURCE.parent / "shell-smoke.log"
    with log_path.open("w") as log:
        shell = subprocess.Popen([
            "gnome-shell", "--headless", "--no-x11",
            "--virtual-monitor", "3840x2160",
            "--wayland-display", "scale-toggle-test",
            "--automation-script", str(SOURCE / "tests/shell-bootstrap.mjs"),
        ], env=environment, stdout=log, stderr=subprocess.STDOUT)
        try:
            def shell_ready():
                if shell.poll() is not None:
                    raise AssertionError(f"Isolated Shell exited with {shell.returncode}")
                owned = call(
                    "org.freedesktop.DBus", "/org/freedesktop/DBus",
                    "org.freedesktop.DBus", "NameHasOwner",
                    GLib.Variant("(s)", ("org.gnome.Shell",)),
                )[0]
                if not owned:
                    return False
                try:
                    return call(
                        "org.gnome.Shell", "/org/gnome/Shell", "org.gnome.Shell", "Eval",
                        GLib.Variant("(s)", ("global.context.unsafe_mode",)),
                    )[0]
                except GLib.Error as error:
                    if error.matches(Gio.dbus_error_quark(), Gio.DBusError.UNKNOWN_METHOD):
                        return False
                    raise

            wait_for(shell_ready, "isolated GNOME Shell startup")
            enabled = call(
                "org.gnome.Shell", "/org/gnome/Shell",
                "org.gnome.Shell.Extensions", "EnableExtension",
                GLib.Variant("(s)", (UUID,)),
            )[0]
            assert enabled, "The isolated Shell did not discover the extension"
            extension = f"Main.extensionManager.lookup('{UUID}').stateObj"

            def snapshot():
                return evaluate(f"""(() => {{
                    const e = {extension};
                    return {{
                        state: Main.extensionManager.lookup('{UUID}').state,
                        ready: !!e?._state,
                        busy: e?._busy,
                        checked: e?._toggle?.checked,
                        scale: e?._state?.scale,
                        preferred: e?._settings?.get_double('preferred-scale'),
                        submenu: e?._toggle?.menu.isOpen,
                        choices: e?._section?._getMenuItems().map(i => i.label.text),
                    }};
                }})()""")

            wait_for(lambda: snapshot().get("ready"), "tile initialization")
            state = snapshot()
            assert state["state"] == 1, state
            assert state["scale"] == 1 and not state["checked"], state
            assert state["preferred"] == 2 and "200%" in state["choices"], state
            print("PASS: GNOME 51 loads the native tile at 100%, with 200% selected.")

            evaluate(f"Main.panel.statusArea.quickSettings.menu.open(); {extension}._toggle._menuButton.emit('clicked', 1)")
            assert snapshot()["submenu"] and snapshot()["scale"] == 1
            print("PASS: the arrow opens the submenu without toggling scaling.")

            def select(label):
                evaluate(f"""{extension}._section._getMenuItems()
                    .find(item => item.label.text === '{label}').emit('activate', null)""")

            alternative = next((choice for choice in state["choices"] if choice != "200%"), None)
            assert alternative is not None, state
            select(alternative)
            state = snapshot()
            assert state["scale"] == 1 and not state["checked"], state
            assert round(state["preferred"] * 100) == int(alternative[:-1]), state
            print("PASS: choosing an on-scale while off does not change 100%.")

            select("200%")
            evaluate(f"{extension}._toggle._box.get_first_child().emit('clicked', 1)")
            wait_for(lambda: snapshot().get("scale") == 2 and not snapshot()["busy"], "left-click switches to 200%")
            assert snapshot()["checked"]
            print("PASS: the left half switches the real compositor to 200%.")

            select(alternative)
            wait_for(
                lambda: round(snapshot().get("scale", 0) * 100) == int(alternative[:-1]) and not snapshot()["busy"],
                "submenu changes the active scale",
            )
            print("PASS: selecting a different on-scale applies it immediately.")

            evaluate(f"{extension}._toggle._box.get_first_child().emit('clicked', 1)")
            wait_for(lambda: snapshot().get("scale") == 1 and not snapshot()["busy"], "left-click resets to 100%")
            assert not snapshot()["checked"]
            assert round(snapshot()["preferred"] * 100) == int(alternative[:-1])
            print("PASS: off restores 100% without forgetting the on-scale.")

            select("200%")
            call(
                "org.gnome.Shell", "/org/gnome/Shell",
                "org.gnome.Shell.Extensions", "DisableExtension",
                GLib.Variant("(s)", (UUID,)),
            )
            wait_for(lambda: evaluate(f"{extension}._toggle === null"), "extension cleanup")
            print("PASS: disabling removes the tile and disconnects the display listener.")
        except Exception:
            print(log_path.read_text()[-10000:])
            raise
        finally:
            shell.terminate()
            try:
                shell.wait(timeout=8)
            except subprocess.TimeoutExpired:
                shell.kill()
                shell.wait(timeout=5)
