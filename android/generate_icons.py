"""Generate Android launcher icons from the repository's official icon.png."""

from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
RES = Path(__file__).resolve().parent / "app" / "src" / "main" / "res"
DENSITIES = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}


def main():
    with Image.open(ROOT / "icon.png") as source:
        icon = source.convert("RGBA")
    for density, size in DENSITIES.items():
        folder = RES / f"mipmap-{density}"
        folder.mkdir(parents=True, exist_ok=True)
        icon.resize((size, size), Image.Resampling.LANCZOS).save(folder / "ic_launcher.png")

        # Adaptive icons show only the center two thirds on masked launchers.
        # Fit the complete artwork there so the lettering and antenna stay visible.
        foreground_size = size * 9 // 4  # 108 dp at mdpi
        artwork_size = foreground_size * 2 // 3
        foreground = Image.new("RGBA", (foreground_size, foreground_size))
        artwork = icon.resize((artwork_size, artwork_size), Image.Resampling.LANCZOS)
        offset = (foreground_size - artwork_size) // 2
        foreground.alpha_composite(artwork, (offset, offset))
        foreground.save(folder / "ic_launcher_foreground.png")

    (RES / "values").mkdir(parents=True, exist_ok=True)
    (RES / "values" / "ic_launcher_background.xml").write_text(
        '<resources><color name="ic_launcher_background">#000000</color></resources>\n',
        encoding="utf-8",
    )
    (RES / "mipmap-anydpi-v26").mkdir(parents=True, exist_ok=True)
    (RES / "mipmap-anydpi-v26" / "ic_launcher.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">\n'
        '    <background android:drawable="@color/ic_launcher_background" />\n'
        '    <foreground android:drawable="@mipmap/ic_launcher_foreground" />\n'
        '</adaptive-icon>\n',
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
