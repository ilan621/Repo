import os
import gzip
import bz2
import json
import hashlib
import subprocess
from pathlib import Path
from email.utils import formatdate


DEB_DIR = Path("debs")
OUTPUT = Path("Packages")
PACKAGES_JSON = Path("packages.json")


# ============================================================
# Debian control 解析
# ============================================================

def parse_control(data):
    result = {}
    current_key = None

    for line in data.splitlines():

        # 空行
        if not line.strip():
            continue

        # Debian control 的续行
        if line.startswith((" ", "\t")) and current_key:
            result[current_key] += "\n" + line.strip()
            continue

        if ":" not in line:
            continue

        key, value = line.split(":", 1)

        key = key.strip()
        value = value.strip()

        result[key] = value
        current_key = key

    return result


# ============================================================
# 读取 DEB
# ============================================================

def read_deb_control(path):

    try:

        result = subprocess.run(
            ["dpkg-deb", "-f", str(path)],
            capture_output=True,
            text=True,
            check=True
        )

        return parse_control(result.stdout)

    except Exception as e:

        print("❌ 读取 DEB 失败:", path)
        print(e)

        return {}


# ============================================================
# 读取网页上传器生成的 packages.json
# ============================================================

def load_custom_metadata():

    if not PACKAGES_JSON.exists():

        print("ℹ️ 没有找到 packages.json")
        return {}

    try:

        with open(
            PACKAGES_JSON,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

    except Exception as e:

        print("⚠️ packages.json 读取失败:")
        print(e)

        return {}

    packages = data.get("packages", [])

    result = {}

    for item in packages:

        package_id = (
            item.get("package")
            or item.get("Package")
        )

        if not package_id:
            continue

        result[package_id] = item

    print(
        f"✅ 读取自定义插件信息：{len(result)} 个"
    )

    return result


# ============================================================
# 计算 SHA256
# ============================================================

def sha256_file(path):

    sha256 = hashlib.sha256()

    with open(path, "rb") as f:

        for chunk in iter(
            lambda: f.read(1024 * 1024),
            b""
        ):

            sha256.update(chunk)

    return sha256.hexdigest()


# ============================================================
# 生成插件条目
# ============================================================

def make_entry(path, custom_metadata):

    control = read_deb_control(path)

    if not control:

        return None

    package_id = control.get(
        "Package",
        ""
    )

    if not package_id:

        print(
            "⚠️ DEB 没有 Package:",
            path
        )

        return None


    # --------------------------------------------------------
    # 原始 DEB 信息
    # --------------------------------------------------------

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


    # --------------------------------------------------------
    # 自定义信息
    # --------------------------------------------------------

    custom = custom_metadata.get(
        package_id,
        {}
    )


    # 自定义 Package ID
    if custom.get("package"):
        entry["Package"] = custom["package"]


    # 自定义名称
    if custom.get("name"):
        entry["Name"] = custom["name"]


    # 自定义版本
    if custom.get("version"):
        entry["Version"] = custom["version"]


    # 自定义作者
    if custom.get("author"):
        entry["Author"] = custom["author"]


    # 自定义架构
    if custom.get("architecture"):
        entry["Architecture"] = custom["architecture"]


    # --------------------------------------------------------
    # ⭐ 自定义中文描述优先
    # --------------------------------------------------------

    if custom.get("description"):

        entry["Description"] = custom["description"]


    # --------------------------------------------------------
    # 文件信息
    # --------------------------------------------------------

    relative = str(
        path
    ).replace("\\", "/")

    entry["Filename"] = relative

    entry["Size"] = str(
        path.stat().st_size
    )

    entry["SHA256"] = sha256_file(path)


    return entry


# ============================================================
# 生成 Packages
# ============================================================

def generate_packages(entries):

    with open(
        OUTPUT,
        "w",
        encoding="utf-8",
        newline="\n"
    ) as f:

        for entry in entries:

            for key, value in entry.items():

                # Debian 多行 Description
                if key == "Description" and "\n" in value:

                    lines = value.splitlines()

                    if lines:

                        f.write(
                            f"{key}: {lines[0]}\n"
                        )

                        for line in lines[1:]:

                            if line.strip():

                                f.write(
                                    f" {line}\n"
                                )

                            else:

                                f.write(
                                    " .\n"
                                )

                    continue

                f.write(
                    f"{key}: {value}\n"
                )

            f.write("\n")


# ============================================================
# 压缩 Packages
# ============================================================

def generate_compressed():

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


# ============================================================
# 生成 Packages.json
# ============================================================

def generate_packages_json(entries):

    with open(
        "Packages.json",
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            entries,
            f,
            ensure_ascii=False,
            indent=2
        )


# ============================================================
# 生成 Release
# ============================================================

def generate_release():

    files = [
        "Packages",
        "Packages.gz",
        "Packages.bz2"
    ]


    md5_lines = []
    sha256_lines = []


    for filename in files:

        path = Path(filename)

        if not path.exists():
            continue


        size = path.stat().st_size


        md5 = hashlib.md5(
            path.read_bytes()
        ).hexdigest()


        sha256 = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()


        md5_lines.append(
            f" {md5} {size} {filename}"
        )

        sha256_lines.append(
            f" {sha256} {size} {filename}"
        )


    content = f"""Origin: ilan
Label: ilan
Suite: stable
Codename: stable
Architectures: iphoneos-arm64 iphoneos-arm64e
Components: main
Description: ilan RootHide Repository
Date: {formatdate(usegmt=True)}

MD5Sum:
{chr(10).join(md5_lines)}

SHA256:
{chr(10).join(sha256_lines)}
"""


    with open(
        "Release",
        "w",
        encoding="utf-8",
        newline="\n"
    ) as f:

        f.write(content)


# ============================================================
# 主程序
# ============================================================

def main():

    print()
    print("================================")
    print("ilan RootHide Packages Builder")
    print("================================")
    print()


    if not DEB_DIR.exists():

        DEB_DIR.mkdir(
            parents=True,
            exist_ok=True
        )


    # 读取自定义信息

    custom_metadata = (
        load_custom_metadata()
    )


    entries = []


    # 扫描 DEB

    deb_files = sorted(
        DEB_DIR.rglob("*.deb")
    )


    print(
        f"📦 找到 DEB：{len(deb_files)} 个"
    )

    print()


    for path in deb_files:

        print(
            "Processing:",
            path
        )


        entry = make_entry(
            path,
            custom_metadata
        )


        if entry:

            entries.append(entry)

            print(
                "  ✅",
                entry.get("Name"),
                entry.get("Version")
            )


    # 生成 Packages

    generate_packages(entries)

    print()
    print("✅ Packages")


    # gzip / bz2

    generate_compressed()

    print("✅ Packages.gz")
    print("✅ Packages.bz2")


    # JSON

    generate_packages_json(entries)

    print("✅ Packages.json")


    # Release

    generate_release()

    print("✅ Release")


    print()
    print("================================")
    print("🎉 RootHide Packages generated")
    print("Plugins:", len(entries))
    print("================================")


if __name__ == "__main__":

    main()
