import gzip
import bz2
import json
import hashlib
import subprocess
import re
from pathlib import Path
from email.utils import formatdate
from functools import cmp_to_key


DEB_DIR = Path("debs")
CHANGELOG_DIR = Path("changelogs")

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
# ============================================================

def debian_version_compare(a, b):

    a = str(a or "")
    b = str(b or "")

    if a == b:
        return 0

    ia = 0
    ib = 0

    while ia < len(a) or ib < len(b):

        if ia < len(a) and a[ia] == "~":
            if ib >= len(b) or b[ib] != "~":
                return -1

        if ib < len(b) and b[ib] == "~":
            if ia >= len(a) or a[ia] != "~":
                return 1

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

        if ia < len(a) and a[ia] == "~":

            if ib >= len(b) or b[ib] != "~":
                return -1

            ia += 1
            ib += 1
            continue

        if ib < len(b) and b[ib] == "~":
            return 1

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
# 读取旧版 packages.json
# ============================================================

def load_custom_metadata():

    if not PACKAGES_JSON.exists():

        print(
            "ℹ️ 没有找到 packages.json，使用 DEB 原始信息"
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

        result[
            (
                package_id,
                version
            )
        ] = item

    print(
        f"✅ 读取自定义插件信息：{len(result)} 个版本"
    )

    return result


# ============================================================
# 判断是否为版本号
#
# 例如：
# 1.1-2~50
# 1.1-2~48
# 2.0
# 1.0.1
# ============================================================

def is_version_line(line):

    line = line.strip()

    if not line:
        return False

    # 更新内容不能被识别为版本
    if line.startswith("-"):
        return False

    if line.startswith("•"):
        return False

    if line.startswith("*"):
        return False

    # 必须包含数字
    if not any(c.isdigit() for c in line):
        return False

    # Debian 常见版本字符
    return bool(
        re.fullmatch(
            r"[0-9][0-9A-Za-z.+:~_-]*",
            line
        )
    )


# ============================================================
# 读取一个插件的更新日志
#
# 一个插件对应一个 TXT
#
# changelogs/
# └── com.netskao.wechatextension.txt
#
# ============================================================

def load_changelog(package_id):

    if not CHANGELOG_DIR.exists():

        return {}

    path = CHANGELOG_DIR / f"{package_id}.txt"

    if not path.exists():

        print(
            f"  ℹ️ 没有找到更新日志：{path}"
        )

        return {}

    try:

        content = path.read_text(
            encoding="utf-8-sig"
        )

    except Exception as e:

        print(
            f"  ❌ 更新日志读取失败：{path}"
        )

        print(e)

        return {}

    result = {}

    current_version = None
    current_lines = []

    def save_version():

        nonlocal current_version
        nonlocal current_lines

        if not current_version:
            return

        cleaned = []

        for line in current_lines:

            line = line.strip()

            if not line:
                continue

            cleaned.append(line)

        result[current_version] = cleaned

    for raw_line in content.splitlines():

        line = raw_line.strip()

        # 空行
        if not line:

            if current_version:
                current_lines.append("")

            continue

        # 版本标题
        if is_version_line(line):

            save_version()

            current_version = line
            current_lines = []

            continue

        # 更新内容
        if current_version:

            current_lines.append(line)

    # 保存最后一个版本
    save_version()

    print()
    print(
        f"  📝 读取更新日志：{package_id}"
    )

    if result:

        for version, changes in result.items():

            print(
                f"     {version}: "
                f"{len(changes)} 条"
            )

    else:

        print(
            "     ⚠️ 没有解析到任何版本"
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
    # 自定义 metadata
    # --------------------------------------------------------

    custom = custom_metadata.get(
        (
            package_id,
            version
        ),
        {}
    )

    # 兼容旧 metadata
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
                f"  ℹ️ 使用兼容 metadata：{package_id}"
            )

    custom_package = (
        custom.get("package")
        or custom.get("Package")
        or ""
    ).strip()

    if custom_package:
        entry["Package"] = custom_package

    custom_name = (
        custom.get("name")
        or custom.get("Name")
        or ""
    ).strip()

    if custom_name:
        entry["Name"] = custom_name

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

    custom_author = (
        custom.get("author")
        or custom.get("Author")
        or ""
    ).strip()

    if custom_author:
        entry["Author"] = custom_author

    custom_architecture = (
        custom.get("architecture")
        or custom.get("Architecture")
        or ""
    ).strip()

    if custom_architecture:
        entry["Architecture"] = custom_architecture

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

    # --------------------------------------------------------
    # 更新日志
    # --------------------------------------------------------

    changelogs = load_changelog(
        package_id
    )

    # 当前版本
    current_changelog = changelogs.get(
        version,
        []
    )

    # 当前版本更新日志
    entry["_Changelog"] = current_changelog

    # 所有版本更新日志
    entry["_Changelogs"] = changelogs

    if current_changelog:

        print(
            f"  ✅ 匹配 {version} 更新日志："
            f"{len(current_changelog)} 条"
        )

    else:

        print(
            f"  ⚠️ {version} 没有对应更新日志"
        )

    return entry


# ============================================================
# 去除完全重复版本
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

        architecture = entry.get(
            "Architecture",
            ""
        )

        key = (
            package,
            version,
            architecture
        )

        if key not in grouped:

            grouped[key] = entry

        else:

            old = grouped[key]

            print()
            print(
                "⚠️ 发现完全重复版本:"
            )

            print(
                "   ",
                old.get("Filename")
            )

            print(
                "   ",
                entry.get("Filename")
            )

            grouped[key] = entry

    return list(
        grouped.values()
    )


# ============================================================
# 排序
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

        return -debian_version_compare(
            a.get("Version", ""),
            b.get("Version", "")
        )

    return sorted(
        entries,
        key=cmp_to_key(compare)
    )


# ============================================================
# 检查插件版本
# ============================================================

def check_plugin_versions(
    entries,
    package_id
):

    versions = []

    for entry in entries:

        if entry.get("Package") == package_id:

            version = entry.get(
                "Version",
                ""
            )

            if version:
                versions.append(version)

    return versions


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

                # 内部字段不能进入 Debian Packages
                if key.startswith("_"):
                    continue

                if value is None:
                    continue

                value = str(value)

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
# 验证 Packages
# ============================================================

def verify_packages_file(
    package_id,
    required_versions
):

    if not OUTPUT.exists():

        print(
            "❌ Packages 文件不存在"
        )

        return False

    content = OUTPUT.read_text(
        encoding="utf-8"
    )

    blocks = content.split(
        "\n\n"
    )

    missing = []

    for version in required_versions:

        found = False

        for block in blocks:

            if (
                f"Package: {package_id}" in block
                and
                f"Version: {version}" in block
            ):

                found = True
                break

        if not found:

            missing.append(version)

    if missing:

        print()
        print(
            f"❌ Packages 中缺少 {package_id} 版本:"
        )

        for version in missing:

            print(
                f"   ❌ {version}"
            )

        return False

    print()
    print(
        f"✅ Packages 已确认包含 "
        f"{package_id} 的所有指定版本"
    )

    return True


# ============================================================
# 压缩
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
# 清理 JSON 字段
# ============================================================

def clean_entry_for_json(entry):

    result = {}

    for key, value in entry.items():

        if key == "_Changelog":

            result["changelog"] = value
            continue

        if key == "_Changelogs":

            result["changelogs"] = value
            continue

        if key.startswith("_"):
            continue

        result[key] = value

    return result


# ============================================================
# 生成 Packages.json
# ============================================================

def generate_packages_json(entries):

    output_entries = []

    for entry in entries:

        output_entries.append(
            clean_entry_for_json(entry)
        )

    with open(
        GENERATED_JSON,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            output_entries,
            f,
            ensure_ascii=False,
            indent=2
        )


# ============================================================
# Release
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

    if not CHANGELOG_DIR.exists():

        CHANGELOG_DIR.mkdir(
            parents=True,
            exist_ok=True
        )

        print(
            "📁 已创建 changelogs/"
        )

    custom_metadata = (
        load_custom_metadata()
    )

    entries = []

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
                entry.get("Package"),
                entry.get("Version")
            )

    if not entries:

        print()
        print(
            "❌ 没有成功读取任何 DEB"
        )

        raise SystemExit(1)

    before_count = len(entries)

    entries = remove_duplicate_versions(
        entries
    )

    after_count = len(entries)

    print()
    print(
        f"🔎 去重前：{before_count}"
    )

    print(
        f"🔎 去重后：{after_count}"
    )

    # --------------------------------------------------------
    # 微信扩展检查
    # --------------------------------------------------------

    wechat_package = (
        "com.netskao.wechatextension"
    )

    wechat_versions = check_plugin_versions(
        entries,
        wechat_package
    )

    print()
    print(
        "🔍 微信扩展最终版本："
    )

    if wechat_versions:

        for version in wechat_versions:

            print(
                f"   • {version}"
            )

    else:

        print(
            "   ⚠️ 没有找到微信扩展"
        )

    required_wechat_versions = [
        "1.1-2~48",
        "1.1-2~50"
    ]

    if (
        "1.1-2~48" in wechat_versions
        and
        "1.1-2~50" in wechat_versions
    ):

        print()
        print(
            "✅ 微信扩展 ~48 和 ~50 均保留"
        )

    else:

        print()
        print(
            "⚠️ 当前微信扩展没有同时检测到 ~48 和 ~50"
        )

    # --------------------------------------------------------
    # 排序
    # --------------------------------------------------------

    entries = sort_entries(
        entries
    )

    print()
    print(
        f"📋 最终插件版本数量：{len(entries)}"
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
    # 验证
    # --------------------------------------------------------

    if wechat_versions:

        verified = verify_packages_file(
            wechat_package,
            wechat_versions
        )

        if not verified:

            raise SystemExit(1)

    if (
        "1.1-2~48" in wechat_versions
        and
        "1.1-2~50" in wechat_versions
    ):

        verified_wechat = verify_packages_file(
            wechat_package,
            required_wechat_versions
        )

        if not verified_wechat:

            raise SystemExit(1)

        print()
        print(
            "🎉 微信扩展多版本验证成功"
        )

    # --------------------------------------------------------
    # 压缩
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
