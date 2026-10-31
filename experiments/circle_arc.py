"""Select the pure visible-arc path for the native circle benchmark."""
def install():
    import tipy.circle_arc_runtime as arc
    original = arc.circle
    arc.circle = lambda adaptive=True: original(adaptive=False)
