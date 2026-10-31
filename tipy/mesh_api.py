"""Static indexed meshes with an allocation-free, bounded Q8.8 renderer."""

from .errors import CompileError

PUBLIC = {
    "mesh",
    "draw_mesh",
    "draw_shadow",
    "load_obj",
    "collision_map",
    "collides",
    "mesh_light",
    "no_lights",
    "ambient_light",
    "directional_light",
    "point_light",
}


def install(tree):
    from .graphics_library import walk

    nodes = list(walk(tree))
    for node in nodes:
        if node.get("shared_graphics") and node.get("name") == "line" and len(node["arguments"]) == 6:
            node["name"] = "__tipy_native_mesh_line"
    shared_camera = any(n.get("shared_graphics") and n.get("name") in {"init_camera", "camera", "set_fov"} for n in nodes)
    if not shared_camera and not any(n.get("kind") == "call" and n.get("name") in PUBLIC for n in nodes):
        return
    functions = {n["name"]: n for n in tree["body"] if n.get("kind") == "function"}
    active = [n for n in tree["body"] if n.get("kind") != "function"]
    seen = set()
    pending = {n["name"] for n in walk(active) if n.get("kind") in {"call", "name"}}
    while pending:
        name = pending.pop()
        if name in seen or name not in functions:
            continue
        seen.add(name)
        active.append(functions[name])
        pending.update(
            n["name"]
            for n in walk(functions[name])
            if n.get("kind") in {"call", "name"}
        )
    mesh_active = any(
        n.get("kind") == "call" and n.get("name") in {"draw_mesh", "draw_shadow"}
        for n in walk(active)
    )
    # This camera has a deliberately bounded coordinate range. Mixing it with
    # the general XYZ path would silently select two different camera states.
    for n in walk(active) if mesh_active else []:
        if n.get("kind") == "call" and (
            n.get("name") == "line"
            and len(n["arguments"]) == 6
            or n.get("name") in {"point", "vertex"}
            and len(n["arguments"]) == 3
        ):
            raise CompileError(
                "Use mesh edges for XYZ geometry when drawing meshes; XY drawing remains available",
                n,
            )

    def integer(n):
        if n.get("kind") == "number":
            return n["value"]
        if n.get("kind") == "negate":
            v = integer(n["value"])
            return -v if v is not None else None
        return None

    def rows(n, width):
        if n.get("kind") not in {"list", "tuple"}:
            raise CompileError("mesh requires literal vertices and edges", n)
        result = []
        for row in n["values"]:
            if row.get("kind") not in {"list", "tuple"} or len(row["values"]) != width:
                raise CompileError(f"Expected {width} integers per mesh entry", row)
            values = [integer(v) for v in row["values"]]
            if any(v is None for v in values):
                raise CompileError("Mesh entries must be constant integers", row)
            result.append(values)
        return result

    effective_points = False
    for call in nodes:
        if call.get("kind") == "call" and call.get("name") in {
            "point_light",
            "__tipy_native_solid_point_light",
        }:
            args = call["arguments"]
            positionals = [a for a in args if a.get("kind") != "keyword"]
            intensity = next(
                (
                    a["value"]
                    for a in args
                    if a.get("kind") == "keyword" and a["name"] == "intensity"
                ),
                positionals[4] if len(positionals) > 4 else None,
            )
            if intensity is None or integer(intensity) not in {0, 1}:
                effective_points = True
    quad_format = tree.get("render_quality") == "aggressive"
    face_centroids = quad_format and effective_points
    for n in nodes:
        if n.get("kind") != "call":
            continue
        name = n.get("name")
        if name == "collision_map":
            arguments = n["arguments"]
            if len(arguments) != 1:
                raise CompileError(
                    'Use collision_map(boxes) or collision_map("level.obj")', n
                )
            if arguments[0].get("kind") == "string":
                from .scene_assets import collision_boxes

                filename = arguments[0]["value"]
                if filename not in tree.get("assets", {}):
                    raise CompileError(
                        "Open the OBJ asset in the project first: " + filename, n
                    )
                boxes = collision_boxes(tree["assets"][filename], filename, n)
            else:
                boxes = rows(arguments[0], 4)
            if len(boxes) > 64 or any(
                not -120 <= value <= 120 for box in boxes for value in box
            ):
                raise CompileError(
                    "Collision maps allow up to 64 boxes with coordinates in -120..120",
                    n,
                )
            if any(box[0] > box[2] or box[1] > box[3] for box in boxes):
                raise CompileError(
                    "Collision box must be (min_x, min_z, max_x, max_z)", n
                )
            words = [0x4342, len(boxes)] + [value for box in boxes for value in box]
            data = [
                byte for value in words for byte in (value & 255, (value >> 8) & 255)
            ]
            location = {
                key: n[key] for key in ("line", "column", "filename") if key in n
            }
            n.clear()
            n.update(
                kind="geometry_data",
                data=data,
                vertex_count=0,
                collision_map=True,
                **location,
            )
        elif name in {"mesh", "load_obj"}:
            positional = []
            keywords = {}
            for a in n["arguments"]:
                if a.get("kind") == "keyword":
                    if a["name"] in keywords:
                        raise CompileError("Duplicate mesh argument " + a["name"], a)
                    keywords[a["name"]] = a["value"]
                elif keywords:
                    raise CompileError(
                        "Positional arguments must precede mesh keywords", a
                    )
                else:
                    positional.append(a)
            if name == "load_obj":
                if (
                    len(positional) != 1
                    or positional[0].get("kind") != "string"
                    or set(keywords) - {"radius", "object", "normalize", "brightness"}
                ):
                    raise CompileError('Use load_obj("model.obj", radius=32)', n)
                filename = positional[0]["value"]
                radius = integer(keywords["radius"]) if "radius" in keywords else 32
                if radius is None or not 1 <= radius <= 59:
                    raise CompileError(
                        "OBJ radius must be a constant integer in 1..59", n
                    )
                assets = tree.get("assets", {})
                if not filename.endswith(".obj") or filename not in assets:
                    raise CompileError(
                        "Open the OBJ asset in the project first: " + filename, n
                    )
                from .obj_loader import load

                source = assets[filename]
                if "object" in keywords:
                    from .scene_assets import select

                    if keywords["object"].get("kind") != "string":
                        raise CompileError("OBJ object must be a constant name", n)
                    source = select(source, filename, keywords["object"]["value"], n)
                normalize = keywords.get(
                    "normalize", {"kind": "boolean", "value": True}
                )
                if normalize.get("kind") != "boolean":
                    raise CompileError("OBJ normalize must be True or False", n)
                vertices, faces = load(
                    source, filename, radius, n, normalize=normalize["value"]
                )
                brightness = (
                    integer(keywords["brightness"]) if "brightness" in keywords else 16
                )
                if brightness is None or not 0 <= brightness <= 16:
                    raise CompileError("OBJ brightness must be a constant in 0..16", n)
                faces = [face + [brightness] for face in faces]
                # Polygon boundaries should not display triangulation diagonals.
                from .scene_assets import objects

                _, object_groups = objects(source, filename, n)
                edges = sorted(
                    {
                        tuple(sorted((face[i], face[(i + 1) % len(face)])))
                        for group in object_groups.values()
                        for face in group["faces"]
                        for i in range(len(face))
                    }
                )
            else:
                if not 1 <= len(positional) <= 3 or set(keywords) - {"edges", "faces"}:
                    raise CompileError("Use mesh(vertices, edges=[], faces=[])", n)
                if (
                    len(positional) > 1
                    and "edges" in keywords
                    or len(positional) > 2
                    and "faces" in keywords
                ):
                    raise CompileError("Duplicate mesh argument", n)
                vertices = rows(positional[0], 3)
                edge_arg = (
                    positional[1] if len(positional) > 1 else keywords.get("edges")
                )
                face_arg = (
                    positional[2] if len(positional) > 2 else keywords.get("faces")
                )
                edges = rows(edge_arg, 2) if edge_arg else []
                faces = []
                if face_arg:
                    if face_arg.get("kind") not in {"list", "tuple"}:
                        raise CompileError(
                            "Mesh faces must be literal triangles", face_arg
                        )
                    for f in face_arg["values"]:
                        if f.get("kind") not in {"list", "tuple"} or len(
                            f["values"]
                        ) not in {3, 4}:
                            raise CompileError(
                                "A face is (a,b,c) or (a,b,c,brightness)", f
                            )
                        values = [integer(v) for v in f["values"]]
                        if any(v is None for v in values):
                            raise CompileError(
                                "Face entries must be constant integers", f
                            )
                        faces.append(values)
            if (
                not 1 <= len(vertices) <= 64
                or len(edges) > 192
                or not edges
                and not faces
            ):
                raise CompileError(
                    "Meshes allow 1..64 vertices, up to 192 edges, and require edges or faces",
                    n,
                )
            if len(faces) > 128:
                raise CompileError("Meshes allow at most 128 triangles", n)
            if any(sum(abs(c) for c in v) > 60 for v in vertices):
                raise CompileError("Mesh vertex |x| + |y| + |z| must be at most 60", n)
            if any(not 0 <= i < len(vertices) for edge in edges for i in edge):
                raise CompileError("Mesh edge index is outside the vertex array", n)
            face_data = []
            records = []
            import math

            for face in faces:
                if any(not 0 <= i < len(vertices) for i in face[:3]):
                    raise CompileError("Mesh face index is outside the vertex array", n)
                shade = face[3] if len(face) == 4 else 16
                if not 0 <= shade <= 16:
                    raise CompileError("Face brightness must be 0..16", n)
                a, b, c = [vertices[i] for i in face[:3]]
                u = [b[i] - a[i] for i in range(3)]
                v = [c[i] - a[i] for i in range(3)]
                normal = [
                    u[(i + 1) % 3] * v[(i + 2) % 3] - u[(i + 2) % 3] * v[(i + 1) % 3]
                    for i in range(3)
                ]
                length = math.sqrt(sum(x * x for x in normal))
                if length == 0:
                    raise CompileError(
                        "Mesh triangle has zero area after integer quantization", n
                    )
                normal = [round(x * 256 / length) for x in normal]
                records.append((face[:3], shade, normal))
            if quad_format:
                records = merge_coplanar(vertices, records)
            for indices, shade, normal in records:
                face_data.extend(
                    indices
                    + (
                        ([255] if len(indices) == 3 else []) + [shade, 0]
                        if quad_format
                        else [shade]
                    )
                    + [byte for x in normal for byte in (x & 255, (x >> 8) & 255)]
                )
                if face_centroids:
                    for axis in range(3):
                        total = sum(vertices[index][axis] for index in indices)
                        center = (abs(total) + len(indices) // 2) // len(indices)
                        if total < 0:
                            center = -center
                        face_data.append(center & 255)
            if faces and not edges:
                edge_faces = {}
                for indices, shade, normal in records:
                    for a, b in zip(indices, indices[1:] + indices[:1]):
                        edge = tuple(sorted((a, b)))
                        edge_faces.setdefault(edge, []).append((tuple(normal), shade))
                # Coplanar triangle seams are implementation details, not shape
                # borders. Explicit edge lists remain under the caller's control.
                edges = [edge for edge, normals in edge_faces.items()
                         if not (len(normals) == 2 and normals[0] == normals[1])]
                if len(edges) > 192:
                    raise CompileError(
                        "Triangulated mesh exceeds the 192-edge limit", n
                    )
            words = [
                0x4D54,
                len(vertices),
                len(edges),
                math.ceil(math.sqrt(max(sum(c * c for c in v) for v in vertices))) + 2,
                len(records),
            ] + [c for v in vertices for c in v]
            data = (
                [b for w in words for b in (w & 255, (w >> 8) & 255)]
                + [i for e in edges for i in e]
                + face_data
            )
            from .wireframe import box_surface_bounds

            hull_bounds = box_surface_bounds(vertices, records, edges)
            location = {k: n[k] for k in ("line", "column", "filename") if k in n}
            n.clear()
            n.update(
                kind="geometry_data",
                data=data,
                vertex_count=len(vertices),
                face_count=len(records),
                quad_format=quad_format,
                face_centroids=face_centroids,
                hull_bounds=hull_bounds,
                **location,
            )
        elif (
            name
            in {
                "draw_mesh",
                "draw_shadow",
                "collides",
                "mesh_light",
                "no_lights",
                "ambient_light",
                "directional_light",
                "point_light",
            }
            or (mesh_active or n.get("shared_graphics"))
            and name in {"init_camera", "camera", "set_fov"}
            or name == "camera" and any(argument.get("kind") == "keyword" and argument.get("name") == "coordinate_scale" for argument in n["arguments"])
        ):
            if name in {"camera", "collides"}:
                scales = [
                    argument
                    for argument in n["arguments"]
                    if argument.get("kind") == "keyword"
                    and argument["name"] == "coordinate_scale"
                ]
                if scales:
                    scale = integer(scales[0]["value"])
                    if len(scales) != 1 or scale not in {1, 2, 4, 8, 16}:
                        raise CompileError(
                            "coordinate_scale must be a constant 1, 2, 4, 8, or 16", n
                        )
                    n["arguments"] = [
                        argument
                        for argument in n["arguments"]
                        if argument not in scales
                    ]
                    target = "mesh_look" if name == "camera" else "mesh_collides"
                    n["name"] = (
                        "__tipy_native_"
                        + target
                        + (f"_q{scale.bit_length()-1}" if scale > 1 else "")
                    )
                    continue
            n["name"] = (
                "__tipy_native_"
                + {
                    "draw_mesh": "mesh_draw",
                    "draw_shadow": "mesh_shadow",
                    "collides": "mesh_collides",
                    "mesh_light": "solid_light",
                    "no_lights": "solid_no_lights",
                    "ambient_light": "solid_ambient_light",
                    "directional_light": "solid_directional_light",
                    "point_light": "solid_point_light",
                    "init_camera": "mesh_init",
                    "camera": "mesh_look",
                    "set_fov": "mesh_fov",
                }[name]
            )


def merge_coplanar(vertices, records):
    """Merge consecutive equally shaded triangles only into convex planar quads."""

    def cross(a, b):
        return [
            a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0],
        ]

    def delta(a, b):
        return [a[i] - b[i] for i in range(3)]

    output = []
    i = 0
    while i < len(records):
        first = records[i]
        merged = None
        if i + 1 < len(records):
            second = records[i + 1]
            a, b = first[0], second[0]
            if first[1:] == second[1:] and len(set(a) & set(b)) == 2:
                edges = [(t[j], t[(j + 1) % 3]) for t in (a, b) for j in range(3)]
                boundary = [e for e in edges if (e[1], e[0]) not in edges]
                if len(boundary) == 4:
                    quad = list(boundary[0])
                    while len(quad) < 4:
                        next_vertex = next(
                            (v for u, v in boundary if u == quad[-1] and v not in quad),
                            None,
                        )
                        if next_vertex is None:
                            break
                        quad.append(next_vertex)
                    if len(quad) == 4 and (quad[-1], quad[0]) in boundary:
                        p = [vertices[j] for j in quad]
                        normal = cross(delta(p[1], p[0]), delta(p[2], p[0]))
                        planar = (
                            sum(normal[j] * (p[3][j] - p[0][j]) for j in range(3)) == 0
                        )
                        convex = all(
                            sum(
                                x * y
                                for x, y in zip(
                                    cross(
                                        delta(p[(j + 1) % 4], p[j]),
                                        delta(p[(j + 2) % 4], p[(j + 1) % 4]),
                                    ),
                                    normal,
                                )
                            )
                            > 0
                            for j in range(4)
                        )
                        if planar and convex:
                            merged = (quad, first[1], first[2])
        output.append(merged or first)
        i += 2 if merged else 1
    return output
