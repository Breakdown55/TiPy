"""Group pure constant XYZ edges into shared, static vertex/index packets."""

def batch_lines(tree):
    def integer(n):
        if n.get('kind')=='number':return n['value']
        if n.get('kind')=='negate':
            value=integer(n['value']);return -value if value is not None else None
        return None
    def walk(n):
        if isinstance(n,dict):
            yield n
            for value in n.values():yield from walk(value)
        elif isinstance(n,list):
            for value in n:yield from walk(value)
    from copy import deepcopy
    original=deepcopy(tree['body'])
    calls=[n for n in walk(tree) if n.get('kind')=='call']
    fast=None
    if tree.get('fast_wireframes') and not any(n['name'] in {'translate','scale','rotate','rotate_x','rotate_y','rotate_z','apply_matrix','reset_matrix','push_matrix','pop_matrix','set_fov','stroke_weight'} for n in calls):
        cameras=[n for n in calls if n['name']=='camera'];configs=[]
        for n in calls:
            if n['name']!='init_camera':continue
            values={'fov':60,'near':1,'far':1000}
            for i,a in enumerate(n['arguments']):
                key=a['name'] if a['kind']=='keyword' else ['fov','near','far'][i] if i<3 else '?'
                values[key]=integer(a['value'] if a['kind']=='keyword' else a)
            configs.append(values)
        if configs and all(c==configs[0] for c in configs) and all(c.get('fov') is not None and 55<=c['fov']<=120 and c.get('near') is not None and 1<=c['near']<=127 and c.get('far') is not None and c['far']>c['near'] for c in configs):
            if cameras and all(len(n['arguments']) in {6,9} and all(integer(v)==0 for v in n['arguments'][:3]) and (len(n['arguments'])==6 or [integer(v) for v in n['arguments'][6:]]==[0,1,0]) for n in cameras):fast=configs[0]
    def edge(statement):
        n=statement.get('value',{})
        if statement.get('kind')!='expression_statement' or n.get('kind')!='call' or n.get('name')!='line' or len(n['arguments'])!=6:return None
        values=[integer(a) for a in n['arguments']]
        return values if all(v is not None and -32768<=v<=32767 for v in values) else None
    def process(items,camera_ready=False):
        result=[];i=0
        while i<len(items):
            edges=[];vertices=[];indices=[];j=i
            while j<len(items) and (values:=edge(items[j])) is not None:
                points=[tuple(values[:3]),tuple(values[3:])]
                if len(set(vertices+points))>64:break
                for p in points:
                    if p not in vertices:vertices.append(p)
                    indices.append(vertices.index(p))
                edges.append(values);j+=1
            if len(edges)>=2:
                location={k:items[i]['value'][k] for k in ('filename','line','column') if k in items[i]['value']}
                compact=fast is not None and camera_ready and all(sum(c*c for c in p)<=120*120 for p in vertices)
                packet=[len(vertices),len(edges)]
                if compact:packet.extend([fast['near']*256,min(32767,fast['far']*256)])
                raw=[b for v in packet+[c for p in vertices for c in p] for b in (v&255,(v>>8)&255)]+indices
                if compact:
                    import math
                    focal=round(32/math.tan(math.radians(fast['fov'])/2)*16)
                    raw.extend(b for d in range(256,512) for b in (round(focal*4096/d)&255,round(focal*4096/d)>>8))
                value={'kind':'call','name':'__tipy_gfx_fast_batch' if compact else '__tipy_gfx_batch','arguments':[{'kind':'geometry_data','data':raw,'vertex_count':len(vertices),'compact':compact,**location}],**location}
                result.append({'kind':'expression_statement','value':value,**location});i=j;continue
            node=items[i]
            if node.get('kind')=='expression_statement' and node.get('value',{}).get('name')=='camera':camera_ready=True
            for key in ('body','alternative','final'):
                if isinstance(node.get(key),list):node[key]=process(node[key],False if node.get('kind')=='function' else camera_ready)
            result.append(node);i+=1
        return result
    tree['body']=process(tree['body'])
    remaining=[n for n in walk(tree) if n.get('kind')=='call' and (n['name']=='__tipy_gfx_batch' or n['name']=='line' and len(n['arguments'])==6 or n['name'] in {'point','vertex'} and len(n['arguments'])==3)]
    has_fast=any(n.get('kind')=='call' and n['name']=='__tipy_gfx_fast_batch' for n in walk(tree))
    top_init=next((i for i,n in enumerate(original) if n.get('kind')=='expression_statement' and n.get('value',{}).get('name')=='init_camera'),None)
    safe_prefix={'no_fill','no_stroke','stroke','clear','background','set_cpu_speed'}
    unsafe_prefix=top_init is not None and any(n.get('kind')=='call' and n.get('name') not in safe_prefix for statement in original[:top_init] for n in walk(statement))
    if has_fast and (remaining or top_init is None or unsafe_prefix or any(n.get('kind') in {'if','while','for'} for n in original[:top_init])):
        fast=None;tree['body']=process(original)
    elif has_fast:
        for n in walk(tree):
            if n.get('kind')=='call' and n['name']=='camera':n['name']='__tipy_native_cam_fast_look'


def vertex_program(vertices,label):
    """Specialize constant coordinate products and share them across vertices."""
    terms={};code=[label+':']
    def emit(*lines):code.extend('        '+line for line in lines)
    for row in range(3):
        for axis in range(3):
            for magnitude in sorted({abs(v[axis]) for v in vertices if v[axis]}):
                index=len(terms);terms[row,axis,magnitude]=index
                emit(f'ld hl,(__gfx_wf_basis+{row*6+axis*2})')
                if magnitude!=1:
                    emit('ld d,h','ld e,l')
                    for bit in bin(magnitude)[3:]:
                        emit('add hl,hl')
                        if bit=='1':emit('add hl,de')
                emit(f'ld ({label}_terms+{index*2}),hl')
    for i,vertex in enumerate(vertices):
        for row in range(3):
            emit('ld hl,0')
            for axis,value in enumerate(vertex):
                if not value:continue
                emit(f'ld de,({label}_terms+{terms[row,axis,abs(value)]*2})')
                if value<0:emit('or a','sbc hl,de')
                else:emit('add hl,de')
            emit(f'ld (__gfx_wf_records+{i*12+row*2}),hl')
    emit('ret');code.append(label+'_terms: defs '+str(len(terms)*2)+',0')
    return '\n'.join(code)
