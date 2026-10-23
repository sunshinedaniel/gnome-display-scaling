#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run with dbus-run-session on an isolated bus, never the desktop bus."""

import argparse
import gettext
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from gi.repository import Gio, GLib


SOURCE = Path(__file__).resolve().parents[1]
UUID = json.loads((SOURCE / "metadata.json").read_text())["uuid"]


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
    raise SystemExit("Use dbus-run-session --config-file=tests/session-bus.conf -- env SCALE_TOGGLE_PRIVATE_TEST_BUS=1 /usr/bin/python3 tests/shell-smoke.py")

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--screenshot", type=Path, help="Save the open Quick Settings submenu as a PNG.")
parser.add_argument("--extension-zip", type=Path, help="Test a packaged extension instead of the source tree.")
parser.add_argument("--language", default="en", help="Gettext language to exercise (default: en).")
arguments = parser.parse_args()
connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
activatable = call(
    "org.freedesktop.DBus", "/org/freedesktop/DBus",
    "org.freedesktop.DBus", "ListActivatableNames",
)[0]
if any(name != "org.freedesktop.DBus" for name in activatable):
    raise SystemExit("Use tests/session-bus.conf so unrelated desktop services cannot auto-start.")

with tempfile.TemporaryDirectory(prefix="scale-toggle-smoke-") as temporary:
    root = Path(temporary)
    data = root / "data"
    extensions = data / "gnome-shell/extensions"
    extensions.mkdir(parents=True)
    installed = extensions / UUID
    environment = os.environ.copy()
    environment.pop("GI_TYPELIB_PATH", None)
    available_locales = subprocess.check_output(["locale", "-a"], text=True).splitlines()
    test_locale = next((
        item for item in available_locales
        if item.lower().startswith("en_") and "utf" in item.lower()
    ), "C.UTF-8")
    if arguments.language != "en" and test_locale == "C.UTF-8":
        raise SystemExit("Native translation tests need an installed English UTF-8 locale.")
    environment["LANG"] = test_locale
    environment["LC_ALL"] = test_locale
    environment["LANGUAGE"] = arguments.language
    runtime = root / "runtime"
    runtime.mkdir(mode=0o700)
    environment.update({
        "XDG_DATA_HOME": str(data),
        "XDG_CONFIG_HOME": str(root / "config"),
        "XDG_CACHE_HOME": str(root / "cache"),
        "XDG_STATE_HOME": str(root / "state"),
        "XDG_RUNTIME_DIR": str(runtime),
        "GSETTINGS_BACKEND": "memory",
        "GNOME_SHELL_SESSION_MODE": "user",
        "HOME": str(root),
    })
    activation_environment = {
        key: value for key, value in environment.items()
        if key.startswith("XDG_") or key in {
            "HOME", "LANG", "LANGUAGE", "LC_ALL", "GSETTINGS_BACKEND",
        }
    }
    activation_environment["GI_TYPELIB_PATH"] = ""
    call(
        "org.freedesktop.DBus", "/org/freedesktop/DBus",
        "org.freedesktop.DBus", "UpdateActivationEnvironment",
        GLib.Variant("(a{ss})", (activation_environment,)),
    )
    if arguments.extension_zip:
        subprocess.run([
            "gnome-extensions", "install", str(arguments.extension_zip.resolve()),
        ], env=environment, check=True)
    else:
        installed.symlink_to(SOURCE, target_is_directory=True)
    translations = gettext.translation(
        "display-scale-toggle", localedir=installed / "locale",
        languages=[arguments.language], fallback=arguments.language == "en",
    )
    log_path = root / "shell-smoke.log"
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
                        title: e?._toggle?.title,
                        menuButtonName: e?._toggle?.menuButtonAccessibleName,
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
            assert state["title"] == translations.gettext("Scaling"), state
            assert state["menuButtonName"] == translations.gettext("Choose display scale"), state
            print(f"PASS: native labels load the {arguments.language} gettext catalog.")
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

            if arguments.screenshot:
                target = arguments.screenshot.resolve()
                target.parent.mkdir(parents=True, exist_ok=True)
                dimensions = evaluate(f"""(async () => {{
                    const {{default: Shell}} = await import('gi://Shell');
                    const {{default: Gio}} = await import('gi://Gio');
                    const {{default: GLib}} = await import('gi://GLib');
                    Main.overview.hide();
                    Main.panel.statusArea.quickSettings.menu.open();
                    {extension}._toggle.menu.open();
                    await new Promise(resolve => GLib.timeout_add(
                        GLib.PRIORITY_DEFAULT, 800, () => {{
                            resolve();
                            return GLib.SOURCE_REMOVE;
                        }}));
                    const toggle = {extension}._toggle;
                    const [tileX, tileY] = toggle.get_transformed_position();
                    const [tileWidth, tileHeight] = toggle.get_transformed_size();
                    const [menuX, menuY] = toggle.menu.actor.get_transformed_position();
                    const [menuWidth, menuHeight] = toggle.menu.actor.get_transformed_size();
                    if (menuWidth < 300 || menuHeight < 200)
                        throw new Error(`Submenu has no usable allocation: ${{menuWidth}} x ${{menuHeight}}`);
                    const left = Math.max(0, Math.floor(Math.min(tileX, menuX)) - 12);
                    const top = Math.max(0, Math.floor(Math.min(tileY, menuY)) - 12);
                    const right = Math.min(global.stage.width,
                        Math.ceil(Math.max(tileX + tileWidth, menuX + menuWidth)) + 12);
                    const bottom = Math.min(global.stage.height,
                        Math.ceil(Math.max(tileY + tileHeight, menuY + menuHeight)) + 12);
                    const stream = Gio.File.new_for_path({json.dumps(str(target))})
                        .replace(null, false, Gio.FileCreateFlags.REPLACE_DESTINATION, null);
                    try {{
                        await new Shell.Screenshot().screenshot_area(
                            left, top, right - left, bottom - top, stream);
                    }} finally {{
                        stream.close(null);
                    }}
                    return [right - left, bottom - top];
                }})()""")
                assert target.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
                print(f"PASS: captured the real open menu ({dimensions[0]} x {dimensions[1]}) to {target}.")

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
            assert evaluate(f"{extension}._subscription === 0 && {extension}._cancellable === null")
            print("PASS: disabling removes the tile and disconnects the display listener.")

            call(
                "org.gnome.Shell", "/org/gnome/Shell",
                "org.gnome.Shell.Extensions", "EnableExtension",
                GLib.Variant("(s)", (UUID,)),
            )
            wait_for(lambda: snapshot().get("ready"), "re-enabling the extension")
            assert snapshot()["preferred"] == 2 and snapshot()["scale"] == 1
            evaluate(f"""(() => {{
                const e = {extension};
                globalThis.scaleToggleTestCancellation = e._cancellable;
                e._applyScale(2);
                Main.extensionManager.disableExtension('{UUID}');
            }})()""")
            wait_for(lambda: evaluate(f"{extension}._toggle === null"), "cleanup during an asynchronous display read")
            assert evaluate("globalThis.scaleToggleTestCancellation.is_cancelled()")
            evaluate("delete globalThis.scaleToggleTestCancellation")
            print("PASS: repeated enable/disable preserves the selection and cancels in-flight work.")
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
