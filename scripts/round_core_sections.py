"""Rebuild two ring-indexed twisted cores as circular closed solid meshes.

Run with Blender: blender --background --python round_core_sections.py --
    --input source.blend --output new.blend [--objects core_a core_b]
"""

import argparse
import json
import math
from pathlib import Path
import sys

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _geometry import (core_metrics, core_objects, recalculate_normals,
                       validate_core_topology)


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--objects", nargs="+", help="Exactly two existing mesh object names")
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])


def transport_frames(tangents):
    reference = Vector((0.0, 0.0, 1.0))
    if abs(tangents[0].dot(reference)) > 0.92:
        reference = Vector((0.0, 1.0, 0.0))
    normal = (reference - tangents[0] * tangents[0].dot(reference)).normalized()
    normals = [normal]
    binormals = [tangents[0].cross(normal).normalized()]
    for previous, current in zip(tangents, tangents[1:]):
        normal = previous.rotation_difference(current) @ normals[-1]
        normal = (normal - current * current.dot(normal)).normalized()
        if normal.length < 0.99:
            raise ValueError("Degenerate parallel-transport frame")
        normals.append(normal)
        binormals.append(current.cross(normal).normalized())
    return normals, binormals


def rebuild(obj, geometry):
    count, segments, radius, _, centers, tangents = geometry
    normals, binormals = transport_frames(tangents)
    inverse = obj.matrix_world.inverted()
    # Single-user geometry prevents an unselected object sharing this mesh from changing.
    if obj.data.users > 1:
        obj.data = obj.data.copy()
    for index, center in enumerate(centers):
        for offset in range(segments):
            angle = 2.0 * math.pi * offset / segments
            point = center + radius * (math.cos(angle) * normals[index]
                                      + math.sin(angle) * binormals[index])
            obj.data.vertices[index * segments + offset].co = inverse @ point
    for face in obj.data.polygons:
        face.use_smooth = len(face.vertices) < 8
    recalculate_normals(obj)
    obj["cross_section_shape"] = "constant_radius_circle"
    obj["cross_section_frame"] = "parallel_transport"
    obj["round_solid_rebuild"] = True
    # V7 metadata is historical: circular end rings need not share a global X plane.
    obj["end_cap_geometry"] = "flat_circle_perpendicular_to_endpoint_tangent"
    for stale in ("flat_end_plane_x", "end_face_is_flat"):
        if stale in obj:
            del obj[stale]
    if obj.get("extension_mode") == "continued_twist_with_flat_coplanar_caps":
        obj["source_extension_mode"] = obj["extension_mode"]
        obj["extension_mode"] = "continued_twist_with_local_circular_caps"
    validate_core_topology(obj)
    result = core_metrics(obj)
    tolerance = max(radius * 3.0e-4, 3.0e-6)
    if result["max_radius_error"] > tolerance or result["max_orthogonal_error"] > tolerance:
        raise ValueError(f"Rebuilt circle check failed on {obj.name}: tolerance={tolerance}")
    return {key: value for key, value in result.items() if key != "rings"}


def main():
    args = arguments()
    source = args.input.expanduser().resolve(strict=True)
    target = args.output.expanduser().resolve()
    if source.suffix.lower() != ".blend" or target.suffix.lower() != ".blend":
        raise ValueError("Both input and output must have the .blend extension")
    if source == target or target.exists():
        raise FileExistsError("Refusing to overwrite the input or any existing output")
    bpy.ops.wm.open_mainfile(filepath=str(source), load_ui=False, use_scripts=False)
    objects = core_objects(args.objects)
    geometries = [validate_core_topology(obj) for obj in objects]
    if geometries[0][:2] != geometries[1][:2]:
        raise ValueError("The two cores must have matching ring and radial segment counts")
    results = {obj.name: rebuild(obj, geometry) for obj, geometry in zip(objects, geometries)}
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(f"Output appeared during processing: {target}")
    result = bpy.ops.wm.save_as_mainfile(filepath=str(target), check_existing=False)
    if "FINISHED" not in result:
        raise RuntimeError(f"Blender failed to save: {result}")
    print(json.dumps({"output": str(target), "cores": results}, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1)
