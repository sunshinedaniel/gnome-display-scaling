#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check complete gettext coverage and format safety without a desktop."""

import gettext
import json
from pathlib import Path
import subprocess
import tempfile


SOURCE = Path(__file__).resolve().parents[1]
PO = SOURCE / "po"


def message_ids(text):
    messages = set()
    current = None
    for line in text.splitlines():
        if line.startswith("msgid "):
            current = json.loads(line.removeprefix("msgid "))
        elif current is not None and line.startswith('"'):
            current += json.loads(line)
        elif current is not None and line.startswith("msgstr "):
            if current:
                messages.add(current)
            current = None
    return messages


locales = PO.joinpath("LINGUAS").read_text().split()
assert len(locales) == len(set(locales)) == 20, "Expected twenty unique locales"
assert set(locales) == {path.stem for path in PO.glob("*.po")}

with tempfile.TemporaryDirectory(prefix="display-scale-translations-") as temporary:
    root = Path(temporary)
    template = root / "messages.pot"
    subprocess.run([
        "xgettext", "--language=JavaScript", "--from-code=UTF-8",
        "--keyword=_", "--keyword=translate:1", "--no-wrap", "--omit-header",
        f"--output={template}", "extension.js", "display-config.mjs",
    ], cwd=SOURCE, check=True)
    messages = message_ids(template.read_text())
    assert messages, "No runtime strings were extracted"
    assert message_ids(PO.joinpath("display-scale-toggle.pot").read_text()) == messages
    for locale in locales:
        source = PO / f"{locale}.po"
        text = source.read_text()
        assert "#, fuzzy" not in text, f"{locale}: fuzzy entries"
        assert message_ids(text) == messages, f"{locale}: incomplete or stale catalog"
        compiled = root / f"{locale}.mo"
        subprocess.run([
            "msgfmt", "--check", "--check-format", f"--output-file={compiled}", str(source),
        ], check=True)
        with compiled.open("rb") as stream:
            catalog = gettext.GNUTranslations(stream)
        for message in messages:
            translated = catalog.gettext(message)
            assert translated, (locale, message)
            if locale != "en":
                assert translated != message, f"{locale}: untranslated {message!r}"
            assert translated.count("%s") == message.count("%s"), (locale, message)
        print(f"PASS: {locale}: {len(messages)} translated messages, valid gettext formats.")
