#!/usr/bin/env python3
"""Merge the hand-written commit log into the cumulative release log.

Rewrite of the private script used by .github/workflows/sdk_release.yml.
Call signature is kept identical:

    python ameba_arduino_release_notes_maker.py <E|R> <release_log> <release_commit_log> <last_tag>

release_commit_log (filled by hand before each release) looks like:

    Feature:
    - Add X

    API Updates:
    - Change Y

    Misc:

release_log (cumulative, newest block on top) looks like:

    Pre Release Version 4.1.1

    Version 4.1.1 - 2026/03/06
    Version 4.1.1 - 2026/10/15

    Feature:
    - ...
    API Updates:
    - ...
    Misc:
    - ...

    <older version blocks>

What it does:
  1. Appends "Version <x.y.z> - <YYYY/MM/DD>" to the date list of the top block.
  2. Appends every bullet from the commit log to the matching section of the top block
     (skipping bullets already present).
  3. Resets release_commit_log to the empty template.
  For a formal release (R) the "Pre Release" header line is changed to "Release".
"""
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SECTIONS = ("Feature:", "API Updates:", "Misc:")
TEMPLATE = "\nFeature:\n\nAPI Updates:\n\nMisc:\n"


def version_from_tag(tag: str, fallback: str = "") -> str:
    """x.y.z from a tag; empty tag -> version from the release log header."""
    m = re.search(r"(\d+\.\d+\.\d+(?:-\d+)?)", tag or "")
    if m:
        return m.group(1)
    m = re.search(r"Version (\d+\.\d+\.\d+(?:-\d+)?)", fallback or "")
    if m:
        print(f"no usable tag ('{tag}'); using version {m.group(1)} from the release log")
        return m.group(1)
    sys.exit(f"cannot parse a version out of tag '{tag}' and no fallback version")


def parse_sections(text: str) -> dict:
    """{'Feature:': [bullets], ...} from a block of text."""
    out = {s: [] for s in SECTIONS}
    current = None
    for line in text.splitlines():
        s = line.strip()
        header = next((h for h in SECTIONS if s.startswith(h)), None)
        if header:
            current = header
            rest = s[len(header):].strip()
            if rest:  # "Feature: text" on one line -> treat text as a bullet
                out[current].append(f"- {rest}")
        elif current and s.startswith("-"):
            out[current].append(s)
    return out


def main(argv):
    if len(argv) != 5:
        sys.exit(__doc__)
    mode, log_path, commit_log_path, last_tag = argv[1:]
    mode = mode.upper()
    log_path, commit_log_path = Path(log_path), Path(commit_log_path)
    first_line = log_path.read_text(encoding="utf-8").splitlines()[0] if log_path.exists() else ""
    version = version_from_tag(last_tag, first_line)
    today = datetime.now(timezone.utc).strftime("%Y/%m/%d")

    new_items = parse_sections(commit_log_path.read_text(encoding="utf-8"))

    lines = log_path.read_text(encoding="utf-8").splitlines()
    # locate the top block: header, blank, date lines, blank, sections ... until next "Version" header or EOF
    try:
        first_section = next(i for i, l in enumerate(lines) if l.strip() == SECTIONS[0])
    except StopIteration:
        sys.exit(f"{log_path} has no 'Feature:' section in its top block")
    # end of top block = next line that looks like a block header after first_section
    block_end = len(lines)
    for i in range(first_section + 1, len(lines)):
        if re.match(r"^(Pre )?Release Version \d", lines[i]) or re.match(r"^Version \d+\.\d+\.\d+ - \d{4}/\d{2}/\d{2}$", lines[i]):
            block_end = i
            break

    head = lines[:first_section]
    body = lines[first_section:block_end]
    tail = lines[block_end:]

    # 1. header line + date list
    if head and head[0].startswith("Pre Release Version") and mode == "R":
        head[0] = head[0].replace("Pre Release Version", "Release Version", 1)
    date_line = f"Version {version} - {today}"
    last_date_idx = max((i for i, l in enumerate(head) if l.startswith("Version ")), default=None)
    if date_line not in head:
        if last_date_idx is None:
            head = [head[0], "", date_line, ""] if head else [f"Pre Release Version {version}", "", date_line, ""]
        else:
            head.insert(last_date_idx + 1, date_line)

    # 2. merge bullets into sections of the top block
    existing = parse_sections("\n".join(body))
    merged = []
    i = 0
    while i < len(body):
        line = body[i]
        merged.append(line)
        if line.strip() in SECTIONS:
            sec = line.strip()
            i += 1
            # copy existing bullets of this section
            while i < len(body) and body[i].strip().startswith("-"):
                merged.append(body[i])
                i += 1
            for item in new_items[sec]:
                if item not in existing[sec]:
                    merged.append(item)
            continue
        i += 1

    out = head + merged + tail
    log_path.write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")

    # 3. reset the commit log template
    commit_log_path.write_text(TEMPLATE, encoding="utf-8")
    added = sum(len(v) for v in new_items.values())
    print(f"release log updated: {date_line}, {added} note(s) merged")


if __name__ == "__main__":
    main(sys.argv)
