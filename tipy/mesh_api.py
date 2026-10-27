"""Static indexed meshes with an allocation-free, bounded Q8.8 renderer."""
from .errors import CompileError

PUBLIC={'mesh','draw_mesh','load_obj','mesh_light'}


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
        if name in {'mesh','load_obj'}:
            positional=[];keywords={}
            for a in n['arguments']:
                if a.get('kind')=='keyword':
                    if a['name'] in keywords:raise CompileError('Duplicate mesh argument '+a['name'],a)
                    keywords[a['name']]=a['value']
                elif keywords:raise CompileError('Positional arguments must precede mesh keywords',a)
                else:positional.append(a)
            if name=='load_obj':
                if len(positional)!=1 or positional[0].get('kind')!='string' or set(keywords)-{'radius'}:raise CompileError('Use load_obj("model.obj", radius=32)',n)
                filename=positional[0]['value'];radius=integer(keywords['radius']) if 'radius' in keywords else 32
                if radius is None or not 1<=radius<=59:raise CompileError('OBJ radius must be a constant integer in 1..59',n)
                assets=tree.get('assets',{})
                if not filename.endswith('.obj') or filename not in assets:raise CompileError('Open the OBJ asset in the project first: '+filename,n)
                from .obj_loader import load
                vertices,faces=load(assets[filename],filename,radius,n);edges=[]
            else:
                if not 1<=len(positional)<=3 or set(keywords)-{'edges','faces'}:raise CompileError('Use mesh(vertices, edges=[], faces=[])',n)
                if len(positional)>1 and 'edges' in keywords or len(positional)>2 and 'faces' in keywords:raise CompileError('Duplicate mesh argument',n)
                vertices=rows(positional[0],3)
                edge_arg=positional[1] if len(positional)>1 else keywords.get('edges')
                face_arg=positional[2] if len(positional)>2 else keywords.get('faces')
                edges=rows(edge_arg,2) if edge_arg else [];faces=[]
                if face_arg:
                    if face_arg.get('kind') not in {'list','tuple'}:raise CompileError('Mesh faces must be literal triangles',face_arg)
                    for f in face_arg['values']:
                        if f.get('kind') not in {'list','tuple'} or len(f['values']) not in {3,4}:raise CompileError('A face is (a,b,c) or (a,b,c,brightness)',f)
                        values=[integer(v) for v in f['values']]
                        if any(v is None for v in values):raise CompileError('Face entries must be constant integers',f)
                        faces.append(values)
            if not 1<=len(vertices)<=64 or len(edges)>192 or not edges and not faces:raise CompileError('Meshes allow 1..64 vertices, up to 192 edges, and require edges or faces',n)
            if len(faces)>128:raise CompileError('Meshes allow at most 128 triangles',n)
            if any(sum(abs(c) for c in v)>60 for v in vertices):raise CompileError('Mesh vertex |x| + |y| + |z| must be at most 60',n)
            if any(not 0<=i<len(vertices) for edge in edges for i in edge):raise CompileError('Mesh edge index is outside the vertex array',n)
            face_data=[]
            import math
            for face in faces:
                if any(not 0<=i<len(vertices) for i in face[:3]):raise CompileError('Mesh face index is outside the vertex array',n)
                shade=face[3] if len(face)==4 else 16
                if not 0<=shade<=16:raise CompileError('Face brightness must be 0..16',n)
                a,b,c=[vertices[i] for i in face[:3]];u=[b[i]-a[i] for i in range(3)];v=[c[i]-a[i] for i in range(3)]
                normal=[u[(i+1)%3]*v[(i+2)%3]-u[(i+2)%3]*v[(i+1)%3] for i in range(3)]
                length=math.sqrt(sum(x*x for x in normal))
                if length==0:raise CompileError('Mesh triangle has zero area after integer quantization',n)
                normal=[round(x*256/length) for x in normal]
                face_data.extend(face[:3]+[shade]+[byte for x in normal for byte in (x&255,(x>>8)&255)])
            if faces and not edges:
                seen_edges=set()
                for f in faces:
                    for a,b in ((f[0],f[1]),(f[1],f[2]),(f[2],f[0])):
                        edge=tuple(sorted((a,b)))
                        if edge not in seen_edges:edges.append(edge);seen_edges.add(edge)
                if len(edges)>192:raise CompileError('Triangulated mesh exceeds the 192-edge limit',n)
            words=[0x4d54,len(vertices),len(edges),max(sum(abs(c) for c in v) for v in vertices)+2,len(faces)]+[c for v in vertices for c in v]
            data=[b for w in words for b in (w&255,(w>>8)&255)]+[i for e in edges for i in e]+face_data
            location={k:n[k] for k in ('line','column','filename') if k in n}
            n.clear();n.update(kind='geometry_data',data=data,vertex_count=len(vertices),face_count=len(faces),**location)
        elif name in {'draw_mesh','mesh_light'} or mesh_active and name in {'init_camera','camera','set_fov'}:
            n['name']='__tipy_native_'+{'draw_mesh':'mesh_draw','mesh_light':'solid_light','init_camera':'mesh_init','camera':'mesh_look','set_fov':'mesh_fov'}[name]
