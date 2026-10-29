"""Compile this project's singular PO entries using only Python's standard library.

Run: .venv/bin/python scripts/compile_translations.py
Use --check in verification to reject missing or stale committed MO catalogs.
GNU gettext can also read and compile these standard PO files.
"""
import argparse
import ast
from pathlib import Path
import re
import struct

ROOT = Path(__file__).resolve().parent.parent
PLACEHOLDER = re.compile(r"%\(([^)]+)\)[sdi]")


def read_catalog(path):
    messages = {}
    entry = {}
    field = None

    def finish():
        if not entry:
            return
        if set(entry) != {"msgid", "msgstr"}:
            raise ValueError(f"{path}: incomplete translation entry")
        source, target = entry["msgid"], entry["msgstr"]
        if source in messages:
            raise ValueError(f"{path}: duplicate message {source!r}")
        if not target:
            raise ValueError(f"{path}: empty translation for {source!r}")
        if set(PLACEHOLDER.findall(source)) != set(PLACEHOLDER.findall(target)):
            raise ValueError(f"{path}: translation placeholders differ for {source!r}")
        messages[source] = target
        entry.clear()

    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            finish()
            field = None
        elif line.startswith("#, fuzzy"):
            raise ValueError(f"{path}:{number}: review fuzzy translations first")
        elif line.startswith("#"):
            continue
        elif line.startswith(("msgid ", "msgstr ")):
            field, literal = line.split(" ", 1)
            if field == "msgid" and entry:
                finish()
            entry[field] = ast.literal_eval(literal)
        elif line.startswith('"') and field:
            entry[field] += ast.literal_eval(line)
        else:
            raise ValueError(f"{path}:{number}: unsupported PO syntax: {line}")
    finish()
    return messages


def compile_catalog(messages):
    pairs = sorted((key.encode("utf-8"), value.encode("utf-8")) for key, value in messages.items())
    count = len(pairs)
    originals, translations = bytearray(), bytearray()
    original_index, translation_index = [], []
    data_offset = 28 + 16 * count
    for source, _target in pairs:
        original_index.extend((len(source), data_offset + len(originals)))
        originals.extend(source + b"\0")
    for _source, target in pairs:
        translation_index.extend((len(target), data_offset + len(originals) + len(translations)))
        translations.extend(target + b"\0")
    header = struct.pack("<7I", 0x950412DE, 0, count, 28, 28 + count * 8, 0, 0)
    indexes = struct.pack(f"<{count * 4}I", *original_index, *translation_index)
    return header + indexes + originals + translations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check catalogs without writing files")
    options = parser.parse_args()
    for source in sorted((ROOT / "locale").glob("*/LC_MESSAGES/django.po")):
        compiled = compile_catalog(read_catalog(source))
        destination = source.with_suffix(".mo")
        if options.check:
            if not destination.exists() or destination.read_bytes() != compiled:
                parser.exit(1, f"Recompile stale translations: {destination.relative_to(ROOT)}\n")
        else:
            destination.write_bytes(compiled)
        print(f"{'Checked' if options.check else 'Compiled'} {source.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
