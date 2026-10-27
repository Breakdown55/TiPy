"""Compile-time Wavefront geometry import. Never opens paths or runs OBJ text."""
import math
from .errors import CompileError


def triangulate(points, polygon, location):
    """Ear clipping handles simple concave polygons, retaining input winding."""
    if len(set(polygon))!=len(polygon):raise CompileError('OBJ polygon repeats a vertex',location)
    p=[points[i] for i in polygon]
    normal=[sum((p[i][(a+1)%3]-p[(i+1)%len(p)][(a+1)%3])*(p[i][(a+2)%3]+p[(i+1)%len(p)][(a+2)%3]) for i in range(len(p))) for a in range(3)]
    axis=max(range(3),key=lambda a:abs(normal[a]))
    if abs(normal[axis])<1e-10:raise CompileError('OBJ polygon has zero area',location)
    axes=[a for a in range(3) if a!=axis];xy=[(v[axes[0]],v[axes[1]]) for v in p]
    def cross(a,b,c):return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    for i in range(len(p)):
        for j in range(i+1,len(p)):
            if j in {i,(i+1)%len(p)} or (j+1)%len(p)==i:continue
            a,b,c,d=xy[i],xy[(i+1)%len(p)],xy[j],xy[(j+1)%len(p)]
            if cross(a,b,c)*cross(a,b,d)<0 and cross(c,d,a)*cross(c,d,b)<0:raise CompileError('OBJ polygon intersects itself',location)
    area=sum(xy[i][0]*xy[(i+1)%len(p)][1]-xy[(i+1)%len(p)][0]*xy[i][1] for i in range(len(p)))
    sign=1 if area>0 else -1
    ids=list(range(len(p)));result=[]
    while len(ids)>3:
        for j,b in enumerate(ids):
            a,c=ids[j-1],ids[(j+1)%len(ids)]
            if sign*cross(xy[a],xy[b],xy[c])<=1e-10:continue
            if any(all(sign*cross(xy[u],xy[v],xy[k])>=-1e-10 for u,v in ((a,b),(b,c),(c,a))) for k in ids if k not in {a,b,c}):continue
            result.append([polygon[a],polygon[b],polygon[c]]);ids.pop(j);break
        else:raise CompileError('OBJ polygon must be simple and non-degenerate',location)
    result.append([polygon[i] for i in ids]);return result


def load(text, name, radius, origin):
    points=[];polygons=[]
    for line_number,line in enumerate(text.splitlines(),1):
        fields=line.split('#',1)[0].split()
        if not fields:continue
        location={'filename':name,'line':line_number,'column':1}
        try:
            if fields[0]=='v':
                if len(fields) not in {4,5}:raise ValueError('OBJ vertex requires x y z and optional w')
                values=[float(v) for v in fields[1:]]
                if not all(math.isfinite(v) for v in values):raise ValueError('OBJ coordinates must be finite')
                if len(values)==4:
                    if values[3]==0:raise ValueError('OBJ homogeneous w cannot be zero')
                    values=[v/values[3] for v in values[:3]]
                    if not all(math.isfinite(v) for v in values):raise ValueError('OBJ homogeneous coordinates overflow')
                points.append(values)
                if len(points)>64:raise ValueError('OBJ exceeds the 64-vertex mesh limit; simplify the model first')
            elif fields[0]=='f':
                if len(fields)<4:raise ValueError('OBJ face requires at least three vertices')
                indices=[]
                for value in fields[1:]:
                    parts=value.split('/')
                    if len(parts)>3:raise ValueError('Invalid OBJ face index')
                    index=int(parts[0]);index=index-1 if index>0 else len(points)+index if index<0 else -1
                    if not 0<=index<len(points):raise ValueError('OBJ face index is outside the declared vertices')
                    for v in parts[1:]:
                        if v:int(v)
                    indices.append(index)
                polygons.append((indices,location))
            elif fields[0] not in {'vt','vn','o','g','s','usemtl','mtllib'}:
                raise ValueError('Unsupported OBJ record '+fields[0]+'; export a polygon mesh')
        except (ValueError,OverflowError) as e:raise CompileError(str(e),location) from e
    if not points or not polygons:raise CompileError('OBJ requires vertices and polygon faces',origin)
    center=[min(p[a] for p in points)/2+max(p[a] for p in points)/2 for a in range(3)]
    centered=[[p[a]-center[a] for a in range(3)] for p in points]
    extent=max(sum(abs(c) for c in p) for p in centered)
    if not math.isfinite(extent) or extent<=0:raise CompileError('OBJ has no finite spatial extent',origin)
    normalized=[[c/extent*radius for c in p] for p in centered]
    vertices=[[round(c) for c in p] for p in normalized]
    faces=[]
    for polygon,location in polygons:
        faces.extend(triangulate(normalized,polygon,location))
        if len(faces)>128:raise CompileError('OBJ exceeds the 128-triangle mesh limit; simplify the model first',location)
    return vertices,faces
