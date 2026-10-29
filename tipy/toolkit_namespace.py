"""Both public rendering namespaces resolve to the same graphics operations."""

COMMON = {
    "clear",
    "render",
    "clear_rect",
    "pixel_on",
    "pixel_off",
    "flip_pixel",
    "line",
    "h_line",
    "v_line",
    "rectangle",
    "circle",
    "ellipse",
    "rect",
    "triangle",
    "quad",
    "polygon",
    "polyline",
    "fill",
    "no_fill",
    "stroke",
    "no_stroke",
    "stroke_weight",
    "print",
    "input",
    "key_down",
    "init_camera",
    "camera",
    "set_fov",
    "mesh",
    "load_obj",
    "draw_mesh",
    "draw_shadow",
    "collision_map",
    "collides",
    "load_scene",
    "draw_scene",
    "set_visible",
    "scene_collisions",
    "no_lights",
    "ambient_light",
    "point_light",
    "directional_light",
}
ALIASES = {
    "gfx": {name: name for name in COMMON},
    "gx": {name: name for name in COMMON},
}
ALIASES["gfx"].update(
    {
        "init": "__tipy_native_mesh_session_begin",
        "quit": "__tipy_native_mesh_session_end",
        "flip": "render",
        "draw_line": "__tipy_toolkit_line",
        "draw_rect": "__tipy_toolkit_rect",
        "draw_circle": "__tipy_toolkit_circle",
        "draw_text": "print",
    }
)
ALIASES["gx"].update(
    {
        "load_view_matrix": "__tipy_native_mesh_load_view",
        "set_projection": "init_camera",
        "mul_fixed": "__tipy_native_mesh_mul",
        "present": "render",
        "get_buffer_byte": "__tipy_native_mesh_buffer_read",
        "set_buffer_byte": "__tipy_native_mesh_buffer_write",
        "camera_x": "__tipy_native_mesh_eye_x",
        "camera_y": "__tipy_native_mesh_eye_y",
        "camera_z": "__tipy_native_mesh_eye_z",
    }
)
for members in ALIASES.values():
    members["get_pixel"] = "__tipy_native_mesh_get_pixel"
