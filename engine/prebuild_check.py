"""
prebuild_check.py  --  Run before `npm run tauri build`.

Tauri bundles engine/dist/nyam-engine/ wholesale as an application resource, so
anything sitting in that directory ships inside the installer. A `.env` copied
there for a quick local test would put a live API key into a distributable
binary — and nothing about the build would say so.

PyInstaller's --noconfirm happens to wipe dist/ each run, which has masked this
so far. That is luck, not a guarantee: build without --noconfirm, or drop a file
in afterwards, and it ships.

    python prebuild_check.py     # exits non-zero if anything looks unsafe
"""
import os
import re
import sys

DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dist", "nyam-engine")

#: Files that must never end up inside the bundled resource directory.
FORBIDDEN_NAMES = {".env", ".env.local", "credentials.json", "id_rsa"}

#: Anything matching these in a bundled file is treated as a live secret.
SECRET_PATTERNS = [
    (re.compile(rb"sk-ant-api\d{2}-[A-Za-z0-9_\-]{20,}"), "Anthropic API key"),
    (re.compile(rb"sk-[A-Za-z0-9]{32,}"), "generic secret key"),
    (re.compile(rb"ghp_[A-Za-z0-9]{20,}"), "GitHub token"),
]

#: Only scan small text-ish files; the bundle is ~120MB of compiled artifacts.
SCAN_MAX_BYTES = 256 * 1024
SCAN_SUFFIXES = {".env", ".txt", ".json", ".cfg", ".ini", ".yaml", ".yml",
                 ".md", ".py", ".toml", ""}

problems = []


def main():
    if not os.path.isdir(DIST):
        print(f"[skip] no build at {DIST} — nothing to check")
        return

    print(f"scanning {DIST}")
    files = 0
    for root, _dirs, names in os.walk(DIST):
        for name in names:
            files += 1
            path = os.path.join(root, name)
            rel = os.path.relpath(path, DIST)

            if name in FORBIDDEN_NAMES:
                problems.append(f"forbidden file bundled: {rel}")
                continue

            ext = os.path.splitext(name)[1].lower()
            if ext not in SCAN_SUFFIXES:
                continue
            try:
                if os.path.getsize(path) > SCAN_MAX_BYTES:
                    continue
                with open(path, "rb") as f:
                    blob = f.read()
            except OSError:
                continue
            for rx, label in SECRET_PATTERNS:
                if rx.search(blob):
                    problems.append(f"{label} found in bundled file: {rel}")
                    break

    print(f"  {files} files checked")
    if problems:
        print("\nUNSAFE TO BUILD:")
        for p in problems:
            print(f"  - {p}")
        print("\nRemove these from engine/dist/nyam-engine/ and rebuild the engine.")
        print("Secrets belong in the DEPLOYED engine/.env, which is never part of")
        print("the source bundle and survives redeploys.")
        sys.exit(1)

    print("  clean — no secrets in the bundle")


if __name__ == "__main__":
    main()
