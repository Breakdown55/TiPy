# GX and Easy GX

`gx` is the explicit primitive API. `egx` provides shapes, text, cameras, loaded
models, lights, scenes, input, and application helpers. They share a drawing
buffer, the native mesh camera, model transforms, and the depth buffer.
Either `gx.render()` or `egx.render()` displays the combined drawing buffer.

## Built-in EGX shapes and custom GX geometry

```python
import egx

egx.init_camera()
egx.camera(0, 14, 38, 0, 0, 0)
egx.clear()
egx.plane(36, 28, y=-10, brightness=14, shaded=False)
egx.cube(12, yaw=32, pitch=12)
egx.render()
```

`cube(size=12, ...)` draws a centered cube; size is its edge length in world
units, from 1 to 40. Runtime size is supported; values outside that range draw
nothing. `plane(width=24, depth=24, ...)` draws a centered, two-sided XZ plane.
Plane dimensions are positive compile-time integers; the sum of their rounded
up half-extents must be at most 60. Both support x/y/z, yaw/pitch/roll, filled,
shaded, outline, depth_test and constant brightness (0..16). Defaults are filled,
shaded and outlined, with depth testing off. Rotations use 256 steps per turn,
as in the native model renderer. Shared translate/rotate/scale transforms also
apply; transform-stack rotation functions continue to use radians.

Built-in geometry is packed and reused at compile time, with no per-frame list
construction. For custom vertex and face lists, use `gx.mesh(...)` and
`gx.draw_mesh(model, ...)`. These raw geometry APIs are absent from EGX.
`egx.load_obj(...)` and `egx.draw_model(model, ...)` remain the convenient path
for OBJ assets; scenes and lights remain in EGX.

### Reusable shape objects

```python
cube = egx.Cube(size=12)
floor = egx.Plane(width=36, depth=28, y=-10, shaded=False)
cube.rotation_y = 0.8
cube.outline = True

egx.clear()
floor.draw()
cube.draw()
egx.render()
```

Objects expose x/y/z, rotation_x/y/z, scale, visible, filled, shaded, outline,
and depth_test. Object rotations use **radians**; immediate cube/plane yaw,
pitch and roll retain their existing 256-step convention. Rotations and scale
use native Q8.8 storage (about 1/256 precision, -128..127.996 range). Integers
can be assigned to these properties; a nonpositive scale hides the shape.
`cube.size` can change. Plane width/depth and material brightness are fixed at
construction; use scale for uniform resizing. Constructors accept position
and drawing flags as keywords; set rotation/scale properties afterward.

`draw()` only draws: it does not clear, present, move the camera, update lights
or change the shared model transform. It allocates no per-frame lists or
geometry. Simple non-escaping module-level objects and their direct attribute
aliases use static scalar fields. Escaping objects retain TiPy's ordinary
object ABI and its RAM costs. `egx.cube()` and `egx.plane()` remain available
and can be mixed with object drawing and GX submissions.

## Primitive submission

```python
import gx

gx.clear()
gx.shade(0)
gx.begin(gx.TRIANGLES)
gx.vertex(10, 10)
gx.vertex(40, 10)
gx.vertex(40, 30)
gx.end()
gx.render()
```

`TRIANGLES` consumes independent triples; `LINES` consumes independent pairs.
Each complete primitive is rasterized as it arrives. `end()` validates that
there is no incomplete primitive. Only one primitive is buffered in fixed
storage; immediate submission does not allocate a growing list or use a heap.
Nested `begin()`, vertices outside a submission, mixed XY/XYZ vertices in one
submission, and `end()` without `begin()` raise `RuntimeError`. An incomplete
primitive at `end()` also raises `RuntimeError`; complete primitives already
submitted remain drawn. These sequence errors reset the submission state.

`vertex(x, y)` uses XY drawing coordinates, independent of the camera.
`vertex(x, y, z)` uses world coordinates and the shared native mesh camera.
The current model transform applies when each vertex is submitted. XYZ uses
the existing bounded native projector: the sum of absolute camera-relative
X, Y, and Z components must not exceed 120 world units. This conservative bound
prevents overflow when the view rotates. Use a suitable camera and scene scale.
Near/far and screen clipping happen in the native rasterizer.

## Indexed buffers

```python
vertices = [(10, 10), (40, 10), (40, 30), (10, 30)]
indices = [0, 1, 2, 0, 2, 3]
gx.draw(gx.TRIANGLES, vertices, indices)
```

`draw(mode, vertices, indices=None, dimensions=2)` submits a reusable sequence.
Omit indices to use the vertex order directly. Use `dimensions=3` for XYZ
buffers. Indices are zero-based; they select vertices, not individual scalar
components. All supplied argument expressions are evaluated once in source
order. Buffers are read without being modified. Runtime containers use ordinary
TiPy managed storage; fully literal constant buffers are expanded directly into
native vertex calls and do not require a heap.

## Explicit state

- `shade(color)`: flat grayscale, 0 black through 255 white. Triangles use the
  existing 4 x 4 ordered dither patterns (17 monochrome levels). Lines use black
  below the midpoint and white at or above it.
- `cull(enabled)`: defaults to False. When enabled, clockwise triangles in
  screen coordinates are front faces; counterclockwise faces are discarded.
  This does not affect lines.
- `depth_test(enabled)`: defaults to False. Filled XYZ triangles use the shared
  8-bit inverse-depth buffer. XY triangles and lines are overlays and neither
  test nor write depth. The 6,144-byte depth buffer is linked only when a
  reachable XYZ or mesh draw can need it.
- `clear()`: clears the drawing buffer and any linked depth buffer, while
  retaining shade, culling, matrices, and projection settings.

These GX submission settings persist across GX calls. EGX model draws retain
their own explicit per-draw options; neither API saves the other one's state.
GX supplies flat shade directly rather than calculating EGX lighting.

## Matrices and buffer access

`load_matrix(m00, ..., m33)` replaces the shared model matrix with sixteen
row-major numeric entries. The final affine row must be literal `0,0,0,1`.
Translation is in m03, m13, and m23. `reset_matrix()`, `push_matrix()`, and
`pop_matrix()` operate on the same model transform as EGX, with at most eight
saved transforms. The stack does not save the camera, shade, culling, or depth
settings. EGX's `translate`, `rotate`, and `scale` can be mixed with these calls.

`set_projection(fov=75, near=2, far=120)` selects perspective projection.
`load_view_matrix(eye_x, eye_y, eye_z, r00, ..., r22)` loads the native camera
position and nine Q8.8 rotation coefficients (256 means 1). EGX's look-at
`camera()` configures that same camera without requiring a view matrix.

`get_buffer_byte(index)` and `set_buffer_byte(index, value)` expose packed screen
bytes directly: 768 bytes, 12 per row, with bit 7 the leftmost pixel in a byte.
These bypass transforms and depth. `mul_fixed(a, b)` multiplies signed Q8.8
values, with a result scaled down by 256.

Only used native routines and state are linked. GX does not expose hardware
features the TI-84 renderer lacks, such as textures, alpha blending, or a GPU.
