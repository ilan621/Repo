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

        # ----------------------------------------------------
        # ~ 特殊规则
        # ----------------------------------------------------

        if ia < len(a) and a[ia] == "~":
            if ib >= len(b) or b[ib] != "~":
                return -1

        if ib < len(b) and b[ib] == "~":
            if ia >= len(a) or a[ia] != "~":
                return 1

        # ----------------------------------------------------
        # 非数字部分
        # ----------------------------------------------------

        while ia < len(a) and not a[ia].isdigit():

            if a[ia] == "~":
                break

            ca = a[ia]
            cb = b[ib] if ib < len(b) else ""

            if cb == "~":
                return 1

            if cb.isdigit():
                break

            if ca != cb:

                if ca.isalpha() and not cb.isalpha():
                    return 1

                if cb.isalpha() and not ca.isalpha():
                    return -1

                return -1 if ca < cb else 1

            ia += 1

            if ib < len(b):
                ib += 1

        while ib < len(b) and not b[ib].isdigit():

            if b[ib] == "~":
                break

            cb = b[ib]
            ca = a[ia] if ia < len(a) else ""

            if ca == "~":
                return -1

            if ca.isdigit():
                break

            if ca != cb:

                if ca.isalpha() and not cb.isalpha():
                    return 1

                if cb.isalpha() and not ca.isalpha():
                    return -1

                return -1 if ca < cb else 1

            ib += 1

            if ia < len(a):
                ia += 1

        # ----------------------------------------------------
        # ~
        # ----------------------------------------------------

        if ia < len(a) and a[ia] == "~":

            if ib >= len(b) or b[ib] != "~":
                return -1

            ia += 1
            ib += 1
            continue

        if ib < len(b) and b[ib] == "~":
            return 1

        # ----------------------------------------------------
        # 数字部分
        # ----------------------------------------------------

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

            continue

        # ----------------------------------------------------
        # 一个结束
        # ----------------------------------------------------

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
# 读取网页上传器 packages.json
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

        # Package + Version 才是唯一 metadata
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
    # Package + Version 匹配自定义信息
    # --------------------------------------------------------

    custom = custom_metadata.get(
        (
            package_id,
            version
        ),
        {}
    )

    # --------------------------------------------------------
    # 兼容以前只有 Package 的 metadata
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # 自定义 Package
    # --------------------------------------------------------

    custom_package = (
        custom.get("package")
        or custom.get("Package")
        or ""
    ).strip()

    if custom_package:
        entry["Package"] = custom_package

    # --------------------------------------------------------
    # 自定义名称
    # --------------------------------------------------------

    custom_name = (
        custom.get("name")
        or custom.get("Name")
        or ""
    ).strip()

    if custom_name:
        entry["Name"] = custom_name

    # --------------------------------------------------------
    # 自定义版本
    # --------------------------------------------------------

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
                "  ⚠️ 忽略 metadata 版本不一致:"
            )

            print(
                f"     DEB: {version}"
            )

            print(
                f"     JSON: {custom_version}"
            )

            entry["Version"] = version

    # --------------------------------------------------------
    # 作者
    # --------------------------------------------------------

    custom_author = (
        custom.get("author")
        or custom.get("Author")
        or ""
    ).strip()

    if custom_author:
        entry["Author"] = custom_author

    # --------------------------------------------------------
    # 架构
    # --------------------------------------------------------

    custom_architecture = (
        custom.get("architecture")
        or custom.get("Architecture")
        or ""
    ).strip()

    if custom_architecture:
        entry["Architecture"] = custom_architecture

    # --------------------------------------------------------
    # 中文描述
    # --------------------------------------------------------

    custom_description = (
        custom.get("description")
        or custom.get("Description")
        or ""
    ).strip()

    if custom_description:

        entry["Description"] = custom_description

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
# 去除完全重复的 Package + Version
#
# 注意：
# 这里只删除完全相同的 Package + Version。
#
# 不同版本：
# ~48
# ~50
# ~51
#
# 全部保留。
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
                "⚠️ 发现重复 Package + Version:"
            )

            print(
                "   ",
                old.get("Filename")
            )

            print(
                "   ",
                entry.get("Filename")
            )

            # 保留后扫描到的文件
            grouped[key] = entry

    return list(
        grouped.values()
    )


# ============================================================
# Packages 排序
#
# 同一个插件的多个版本全部保留。
#
# 例如：
#
# com.example.test 1.0
# com.example.test 1.1
# com.example.test 1.2
#
# 都会存在。
# ============================================================

def sort_entries(entries):

    def compare(a, b):

        package_a = a.get(
            "Package",
            ""
        )

        package_b = b.get(
            "Package",
            ""
        )

        if package_a != package_b:

            return (
                -1
                if package_a < package_b
                else 1
            )

        arch_a = a.get(
            "Architecture",
            ""
        )

        arch_b = b.get(
            "Architecture",
            ""
        )

        if arch_a != arch_b:

            return (
                -1
                if arch_a < arch_b
                else 1
            )

        # 同 Package + Architecture
        # 版本从高到低排列
        return -debian_version_compare(
            a.get("Version", ""),
            b.get("Version", "")
        )

    from functools import cmp_to_key

    return sorted(
        entries,
        key=cmp_to_key(compare)
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

    # --------------------------------------------------------
    # 读取网页自定义信息
    # --------------------------------------------------------

    custom_metadata = (
        load_custom_metadata()
    )

    entries = []

    # --------------------------------------------------------
    # 扫描 DEB
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # 去掉完全重复的 Package + Version
    # --------------------------------------------------------

    entries = remove_duplicate_versions(
        entries
    )

    # --------------------------------------------------------
    # ⚠️ 这里故意没有调用 keep_latest_versions()
    #
    # 因此同一个插件的所有版本都会保留。
    #
    # 例如：
    #
    # ~48
    # ~50
    #
    # 两个都会进入 Packages。
    # --------------------------------------------------------

    # --------------------------------------------------------
    # 排序
    # --------------------------------------------------------

    entries = sort_entries(
        entries
    )

    print()
    print(
        f"📋 最终插件版本数量："
        f"{len(entries)}"
    )

    # --------------------------------------------------------
    # Packages
    # --------------------------------------------------------

    generate_packages(
        entries
    )

    print(
        "✅ Packages"
    )

    # --------------------------------------------------------
    # Packages.gz / Packages.bz2
    # --------------------------------------------------------

    generate_compressed()

    print(
        "✅ Packages.gz"
    )

    print(
        "✅ Packages.bz2"
    )

    # --------------------------------------------------------
    # Packages.json
    # --------------------------------------------------------

    generate_packages_json(
        entries
    )

    print(
        "✅ Packages.json"
    )

    # --------------------------------------------------------
    # Release
    # --------------------------------------------------------

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
        "Plugin versions:",
        len(entries)
    )
    print("================================")
    print()


if __name__ == "__main__":
    main()
