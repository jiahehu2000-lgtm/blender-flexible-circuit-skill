"""Shared Blender geometry checks; Python standard library and Blender only."""

import math

import bmesh
import bpy
from mathutils import Vector


def core_objects(names=None):
    if names:
        objects = []
        for name in names:
            obj = bpy.context.scene.objects.get(name)
            if obj is None:
                raise ValueError(f"Object is absent from the active scene: {name}")
            objects.append(obj)
    else:
        objects = [obj for obj in bpy.context.scene.objects
                   if obj.type == "MESH" and "twisted_wire" in obj.name.lower()
                   and "solid" in obj.name.lower()]
    if len(objects) != 2 or len({obj.as_pointer() for obj in objects}) != 2:
        raise ValueError(f"Expected exactly two different cores, found {len(objects)}")
    if any(obj.type != "MESH" for obj in objects):
        raise ValueError("Both cores must be mesh objects")
    return sorted(objects, key=lambda obj: obj.name)


def ring_geometry(obj):
    raw_count = obj.get("centerline_rings", 0)
    count = int(raw_count)
    if count != raw_count or count < 2:
        raise ValueError(f"Invalid centerline_rings on {obj.name}: {raw_count}")
    segments, remainder = divmod(len(obj.data.vertices), count)
    if remainder or segments < 12:
        raise ValueError(f"Expected complete ordered rings with >=12 vertices on {obj.name}")
    radius = float(obj.get("solid_radius", 0.0))
    if not math.isfinite(radius) or radius <= 0.0:
        raise ValueError(f"Invalid solid_radius on {obj.name}: {radius}")
    points = [obj.matrix_world @ vertex.co for vertex in obj.data.vertices]
    if not all(math.isfinite(value) for point in points for value in point):
        raise ValueError(f"Non-finite coordinates on {obj.name}")
    rings = [points[index * segments:(index + 1) * segments] for index in range(count)]
    # Blender Vectors store float32; summing 64 large X coordinates in-place
    # introduces artificial radius/tangent errors. Accumulate in Python doubles.
    centers = [Vector(tuple(math.fsum(point[axis] for point in ring) / segments
                            for axis in range(3))) for ring in rings]
    tangents = []
    for index in range(count):
        tangent = centers[min(index + 1, count - 1)] - centers[max(index - 1, 0)]
        if tangent.length < 1.0e-10:
            raise ValueError(f"Degenerate centerline tangent on {obj.name}, ring {index}")
        tangents.append(tangent.normalized())
    return count, segments, radius, rings, centers, tangents


def mesh_metrics(obj):
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        signed = bm.calc_volume(signed=True)
        determinant = obj.matrix_world.to_3x3().determinant()
        boundary = sum(edge.is_boundary for edge in bm.edges)
        non_manifold = sum(not edge.is_manifold for edge in bm.edges)
        return {
            "vertices": len(bm.verts), "edges": len(bm.edges), "faces": len(bm.faces),
            "boundary_edges": boundary, "non_manifold_edges": non_manifold,
            "loose_edges": sum(edge.is_wire for edge in bm.edges),
            "signed_local_volume": signed,
            "absolute_world_volume": abs(signed * determinant),
            "closed_positive_volume": boundary == 0 and non_manifold == 0
                                      and abs(signed * determinant) > 0.0,
            "basis": "base mesh before modifiers; no self-intersection test",
        }
    finally:
        bm.free()


def end_cap_metrics(obj, segments=None):
    caps = [face for face in obj.data.polygons if len(face.vertices) >= 8]
    result = []
    for face in caps:
        points = [obj.matrix_world @ obj.data.vertices[index].co for index in face.vertices]
        center = Vector(tuple(math.fsum(point[axis] for point in points) / len(points)
                              for axis in range(3)))
        # Newell's method gives the plane normal of an ordered planar polygon.
        pairs = list(zip(points, points[1:] + points[:1]))
        normal = Vector((math.fsum((a.y - b.y) * (a.z + b.z) for a, b in pairs),
                         math.fsum((a.z - b.z) * (a.x + b.x) for a, b in pairs),
                         math.fsum((a.x - b.x) * (a.y + b.y) for a, b in pairs)))
        normal = normal.normalized() if normal.length else Vector()
        distances = [abs((point - center).dot(normal)) for point in points]
        result.append({
            "polygon_index": face.index, "vertex_count": len(points),
            "center_world": list(center), "normal_world": list(normal),
            "plane_fit": "Newell polygon normal through centroid",
            "max_plane_distance": max(distances),
            "x_span": max(point.x for point in points) - min(point.x for point in points),
            "smooth": bool(face.use_smooth),
        })
    return sorted(result, key=lambda item: item["center_world"][0])


def validate_core_topology(obj):
    count, segments, radius, rings, centers, tangents = ring_geometry(obj)
    first = set(range(segments))
    last = set(range((count - 1) * segments, count * segments))
    caps = [face for face in obj.data.polygons if len(face.vertices) >= 8]
    if len(caps) != 2 or {frozenset(face.vertices) for face in caps} != {
            frozenset(first), frozenset(last)}:
        raise ValueError(f"Expected exactly two end caps on first/last rings: {obj.name}")
    if len(obj.data.polygons) != (count - 1) * segments + 2:
        raise ValueError(f"Unexpected side-face count on {obj.name}")
    side_pairs = {}
    for face in obj.data.polygons:
        if face in caps:
            continue
        groups = sorted(index // segments for index in face.vertices)
        if (len(groups) != 4 or groups[0] != groups[1] or groups[2] != groups[3]
                or groups[2] != groups[0] + 1):
            raise ValueError(f"Side faces must connect consecutive ordered rings: {obj.name}")
        key = (groups[0], groups[2])
        side_pairs[key] = side_pairs.get(key, 0) + 1
    if any(side_pairs.get((index, index + 1)) != segments for index in range(count - 1)):
        raise ValueError(f"Incomplete ring connections on {obj.name}")
    metrics = mesh_metrics(obj)
    if not metrics["closed_positive_volume"]:
        raise ValueError(f"Core is not a closed positive-volume mesh: {obj.name}: {metrics}")
    return count, segments, radius, rings, centers, tangents


def core_metrics(obj):
    count, segments, radius, rings, centers, tangents = ring_geometry(obj)
    per_ring = []
    for index, (ring, center, tangent) in enumerate(zip(rings, centers, tangents)):
        offsets = [point - center for point in ring]
        radial = [abs(offset.length - radius) for offset in offsets]
        axial = [abs(offset.dot(tangent)) for offset in offsets]
        per_ring.append({
            "index": index, "center_world": list(center), "tangent_world": list(tangent),
            "max_radius_error": max(radial), "mean_radius_error": sum(radial) / segments,
            "max_orthogonal_error": max(axial),
            "max_orthogonal_error_over_radius": max(axial) / radius,
        })
    return {
        "ring_count": count, "radial_segments": segments, "metadata_radius_world": radius,
        "max_radius_error": max(row["max_radius_error"] for row in per_ring),
        "max_orthogonal_error": max(row["max_orthogonal_error"] for row in per_ring),
        "max_orthogonal_error_over_radius": max(row["max_orthogonal_error_over_radius"] for row in per_ring),
        "end_caps": end_cap_metrics(obj, segments), "rings": per_ring,
    }


def recalculate_normals(obj):
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
        if bm.calc_volume(signed=True) < 0.0:
            bmesh.ops.reverse_faces(bm, faces=list(bm.faces))
        bm.to_mesh(obj.data)
    finally:
        bm.free()
    obj.data.update()
