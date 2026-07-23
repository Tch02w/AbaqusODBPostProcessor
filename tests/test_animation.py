from pathlib import Path

from PIL import Image

from abaqus_odb_postprocessor.postprocess_core import (
    build_gifs,
    build_transparent_backgrounds,
)


def test_build_gif_contains_every_rendered_frame(tmp_path: Path) -> None:
    frame_dir = tmp_path / "frames" / "PILE_U_MAG"
    frame_dir.mkdir(parents=True)
    colors = ((255, 0, 0), (0, 255, 0), (0, 0, 255))
    for index, color in enumerate(colors):
        Image.new("RGB", (24, 16), color).save(frame_dir / f"{index:04d}.png")

    counts = build_gifs(tmp_path, fps=5)

    target = tmp_path / "animations" / "PILE_U_MAG.gif"
    assert counts == {"PILE_U_MAG": 3}
    with Image.open(target) as animation:
        assert animation.n_frames == 3
        assert animation.info["duration"] == 200


def test_transparent_copy_does_not_modify_original_png(tmp_path: Path) -> None:
    source_path = tmp_path / "contours" / "PILE_U_MAG_LAST.png"
    source_path.parent.mkdir(parents=True)
    source = Image.new("RGB", (12, 8), (255, 255, 255))
    for x in range(4, 8):
        for y in range(2, 6):
            source.putpixel((x, y), (180, 20, 20))
    source.save(source_path)
    original_bytes = source_path.read_bytes()

    assert build_transparent_backgrounds(tmp_path) == 1

    assert source_path.read_bytes() == original_bytes
    target = tmp_path / "contours_transparent" / source_path.name
    with Image.open(target) as transparent:
        rgba = transparent.convert("RGBA")
        assert rgba.getpixel((0, 0))[3] == 0
        assert rgba.getpixel((5, 4))[3] == 255
