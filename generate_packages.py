import gzip
import bz2
import json
import hashlib
import subprocess
from pathlib import Path
from email.utils import formatdate
from functools import cmp_to_key


DEB_DIR = Path("debs")

OUTPUT = Path("Packages")
PACKAGES_JSON = Path("packages.json")
GENERATED_JSON = Path("Packages.json")


# ============================================================
# Debian control 解析
# ============================================================

def parse_control(data):
    result = {}
    current_key = None

    for line in data.splitlines():

        if not line.strip():
            continue

        # Debian control 续行
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
# Debian 版本比较
#
# 支持：
# 1.1-2~48
# 1.1-2~50
# 1.1-2
# 2.0
# ============================================================

def debian_version_compare(a, b):

    a = str(a or "")
    b = str(b or "")

    if a == b:
        return 0

    ia = 0
    ib = 0

    while ia < len(a) or ib < len(b):

        # -------------------------
        # 非数字部分
        # -------------------------

        while (
            ia < len(a)
            and not a[ia].isdigit()
        ):

            ca = a[ia]

            if ib < len(b) and b[ib].isdigit():
                break

            cb = b[ib] if ib < len(b) else ""

            # Debian 特殊规则：
            # ~ 永远比其他字符更小
            if ca == "~" or cb == "~":

                if ca == "~" and cb != "~":
                    return -1

                if cb == "~" and ca != "~":
                    return 1

            if ca != cb:

                if ca == "":
                    return -1

                if cb == "":
                    return 1

                # 字母通常排在非字母后面
                if ca.isalpha() and not cb.isalpha():
                    return 1

                if cb.isalpha() and not ca.isalpha():
                    return -1

                return -1 if ca < cb else 1

            ia += 1
            if ib < len(b):
                ib += 1

        # -------------------------
        # 跳过 ~ 等特殊字符
        # -------------------------

        if ia < len(a) and a[ia] == "~":
            if ib >= len(b) or b[ib] != "~":
                return -1

        if ib < len(b) and b[ib] == "~":
            if ia >= len(a) or a[ia] != "~":
                return 1

        # -------------------------
        # 数字部分
        # -------------------------

        while ia < len(a) and not a[ia].isdigit():

            if a[ia] == "~":
                break

            ia += 1

        while ib < len(b) and not b[ib].isdigit():

            if b[ib] == "~":
                break

            ib += 1

        if (
            ia < len(a)
            and ib < len(b)
            and a[ia].isdigit()
            and b[ib].isdigit()
        ):

            sa = ia
            sb = ib

            while ia < len(a) and a[ia].isdigit():
                ia += 1

            while ib < len(b) and b[ib].isdigit():
                ib += 1

            na = a[sa:ia].lstrip("0") or "0"
            nb = b[sb:ib].lstrip("0") or "0"

            if len(na) != len(nb):
                return 1 if len(na) > len(nb) else -1

            if na != nb:
                return 1 if na > nb else -1

        else:

            if ia < len(a) and a[ia] == "~":
                if not (
                    ib < len(b)
                    and b[ib] == "~"
                ):
                    return -1

            if ib < len(b) and b[ib] == "~":
                if not (
                    ia < len(a)
                    and a[ia] == "~"
                ):
                    return 1

            if ia >= len(a) and ib >= len(b):
                break

            if ia >= len(a):
                return -1

            if ib >= len(b):
                return 1

            if a[ia] != b[ib]:
                return -1 if a[ia] < b[ib] else 1

            ia += 1
            ib += 1

    return 0


def version_key_compare(item_a, item_b):

    return debian_version_compare(
        item_a.get("Version", ""),
        item_b.get("Version", "")
    )


# ============================================================
# 读取 DEB control
# ============================================================

def read_deb_control(path):

    try:

        result = subprocess.run(
            [
                "dpkg-deb",
                "-f",
                str(path)
            ],
            capture_output=True,
            text=True,
            check=True
        )

        return parse_control(result.stdout)

    except Exception as e:

        print()
        print("❌ 读取 DEB 失败:")
        print(path)
        print(e)

        return {}


# ============================================================
# 读取网页上传器的 packages.json
# ============================================================

def load_custom_metadata():

    if not PACKAGES_JSON.exists():

        print(
            "ℹ️ 没有找到 packages.json，"
            "使用 DEB 原始信息"
        )

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

    packages = data.get(
        "packages",
        []
    )

    result = {}

    for item in packages:

        package_id = (
            item.get("package")
            or item.get("Package")
            or ""
        ).strip()

        version = (
            item.get("version")
            or item.get("Version")
            or ""
        ).strip()

        if not package_id:
            continue

        # ====================================================
        # ⭐ 关键修改
        #
        # 不再只使用 Package ID
        #
        # Package + Version 才是唯一 metadata
        # ====================================================

        key = (
            package_id,
            version
        )

        result[key] = item

    print(
        f"✅ 读取自定义插件信息："
        f"{len(result)} 个版本"
    )

    return result


# ============================================================
# SHA256
# ============================================================

def sha256_file(path):

    sha256 = hashlib.sha256()

    with open(
        path,
        "rb"
    ) as f:

        for chunk in iter(
            lambda: f.read(1024 * 1024),
            b""
        ):

            sha256.update(chunk)

    return sha256.hexdigest()


# ============================================================
# 创建 Packages 条目
# ============================================================

def make_entry(
    path,
    custom_metadata
):

    control = read_deb_control(path)

    if not control:
        return None

    package_id = control.get(
        "Package",
        ""
    ).strip()

    version = control.get(
        "Version",
        ""
    ).strip()

    if not package_id:

        print(
            "⚠️ DEB 没有 Package:",
            path
        )

        return None

    if not version:

        print(
            "⚠️ DEB 没有 Version:",
            path
        )

        return None

    # ========================================================
    # 原始 DEB 信息
    # ========================================================

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


    # ========================================================
    # ⭐ Package + Version 匹配自定义信息
    # ========================================================

    custom = custom_metadata.get(
        (
            package_id,
            version
        ),
        {}
    )


    # ========================================================
    # 如果没找到精确版本
    # 尝试寻找 Package 对应的 metadata
    #
    # 这样可以兼容以前的 packages.json
    # ========================================================

    if not custom:

        candidates = []

        for key, item in custom_metadata.items():

            if key[0] == package_id:

                candidates.append(
                    (key[1], item)
                )

        if len(candidates) == 1:

            custom = candidates[0][1]

            print(
                f"  ℹ️ 使用兼容 metadata："
                f"{package_id}"
            )


    # ========================================================
    # 自定义 Package
    # ========================================================

    custom_package = (
        custom.get("package")
        or custom.get("Package")
        or ""
    ).strip()

    if custom_package:
        entry["Package"] = custom_package


    # ========================================================
    # 自定义名称
    # ========================================================

    custom_name = (
        custom.get("name")
        or custom.get("Name")
        or ""
    ).strip()

    if custom_name:
        entry["Name"] = custom_name


    # ========================================================
    # 自定义版本
    #
    # ⭐ 默认永远使用 DEB 自己的 Version
    #
    # 防止 ~48 被错误改成 ~50
    # ========================================================

    custom_version = (
        custom.get("version")
        or custom.get("Version")
        or ""
    ).strip()

    if custom_version:

        if custom_version == version:

            entry["Version"] = custom_version

        else:

            print(
                "  ⚠️ 忽略 metadata 版本不一致："
            )

            print(
                f"     DEB: {version}"
            )

            print(
                f"     JSON: {custom_version}"
            )

            entry["Version"] = version


    # ========================================================
    # 作者
    # ========================================================

    custom_author = (
        custom.get("author")
        or custom.get("Author")
        or ""
    ).strip()

    if custom_author:
        entry["Author"] = custom_author


    # ========================================================
    # 架构
    # ========================================================

    custom_architecture = (
        custom.get("architecture")
        or custom.get("Architecture")
        or ""
    ).strip()

    if custom_architecture:
        entry["Architecture"] = (
            custom_architecture
        )


    # ========================================================
    # 中文描述
    # ========================================================

    custom_description = (
        custom.get("description")
        or custom.get("Description")
        or ""
    ).strip()

    if custom_description:

        entry["Description"] = (
            custom_description
        )


    # ========================================================
    # 文件信息
    # ========================================================

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
# ⭐ 去除重复 Package + Version
#
# 如果同一个 Package 有：
#
# ~48
# ~50
#
# 只保留最高版本。
# ============================================================

def remove_duplicate_versions(entries):

    grouped = {}

    for entry in entries:

        package = entry.get(
            "Package",
            ""
        )

        version = entry.get(
            "Version",
            ""
        )

        key = (
            package,
            version
        )

        if key not in grouped:

            grouped[key] = entry

        else:

            old = grouped[key]

            print(
                "⚠️ 删除重复版本："
            )

            print(
                "   ",
                old.get("Filename")
            )

            print(
                "   ",
                entry.get("Filename")
            )

            # 文件名更稳定的一个
            # 默认保留后扫描到的版本

            grouped[key] = entry


    return list(
        grouped.values()
    )


# ============================================================
# ⭐ 同 Package 多版本只保留最高版本
# ============================================================

def keep_latest_versions(entries):

    grouped = {}

    for entry in entries:

        package = entry.get(
            "Package",
            ""
        )

        if not package:
            continue

        if package not in grouped:

            grouped[package] = entry

            continue

        old = grouped[package]

        old_version = old.get(
            "Version",
            ""
        )

        new_version = entry.get(
            "Version",
            ""
        )

        result = debian_version_compare(
            new_version,
            old_version
        )

        if result > 0:

            print(
                f"🔄 {package}: "
                f"{old_version} → {new_version}"
            )

            grouped[package] = entry

        else:

            print(
                f"⏭️ 保留版本："
                f"{old_version}"
            )

    return list(
        grouped.values()
    )


# ============================================================
# Packages 排序
# ============================================================

def sort_entries(entries):

    return sorted(
        entries,
        key=lambda x: (
            x.get("Package", ""),
            x.get("Architecture", "")
        )
    )


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

                if value is None:
                    continue

                value = str(value)

                # Debian 多行 Description
                if (
                    key == "Description"
                    and "\n" in value
                ):

                    lines = value.splitlines()

                    if lines:

                        f.write(
                            f"{key}: "
                            f"{lines[0]}\n"
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

    content = OUTPUT.read_bytes()


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
        GENERATED_JSON,
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

        content = path.read_bytes()

        size = len(content)

        md5 = hashlib.md5(
            content
        ).hexdigest()

        sha256 = hashlib.sha256(
            content
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


    # ========================================================
    # 读取网页自定义信息
    # ========================================================

    custom_metadata = (
        load_custom_metadata()
    )


    entries = []


    # ========================================================
    # 扫描 DEB
    # ========================================================

    deb_files = sorted(
        DEB_DIR.rglob("*.deb")
    )


    print(
        f"📦 找到 DEB："
        f"{len(deb_files)} 个"
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
                entry.get("Package"),
                entry.get("Version")
            )


    # ========================================================
    # 去重
    # ========================================================

    entries = remove_duplicate_versions(
        entries
    )


    # ========================================================
    # 同一个插件只保留最新版本
    # ========================================================

    entries = keep_latest_versions(
        entries
    )


    # ========================================================
    # 排序
    # ========================================================

    entries = sort_entries(
        entries
    )


    print()
    print(
        f"📋 最终插件数量："
        f"{len(entries)}"
    )


    # ========================================================
    # Packages
    # ========================================================

    generate_packages(
        entries
    )

    print(
        "✅ Packages"
    )


    # ========================================================
    # Packages.gz / Packages.bz2
    # ========================================================

    generate_compressed()

    print(
        "✅ Packages.gz"
    )

    print(
        "✅ Packages.bz2"
    )


    # ========================================================
    # Packages.json
    # ========================================================

    generate_packages_json(
        entries
    )

    print(
        "✅ Packages.json"
    )


    # ========================================================
    # Release
    # ========================================================

    generate_release()

    print(
        "✅ Release"
    )


    print()
    print("================================")
    print(
        "🎉 RootHide Packages generated"
    )
    print(
        "Plugins:",
        len(entries)
    )
    print("================================")
    print()


if __name__ == "__main__":
    main()
