"""Demand-linked py5-style facade, compiled to Z80 with 4x4 matrices."""
from pathlib import Path
from .errors import CompileError
from .camera_api import CAMERA_PUBLIC,install_helpers as install_camera_helpers
from .mesh_api import PUBLIC as MESH_PUBLIC
from .scene_api import PUBLIC as SCENE_PUBLIC
from .toolkit_api import PUBLIC as TOOLKIT_PUBLIC
MATRIX_NAMES={'translate','scale','rotate','rotate_x','rotate_y','rotate_z','push_matrix','pop_matrix','reset_matrix','apply_matrix'}
PUBLIC={'background','circle','ellipse','rect','triangle','quad','polygon','polyline','point','fill','no_fill','stroke','no_stroke','stroke_weight','begin_shape','vertex','end_shape'}|MATRIX_NAMES|CAMERA_PUBLIC|MESH_PUBLIC
PUBLIC |= SCENE_PUBLIC | TOOLKIT_PUBLIC
SIGNATURES={'circle':(3,),'ellipse':(4,),'rect':(4,),'triangle':(6,),'quad':(8,),'polygon':(1,),'polyline':(1,2),'point':(2,3),'line':(4,6),'fill':(1,),'stroke':(1,),'no_fill':(0,),'no_stroke':(0,),'stroke_weight':(1,),'translate':(2,3),'scale':(1,2,3),'rotate':(1,),'rotate_x':(1,),'rotate_y':(1,),'rotate_z':(1,),'push_matrix':(0,),'pop_matrix':(0,),'reset_matrix':(0,),'apply_matrix':(16,),'begin_shape':(0,),'vertex':(2,3),'end_shape':(0,1)}
SIGNATURES['background']=(1,)
def walk(n):
    if isinstance(n,dict):
        yield n
        for v in n.values():yield from walk(v)
    elif isinstance(n,list):
        for v in n:yield from walk(v)
def install(tree):
    from .mesh_api import install as install_meshes
    install_meshes(tree)
    nodes=list(walk(tree));called={n['name'] for n in nodes if n.get('kind')=='call'}
    if not called & PUBLIC and not any(n.get('kind')=='call' and n.get('name')=='line' and len(n['arguments'])==6 for n in nodes):return
    user_functions={n['name']:n for n in tree['body'] if n.get('kind')=='function'}
    active=[n for n in tree['body'] if n.get('kind')!='function']+[p['default'] for f in tree['body'] if f.get('kind')=='function' for p in f['params'] if p.get('default') is not None];visited=set()
    pending={n['name'] for n in walk(active) if n.get('kind') in {'call','name'}}
    while pending:
        name=pending.pop()
        if name in visited or name not in user_functions:continue
        visited.add(name);active.append(user_functions[name]);pending.update(n['name'] for n in walk(user_functions[name]) if n.get('kind') in {'call','name'})
    matrix=any(n.get('kind')=='call' and n['name'] in MATRIX_NAMES for n in walk(active))
    camera=any(n.get('kind')=='call' and n['name'] in CAMERA_PUBLIC for n in walk(active))
    if camera:
        from .wireframe import batch_lines
        batch_lines(tree)
        nodes=list(walk(tree))
    dimensions={len(n['arguments']) for n in walk(active) if n.get('kind')=='call' and n.get('name')=='vertex'}
    outline_only=False
    if 'fill' not in called:
        for statement in tree['body']:
            if statement.get('kind')=='pass':continue
            if statement.get('kind')=='function':
                if any(n.get('kind') in {'call','method'} for p in statement['params'] for n in walk(p.get('default'))):break
                continue
            if statement.get('kind') in {'declare','assign'} and not any(n.get('kind') in {'call','method'} for n in walk(statement.get('value'))):continue
            value=statement.get('value',{})
            if statement.get('kind')=='expression_statement' and value.get('kind')=='call':
                if value['name']=='clear' or value['name']=='background' and all(a.get('kind')=='number' for a in value['arguments']):continue
                if value['name'] in CAMERA_PUBLIC|MATRIX_NAMES|{'stroke','no_stroke','stroke_weight'} and not any(a.get('kind') in {'call','method'} for arg in value['arguments'] for a in walk(arg)):continue
                if value['name']=='no_fill':outline_only=True
            break
    for n in nodes:
        if n.get('raw_graphics'):continue
        if n.get('kind')=='function' and (n['name'] in PUBLIC or n['name'].startswith('__tipy_gfx_')):raise CompileError('Graphics names are reserved',n)
        if n.get('kind')=='call' and n['name'] in CAMERA_PUBLIC:
            n['name']='__tipy_native_'+{'init_camera':'cam_init','camera':'cam_look','set_fov':'cam_fov'}[n['name']]
        elif n.get('kind')=='call' and camera and n['name'] in MATRIX_NAMES:
            name=n['name']
            if len(n['arguments']) not in SIGNATURES[name]:raise CompileError(f'Invalid arguments to {name}',n)
            n['name']='__tipy_matrix_'+name
        elif n.get('kind')=='call' and n['name'] in SIGNATURES:
            name=n['name'];args=n['arguments'];count=len(args)
            if count not in SIGNATURES[name]:raise CompileError(f'Invalid arguments to {name}',n)
            if any(a.get('kind')=='keyword' for a in args):raise CompileError('Use positional arguments for graphics functions',n)
            if name in {'translate','scale','point','line','vertex'}:name+=str(count)
            if name=='rotate':name='rotate_z'
            if name=='polyline' and count==1 or name=='end_shape' and count==0:args.append({'kind':'boolean','value':False,'line':n['line'],'column':n['column']})
            n['name']='__tipy_gfx_'+name
    from .lexer import tokenize
    from .parser import parse
    helpers=parse(tokenize(Path(__file__).with_suffix('.tip').read_text(encoding='utf8')))
    def fill_condition(item):
        condition=item.get('condition',{});left=condition.get('left',{})
        return condition.get('kind')=='compare' and condition.get('operator')=='!=' and left.get('kind')=='index' and left.get('base',{}).get('name')=='__tipy_gfx_style' and left.get('index',{}).get('value')==0 and condition.get('right',{}).get('value')==0
    def prune(items):
        result=[]
        for item in items:
            if outline_only and fill_condition(item):continue
            if item.get('kind')=='if' and item['condition'].get('kind')=='call' and item['condition'].get('name')=='__tipy_gfx_transformed':
                result.extend(prune(item['body'] if matrix else item['alternative']))
                if matrix:break
                continue
            for key in ('body','alternative','final'):
                if isinstance(item.get(key),list):item[key]=prune(item[key])
            result.append(item)
        return result
    for n in helpers['body']:
        if not matrix and n.get('kind')=='function' and n['name']=='__tipy_gfx_project':n['body']=parse(tokenize('def identity(int x,int y,int z):\n    return x,y\n'))['body'][0]['body']
        if n.get('kind')=='function':n['body']=prune(n['body'])
    if camera:
        install_camera_helpers(helpers,matrix,dimensions)
        # The camera end-shape wrapper also needs the proven outline-only pass.
        for n in helpers['body']:
            if n.get('kind')=='function' and n['name']=='__tipy_gfx_end_shape':n['body']=prune(n['body'])
    functions={n['name']:n for n in helpers['body'] if n.get('kind')=='function'}
    globals_={n['name']:n for n in helpers['body'] if n.get('kind')=='declare'}
    live=set();pending={n['name'] for n in walk(tree) if n.get('kind') in {'call','name'}}
    while pending:
        name=pending.pop()
        if name in live:continue
        node=functions.get(name,globals_.get(name))
        if node is None:continue
        live.add(name);pending.update(n['name'] for n in walk(node) if n.get('kind') in {'call','name'})
    selected=[n for n in helpers['body'] if n.get('name') in live]
    for n in walk(selected):
        if n.get('kind') in {'declare','name'} and n.get('name') in globals_:n['name']='$'+n['name']
    declared={n.get('name') for n in tree['body'] if n.get('kind') in {'declare','assign'}}
    for name,value in {'BLACK':0,'WHITE':255,'CLOSE':True,'OPEN':False}.items():
        if name not in declared and any(n.get('kind')=='name' and n.get('name')==name for n in nodes):selected.append({'kind':'declare','name':name,'type':'bool' if isinstance(value,bool) else 'int','value':{'kind':'boolean' if isinstance(value,bool) else 'number','value':value,'line':1,'column':1},'line':1,'column':1})
    tree['body']=selected+tree['body']
