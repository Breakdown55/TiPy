"""Static indexed meshes with an allocation-free, bounded Q8.8 renderer."""
from .errors import CompileError

PUBLIC={'mesh','draw_mesh'}


def install(tree):
    from .graphics_library import walk
    nodes=list(walk(tree))
    if not any(n.get('kind')=='call' and n.get('name') in PUBLIC for n in nodes):return
    functions={n['name']:n for n in tree['body'] if n.get('kind')=='function'}
    active=[n for n in tree['body'] if n.get('kind')!='function'];seen=set()
    pending={n['name'] for n in walk(active) if n.get('kind') in {'call','name'}}
    while pending:
        name=pending.pop()
        if name in seen or name not in functions:continue
        seen.add(name);active.append(functions[name]);pending.update(n['name'] for n in walk(functions[name]) if n.get('kind') in {'call','name'})
    mesh_active=any(n.get('kind')=='call' and n.get('name')=='draw_mesh' for n in walk(active))
    # This camera has a deliberately bounded coordinate range. Mixing it with
    # the general XYZ path would silently select two different camera states.
    for n in walk(active) if mesh_active else []:
        if n.get('kind')=='call' and (n.get('name')=='line' and len(n['arguments'])==6 or n.get('name') in {'point','vertex'} and len(n['arguments'])==3):
            raise CompileError('Use mesh edges for XYZ geometry when drawing meshes; XY drawing remains available',n)
    def integer(n):
        if n.get('kind')=='number':return n['value']
        if n.get('kind')=='negate':
            v=integer(n['value']);return -v if v is not None else None
        return None
    def rows(n,width):
        if n.get('kind') not in {'list','tuple'}:raise CompileError('mesh requires literal vertices and edges',n)
        result=[]
        for row in n['values']:
            if row.get('kind') not in {'list','tuple'} or len(row['values'])!=width:raise CompileError(f'Expected {width} integers per mesh entry',row)
            values=[integer(v) for v in row['values']]
            if any(v is None for v in values):raise CompileError('Mesh entries must be constant integers',row)
            result.append(values)
        return result
    for n in nodes:
        if n.get('kind')!='call':continue
        name=n.get('name')
        if name=='mesh':
            if len(n['arguments'])!=2:raise CompileError('mesh(vertices, edges) requires two literal arrays',n)
            vertices,edges=rows(n['arguments'][0],3),rows(n['arguments'][1],2)
            if not 1<=len(vertices)<=64 or not 1<=len(edges)<=192:raise CompileError('Meshes allow 1..64 vertices and 1..192 edges',n)
            if any(sum(abs(c) for c in v)>60 for v in vertices):raise CompileError('Mesh vertex |x| + |y| + |z| must be at most 60',n)
            if any(not 0<=i<len(vertices) for edge in edges for i in edge):raise CompileError('Mesh edge index is outside the vertex array',n)
            words=[0x4d54,len(vertices),len(edges),max(sum(abs(c) for c in v) for v in vertices)+2]+[c for v in vertices for c in v]
            data=[b for w in words for b in (w&255,(w>>8)&255)]+[i for e in edges for i in e]
            location={k:n[k] for k in ('line','column','filename') if k in n}
            n.clear();n.update(kind='geometry_data',data=data,vertex_count=len(vertices),**location)
        elif name=='draw_mesh' or mesh_active and name in {'init_camera','camera','set_fov'}:
            n['name']='__tipy_native_'+{'draw_mesh':'mesh_draw','init_camera':'mesh_init','camera':'mesh_look','set_fov':'mesh_fov'}[name]
