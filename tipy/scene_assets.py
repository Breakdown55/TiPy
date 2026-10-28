"""Compile-time OBJ selections and axis-aligned collision geometry."""

from .errors import CompileError


def objects(text, filename, origin):
    """Keep OBJ indices global while collecting each object's referenced vertices."""
    points = []
    groups = {}
    current = "default"
    for line_number, line in enumerate(text.splitlines(), 1):
        fields = line.split("#", 1)[0].split()
        if not fields:
            continue
        location = {"filename": filename, "line": line_number, "column": 1}
        if fields[0] in {"o", "g"}:
            current = fields[1] if len(fields) > 1 else "default"
        elif fields[0] == "v":
            points.append(" ".join(fields))
            # Collider boxes need only two diagonal corners, without faces.
            groups.setdefault(current, {"vertices": set(), "faces": []})[
                "vertices"
            ].add(len(points) - 1)
        elif fields[0] == "f":
            indices = []
            for field in fields[1:]:
                try:
                    index = int(field.split("/", 1)[0])
                    index = (
                        index - 1
                        if index > 0
                        else len(points) + index if index < 0 else -1
                    )
                except ValueError as error:
                    raise CompileError("Invalid OBJ face index", location) from error
                if not 0 <= index < len(points):
                    raise CompileError(
                        "OBJ face index is outside the declared vertices", location
                    )
                indices.append(index)
            group = groups.setdefault(current, {"vertices": set(), "faces": []})
            group["vertices"].update(indices)
            group["faces"].append(indices)
        elif fields[0] not in {"vt", "vn", "s", "usemtl", "mtllib"}:
            raise CompileError("Unsupported OBJ record " + fields[0], location)
    return points, groups


def select(text, filename, name, origin):
    points, groups = objects(text, filename, origin)
    if name not in groups or not groups[name]["faces"]:
        raise CompileError(f"OBJ has no polygon object named {name!r}", origin)
    group = groups[name]
    indices = sorted(group["vertices"])
    remap = {index: position + 1 for position, index in enumerate(indices)}
    # Keep blank lines for unselected records so import diagnostics still
    # point at the original OBJ's line numbers.
    lines = []
    current = "default"
    vertex = -1
    for line in text.splitlines():
        fields = line.split("#", 1)[0].split()
        selected = ""
        if fields:
            if fields[0] in {"o", "g"}:
                current = fields[1] if len(fields) > 1 else "default"
            elif fields[0] == "v":
                vertex += 1
                if vertex in remap:
                    selected = points[vertex]
            elif fields[0] == "f" and current == name:
                face = []
                for field in fields[1:]:
                    index = int(field.split("/", 1)[0])
                    index = index - 1 if index > 0 else vertex + 1 + index
                    face.append(remap[index])
                selected = "f " + " ".join(map(str, face))
        lines.append(selected)
    return "\n".join(lines)


def collision_boxes(text, filename, origin):
    import math

    points, groups = objects(text, filename, origin)
    boxes = []
    for name, group in groups.items():
        if not name.startswith("collider_"):
            continue
        try:
            coordinates = [
                [float(value) for value in points[index].split()[1:]]
                for index in group["vertices"]
            ]
            if not coordinates or any(
                len(point) != 3 or not all(math.isfinite(v) for v in point)
                for point in coordinates
            ):
                raise ValueError()
            boxes.append(
                [
                    math.floor(min(p[0] for p in coordinates)),
                    math.floor(min(p[2] for p in coordinates)),
                    math.ceil(max(p[0] for p in coordinates)),
                    math.ceil(max(p[2] for p in coordinates)),
                ]
            )
        except (ValueError, OverflowError) as error:
            raise CompileError(
                f"Collider {name!r} needs finite XYZ vertices", origin
            ) from error
    if not boxes:
        raise CompileError("OBJ has no collider_* objects", origin)
    return boxes
