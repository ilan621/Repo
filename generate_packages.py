import os
import gzip
import bz2
import json
import hashlib
import re
from pathlib import Path


DEB_DIR = Path("debs")
OUTPUT = Path("Packages")


def parse_control(data):
    result = {}

    for line in data.splitlines():
        if not line.strip():
            continue

        if ":" not in line:
            continue

        key, value = line.split(":", 1)

        result[key.strip()] = value.strip()

    return result


def read_deb_control(path):

    import subprocess
    import tempfile

    try:

        result = subprocess.run(
            ["dpkg-deb", "-f", str(path)],
            capture_output=True,
            text=True,
            check=True
        )

        return parse_control(result.stdout)

    except Exception as e:

        print("读取 DEB 失败:", path)
        print(e)

        return {}


def make_entry(path):

    control = read_deb_control(path)

    if not control:
        return None

    relative = str(path).replace("\\", "/")

    size = path.stat().st_size

    with open(path, "rb") as f:
        sha256 = hashlib.sha256(f.read()).hexdigest()

    entry = {}

    fields = [
        "Package",
        "Name",
        "Version",
        "Architecture",
        "Description",
        "Maintainer",
        "Author",
        "Section",
        "Depends",
        "Conflicts",
        "Provides",
        "Installed-Size",
        "Homepage"
    ]

    for field in fields:

        if field in control:
            entry[field] = control[field]

    entry["Filename"] = relative
    entry["Size"] = str(size)
    entry["SHA256"] = sha256

    return entry


def main():

    entries = []

    if not DEB_DIR.exists():

        DEB_DIR.mkdir(
            parents=True,
            exist_ok=True
        )

    for path in sorted(DEB_DIR.rglob("*.deb")):

        print("Processing:", path)

        entry = make_entry(path)

        if entry:
            entries.append(entry)

    with open(
        OUTPUT,
        "w",
        encoding="utf-8"
    ) as f:

        for entry in entries:

            for key, value in entry.items():

                f.write(
                    f"{key}: {value}\n"
                )

            f.write("\n")

    with open(
        OUTPUT,
        "rb"
    ) as f:

        content = f.read()

    with gzip.open(
        "Packages.gz",
        "wb"
    ) as f:

        f.write(content)

    with bz2.open(
        "Packages.bz2",
        "wb"
    ) as f:

        f.write(content)

    json_data = []

    for entry in entries:
        json_data.append(entry)

    with open(
        "Packages.json",
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            json_data,
            f,
            ensure_ascii=False,
            indent=2
        )

    print()
    print("================================")
    print("RootHide Packages generated")
    print("Plugins:", len(entries))
    print("================================")


if __name__ == "__main__":
    main()
