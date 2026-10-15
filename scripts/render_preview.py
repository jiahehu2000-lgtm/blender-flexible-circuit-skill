"""Render an existing Blender scene without saving or modifying its .blend file.

Run with Blender, for example::

    blender --background --python render_preview.py -- \
        --input final.blend --output preview.png --width 1600 --samples 24

The existing camera, materials, lights, render engine and color management are
retained. Only preview size/sample count and PNG output settings are changed.
"""

import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile

import bpy


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--width", type=positive_int, default=1600)
    parser.add_argument("--samples", type=positive_int, default=24)
    parser.add_argument(
        "--white-background", action="store_true",
        help="Use a white World and disable transparent film for this render only.",
    )
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    return parser.parse_args(argv)


def set_samples(scene, samples):
    if scene.render.engine == "CYCLES":
        scene.cycles.samples = samples
        return "scene.cycles.samples"
    eevee = getattr(scene, "eevee", None)
    if eevee is not None:
        for name in ("taa_render_samples", "taa_samples"):
            if hasattr(eevee, name):
                setattr(eevee, name, samples)
                return "scene.eevee." + name
    # Do not silently switch to another engine or replace the scene's materials.
    raise RuntimeError(
        "Cannot set render samples for existing engine " + scene.render.engine
    )


def set_white_background(scene):
    world = bpy.data.worlds.new("Preview_White_World")
    world.use_nodes = True
    world.color = (1.0, 1.0, 1.0)
    world.node_tree.nodes.clear()
    background = world.node_tree.nodes.new("ShaderNodeBackground")
    background.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    background.inputs["Strength"].default_value = 1.0
    output = world.node_tree.nodes.new("ShaderNodeOutputWorld")
    world.node_tree.links.new(background.outputs["Background"], output.inputs["Surface"])
    scene.world = world
    scene.render.film_transparent = False


def main():
    args = arguments()
    source = args.input.expanduser().resolve(strict=True)
    target = args.output.expanduser().resolve()
    if source.suffix.lower() != ".blend":
        raise ValueError("--input must point to a .blend file")
    if target.suffix.lower() != ".png":
        raise ValueError("--output must use the .png extension")
    if target.exists():
        raise FileExistsError("Refusing to overwrite existing output: " + str(target))
    target.parent.mkdir(parents=True, exist_ok=True)

    bpy.ops.wm.open_mainfile(filepath=str(source))
    scene = bpy.context.scene
    if scene.camera is None:
        raise RuntimeError("The saved scene has no active camera")
    old_width = scene.render.resolution_x
    old_height = scene.render.resolution_y
    scene.render.resolution_x = args.width
    scene.render.resolution_y = max(1, round(args.width * old_height / old_width))
    scene.render.resolution_percentage = 100
    sample_property = set_samples(scene, args.samples)
    if args.white_background:
        set_white_background(scene)
    scene.render.image_settings.file_format = "PNG"
    scene.render.use_file_extension = True
    scene.render.use_overwrite = False

    print("PREVIEW_SETTINGS " + json.dumps({
        "input": str(source), "output": str(target),
        "engine": scene.render.engine, "camera": scene.camera.name,
        "width": scene.render.resolution_x, "height": scene.render.resolution_y,
        "samples": args.samples, "sample_property": sample_property,
        "white_background": args.white_background,
    }, ensure_ascii=False), flush=True)

    # A private temporary output lets us check success before publishing. Opening
    # the final file with 'xb' also prevents a concurrent process from overwriting
    # a file that appeared after the initial existence check.
    with tempfile.TemporaryDirectory(prefix=".blender_preview_", dir=target.parent) as directory:
        temporary = Path(directory) / "preview.png"
        scene.render.filepath = str(temporary)
        bpy.ops.render.render(write_still=True)
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise RuntimeError("Blender did not produce a nonempty PNG")
        with temporary.open("rb") as image:
            if image.read(8) != b"\x89PNG\r\n\x1a\n":
                raise RuntimeError("Rendered output is not a PNG")
            image.seek(0)
            created = False
            try:
                with target.open("xb") as destination:
                    created = True
                    shutil.copyfileobj(image, destination)
            except BaseException:
                if created:
                    target.unlink(missing_ok=True)
                raise
    print("PREVIEW_COMPLETE " + str(target) + " (" + str(target.stat().st_size) + " bytes)", flush=True)


if __name__ == "__main__":
    main()
