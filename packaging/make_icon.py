"""从仓库根目录的 icon瓜.png 生成启动器图标资源。

像素风图标：缩放一律用最近邻（NEAREST），不要 LANCZOS/BICUBIC，
否则标题栏和 exe 文件图标都会出现"高斯模糊"一样的软边。

生成：
- packaging/GeLauncher.ico        （exe 文件图标，多尺寸）
- packaging/GeLauncher-<n>.png   （窗口图标，多尺寸，供 iconphoto 使用）

用法：
    python packaging/make_icon.py
需要 Pillow（只在构建时用，运行时不依赖）。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "icon瓜.png"
OUT = ROOT / "packaging"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def main() -> None:
    image = Image.open(SOURCE).convert("RGBA")
    frames = {size: image.resize((size, size), Image.NEAREST) for size in SIZES}

    # ico 里放满所有尺寸，Windows 会挑最接近的，不再二次平滑缩放
    frames[256].save(
        OUT / "GeLauncher.ico",
        format="ICO",
        sizes=[(size, size) for size in SIZES],
        append_images=[frames[size] for size in SIZES if size != 256],
    )
    for size, frame in frames.items():
        frame.save(OUT / f"GeLauncher-{size}.png")
    print("generated:", ", ".join(str(OUT / f"GeLauncher-{s}.png") for s in SIZES))


if __name__ == "__main__":
    main()
