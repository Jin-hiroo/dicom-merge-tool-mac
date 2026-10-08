"""ビルドした head3Dv1.app の静的検査。

    python macos/check_bundle.py dist/head3Dv1.app

* 同梱の Mach-O がすべて arm64 を含むこと (Rosetta 用 x86_64 だけのものが混ざっていない)
* 同梱バイナリが要求する最小 macOS が Info.plist の LSMinimumSystemVersion 以下であること
* アドホック署名が壊れていないこと (codesign --verify)
"""
from __future__ import annotations

import plistlib
import struct
import subprocess
import sys
from pathlib import Path

MH_MAGIC_64 = 0xFEEDFACF
FAT_MAGIC = 0xCAFEBABE
FAT_MAGIC_64 = 0xCAFEBABF
CPU_ARM64 = 0x0100000C
LC_VERSION_MIN_MACOSX = 0x24
LC_BUILD_VERSION = 0x32


def _version(packed: int) -> tuple:
    return (packed >> 16, (packed >> 8) & 0xFF, packed & 0xFF)


def _thin_minos(fp, offset: int) -> tuple | None:
    fp.seek(offset)
    header = fp.read(32)
    ncmds, sizeofcmds = struct.unpack_from("<II", header, 16)
    cmds = fp.read(sizeofcmds)
    pos = 0
    for _ in range(ncmds):
        cmd, size = struct.unpack_from("<II", cmds, pos)
        if cmd == LC_BUILD_VERSION:
            return _version(struct.unpack_from("<I", cmds, pos + 12)[0])
        if cmd == LC_VERSION_MIN_MACOSX:
            return _version(struct.unpack_from("<I", cmds, pos + 8)[0])
        pos += size
    return None


def inspect(path: Path):
    """(arm64 を含むか, arm64 スライスの minos) を返す。Mach-O でなければ None。"""
    with open(path, "rb") as fp:
        head = fp.read(8)
        if len(head) < 8:
            return None
        magic_be, = struct.unpack(">I", head[:4])
        magic_le, = struct.unpack("<I", head[:4])

        if magic_le == MH_MAGIC_64:
            cpu, = struct.unpack_from("<I", head, 4)
            return cpu == CPU_ARM64, _thin_minos(fp, 0)
        if magic_be not in (FAT_MAGIC, FAT_MAGIC_64):
            return None

        count, = struct.unpack_from(">I", head, 4)
        if count > 16:          # Java の .class も 0xCAFEBABE で始まる
            return None
        entry = 20 if magic_be == FAT_MAGIC else 32
        table = fp.read(count * entry)
        for i in range(count):
            base = i * entry
            cpu, = struct.unpack_from(">I", table, base)
            if magic_be == FAT_MAGIC:
                offset, = struct.unpack_from(">I", table, base + 8)
            else:
                offset, = struct.unpack_from(">Q", table, base + 8)
            if cpu == CPU_ARM64:
                return True, _thin_minos(fp, offset)
        return False, None


def main(app: Path) -> int:
    with open(app / "Contents" / "Info.plist", "rb") as fp:
        info = plistlib.load(fp)
    declared = tuple(int(v) for v in info["LSMinimumSystemVersion"].split("."))
    declared += (0,) * (3 - len(declared))

    not_arm64, too_new, n = [], [], 0
    newest = (0, 0, 0)
    for path in sorted(app.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        result = inspect(path)
        if result is None:
            continue
        n += 1
        has_arm64, minos = result
        rel = path.relative_to(app)
        if not has_arm64:
            not_arm64.append(rel)
        if minos is not None:
            newest = max(newest, minos)
            if minos > declared:
                too_new.append((rel, minos))

    fmt = ".".join
    print(f"Mach-O: {n} 件 / 最も新しい minos: {fmt(map(str, newest))} "
          f"/ LSMinimumSystemVersion: {info['LSMinimumSystemVersion']}")
    ok = True
    for rel in not_arm64:
        print(f"NG  arm64 を含まない: {rel}")
        ok = False
    for rel, minos in too_new:
        print(f"NG  macOS {fmt(map(str, minos))} 以上が必要: {rel}")
        ok = False

    verify = subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)],
                            capture_output=True, text=True)
    if verify.returncode != 0:
        print("NG  codesign --verify:", verify.stderr.strip())
        ok = False
    else:
        print("OK  codesign --verify")

    print("バンドル検査:", "成功" if ok else "失敗")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "dist/head3Dv1.app")))
