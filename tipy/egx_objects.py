"""Reusable EGX shapes backed by ordinary TiPy records and shared geometry."""
from copy import deepcopy
from .scene_data import arguments, fragment, number, walk
from .errors import CompileError

CONSTRUCTORS = {"__egx_Cube", "__egx_Plane"}
NUMERIC_PROPERTIES = {"rotation_x", "rotation_y", "rotation_z", "scale"}


def prepare(tree):
    classes = {}

    def constructor(node):
        cube = node["name"] == "__egx_Cube"
        dimensions = ["size"] if cube else ["width", "depth"]
        options = ["x", "y", "z", "filled", "shaded", "outline", "depth_test", "visible", "brightness"]
        defaults = {"x": 0, "y": 0, "z": 0, "filled": True, "shaded": True,
                    "outline": True, "depth_test": False, "visible": True, "brightness": 16}
        defaults.update({"size": 12} if cube else {"width": 24, "depth": 24})
        args = arguments(node, dimensions + options, defaults)
        brightness = int(number(args["brightness"], 0, 16, integer=True))
        if cube:
            key = ("Cube", brightness)
            dimension_fields = "        self.size = size"
            geometry = "__egx_cube(self.size"
            params = "int size=12, "
        else:
            width = int(number(args["width"], 1, 118, integer=True))
            depth = int(number(args["depth"], 1, 118, integer=True))
            if (width + 1)//2 + (depth + 1)//2 > 60:
                raise CompileError("Plane dimensions exceed the native vertex range", node)
            key = ("Plane", width, depth, brightness)
            dimension_fields = f"        self.width = {width}\n        self.depth = {depth}"
            geometry = f"__egx_plane({width}, {depth}"
            params = ""
        if key not in classes:
            name = f"__Egx{key[0]}_{len(classes)}"
            source = f"""class {name}:
    def __init__(self, {params}int x=0, int y=0, int z=0, bool filled=True, bool shaded=True, bool outline=True, bool depth_test=False, bool visible=True):
{dimension_fields}
        self.x = x
        self.y = y
        self.z = z
        self.rotation_x = 0.0
        self.rotation_y = 0.0
        self.rotation_z = 0.0
        self.scale = 1.0
        self.filled = filled
        self.shaded = shaded
        self.outline = outline
        self.depth_test = depth_test
        self.visible = visible
        self.brightness = {brightness}
    def draw(self):
        if self.visible and self.scale > 0:
            {geometry}, x=self.x, y=self.y, z=self.z,
                pitch=__egx_angle(self.rotation_x), yaw=__egx_angle(self.rotation_y), roll=__egx_angle(self.rotation_z),
                filled=self.filled, shaded=self.shaded, outline=self.outline, depth_test=self.depth_test,
                brightness={brightness}, __native_scale=__egx_fixed_bits(self.scale))
"""
            definition = fragment(source, node)[0]
            definition["egx_shape"] = True
            definition["readonly_fields"] = ["brightness"] + ([] if cube else ["width", "depth"])
            for child in walk(definition):
                if child.get("kind") == "call" and child.get("name") in {"__egx_cube", "__egx_plane"}:
                    child["egx_object_draw"] = True
                    child["shared_graphics"] = True
            classes[key] = definition
        # Dimensions/material are compile-time configuration. Other supplied
        # expressions keep their original source order in the constructor call.
        skip = {"brightness"} | (set() if cube else {"width", "depth"})
        node["name"] = classes[key]["name"]
        node["arguments"] = [{"kind": "keyword", "name": name, "value": value}
                             for name, value in args.items() if name not in skip]

    def visit(value):
        if isinstance(value, list):
            for child in value: visit(child)
        elif isinstance(value, dict):
            for child in list(value.values()): visit(child)
            if value.get("kind") == "call" and value.get("name") in CONSTRUCTORS:
                constructor(value)
    visit(tree["body"])
    tree["body"] = list(classes.values()) + tree["body"]
    collapse_aliases(tree, {definition["name"] for definition in classes.values()})


def collapse_aliases(tree, class_names):
    """Keep non-escaping module-level aliases eligible for scalar replacement.

    Identity tests, container storage, rebindings and shadowed names retain the
    ordinary object ABI. This only erases aliases used for attribute access.
    """
    from .static_records import walk as contextual_walk
    owners, declarations, aliases = {}, {}, {}
    for statement in tree['body']:
        if statement.get('kind') not in {'assign','declare'}: continue
        value=statement.get('value',{})
        if value.get('kind')=='call' and value.get('name') in class_names:
            owners[statement['name']]=statement['name'];declarations[statement['name']]=statement
        elif value.get('kind')=='name' and value['name'] in owners:
            owners[statement['name']]=owners[value['name']]
            declarations[statement['name']]=statement
            aliases[statement['name']]=owners[value['name']]
    for owner in set(aliases.values()):
        names={name for name,target in owners.items() if target==owner}
        allowed={id(declarations[name]) for name in names}
        safe=True
        for node,parent,key in contextual_walk(tree['body']):
            if node.get('kind') in {'assign','declare','parameter','for','target_name'} and node.get('name') in names and id(node) not in allowed:
                safe=False
            if node.get('kind')=='name' and node.get('name') in names:
                if parent and parent.get('kind') in {'attribute','method'} and key=='base':continue
                if parent and id(parent) in allowed and key=='value':continue
                safe=False
        if not safe: continue
        selected={name:target for name,target in aliases.items() if target==owner}
        for node,parent,key in contextual_walk(tree['body']):
            if node.get('kind')=='name' and node.get('name') in selected and key=='base':
                node['name']=owner
        tree['body']=[statement for statement in tree['body'] if not any(statement is declarations[name] for name in selected)]
