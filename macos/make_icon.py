"""アプリアイコン (AppIcon.icns) を生成する。

    python macos/make_icon.py [出力フォルダ]   # 既定: build/icon

暗色の角丸正方形の上に、Fixed (アイボリー) と Moving (シアン) の CT スライスの
束が重なって 1 つになる様子を描く。色は元コードの config.COLOR_FIXED /
COLOR_MOVING に合わせている。

.icns への変換には macOS の iconutil を使う。無い環境では PNG だけを書き出す。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

SS = 2048                       # 描画解像度 (1024 に縮小してアンチエイリアス)
IVORY = (235, 224, 199)         # config.COLOR_FIXED
CYAN = (77, 199, 230)           # config.COLOR_MOVING
BG_TOP = (44, 49, 61)
BG_BOTTOM = (22, 24, 30)

ICONSET = [(16, 1), (16, 2), (32, 1), (32, 2), (128, 1), (128, 2),
           (256, 1), (256, 2), (512, 1), (512, 2)]


def s(v: float) -> int:
    """1024 基準の座標を描画解像度へ。"""
    return round(v * SS / 1024)


def background() -> Image.Image:
    # macOS のアイコングリッド: 1024 の中に 824 の角丸正方形 (角半径 ~185)
    grad = Image.new("RGBA", (SS, SS))
    draw = ImageDraw.Draw(grad)
    for y in range(SS):
        t = y / (SS - 1)
        draw.line([(0, y), (SS, y)], fill=tuple(
            round(BG_TOP[i] * (1 - t) + BG_BOTTOM[i] * t) for i in range(3)) + (255,))
    mask = Image.new("L", (SS, SS), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [s(100), s(100), s(924), s(924)], radius=s(185), fill=255)

    # 下に落ちる柔らかい影
    shadow = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        [s(100), s(118), s(924), s(942)], radius=s(185), fill=(0, 0, 0, 110))
    shadow = shadow.filter(ImageFilter.GaussianBlur(s(18)))

    out = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))
    out.alpha_composite(shadow)
    out.paste(grad, (0, 0), mask)
    return out


def slab(cx: float, cy: float, color, alpha: int) -> Image.Image:
    """等角投影したスライス 1 枚 (菱形)。"""
    w, h = 300, 150
    layer = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))
    pts = [(cx, cy - h / 2), (cx + w, cy), (cx, cy + h / 2), (cx - w, cy)]
    pts = [(s(x), s(y)) for x, y in pts]
    draw = ImageDraw.Draw(layer)
    draw.polygon(pts, fill=color + (alpha,))
    draw.line(pts + [pts[0]], fill=tuple(min(255, c + 25) for c in color) + (255,),
              width=s(5), joint="curve")
    return layer


def stack(img: Image.Image, cx: float, top: float, n: int, gap: float,
          color, alpha: int):
    for i in range(n):
        img.alpha_composite(slab(cx, top + i * gap, color, alpha))


def render() -> Image.Image:
    img = background()
    # 上の束 = Moving (シアン)、下の束 = Fixed (アイボリー)。中央で重なる。
    stack(img, 512, 330, 4, 62, CYAN, 150)
    stack(img, 512, 520, 4, 62, IVORY, 175)
    return img.resize((1024, 1024), Image.LANCZOS)


def main(out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    master = render()
    master.save(out_dir / "AppIcon.png")

    iconset = out_dir / "AppIcon.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir()
    for size, scale in ICONSET:
        px = size * scale
        name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
        master.resize((px, px), Image.LANCZOS).save(iconset / name)

    iconutil = shutil.which("iconutil")
    if iconutil is None:
        print("iconutil が無いため .icns は作りません (PNG のみ):", out_dir)
        return 0
    subprocess.run([iconutil, "-c", "icns", str(iconset),
                    "-o", str(out_dir / "AppIcon.icns")], check=True)
    print("生成しました:", out_dir / "AppIcon.icns")
    return 0


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    sys.exit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else root / "build" / "icon"))
