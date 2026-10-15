"""Read a .blend without saving or rendering; report dependency and geometry evidence."""

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _geometry import core_metrics, mesh_metrics


def json_value(value):
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, bpy.types.ID):
        return {"name": value.name, "type": type(value).__name__}
    if hasattr(value, "to_dict"):
        return {str(key): json_value(item) for key, item in value.to_dict().items()}
    if hasattr(value, "to_list"):
        return [json_value(item) for item in value.to_list()]
    try:
        return [json_value(item) for item in value]
    except TypeError:
        return str(value)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def user_reachability():
    # A weak library reference is provenance and is deliberately not followed.
    dependency_map = defaultdict(set)
    for datablock, users in bpy.data.user_map().items():
        for user in users:
            dependency_map[user].add(datablock)

    def reachable(scene):
        found = set()
        pending = [scene]
        while pending:
            item = pending.pop()
            if item in found:
                continue
            found.add(item)
            pending.extend(dependency_map.get(item, set()) - found)
        return found

    current = reachable(bpy.context.scene)
    all_scenes = set().union(*(reachable(scene) for scene in bpy.data.scenes))
    return current, all_scenes


def all_datablocks():
    seen = set()
    for prop in bpy.data.bl_rna.properties:
        if prop.type != "COLLECTION":
            continue
        for block in getattr(bpy.data, prop.identifier):
            if isinstance(block, bpy.types.ID) and block.as_pointer() not in seen:
                seen.add(block.as_pointer())
                yield block


def dependency_report():
    current, saved = user_reachability()
    records = []
    weak = []
    mapped_paths = set()

    def resolve(raw, owner=None):
        return str(Path(bpy.path.abspath(raw, library=getattr(owner, "library", None))).resolve())

    def add(block, kind, raw, packed=False):
        if not raw or raw == "<builtin>":
            return
        absolute = resolve(raw, block)
        mapped_paths.add(absolute.casefold())
        usage = "active_scene" if block in current else "other_saved_scene" if block in saved else "unused"
        records.append({
            "kind": kind, "datablock": block.name, "raw_path": raw, "absolute_path": absolute,
            "exists": Path(absolute).exists(), "packed": packed, "users": block.users,
            "usage": usage, "required_by_saved_scene": block in saved and not packed,
        })

    for block in all_datablocks():
        reference = getattr(block, "library_weak_reference", None)
        if reference:
            weak.append({"datablock": block.name, "id_name": reference.id_name,
                         "absolute_path": resolve(reference.filepath),
                         "required": False, "role": "append provenance only"})
    for library in bpy.data.libraries:
        # IDs linked from a library make the actual library a required dependency.
        linked = [block for block in saved if getattr(block, "library", None) == library]
        add(library, "linked_library", library.filepath, bool(getattr(library, "packed_file", None)))
        records[-1]["required_by_saved_scene"] = bool(linked) and not records[-1]["packed"]
        records[-1]["linked_saved_datablocks"] = [block.name for block in linked]
    for attr in ("images", "fonts", "sounds", "movieclips", "cache_files", "volumes"):
        for block in getattr(bpy.data, attr, []):
            if attr == "images" and block.source not in {"FILE", "MOVIE", "SEQUENCE", "TILED"}:
                continue
            add(block, attr, getattr(block, "filepath", ""), bool(getattr(block, "packed_file", None)))
    weak_paths = {row["absolute_path"].casefold() for row in weak}
    extra = []
    for raw in sorted(set(bpy.utils.blend_paths(absolute=True, packed=False))):
        absolute = str(Path(raw).resolve())
        if absolute.casefold() not in mapped_paths and absolute.casefold() not in weak_paths:
            extra.append({"absolute_path": absolute, "exists": Path(absolute).exists(),
                          "usage": "unclassified_external_reference", "required": "unknown"})
    return {
        "usage_definition": "Reachable through Blender ID user_map from saved scenes; this is data dependency reachability, not pixel contribution. Hidden or disconnected-node references may still be retained.",
        "external_data": records, "weak_provenance": weak, "unclassified_references": extra,
        "required_external_paths": sorted({row["absolute_path"] for row in records if row["required_by_saved_scene"]}),
        "self_contained": not extra and not any(row["required_by_saved_scene"] for row in records),
    }


def material_report(material):
    nodes = []
    if material.use_nodes and material.node_tree:
        for node in material.node_tree.nodes:
            entry = {"name": node.name, "type": node.bl_idname}
            if node.type == "BSDF_PRINCIPLED":
                entry["inputs"] = {socket.name: json_value(socket.default_value)
                                   for socket in node.inputs if hasattr(socket, "default_value")}
            if node.type == "TEX_IMAGE":
                entry["image"] = node.image.name if node.image else None
            nodes.append(entry)
    return {"name": material.name, "users": material.users,
            "diffuse_color": list(material.diffuse_color), "use_nodes": material.use_nodes,
            "surface_render_method": getattr(material, "surface_render_method", None),
            "nodes": nodes}


def object_report(obj):
    item = {
        "name": obj.name, "type": obj.type, "data": obj.data.name if obj.data else None,
        "library": obj.library.filepath if obj.library else None,
        "hide_render": obj.hide_render, "hide_viewport": obj.hide_viewport,
        "visible_in_view_layer": obj.visible_get(), "matrix_world": [list(row) for row in obj.matrix_world],
        "location": list(obj.location), "rotation_euler": list(obj.rotation_euler),
        "scale": list(obj.scale), "dimensions": list(obj.dimensions),
        "materials": [slot.material.name if slot.material else None for slot in obj.material_slots],
        "custom_properties": {key: json_value(value) for key, value in obj.items()},
        "modifiers": [{"name": modifier.name, "type": modifier.type,
                       "show_render": modifier.show_render, "show_viewport": modifier.show_viewport}
                      for modifier in obj.modifiers],
    }
    if obj.type == "MESH":
        item["mesh"] = mesh_metrics(obj)
        if "centerline_rings" in obj and "solid_radius" in obj:
            try:
                item["core_geometry"] = core_metrics(obj)
            except Exception as error:
                item["core_geometry_error"] = f"{type(error).__name__}: {error}"
    if obj.type == "CAMERA":
        camera = obj.data
        item["camera"] = {key: json_value(getattr(camera, key)) for key in
                          ("type", "lens", "ortho_scale", "clip_start", "clip_end", "sensor_width",
                           "sensor_height", "shift_x", "shift_y")}
    if obj.type == "LIGHT":
        item["light"] = {"type": obj.data.type, "energy": obj.data.energy, "color": list(obj.data.color)}
    return item


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--report", type=Path, help="New JSON path; existing files are not overwritten")
    parser.add_argument("--candidate", action="append", default=[], type=Path,
                        help="Check a proposed cleanup path against dependencies; repeatable")
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])


def main():
    args = arguments()
    source = args.input.expanduser().resolve(strict=True)
    report = args.report.expanduser().resolve() if args.report else None
    if report and report.exists():
        raise FileExistsError(f"Refusing to overwrite existing report: {report}")
    before = sha256(source)
    bpy.ops.wm.open_mainfile(filepath=str(source), load_ui=False, use_scripts=False)
    scene = bpy.context.scene
    dependencies = dependency_report()
    render = scene.render
    result = {
        "schema_version": 1, "created_utc": datetime.now(timezone.utc).isoformat(),
        "input": str(source), "input_sha256": before, "blender_version": bpy.app.version_string,
        "scene": scene.name, "scene_count": len(bpy.data.scenes),
        "units": {"system": scene.unit_settings.system, "scale_length": scene.unit_settings.scale_length},
        "dependencies": dependencies,
        "images": [{"name": image.name, "source": image.source, "filepath": image.filepath,
                    "users": image.users, "packed": bool(image.packed_file)} for image in bpy.data.images],
        "objects": [object_report(obj) for obj in sorted(scene.objects, key=lambda obj: obj.name)],
        "materials": [material_report(mat) for mat in sorted(bpy.data.materials, key=lambda mat: mat.name)],
        "render": {"engine": render.engine, "camera": scene.camera.name if scene.camera else None,
                   "resolution_x": render.resolution_x, "resolution_y": render.resolution_y,
                   "resolution_percentage": render.resolution_percentage,
                   "filepath": render.filepath, "film_transparent": render.film_transparent,
                   "image_format": render.image_settings.file_format,
                   "color_mode": render.image_settings.color_mode,
                   "view_transform": scene.view_settings.view_transform,
                   "cycles_samples": scene.cycles.samples if hasattr(scene, "cycles") else None},
        "limits": ["Base mesh only; render modifiers are listed but not evaluated.",
                   "Closed manifold and positive volume do not prove absence of self-intersections.",
                   "Model distances are Blender units; physical dimensions require independent calibration.",
                   "End caps are identified as polygons with at least eight vertices."]
    }
    external = dependencies["external_data"]
    result["cleanup_candidates"] = []
    for candidate in args.candidate:
        path = candidate.expanduser().resolve()
        matching = [row for row in external if row["absolute_path"].casefold() == str(path).casefold()]
        weak = [row for row in dependencies["weak_provenance"]
                if row["absolute_path"].casefold() == str(path).casefold()]
        unknown = [row for row in dependencies["unclassified_references"]
                   if row["absolute_path"].casefold() == str(path).casefold()]
        result["cleanup_candidates"].append({"path": str(path), "exists": path.exists(),
            "data_references": matching, "weak_provenance_references": weak,
            "required_by_saved_scene": any(row["required_by_saved_scene"] for row in matching),
            "unclassified_reference": bool(unknown),
            "status": "required" if any(row["required_by_saved_scene"] for row in matching)
                      else "unclassified" if unknown else "not_required_by_this_blend"})
    after = sha256(source)
    result["input_sha256_after"] = after
    result["input_unchanged"] = before == after
    if before != after:
        raise RuntimeError("Input file changed during inspection")
    serialized = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    if report:
        report.parent.mkdir(parents=True, exist_ok=True)
        with report.open("x", encoding="utf-8") as stream:
            stream.write(serialized + "\n")
        print(f"REPORT: {report}")
    else:
        print(serialized)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1)
