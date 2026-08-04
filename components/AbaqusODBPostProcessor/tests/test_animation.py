from pathlib import Path

from PIL import Image

from abaqus_odb_postprocessor.postprocess_core import (
    build_gifs,
    build_transparent_backgrounds,
)
from abaqus_odb_postprocessor.postprocess import finalize_render_output


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


def test_finalize_render_output_can_export_only_transparent_pngs(
    tmp_path: Path,
) -> None:
    frame = tmp_path / "frames" / "PILE_U_MAG" / "0000.png"
    contour = tmp_path / "contours" / "PILE_U_MAG_LAST.png"
    frame.parent.mkdir(parents=True)
    contour.parent.mkdir(parents=True)
    Image.new("RGB", (12, 8), "white").save(frame)
    Image.new("RGB", (12, 8), "white").save(contour)

    manifest = finalize_render_output(
        tmp_path,
        export_white_background_png=False,
        export_transparent_background_png=True,
    )

    assert not (tmp_path / "frames").exists()
    assert not (tmp_path / "contours").exists()
    assert (tmp_path / "frames_transparent" / "PILE_U_MAG" / "0000.png").is_file()
    assert (tmp_path / "contours_transparent" / "PILE_U_MAG_LAST.png").is_file()
    assert (tmp_path / "animations" / "PILE_U_MAG.gif").is_file()
    assert manifest["original_pngs_preserved"] is False
    assert manifest["white_png_count"] == 0
    assert manifest["transparent_png_count"] == 2


def test_finalize_render_output_can_export_only_white_pngs(tmp_path: Path) -> None:
    frame = tmp_path / "frames" / "PILE_U_MAG" / "0000.png"
    contour = tmp_path / "contours" / "PILE_U_MAG_LAST.png"
    frame.parent.mkdir(parents=True)
    contour.parent.mkdir(parents=True)
    Image.new("RGB", (12, 8), "white").save(frame)
    Image.new("RGB", (12, 8), "white").save(contour)

    manifest = finalize_render_output(
        tmp_path,
        export_white_background_png=True,
        export_transparent_background_png=False,
    )

    assert frame.is_file()
    assert contour.is_file()
    assert not (tmp_path / "frames_transparent").exists()
    assert not (tmp_path / "contours_transparent").exists()
    assert manifest["original_pngs_preserved"] is True
    assert manifest["white_png_count"] == 2
    assert manifest["transparent_png_count"] == 0
