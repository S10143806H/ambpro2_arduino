#!/usr/bin/env python3
"""Register a freshly built board package in the Arduino Boards Manager index.

Rewrite of the private script used by .github/workflows/sdk_release.yml.
Call signature is kept identical so the workflow does not change:

    python ameba_arduino_release_package_maker.py \
        <repo_name> <E|R> <last_tag> <sha256> <size_bytes> <index_json_link> <release_branch>

What it does:
  1. Derives version "<x.y.z>-build<YYYYMMDD>" from the latest git tag (V4.1.1-QC-V13 -> 4.1.1).
  2. Renames Arduino_package/release/TEMP.tar.gz to <repo_name>-<version>.tar.gz.
  3. Inserts a new platform entry at the top of the index json, copied from the
     newest existing entry, with url / archiveFileName / checksum / size replaced
     and every toolsDependencies version bumped to the newest version listed in
     the "tools" section of the same file.
  4. Writes temp_release_commit_log.txt (used as the commit message).

E = early release -> package_realtek_amebapro2_early_index.json
R = formal release -> package_realtek_amebapro2_index.json
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

RELEASE_DIR = Path("Arduino_package/release")
INDEX_FILES = {
    "E": Path("Arduino_package/package_realtek_amebapro2_early_index.json"),
    "R": Path("Arduino_package/package_realtek_amebapro2_index.json"),
}
RAW_BASE = "https://github.com/Ameba-AIoT/ameba-arduino-pro2/raw/{branch}/Arduino_package/release/"


def version_from_tag(tag: str, fallback: str = "") -> str:
    """x.y.z from a tag like V4.1.1-QC-V13; if the tag is empty (a fork with no
    tags, say) fall back to the base version of the newest index entry."""
    m = re.search(r"(\d+\.\d+\.\d+(?:-\d+)?)", tag or "")
    if m:
        return m.group(1)
    m = re.match(r"(\d+\.\d+\.\d+(?:-\d+)?)", fallback or "")
    if m:
        print(f"no usable tag ('{tag}'); using version {m.group(1)} from the index")
        return m.group(1)
    sys.exit(f"cannot parse a version out of tag '{tag}' and no fallback version")


def newest_tool_versions(tools: list) -> dict:
    """name -> newest version string, comparing numeric parts first, then suffix."""
    def key(v):
        nums = [int(x) for x in re.findall(r"\d+", v.split("-")[0])]
        return (nums, v)
    best = {}
    for t in tools:
        n, v = t["name"], t["version"]
        if n not in best or key(v) > key(best[n]):
            best[n] = v
    return best


def first_platform_block(text: str):
    """Return (block_text, start_offset) of the first entry under "platforms": [
    — from its opening line (leading whitespace included) to its closing brace."""
    i = text.index('"platforms"')
    i = text.index("[", i)
    j = text.index("{", i)
    line_start = text.rfind("\n", 0, j) + 1
    depth, k, in_str, esc = 0, j, False, False
    while k < len(text):
        c = text[k]
        if in_str:
            if esc: esc = False
            elif c == "\\": esc = True
            elif c == '"': in_str = False
        else:
            if c == '"': in_str = True
            elif c == "{": depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[line_start:k + 1], line_start
        k += 1
    sys.exit("could not find the first platform entry in the index")


def main(argv):
    if len(argv) != 8:
        sys.exit(__doc__)
    repo_name, mode, last_tag, sha256, size, _json_link, release_branch = argv[1:]
    mode = mode.upper()
    if mode not in INDEX_FILES:
        sys.exit("mode must be E (early) or R (release)")

    index_path = INDEX_FILES[mode]
    data = json.loads(index_path.read_text(encoding="utf-8"))
    pkg = data["packages"][0]
    platforms = pkg["platforms"]
    if not platforms:
        sys.exit("index has no platform entry to copy from")

    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    version = f"{version_from_tag(last_tag, platforms[0].get('version', ''))}-build{today}"
    archive = f"{repo_name}-{version}.tar.gz"

    temp = RELEASE_DIR / "TEMP.tar.gz"
    if not temp.exists():
        sys.exit(f"{temp} not found; run tar_package.sh first")
    target = RELEASE_DIR / archive
    if target.exists():
        sys.exit(f"{target} already exists; a build for today was already released")
    temp.rename(target)

    newest = newest_tool_versions(pkg.get("tools", []))
    entry = json.loads(json.dumps(platforms[0]))  # deep copy, used for the commit message
    entry["version"] = version
    for dep in entry.get("toolsDependencies", []):
        if dep["name"] in newest:
            dep["version"] = newest[dep["name"]]

    # Splice the new entry in as TEXT so the rest of the hand-formatted file stays
    # byte-identical and the release diff is only the new block.
    branch = "dev" if mode == "E" else release_branch
    text = index_path.read_text(encoding="utf-8")
    block, start = first_platform_block(text)
    old = platforms[0]
    new = block
    new = new.replace(f'"{old["version"]}"', f'"{version}"')
    new = new.replace(old["url"], RAW_BASE.format(branch=branch) + archive)
    new = new.replace(old["archiveFileName"], archive)
    new = new.replace(old["checksum"], f"SHA-256:{sha256}")
    new = new.replace(f'"size": "{old["size"]}"', f'"size": "{size}"')
    for dep_old, dep_new in zip(old.get("toolsDependencies", []), entry.get("toolsDependencies", [])):
        if dep_old["version"] != dep_new["version"]:
            new = re.sub(
                r'("name":\s*"' + re.escape(dep_old["name"]) + r'",\s*"version":\s*")' + re.escape(dep_old["version"]) + '"',
                r'\g<1>' + dep_new["version"] + '"', new, count=1)
    text = text[:start] + new + ",\n" + text[start:]
    json.loads(text)  # must still parse
    index_path.write_text(text, encoding="utf-8")

    deps = ", ".join(f'{d["name"]} {d["version"]}' for d in entry.get("toolsDependencies", []))
    Path("temp_release_commit_log.txt").write_text(
        f"feat(arch): {'early' if mode == 'E' else 'formal'} release {version}\n\n"
        f"- package: {archive} ({int(size):,} bytes)\n"
        f"- sha256: {sha256}\n"
        f"- tools: {deps}\n",
        encoding="utf-8",
    )
    print(f"registered {archive} in {index_path} as {version}")


if __name__ == "__main__":
    main(sys.argv)
