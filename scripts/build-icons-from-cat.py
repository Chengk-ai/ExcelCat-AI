"""
Resize cat mascot PNG to Office add-in icon sizes (square, slight inset for clarity at 16px).
Source: standalone cat asset (RGBA) from project / Cursor assets folder.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

# Slight inset so whiskers/details don't clip at 16x16
PAD_RATIO = 0.10

SIZES = (16, 32, 64, 80, 128)


def fit_icon(src: Image.Image, size: int) -> Image.Image:
    """Scale uniformly to fit inside (size x size) with transparent padding."""
    src = src.convert("RGBA")
    w, h = src.size
    inner = max(1, int(size * (1 - 2 * PAD_RATIO)))
    scale = min(inner / w, inner / h)
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    resized = src.resize((nw, nh), Image.Resampling.LANCZOS)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ox = (size - nw) // 2
    oy = (size - nh) // 2
    out.paste(resized, (ox, oy), resized)
    return out


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    assets_out = root / "assets"
    assets_out.mkdir(parents=True, exist_ok=True)

    # Prefer repo copy of mascot; then Cursor-saved asset; then legacy logo
    candidates = [
        root / "assets" / "mascot-source.png"
    ]
    src_path = next((p for p in candidates if p.is_file()), None)
    if not src_path:
        raise SystemExit("No source cat PNG found.")

    src = Image.open(src_path)
    for s in SIZES:
        icon = fit_icon(src, s)
        dest = assets_out / f"icon-{s}.png"
        icon.save(dest, "PNG", optimize=True)
        print(f"Wrote {dest} ({s}x{s})")


if __name__ == "__main__":
    main()
