"""Resolve dictionary nodes, check types/flow, and lay out per-call storage."""

from copy import deepcopy
from .errors import CompileError
from .types import is_dict, dict_types, managed, is_tuple, tuple_types, split_types, dynamic_tuple, is_set, element as element_type, is_view, view_types, iterable_element, is_cell, is_closure, is_iterator, iterator_types, is_optional, is_object

PIXEL_BUILTINS = {"flip_pixel", "pixel_on", "pixel_off"}
GRAPHICS_BUILTINS = PIXEL_BUILTINS | {"clear", "render", "v_line", "h_line", "line", "rectangle", "print", "input"}
PYTHON_BUILTINS = {"range", "list", "abs", "sum", "min", "max", "any", "all", "enumerate", "zip"}


def check(tree):
    checked = deepcopy(tree)
    from .classes import prepare as lower_classes
    lower_classes(checked)
    classes=checked["classes"]
    from .closures import prepare as lower_closures
    lower_closures(checked)
    from .syntax_lowering import prepare as lower_syntax
    lower_syntax(checked)
    def uses_exceptions(n):
        if isinstance(n,dict):return n.get("kind") in {"try_except","try_finally","raise","assert"} or any(uses_exceptions(v) for v in n.values())
        return isinstance(n,list) and any(uses_exceptions(v) for v in n)
    checked["tracing_heap"]=uses_exceptions(checked)
    from .comprehensions import prepare
    prepare(checked)
    def yielded(n):
        if isinstance(n,dict):return n.get("kind")=="yield" or any(yielded(v) for v in n.values())
        return isinstance(n,list) and any(yielded(v) for v in n)
    for f in checked["body"]:
        if f["kind"]=="function" and yielded(f["body"]):
            f["generator"]=True;f["yield_type"]=f["return_type"];f["return_type"]="void";checked["tracing_heap"]=True
    checked["generators"]=any(f.get("generator") for f in checked["body"])
    from .inference import infer_returns
    infer_returns(checked)
    globals_ = {}
    functions = {}
    global_calls = []
    context = {"locals": globals_, "initialized": set(), "function": None}
    checked["temporaries"] = {}

    def normalize(name):
        if is_set(name):return f"set[{normalize(element_type(name))}]"
        if is_tuple(name):return name[:name.index("[")]+"["+",".join(normalize(t) for t in tuple_types(name))+"]"
        if is_dict(name):
            key, value = dict_types(name)
            return f"dict[{normalize(key)},{normalize(value)}]"
        if name.endswith("[]"):
            return normalize(name[:-2]) + "[]"
        return "i16" if name == "int" else name

    # Signatures are collected before bodies, allowing forward and recursive calls.
    for statement in checked["body"]:
        if statement["kind"] != "function":
            continue
        name = statement["name"]
        if name == "len":
            raise CompileError("len is a reserved array builtin", statement)
        if name in GRAPHICS_BUILTINS - {"clear"}:
            raise CompileError(f"{name} is a reserved graphics builtin", statement)
        if name in functions:
            raise CompileError(f"Function {name!r} is already defined", statement)
        if statement["return_type"] == "auto":
            def valued_return(value):
                if isinstance(value, dict):
                    return value.get("kind") == "return" and value.get("value") is not None or any(valued_return(v) for v in value.values())
                return isinstance(value, list) and any(valued_return(v) for v in value)
            statement["return_type"] = "i16" if valued_return(statement["body"]) else "void"
        statement["return_type"] = normalize(statement["return_type"])
        if statement.get("generator"):statement["yield_type"]=normalize(statement.get("yield_type","i16"))
        if statement["return_type"] == "void[]":
            raise CompileError("An array cannot have void elements", statement)
        statement["label"] = f"__fn_{len(functions)}"
        statement["requires_globals"] = set()
        statement["calls"] = set()
        if len(statement["params"]) > 62:
            raise CompileError("At most 62 parameters fit the current stack frame layout", statement)
        names = set()
        for parameter in statement["params"]:
            parameter["type"] = normalize(parameter["type"])
            if parameter["type"] in {"void", "void[]"}:
                raise CompileError("A parameter cannot be void", parameter)
            if parameter["name"] in names:
                raise CompileError(f"Duplicate parameter {parameter['name']!r}", parameter)
            names.add(parameter["name"])
        functions[name] = statement

    def bind(item, symbol):
        item["symbol"] = symbol["label"]
        item["storage"] = symbol.get("storage", "global")
        if "offset" in symbol:
            item["offset"] = symbol["offset"]
        for field in ("cell","capture_index","env_slot"):
            if field in symbol:item[field]=symbol[field]

    def lookup(name, item, writing=False):
        function=context["function"]
        if function and name in function.get("capture_types",{}):
            return {"label":"$capture_"+name,"type":function["capture_types"][name],"cell":True,"capture_index":function["captures"].index(name),"env_slot":function["locals"]["$env"]}
        if name in context["locals"]:
            if name not in context["initialized"]:
                raise CompileError(f"Variable {name!r} may not be initialized on this branch", item)
            return context["locals"][name]
        if context["function"] and name in globals_:
            if writing:
                raise CompileError("Declare a local before assigning inside a function; global writes are not supported yet", item)
            context["function"]["requires_globals"].add(name)
            return globals_[name]
        raise CompileError(f"Variable {name!r} has not been declared", item)

    def compatible(target, source_type, constant, item):
        if "float" in {target,source_type}:
            if target!=source_type:raise CompileError(f"Cannot assign {source_type} to {target}; use an explicit numeric conversion",item)
            return
        if "i32" in {target,source_type}:
            if target!=source_type:raise CompileError(f"Cannot assign {source_type} to {target}; use an explicit integer conversion",item)
            return
        if is_object(target) or is_object(source_type):
            source=source_type[7:-1] if is_object(source_type) else None
            while source and f"object[{source}]"!=target:source=classes[source].get("base")
            if not source:raise CompileError(f"Cannot assign {source_type} to {target}",item)
            return
        if is_optional(target) or is_optional(source_type) or "none" in {target,source_type}:
            if target!=source_type:raise CompileError(f"Cannot assign {source_type} to {target}",item)
            return
        if is_iterator(target) or is_iterator(source_type) or is_cell(target) or is_cell(source_type) or is_closure(target) or is_closure(source_type):
            if target!=source_type:raise CompileError(f"Cannot assign {source_type} to {target}",item)
            return
        if is_view(target) or is_view(source_type):
            if target!=source_type:raise CompileError(f"Cannot assign {source_type} to {target}",item)
            return
        if is_set(target) or is_set(source_type):
            if target!=source_type:raise CompileError(f"Cannot assign {source_type} to {target}",item)
            return
        if is_tuple(target) or is_tuple(source_type):
            if target.startswith("tuple_var[") and is_tuple(source_type) and all(t==tuple_types(target)[0] for t in tuple_types(source_type)):return
            if target!=source_type:raise CompileError(f"Cannot assign {source_type} to {target}",item)
            return
        if is_dict(target) or is_dict(source_type):
            if target != source_type: raise CompileError(f"Cannot assign {source_type} to {target}", item)
            return
        if "str" in {target, source_type}:
            if target != source_type: raise CompileError(f"Cannot assign {source_type} to {target}", item)
            return
        if (target == "fixed") != (source_type == "fixed"):
            raise CompileError("Use int(...) to convert fixed-point values to integers", item)
        if target.endswith("[]") or source_type.endswith("[]"):
            if target != source_type:
                raise CompileError(f"Cannot assign {source_type} to {target}", item)
            return
        if source_type == "void":
            raise CompileError("A void function does not produce a value", item)
        if (target == "bool") != (source_type == "bool"):
            raise CompileError(f"Cannot assign {source_type} to {target}", item)
        if target == "bool":
            return
        if constant is not None:
            low, high = (0, 255) if target == "u8" else (-32768, 32767)
            if not low <= constant <= high:
                raise CompileError(f"{constant} is outside {target} range {low} to {high}", item)
        elif target == "u8" and source_type != "u8":
            raise CompileError("Cannot narrow an i16 expression to u8 without a proven range", item)

    expression_depth=0
    def expression(*args,**kwargs):
        nonlocal expression_depth
        expression_depth+=1
        try:
            t,c=_expression(*args,**kwargs);expected=kwargs.get("expected_element")
            if expected and is_optional(expected) and t!=expected:
                if t!="none" and t!=expected[9:-1]:raise CompileError(f"Cannot assign {t} to {expected}",args[0])
                item=args[0];value=deepcopy(item);item.clear();item.update(kind="optional_box",value=value,type=expected,owned=True,line=value["line"],column=value["column"])
                t=expected;c=None
            if expected=="i32" and t in {"i16","u8"}:
                item=args[0];value=deepcopy(item);item.clear();item.update(kind="wide_cast",value=value,type="i32",owned=True,line=value["line"],column=value["column"]);t="i32";c=None
            if expected=="float" and t in {"i16","u8","fixed","i32"}:
                item=args[0];value=deepcopy(item);item.clear();item.update(kind="float_cast",value=value,source_type=t,type="float",owned=True,line=value["line"],column=value["column"]);t="float";c=None
            return t,c
        finally:expression_depth-=1

    def scratch(type_name,index):
        pool=context.setdefault("scratch",{})
        key=(expression_depth,index,type_name)
        if key in pool:return pool[key]
        if context["function"]:
            function=context["function"];function["frame_size"]+=2
            if function["frame_size"]>4096:raise CompileError("Function frame exceeds 4096 bytes",function)
            slot={"type":type_name,"size":2,"storage":"frame","offset":-function["frame_size"],"label":f"__local_{len(context['locals'])}"}
            context["locals"][f"$argument_{len(context['locals'])}"]=slot
        else:
            slot={"type":type_name,"size":2,"label":f"__tmp_{len(checked['temporaries'])}"}
            checked["temporaries"][slot["label"]]=slot
        slot={**slot,"symbol":slot["label"]};pool[key]=slot;return slot

    def freeze_arguments(item,evaluations):
        refs={};snapshots=[]
        for i,value in enumerate(evaluations):
            slot=scratch(value["type"],i)
            refs[id(value)]={**slot,"kind":"name","name":f"$argument_{i}"}
            snapshots.append({"slot":slot,"value":value})
        def replace(value):
            if isinstance(value,dict):
                if id(value) in refs:return refs[id(value)]
                return {key:replace(v) for key,v in value.items()}
            if isinstance(value,list):return [replace(v) for v in value]
            return value
        for field in ("base","arguments","left","right","value","sources","values"):
            if field in item:item[field]=replace(item[field])
        item["evaluations"]=snapshots

    def bind_keywords(arguments,names,defaults,item):
        result=[None]*len(names);order=[];keyword=False;position=0
        for arg in arguments:
            if arg["kind"]=="keyword":
                keyword=True
                if arg["name"] not in names:raise CompileError(f"Unexpected keyword {arg['name']!r}",arg)
                index=names.index(arg["name"]);value=arg["value"]
            else:
                if keyword:raise CompileError("Positional arguments must precede keywords",arg)
                index=position;position+=1;value=arg
            if index>=len(names):raise CompileError("Too many arguments",item)
            if result[index] is not None:raise CompileError(f"Repeated argument {names[index]!r}",arg)
            result[index]=value;order.append(value)
        for i,name in enumerate(names):
            if result[i] is None:
                if name not in defaults:raise CompileError(f"Missing argument {name!r}",item)
                result[i]=deepcopy(defaults[name])
        return result,order

    def iterable_list(source,t,item):
        if is_iterator(t):
            result=iterable_element(t)+"[]"
            return {"kind":"iterator_list","base":source,"type":result,"owned":True},result
        if is_dict(t) or is_set(t) or is_view(t):
            mode="keys"
            if is_view(t):
                mode,map_type=view_types(t)
                source={"kind":"view_target","base":source,"type":map_type,"owned":True}
            else:map_type=t
            result=iterable_element(t)+"[]"
            return {"kind":"dict_list","base":source,"mode":mode,"mapping_type":map_type,"type":result,"owned":True},result
        if t=="str":return {"kind":"string_builtin","name":"to_list","arguments":[source],"type":"str[]","owned":True},"str[]"
        if is_tuple(t):
            field=iterable_element(t)
            if field is None:raise CompileError("A typed list requires matching tuple field types",item)
            from .types import width
            result=field+"[]"
            return {"kind":"sequence_builtin","name":"to_list","arguments":[source,width(result)],"type":result,"owned":True},result
        if t.endswith("[]"):return source,t
        raise CompileError("Value is not iterable",item)

    def make_iterator(source,t,item,reverse=False):
        if is_iterator(t):
            if reverse:raise CompileError("reversed requires an indexed sequence",item)
            return source,t
        mode="sequence"
        if is_view(t):
            mode,t=view_types(t);source={"kind":"view_target","base":source,"type":t,"owned":True}
        elif is_dict(t) or is_set(t):mode="keys"
        elif t=="str":mode="string"
        value=iterable_element(t)
        if mode=="items":value="tuple["+",".join(dict_types(t))+"]"
        elif mode=="values":value=dict_types(t)[1]
        if value is None:raise CompileError("Iteration requires a homogeneous element type",item)
        if reverse and mode not in {"sequence","string"}:raise CompileError("reversed requires a sequence",item)
        result=f"iter[{value},{mode},{t}]"
        return {"kind":"iterator_new","base":source,"reverse":reverse,"type":result,"owned":True},result

    def refined(condition,truth=True):
        result=set(context.get("refinements",set()))
        if condition.get("kind")=="identity" and condition.get("negate")==truth:
            a,b=condition["left"],condition["right"]
            if a["kind"]=="none":a,b=b,a
            if b["kind"]=="none" and a["kind"]=="name" and is_optional(a["type"]):result.add(a["name"])
        return result

    def _expression(item, allow_min_magnitude=False, allow_void=False, expected_element=None):
        kind = item["kind"]
        constant = None
        if kind=="none":item["type"]="none";return "none",None
        if kind=="identity":
            previous=context.get("skip_refinement",False);context["skip_refinement"]=True
            a,_=expression(item["left"]);b,_=expression(item["right"]);context["skip_refinement"]=previous
            if not (a=="none" or b=="none" or managed(a) and managed(b)):raise CompileError("is compares object identity or None",item)
            item["type"]="bool"
            if item["left"].get("owned") or item["right"].get("owned"):freeze_arguments(item,[item["left"],item["right"]])
            return "bool",None
        if kind == "number":
            value_type, constant = "i16", item["value"]
            if expected_element=="float":
                import struct
                try:raw=struct.unpack('<I',struct.pack('<f',float(constant)))[0]
                except OverflowError:raw=0x7f800000
                item.update(kind="float_number",raw=raw,type="float",owned=True);return "float",None
            if expected_element=="i32":
                if constant>(2147483648 if allow_min_magnitude else 2147483647):raise CompileError("Constant is outside i32 range",item)
                item.update(kind="wide_number",type="i32",owned=True);return "i32",constant
            if constant > (32768 if allow_min_magnitude else 32767):
                raise CompileError(f"{constant} is outside i16 literal range", item)
        elif kind == "decimal":
            from decimal import Decimal, ROUND_HALF_UP
            raw = int((Decimal(str(item["value"])) * 256).to_integral_value(rounding=ROUND_HALF_UP))
            if expected_element=="float" or not -32768<=raw<=(32768 if allow_min_magnitude else 32767):
                import struct
                try:bits=struct.unpack('<I',struct.pack('<f',float(item['value'])))[0]
                except OverflowError:bits=0x7f800000
                item.update(kind="float_number",raw=bits,type="float",owned=True);return "float",None
            if not -32768 <= raw <= (32768 if allow_min_magnitude else 32767):
                raise CompileError("Fixed-point literal must be between -128 and 127.996", item)
            item["raw"] = raw
            value_type = "fixed"
        elif kind == "stdlib":
            name, args = item["name"], item["arguments"]
            count = 2 if name == "random.randint" else 1
            if len(args) != count:
                raise CompileError(f"{name} expects {count} arguments", item)
            types = [expression(arg)[0] for arg in args]
            if name in {"random.randint", "random.seed", "math.radians"} and any(t not in {"i16", "u8"} for t in types):
                raise CompileError(f"{name} requires integer arguments", item)
            if name in {"math.sin", "math.cos", "time.sleep"} and any(t not in {"i16", "u8", "fixed"} for t in types):
                raise CompileError(f"{name} requires numeric arguments", item)
            if name == "time.sleep":
                # Literal seconds retain their decimal precision for delays.
                from decimal import Decimal
                arg = args[0]
                sign = -1 if arg["kind"] == "negate" else 1
                literal = arg["value"] if sign == -1 else arg
                if isinstance(literal, dict) and literal["kind"] in {"number", "decimal"}:
                    milliseconds = int(Decimal(str(literal["value"])) * 1000) * sign
                    if not 0 <= milliseconds <= 32767:
                        raise CompileError("sleep must be between 0 and 32.767 seconds", item)
                    item["milliseconds"] = milliseconds
                value_type = "void"
            else:
                value_type = "fixed" if name.startswith("math.") else "void" if name == "random.seed" else "i16"
        elif kind=="call" and item["name"] in {"iter","reversed","map","filter","next"} and item["name"] not in functions:
            name,args=item["name"],item["arguments"]
            if name in {"iter","reversed"}:
                if len(args)!=1:raise CompileError(f"{name} expects one iterable",item)
                t,_=expression(args[0]);value,t=make_iterator(args[0],t,item,name=="reversed")
                item.clear();item.update(value);return t,None
            if name=="next":
                if len(args) not in {1,2}:raise CompileError("next expects an iterator and optional default",item)
                t,_=expression(args[0])
                if not is_iterator(t):raise CompileError("next requires an iterator",item)
                result=iterable_element(t)
                if len(args)==2:
                    other,c=expression(args[1]);compatible(result,other,c,args[1])
                else:checked["tracing_heap"]=True
                item.update(kind="iterator_next",type=result,owned=managed(result));freeze_arguments(item,args);return result,None
            if len(args)<2 or name=="filter" and len(args)!=2:raise CompileError(f"Invalid arguments to {name}",item)
            callback=args[0];ct,_=expression(callback)
            if not is_closure(ct):raise CompileError(f"{name} requires a function",callback)
            function=functions[ct[8:-1]];params=function["params"][int(bool(function.get("closure_env") or function.get("bound_method"))):]
            if len(params)!=len(args)-1:raise CompileError("Callback parameter count does not match iterables",item)
            sources=[];source_types=[]
            for arg,p in zip(args[1:],params):
                t,_=expression(arg);source,t=make_iterator(arg,t,item)
                compatible(p["type"],iterable_element(t),None,arg);sources.append(source);source_types.append(t)
            result=iterable_element(source_types[0]) if name=="filter" else function["return_type"]
            if function["return_type"]=="void":raise CompileError("An iterator callback must return a value",item)
            t=f"iter[{result},{name},{ct},"+",".join(source_types)+"]"
            item.update(kind="iterator_map",base=callback,sources=sources,type=t,owned=True,symbol=function["label"])
            freeze_arguments(item,args)
            if context["function"]:context["function"]["calls"].add(function["name"])
            else:global_calls.append(({**item,"name":function["name"]},context["initialized"].copy()))
            return t,None
        elif kind=="call" and item["name"] in {"pow","divmod","round"} and item["name"] not in functions:
            name,args=item["name"],item["arguments"]
            count=len(args)
            if count not in ({2,3} if name=="pow" else {2} if name=="divmod" else {1,2}):raise CompileError(f"Invalid arguments to {name}",item)
            types=[expression(v)[0] for v in args]
            if name=="round":
                if types[0]=="float" or count==2 and types[0]=="fixed":
                    if count==2 and types[1] not in {"i16","u8"}:raise CompileError("round precision must be an integer",item)
                    if types[0]=="fixed":args[0]={"kind":"float_cast","value":args[0],"source_type":"fixed","type":"float","owned":True}
                    p=args[1] if count==2 else 0
                    result="i16" if count==1 else "float"
                    item.update(kind="float_round",arguments=[args[0],p,int(count==1)],type=result,owned=result=="float");return result,None
                if types[0]=="i32" or count==2:
                    if types[0] not in {"i16","u8","i32"} or count==2 and types[1] not in {"i16","u8"}:raise CompileError("round requires integer precision",item)
                    result=types[0] if types[0]=="i32" else "i16"
                    source=args[0] if types[0]=="i32" else {"kind":"wide_cast","value":args[0],"type":"i32","owned":True}
                    item.update(kind="wide_round",arguments=[source,args[1] if count==2 else 0],type=result,owned=result=="i32");return result,None
                if types[0] not in {"i16","u8","fixed"}:raise CompileError("round requires a number",item)
                item.update(kind="numeric_builtin",name="round_fixed" if types[0]=="fixed" else "identity_number",type="i16");return "i16",None
            if name=="pow" and count==2 and types[0] in {"float","i32"} and types[1] in {"i16","u8"}:
                item.update(kind="float_binary" if types[0]=="float" else "wide_binary",name="power",left=args[0],right=args[1],type=types[0],owned=True);return types[0],None
            if name=="divmod" and "i32" in types:
                if any(t not in {"i32","i16","u8"} for t in types):raise CompileError("divmod requires matching numbers",item)
                wide=[a if t=="i32" else {"kind":"wide_cast","value":a,"type":"i32","owned":True,"line":a["line"],"column":a["column"]} for a,t in zip(args,types)]
                values=[{"kind":"wide_binary","name":k,"left":wide[0],"right":wide[1],"type":"i32","owned":True} for k in ("floor_divide","modulo")]
                item.update(kind="tuple",values=values,type="tuple[i32,i32]",owned=True);freeze_arguments(item,wide);return item["type"],None
            if any(t not in {"i16","u8"} for t in types):raise CompileError(f"{name} requires integers",item)
            if name=="pow":
                if count==2:item.update(kind="power",left=args[0],right=args[1],type="i16")
                else:item.update(kind="numeric_builtin",name="pow_mod",type="i16")
                return "i16",None
            values=[{"kind":k,"left":args[0],"right":args[1],"type":"i16"} for k in ("floor_divide","modulo")]
            item.update(kind="tuple",values=values,type="tuple[i16,i16]",owned=True);freeze_arguments(item,args)
            return item["type"],None
        elif kind=="call" and item["name"]=="float":
            args=item["arguments"]
            if len(args)>1:raise CompileError("float expects zero or one number",item)
            if not args:item.update(kind="float_number",raw=0,type="float",owned=True);return "float",None
            t,_=expression(args[0],expected_element="float")
            if t=="str":item.update(kind="float_parse",type="float",owned=True);return "float",None
            if t!="float":raise CompileError("float expects a number",item)
            item.update(kind="sequence_builtin",name="retain",type="float",owned=True);return "float",None
        elif kind=="call" and item["name"]=="i32":
            if len(item["arguments"])!=1:raise CompileError("i32 expects one integer",item)
            value=item["arguments"][0];t,_=expression(value,expected_element="i32")
            if t=="float":item.update(kind="float_integer",type="i32",owned=True);return "i32",None
            if t!="i32":raise CompileError("i32 expects an integer",item)
            item.update(kind="sequence_builtin",name="retain",type="i32",owned=True);return "i32",None
        elif kind == "call" and item["name"] == "int":
            if len(item["arguments"]) != 1:
                raise CompileError("int expects one numeric value", item)
            source_type, constant = expression(item["arguments"][0], expected_element="i16")
            if source_type not in {"i16", "u8", "bool", "fixed","i32","float"}:
                raise CompileError("int expects a scalar numeric value", item)
            item["kind"] = "cast_int"
            if source_type=="i32":item["kind"]="wide_to_int"
            if source_type=="float":item["kind"]="float_int"
            value_type = "i16"
        elif kind=="call" and item["name"] in {"str","chr","ord","repr"} and item["name"] not in functions:
            name,args=item["name"],item["arguments"]
            if name=="str" and not args:
                item.update(kind="string",value="",type="str",owned=True);return "str",None
            if len(args)!=1:raise CompileError(f"{name} expects one value",item)
            t,_=expression(args[0])
            if name=="ord":
                if t!="str":raise CompileError("ord expects a string",item)
                item.update(kind="string_builtin",name="ord",type="i16");return "i16",None
            if name=="chr":
                if t not in {"i16","u8"}:raise CompileError("chr expects an integer",item)
                helper="chr"
            elif t=="str":helper="repr" if name=="repr" else "identity"
            elif t in {"i16","u8"}:helper="number"
            elif t=="bool":helper="boolean"
            elif t=="fixed":helper="fixed"
            elif t=="i32":helper="i32"
            elif t=="float":helper="float"
            elif t=="exception":helper="exception"
            else:raise CompileError("str conversion currently requires a scalar value",item)
            item.update(kind="string_builtin",name=helper,type="str",owned=True);return "str",None
        elif kind == "compare_chain":
            types = [expression(arg)[0] for arg in item["operands"]]
            if any(t not in {"i16", "u8"} for t in types):
                raise CompileError("Chained comparisons currently require integers", item)
            value_type = "bool"
        elif kind == "boolean":
            value_type, constant = "bool", item["value"]
        elif kind == "string":
            value_type = "str"
            item["owned"] = True
        elif kind=="set" or kind=="call" and item["name"]=="set":
            hint=element_type(expected_element) if expected_element and is_set(expected_element) else None
            if kind=="call":
                args=item["arguments"]
                if len(args)>1:raise CompileError("set expects zero or one iterable",item)
                if not args:item.update(kind="set",values=[])
                else:
                    source=args[0];t,_=expression(source)
                    if is_set(t):item.update(kind="set_method",name="copy",base=source,arguments=[],type=t,owned=True);return t,None
                    if is_dict(t) or is_view(t):source,t=iterable_list(source,t,item)
                    if t=="str":source={"kind":"string_builtin","name":"to_list","arguments":[source],"type":"str[]","owned":True};t="str[]"
                    if is_tuple(t):
                        fields=tuple_types(t)
                        if fields and len(set(fields))>1:raise CompileError("Set elements must have one type",item)
                        key=fields[0] if fields else hint or "i16"
                    elif t.endswith("[]"):key=t[:-2]
                    else:raise CompileError("set requires an iterable",item)
                    if key not in {"i16","u8","str"}:raise CompileError("Set elements must be integers or strings",item)
                    key="i16" if key=="u8" else key
                    item.update(kind="set_from",base=source,type=f"set[{key}]",owned=True);return item["type"],None
            key=hint
            for value in item["values"]:
                t,c=expression(value,expected_element=key)
                if t not in {"i16","u8","str"}:raise CompileError("Set elements must be integers or strings",value)
                if key is None:key="str" if t=="str" else "i16"
                compatible(key,t,c,value)
            key=key or "i16"
            if key not in {"i16","str"}:raise CompileError("Set elements must be integers or strings",item)
            item.update(type=f"set[{key}]",owned=True);return item["type"],None
        elif kind=="fstring":
            for part in item["values"]:expression(part)
            item.update(type="str",owned=True);return "str",None
        elif kind=="call" and item["name"]=="tuple":
            args=item["arguments"]
            if len(args)>1:raise CompileError("tuple expects zero or one iterable",item)
            if not args:
                item.update(kind="tuple",values=[],type="tuple[]",owned=True);return "tuple[]",None
            source=args[0];t,_=expression(source)
            if is_tuple(t):item.update(kind="sequence_builtin",name="retain",type=t,owned=True);return t,None
            if is_dict(t) or is_view(t) or is_set(t):source,t=iterable_list(source,t,item)
            if t=="str":
                source={"kind":"string_builtin","name":"to_list","arguments":[source],"type":"str[]","owned":True};t="str[]"
            if not t.endswith("[]"):raise CompileError("tuple requires an iterable",item)
            result=f"tuple_var[{t[:-2]}]"
            item.update(kind="sequence_builtin",name="to_tuple",arguments=[source,int(managed(t[:-2]))],type=result,owned=True);return result,None
        elif kind=="format_value" or kind=="call" and item["name"]=="format" and item["name"] not in functions:
            import re
            if kind=="call":
                if len(item["arguments"]) not in {1,2}:raise CompileError("format expects a value and constant format string",item)
                value=item["arguments"][0];spec="";conversion=-1
                if len(item["arguments"])==2:
                    if item["arguments"][1]["kind"]!="string":raise CompileError("Format spec must be constant",item)
                    spec=item["arguments"][1]["value"]
            else:value,spec,conversion=item["value"],item["spec"],item["conversion"]
            t,_=expression(value)
            if t not in {"i16","i32","u8","bool","str","fixed","float"}:raise CompileError("Formatting currently requires a scalar value",item)
            match=re.fullmatch(r"(?:(.)([<^>])|([<^>]))?([+ -])?(#)?(0)?(\d+)?(?:\.(\d+))?([dsxXobfF])?",spec)
            if not match:raise CompileError("Unsupported format specification",item)
            fill,alignment,simple,sign,prefix,zero,digits,precision,code=match.groups()
            numeric=t in {"i16","i32","u8","bool","fixed","float"} and conversion==-1
            if code in {"d","x","X","o","b"} and not numeric:raise CompileError("Numeric format requires an integer",item)
            if code in {"d","x","X","o","b"} and t in {"float","fixed"}:raise CompileError("Integer format requires an integer",item)
            helper="repr" if t=="str" and conversion in {114,97} else "identity" if t=="str" else "boolean" if t=="bool" and not code else "number"
            if t in {"i32","fixed","float"}:helper=t
            converted={"kind":"string_builtin","name":helper,"arguments":[value],"type":"str","owned":True}
            if code in {"f","F"} or precision is not None:
                if not numeric:raise CompileError("Decimal format requires a number",item)
                p=int(precision) if precision is not None else 6
                if p>50:raise CompileError("Decimal precision must not exceed 50",item)
                if t!="float":value={"kind":"float_cast","value":value,"source_type":t,"type":"float","owned":True}
                converted.update(name="float_format",arguments=[value,p,1])
            if code in {"x","X","o","b"}:
                if t=="fixed":raise CompileError("Radix formatting requires an integer",item)
                converted.update(name="i32_radix" if t=="i32" else "radix",arguments=[value,{"x":4,"X":4,"o":3,"b":1}[code],int(code=="X"),int(bool(prefix))])
            if sign in {"+"," "} and numeric:converted={"kind":"string_builtin","name":"sign","arguments":[converted,ord(sign)],"type":"str","owned":True}
            if digits:
                align=alignment or simple or (">" if numeric else "<")
                converted={"kind":"string_builtin","name":"pad","arguments":[converted,int(digits),ord(fill or ("0" if zero else " ")),{"<":0,">":1,"^":2}[align],int(numeric)],"type":"str","owned":True}
            location={key:item[key] for key in ("line","column","filename") if key in item}
            item.clear();item.update(converted,**location);return "str",None
        elif kind=="call" and item["name"] in {"bin","oct","hex"} and item["name"] not in functions:
            if len(item["arguments"])!=1:raise CompileError("Radix conversion requires one integer",item)
            t,_=expression(item["arguments"][0])
            if t not in {"i16","i32","u8"}:raise CompileError("Radix conversion requires one integer",item)
            item.update(kind="string_builtin",arguments=[item["arguments"][0],{"bin":1,"oct":3,"hex":4}[item["name"]],0,1],name="i32_radix" if t=="i32" else "radix",type="str",owned=True)
            return "str",None
        elif kind == "call" and item["name"] == "dict":
            if len(item["arguments"]) > 1:
                raise CompileError("dict expects zero arguments or another dictionary", item)
            if item["arguments"]:
                base = item["arguments"][0]
                value_type, _ = expression(base)
                if not is_dict(value_type): raise CompileError("dict expects another dictionary", item)
                item.update(kind="dict_method", name="copy", base=base, arguments=[], owned=True)
            else:
                item.update(kind="dict", entries=[], owned=True)
                value_type, constant = expression(item, expected_element=expected_element)
        elif kind == "dict":
            key_type, element = dict_types(expected_element) if expected_element and is_dict(expected_element) else (None, None)
            for entry in item["entries"]:
                kt, kc = expression(entry["key"], expected_element=key_type)
                vt, vc = expression(entry["value"], expected_element=element)
                if kt not in {"i16", "u8", "str"}: raise CompileError("Dictionary keys must be integers or strings", entry["key"])
                if vt not in {"i16", "u8", "bool", "str"} and not managed(vt): raise CompileError("Dictionary values must be scalar values or containers", entry["value"])
                if key_type is None: key_type = "str" if kt == "str" else "i16"
                if element is None: element = vt if managed(vt) or vt in {"str", "bool"} else "i16"
                compatible(key_type, kt, kc, entry["key"]); compatible(element, vt, vc, entry["value"])
            key_type, element = key_type or "i16", element or "i16"
            if key_type not in {"i16", "str"} or element not in {"i16", "u8", "bool", "str"} and not managed(element):
                raise CompileError("Dictionary types require int/str keys and scalar int/bool/str values", item)
            value_type = f"dict[{key_type},{element}]"
            item["owned"] = True
        elif kind == "call" and item["name"] == "hash" and item["name"] not in functions:
            if "hash" in context["locals"] or "hash" in globals_: raise CompileError("Variable 'hash' is not callable", item)
            if len(item["arguments"]) != 1: raise CompileError("hash expects one integer or string", item)
            t, _ = expression(item["arguments"][0])
            if t not in {"i16", "u8", "str"}: raise CompileError("hash expects one integer or string", item)
            item["kind"] = "hash"
            value_type = "i16"
        elif kind == "tuple":
            fields=[expression(v)[0] for v in item["values"]]
            value_type="tuple["+",".join(fields)+"]";item["owned"]=True
        elif kind=="checked_value":
            return item["type"],None
        elif kind=="closure_default":
            item["owned"]=managed(item["type"]);return item["type"],None
        elif kind=="closure":
            function=functions[item["function"]]
            fields=[];capture_types={}
            for captured in item["captures"]:
                t,_=expression(captured);fields.append(t);capture_types[captured["name"]]=t[5:-1]
            function["capture_types"]=capture_types
            function["params"][0]["type"]="tuple["+",".join(fields)+"]"
            item.update(symbol=function["label"],type=f"closure[{function['name']}]",owned=True)
            return item["type"],None
        elif kind=="cell_ref":
            symbol=lookup(item["name"],item)
            if not symbol.get("cell"):raise CompileError("Captured variable needs a shared cell",item)
            bind(item,symbol);value_type=f"cell[{symbol['type']}]"
        elif kind == "name":
            if item["name"] in functions and item["name"] not in context["locals"]:
                function=functions[item["name"]]
                item.update(kind="function_ref",function=function["name"],symbol=function["label"],type=f"closure[{function['name']}]",owned=True)
                return item["type"],None
            symbol = lookup(item["name"], item)
            bind(item, symbol)
            value_type = symbol["type"]
            if is_optional(value_type) and item["name"] in context.get("refinements",{}) and not context.get("skip_refinement"):
                value=deepcopy(item);value["type"]=value_type
                value_type=value_type[9:-1];item.clear();item.update(kind="optional_get",value=value,type=value_type,owned=managed(value_type),line=value["line"],column=value["column"])
        elif kind=="conditional":
            t,_=expression(item["condition"])
            if t!="bool":raise CompileError("A conditional expression requires a bool condition",item)
            old=context.get("refinements",set());context["refinements"]=refined(item["condition"])
            t,c=expression(item["body"],expected_element=expected_element)
            context["refinements"]=old;context["refinements"]=refined(item["condition"],False)
            other,value=expression(item["alternative"],expected_element=expected_element)
            context["refinements"]=old
            if "none" in {t,other} and t!=other:
                t=f"optional[{other if t=='none' else t}]"
                for field in ("body","alternative"):
                    v=item[field];item[field]={"kind":"optional_box","value":v,"type":t,"owned":True,"line":v["line"],"column":v["column"]}
            else:compatible(t,other,value,item)
            value_type=t
            if managed(t):item["owned"]=True
        elif kind == "comprehension":
            before = context["initialized"].copy()
            item["collect"]["expected_element"] = expected_element
            statements([item["loop"]])
            context["initialized"] = before
            collection=item.get("collection","list")
            value_type = item["collect"]["type"] + "[]" if collection=="list" else f"set[{item['collect']['type']}]" if collection=="set" else f"dict[{item['collect']['key_type']},{item['collect']['type']}]"
            item["owned"] = True
        elif kind == "list":
            hint = expected_element
            if hint and hint.endswith("[]"):hint=hint[:-2]
            element = None
            if hint == "void":
                raise CompileError("Arrays need a scalar element type", item)
            for value in item["values"]:
                value_type, value_constant = expression(value,expected_element=hint)
                if element is None:
                    element = value_type if managed(value_type) or value_type in {"bool","fixed"} else hint or "i16"
                compatible(element, value_type, value_constant, value)
            value_type = (element or hint or "i16") + "[]"
            item["owned"] = True
        elif kind=="slice":
            source,_=expression(item["base"])
            if not (source=="str" or source.endswith("[]") or is_tuple(source)):raise CompileError("Slicing requires a string, list, or tuple",item)
            values=[]
            for field in ("start","stop","step"):
                if item[field] is None:values.append(None);continue
                t,c=expression(item[field])
                if t not in {"i16","u8"}:raise CompileError("Slice bounds must be integers",item[field])
                values.append(c)
            if values[2]==0:raise CompileError("Slice step cannot be zero",item)
            if is_tuple(source):
                fields=tuple_types(source)
                if dynamic_tuple(source) or any(item[f] is not None and c is None for f,c in zip(("start","stop","step"),values)):
                    if len(set(fields))>1:raise CompileError("A dynamic tuple slice requires matching field types",item)
                    value_type=f"tuple_var[{fields[0]}]" if fields else "tuple[]"
                else:value_type="tuple["+",".join(fields[slice(*values)])+"]"
            else:value_type=source
            item.update(type=value_type,owned=True)
            return value_type,None
        elif kind=="call" and item["name"]=="sorted" and item["name"] not in functions:
            args=item["arguments"]
            if not args:raise CompileError("sorted expects a list",item)
            reverse={"kind":"boolean","value":False,"type":"bool"}
            if len(args)>2:raise CompileError("sorted expects a list and optional reverse",item)
            if len(args)==2:
                option=args[1]
                if option["kind"]!="keyword" or option["name"]!="reverse":raise CompileError("Use sorted(values, reverse=bool)",item)
                reverse=option["value"]
            original=args[0];t,_=expression(original);rt,_=expression(reverse)
            source,t=iterable_list(original,t,item)
            if rt!="bool":raise CompileError("reverse must be bool",item)
            item.update(kind="sequence_builtin",name="sorted",arguments=[source,reverse],type=t,owned=True)
            freeze_arguments(item,[original,reverse])
            return t,None
        elif kind in {"index", "method", "attribute"}:
            array_type, _ = expression(item["base"], expected_element=expected_element)
            if is_object(array_type):
                meta=classes[array_type[7:-1]];name=item.get("name")
                if kind=="attribute" and name in meta["fields"]:
                    result=meta["fields"][name];item.update(kind="object_get",index=list(meta["fields"]).index(name),type=result,owned=managed(result));return result,None
                if name not in meta["methods"]:raise CompileError(f"Unknown attribute {name!r}",item)
                function=functions[meta["methods"][name]]
                if kind=="method":
                    base=item["base"];receiver={"kind":"checked_value","value":base,"type":array_type,"owned":base.get("owned",False),"line":base["line"],"column":base["column"]}
                    value={**item,"kind":"call","name":function["name"],"arguments":[receiver,*item["arguments"]]};value.pop("base",None)
                    item.clear();item.update(value);return _expression(item,allow_min_magnitude,allow_void,expected_element)
                function["bound_method"]=True
                item.update(kind="bound_method",symbol=function["label"],type=f"closure[{function['name']}]",owned=True)
                if context["function"]:context["function"]["calls"].add(function["name"])
                else:global_calls.append(({**item,"name":function["name"]},context["initialized"].copy()))
                return item["type"],None
            if kind=="attribute":raise CompileError("Attributes require an object",item)
            if is_set(array_type):
                if kind!="method":raise CompileError("Sets do not support indexing",item)
                key=element_type(array_type);name,args,base=item["name"],item["arguments"],item["base"]
                single={"add","discard","remove"};noargs={"clear","copy","pop"}
                algebra={"union","intersection","difference","symmetric_difference"}
                updates={"update","intersection_update","difference_update","symmetric_difference_update"}
                predicates={"issubset","issuperset","isdisjoint"}
                original=[]
                if name in single:
                    if len(args)!=1:raise CompileError("Set operation expects one element",item)
                    t,c=expression(args[0],expected_element=key);compatible(key,t,c,args[0]);result="void"
                elif name in noargs:
                    if args:raise CompileError("Set operation takes no arguments",item)
                    result=array_type if name=="copy" else key if name=="pop" else "void"
                elif name in algebra|updates|predicates:
                    if name in predicates|{"symmetric_difference","symmetric_difference_update"} and len(args)!=1:raise CompileError("Set operation expects one iterable",item)
                    converted=[]
                    for arg in args:
                        t,_=expression(arg)
                        original.append(arg)
                        if t==array_type:converted.append(arg);continue
                        if t=="str":arg={"kind":"string_builtin","name":"to_list","arguments":[arg],"type":"str[]","owned":True};t="str[]"
                        if t.endswith("[]"):other=t[:-2]
                        elif is_tuple(t):
                            fields=tuple_types(t);other=fields[0] if fields and len(set(fields))==1 else key if not fields else "invalid"
                        else:other="invalid"
                        if other!=key:raise CompileError("Set operation requires matching element types",item)
                        converted.append({"kind":"set_from","base":arg,"type":array_type,"owned":True})
                    args=converted
                    result=array_type if name in algebra else "bool" if name in predicates else "void"
                else:raise CompileError(f"Unsupported set method {name}",item)
                item.update(kind="set_method",arguments=args,type=result)
                if managed(result):item["owned"]=True
                if result=="void" and not allow_void:raise CompileError("A void function does not produce a value",item)
                if original:freeze_arguments(item,[base,*original])
                return result,None
            if array_type=="str":
                if kind=="index":
                    if expression(item["index"])[0] not in {"i16","u8"}:raise CompileError("String index must be integer",item)
                    item.update(kind="string_index",type="str",owned=True);return "str",None
                name,args,base=item["name"],item["arguments"],item["base"]
                num=lambda n:{"kind":"number","value":n,"type":"i16"}
                missing={"kind":"missing"}
                source_length=lambda:{"kind":"length","arguments":[base],"type":"i16"}
                provided=[]
                if name in {"upper","lower","capitalize","title","swapcase"}:
                    if args:raise CompileError(f"{name} takes no arguments",item)
                    native=[];result="str"
                elif name=="join":
                    bound,provided=bind_keywords(args,["iterable"],{},item);t,_=expression(bound[0])
                    if t!="str[]" and not (is_tuple(t) and all(v=="str" for v in tuple_types(t))):raise CompileError("join requires strings",item)
                    native=bound;result="str"
                elif name in {"find","rfind","index","rindex","count","startswith","endswith"}:
                    bound,provided=bind_keywords(args,["sub","start","end"],{"start":num(0),"end":missing},item)
                    if expression(bound[0])[0]!="str":raise CompileError("String search requires a string",item)
                    if expression(bound[1])[0] not in {"i16","u8"}:raise CompileError("String bounds must be integers",item)
                    if bound[2]["kind"]=="missing":bound[2]=source_length()
                    elif expression(bound[2])[0] not in {"i16","u8"}:raise CompileError("String bounds must be integers",item)
                    native=bound;result="bool" if name in {"startswith","endswith"} else "i16"
                elif name=="replace":
                    bound,provided=bind_keywords(args,["old","new","count"],{"count":num(-1)},item)
                    for arg in bound[:2]:
                        if expression(arg)[0]!="str":raise CompileError("replace expects strings",item)
                    if expression(bound[2])[0] not in {"i16","u8"}:raise CompileError("replace count must be integer",item)
                    native=bound;result="str"
                elif name=="split":
                    bound,provided=bind_keywords(args,["sep","maxsplit"],{"sep":missing,"maxsplit":num(-1)},item)
                    whitespace=bound[0]["kind"]=="missing"
                    if not whitespace and expression(bound[0])[0]!="str":raise CompileError("split separator must be a string",item)
                    if expression(bound[1])[0] not in {"i16","u8"}:raise CompileError("maxsplit must be integer",item)
                    native=[0 if whitespace else bound[0],bound[1],int(whitespace)];result="str[]"
                elif name in {"strip","lstrip","rstrip"}:
                    bound,provided=bind_keywords(args,["chars"],{"chars":missing},item)
                    default=bound[0]["kind"]=="missing"
                    if not default and expression(bound[0])[0]!="str":raise CompileError("strip characters must be a string",item)
                    native=[0 if default else bound[0],{"strip":0,"lstrip":1,"rstrip":2}[name],int(default)];name="strip";result="str"
                else:raise CompileError(f"Unsupported string method {name!r}",item)
                item.update(kind="string_method",name=name,arguments=native,type=result)
                if managed(result):item["owned"]=True
                freeze_arguments(item,[base,*provided]);return result,None
            if is_dict(array_type):
                key_type, element = dict_types(array_type)
                if kind == "index":
                    kt, kc = expression(item["index"], expected_element=key_type)
                    compatible(key_type, kt, kc, item["index"])
                    item["kind"] = "dict_index"
                    value_type = element
                else:
                    name, args = item["name"], item["arguments"]
                    counts = {"get": (2,), "setdefault": (2,), "pop": (1,2), "clear": (0,), "copy": (0,), "update": (1,)}
                    if name in {"keys", "values", "items"}:
                        if args:raise CompileError("Dictionary views take no arguments",item)
                        value_type=f"view[{name},{array_type}]"
                        item.update(kind="dict_view_expr",type=value_type,owned=True)
                        return value_type,None
                    if name not in counts or len(args) not in counts[name]: raise CompileError("Unsupported dictionary method or argument count; get/setdefault need an explicit default", item)
                    for i, arg in enumerate(args):
                        expected = array_type if name == "update" else key_type if i == 0 else element
                        t, c = expression(arg, expected_element=expected)
                        compatible(expected, t, c, arg)
                    item["kind"] = "dict_method"
                    value_type = array_type if name == "copy" else "void" if name in {"clear", "update"} else element
                if managed(value_type): item["owned"] = True
                item["type"] = value_type
                if value_type == "void" and not allow_void: raise CompileError("A void function does not produce a value", item)
                return value_type, None
            if not array_type.endswith("[]"):
                if is_tuple(array_type) and kind=="index":
                    t,index=expression(item["index"])
                    fields=tuple_types(array_type)
                    if t not in {"i16","u8"}:raise CompileError("Tuple index must be integer",item)
                    if index is not None:
                        if not dynamic_tuple(array_type) and not -len(fields)<=index<len(fields):raise CompileError("Tuple index out of range",item)
                        value_type=fields[index%len(fields)]
                    elif fields and len(set(fields))==1:value_type=fields[0]
                    else:raise CompileError("A dynamic tuple index requires matching field types",item)
                    if managed(value_type):item["owned"]=True
                    item["type"]=value_type
                    return value_type,None
                raise CompileError("Indexing and list methods require an array", item)
            element = array_type[:-2]
            if kind == "index":
                index_type, _ = expression(item["index"])
                if index_type not in {"i16", "u8"}:
                    raise CompileError("An array index must be an integer", item["index"])
                value_type = element
                if managed(value_type):item["owned"]=True
            else:
                method = item["name"]
                if method=="sort":
                    args=item["arguments"]
                    reverse={"kind":"boolean","value":False,"type":"bool"}
                    if args:
                        if len(args)!=1 or args[0]["kind"]!="keyword" or args[0]["name"]!="reverse":raise CompileError("Use sort(reverse=bool)",item)
                        reverse=args[0]["value"]
                    if expression(reverse)[0]!="bool":raise CompileError("reverse must be bool",item)
                    item["arguments"]=[reverse];item["type"]="void"
                    if not allow_void:raise CompileError("A void function does not produce a value",item)
                    return "void",None
                counts = {"append": (1,), "insert": (2,), "pop": (0, 1), "remove": (1,),
                          "clear": (0,), "extend": (1,), "reverse": (0,), "copy": (0,),
                          "count": (1,), "index": (1,)}
                if method not in counts or len(item["arguments"]) not in counts[method]:
                    raise CompileError(f"Unsupported array method or argument count: {method}", item)
                for index, argument in enumerate(item["arguments"]):
                    argument_type, value = expression(argument, expected_element=element if method == "extend" else None)
                    if method == "extend":
                        compatible(array_type, argument_type, value, argument)
                    elif method in {"insert", "pop"} and index == 0:
                        if argument_type not in {"i16", "u8"}:
                            raise CompileError("An array index must be an integer", argument)
                    else:
                        compatible(element, argument_type, value, argument)
                value_type = element if method == "pop" else "i16" if method in {"count", "index"} else array_type if method == "copy" else "void"
                if managed(value_type):
                    item["owned"] = True
        elif kind == "call" and item["name"] in PYTHON_BUILTINS and item["name"] not in functions:
            name, arguments = item["name"], item["arguments"]
            if name in context["locals"] or name in globals_:
                raise CompileError(f"Variable {name!r} is not callable", item)
            count = len(arguments)
            if name in {"enumerate", "zip"}:
                raise CompileError(f"{name} currently requires a for loop with unpacked targets", item)
            valid = count in {1, 2, 3} if name == "range" else count in {0, 1} if name == "list" else count in {1, 2} if name == "sum" else count >= 1 if name in {"min", "max"} else count == 1
            if not valid:
                raise CompileError(f"Invalid number of arguments to {name}", item)
            types, constants = [], []
            for argument in arguments:
                t, c = expression(argument, expected_element=expected_element if name == "list" else None)
                types.append(t); constants.append(c)
            if name == "range":
                if any(t not in {"i16", "u8"} for t in types):
                    raise CompileError("range arguments must be integers", item)
                if count == 3 and constants[2] == 0:
                    raise CompileError("range step cannot be zero", item)
                value_type = "i16[]"
            elif name == "list":
                if count and (is_iterator(types[0]) or is_dict(types[0]) or is_view(types[0]) or is_set(types[0])):
                    converted,t=iterable_list(arguments[0],types[0],item)
                    location={k:item[k] for k in ("line","column","filename") if k in item}
                    item.clear();item.update(converted,**location);return t,None
                if count and types[0]=="str":
                    item.update(kind="string_builtin",name="to_list",type="str[]",owned=True);return "str[]",None
                if count and is_tuple(types[0]):
                    fields=tuple_types(types[0])
                    if fields and len(set(fields))!=1:raise CompileError("A typed list requires matching tuple field types",item)
                    t=(fields[0] if fields else "i16")+"[]"
                    from .types import width
                    item.update(kind="sequence_builtin",name="to_list",arguments=[arguments[0],width(t)],type=t,owned=True);return t,None
                if count and not types[0].endswith("[]"):
                    raise CompileError("list expects an array or range", item)
                value_type = types[0] if count else (expected_element or "i16") + "[]"
            elif name == "abs":
                if types[0]=="float":item.update(kind="float_unary",name="abs",value=arguments[0],type="float",owned=True);return "float",None
                if types[0]=="i32":item.update(kind="wide_unary",name="abs",value=arguments[0],type="i32",owned=True);return "i32",None
                if types[0] not in {"i16", "u8"}:
                    raise CompileError("abs expects an integer", item)
                value_type = "i16"
                constant = abs(constants[0]) if constants[0] is not None else None
            elif count == 1 or name in {"sum", "any", "all"}:
                if not types[0].endswith("[]"):
                    raise CompileError(f"{name} expects an array or range", item)
                if name == "sum" and count == 2 and types[1] not in {"i16", "u8"}:
                    raise CompileError("sum start must be an integer", item)
                if name=="sum" and types[0][:-2] not in {"i16","u8","bool"}:raise CompileError("sum requires numeric elements",item)
                value_type = "bool" if name in {"any", "all"} else "i16" if name == "sum" else types[0][:-2]
            else:
                if any(t not in {"i16", "u8"} for t in types):
                    raise CompileError(f"{name} scalar arguments must be integers", item)
                value_type = "i16"
                item["scalar"] = True
            item["kind"] = "builtin"
            if managed(value_type):
                item["owned"] = True
        elif kind == "call" and item["name"] in {"print", "input"}:
            name, arguments = item["name"], item["arguments"]
            count = 3
            if len(arguments) != count:
                raise CompileError(f"{name} expects ({'value, x, y' if name == 'print' else 'prompt, x, y'})", item)
            types = [expression(arg)[0] for arg in arguments]
            if any(t not in {"i16", "u8"} for t in types[1:]):
                raise CompileError("Text coordinates must be integers", item)
            if name == "print" and types[0] not in {"i16", "u8", "bool", "str"}:
                if types[0] in {"fixed","i32","float"}:arguments[0]={"kind":"string_builtin","name":types[0],"arguments":[arguments[0]],"type":"str","owned":True}
                else:raise CompileError("print expects text, an integer, or bool", arguments[0])
            if name == "input" and types[0] != "str":
                raise CompileError("input prompt must be text", arguments[0])
            item["kind"] = "text_print" if name == "print" else "text_input"
            value_type = "void" if name == "print" else "i16" if expected_element in {"int", "i16", "u8"} else "str"
            if value_type == "str": item["owned"] = True
        elif kind == "call" and item["name"] in GRAPHICS_BUILTINS and item["name"] not in functions:
            name = item["name"]
            count = 2 if name in PIXEL_BUILTINS else 0 if name in {"clear", "render"} else 6 if name == "rectangle" else 4
            if len(item["arguments"]) != count:
                detail = "two coordinates (x, y)" if count == 2 else f"{count} arguments"
                raise CompileError(f"{name} expects {detail}", item)
            for index, argument in enumerate(item["arguments"]):
                argument_type, _ = expression(argument)
                if name == "rectangle" and index == 5:
                    if argument_type != "bool":
                        raise CompileError("Rectangle filled must be bool", argument)
                    continue
                if argument_type not in {"i16", "u8"}:
                    raise CompileError("Graphics coordinates and width must be integers", argument)
            item["kind"] = "graphics"
            value_type = "void"
        elif kind == "call" and item["name"] == "len":
            if len(item["arguments"]) != 1:
                raise CompileError("len expects one array", item)
            argument_type, _ = expression(item["arguments"][0])
            if not managed(argument_type):
                raise CompileError("len expects an array", item)
            if is_view(argument_type):item.update(kind="view_length",type="i16");return "i16",None
            item["kind"] = "length"
            value_type = "i16"
        elif kind == "call":
            constructor=classes.get(item["name"])
            function = functions[constructor["methods"]["__init__"]] if constructor else functions.get(item["name"])
            callback=None
            if function is None and (item["name"] in context["locals"] or item["name"] in globals_ or context["function"] and item["name"] in context["function"].get("capture_types",{})):
                symbol=lookup(item["name"],item)
                if is_closure(symbol["type"]):
                    callback={"kind":"name","name":item["name"],"type":symbol["type"]};bind(callback,symbol)
                    function=functions[symbol["type"][8:-1]]
            if function is None:
                raise CompileError(f"Function {item['name']!r} has not been defined", item)
            has_env=bool(function.get("closure_env") or function.get("bound_method"))
            params=function["params"][1:] if constructor or callback is not None and has_env else function["params"];args=item["arguments"]
            variadic=params[-1] if params and params[-1].get("variadic") else None
            regular=params[:-1] if variadic else params
            extras=[];ordinary=[];positional=0
            for arg in args:
                if arg["kind"]!="keyword":
                    if variadic and positional>=len(regular):extras.append(arg)
                    else:ordinary.append(arg)
                    positional+=1
                else:ordinary.append(arg)
            defaults={p["name"]:p["default_ref"] for p in regular if "default_ref" in p}
            for p in regular:
                if "default_capture" in p:
                    defaults[p["name"]]={"kind":"closure_default","base":callback,"index":function["captures"].index(p["default_capture"]),"type":p["type"],"line":item["line"],"column":item["column"]}
            if not variadic and not defaults and not any(a["kind"]=="keyword" for a in args) and len(args)!=len(params):
                raise CompileError(f"{item['name']} expects {len(params)} arguments, got {len(args)}",item)
            bound,order=bind_keywords(ordinary,[p["name"] for p in regular],defaults,item)
            if variadic:bound.append({"kind":"tuple","values":extras,"line":item["line"],"column":item["column"]})
            item["arguments"]=bound
            for argument, parameter in zip(bound, params):
                argument_type, value = expression(argument, expected_element=parameter["type"][:-2] if parameter["type"].endswith("[]") else parameter["type"])
                compatible(parameter["type"], argument_type, value, argument)
            if callback is not None:item["base"]=callback
            if defaults or variadic or any(a["kind"]=="keyword" for a in args):
                # Snapshot the original expressions, then pack varargs from their snapshots.
                supplied=[a["value"] if a["kind"]=="keyword" else a for a in args]
                freeze_arguments(item,([callback] if callback is not None else [])+supplied)
            item["symbol"] = function["label"]
            if callback is not None:item.update(kind="closure_call",has_env=has_env)
            value_type = function["return_type"]
            if constructor:
                if value_type!="void":raise CompileError("__init__ must not return a value",item)
                value_type=f"object[{constructor['name']}]";item["constructor"]=constructor["name"];item["field_types"]=list(constructor["fields"].values())
            if function.get("generator"):
                value_type=f"iter[{function['yield_type']},generator,{function['name']}]";item["generator"]=True
            if managed(value_type):
                item["owned"] = True
            if context["function"]:
                context["function"]["calls"].add(function["name"])
            else:
                global_calls.append(({**item,"name":function["name"]}, context["initialized"].copy()))
        elif kind == "membership":
            left_type, left_constant = expression(item["left"])
            right_type, _ = expression(item["right"], expected_element="bool" if left_type == "bool" else None)
            if is_view(right_type):
                compatible(iterable_element(right_type),left_type,left_constant,item)
                item.update(kind="view_contains",type="bool");return "bool",None
            if right_type=="str":
                if left_type!="str":raise CompileError("String membership requires a string",item)
                item.update(kind="string_contains",type="bool");return "bool",None
            if is_dict(right_type) or is_set(right_type):
                compatible(element_type(right_type) if is_set(right_type) else dict_types(right_type)[0], left_type, left_constant, item)
                item.update(kind="dict_contains", type="bool")
                return "bool", None
            if not right_type.endswith("[]"):
                raise CompileError("in requires an array or range on the right", item)
            compatible(right_type[:-2], left_type, left_constant, item)
            value_type = "bool"
        elif kind == "not":
            operand_type, operand = expression(item["value"])
            if operand_type != "bool":
                raise CompileError("not requires bool", item)
            value_type, constant = "bool", not operand if operand is not None else None
        elif kind=="invert":
            t,c=expression(item["value"])
            if t=="i32":item.update(kind="wide_unary",name="invert",type="i32",owned=True);return "i32",None
            if t not in {"i16","u8"}:raise CompileError("Bitwise inversion requires an integer",item)
            value_type="i16";constant=~c if c is not None else None
        elif kind == "negate":
            operand_type, operand = expression(item["value"], allow_min_magnitude=True,expected_element=expected_element)
            if operand_type=="i32":item.update(kind="wide_unary",name="negate",type="i32",owned=True);return "i32",None
            if operand_type=="float":item.update(kind="float_unary",name="negate",type="float",owned=True);return "float",None
            if operand_type == "bool" or managed(operand_type):
                raise CompileError("Cannot negate a bool or array", item)
            value_type = operand_type if operand_type == "fixed" else "i16"
            constant = -operand if operand is not None else None
        else:
            floating_hint="float" if expected_element=="float" and kind in {"add","subtract","multiply","true_divide"} else None
            left_type, left = expression(item["left"],expected_element=floating_hint)
            right_type, right = expression(item["right"],expected_element=floating_hint)
            if "float" in {left_type,right_type} or kind=="true_divide":
                if kind not in {"add","subtract","multiply","true_divide","compare","power"}:raise CompileError("Unsupported float operation",item)
                count=kind=="power"
                if count and right_type not in {"i16","u8"}:raise CompileError("Floating-point power currently requires an integer exponent",item)
                for field,t in (("left",left_type),("right",right_type)):
                    if t not in {"float","fixed","i16","u8","i32"}:raise CompileError("Float arithmetic requires numbers",item)
                    if t!="float" and not(field=="right" and count):item[field]={"kind":"float_cast","value":item[field],"source_type":t,"type":"float","owned":True}
                result="bool" if kind=="compare" else "float"
                item.update(kind="float_compare" if kind=="compare" else "float_binary",name="divide" if kind=="true_divide" else kind,type=result,owned=result=="float");return result,None
            if "i32" in {left_type,right_type}:
                if kind not in {"add","subtract","multiply","floor_divide","modulo","compare","bit_and","bit_or","bit_xor","shift_left","shift_right","power"}:raise CompileError("Unsupported i32 operation",item)
                if left_type not in {"i32","i16","u8"} or right_type not in {"i32","i16","u8"}:raise CompileError("i32 arithmetic requires integers",item)
                count=kind in {"shift_left","shift_right","power"}
                if count and right_type=="i32":raise CompileError("A shift count or exponent must be i16",item)
                for field,t in (("left",left_type),("right",right_type)):
                    if t!="i32" and not(field=="right" and count):
                        item[field]={"kind":"wide_cast","value":item[field],"type":"i32","owned":True}
                result="bool" if kind=="compare" else "i32"
                item.update(kind="wide_compare" if kind=="compare" else "wide_binary",name=kind,type=result,owned=result=="i32");return result,None
            if kind=="true_divide":
                if left_type not in {"i16","u8","fixed"} or right_type not in {"i16","u8","fixed"}:raise CompileError("Division requires numbers",item)
                item.update(type="fixed",shift=16 if left_type!="fixed" and right_type=="fixed" else 0 if left_type=="fixed" and right_type!="fixed" else 8)
                return "fixed",None
            if kind=="compare" and (is_object(left_type) or is_object(right_type)):
                if item["operator"] not in {"==","!="}:raise CompileError("Object ordering requires an explicit comparison method",item)
                item.update(kind="identity",negate=item["operator"]=="!=",type="bool")
                if item["left"].get("owned") or item["right"].get("owned"):freeze_arguments(item,[item["left"],item["right"]])
                return "bool",None
            if is_dict(left_type) and left_type==right_type and kind=="bit_or":
                item.update(kind="dict_merge",type=left_type,owned=True);return left_type,None
            if is_set(left_type) and left_type==right_type and kind in {"bit_or","bit_and","bit_xor","subtract"}:
                item.update(kind="set_binary",name={"bit_or":"union","bit_and":"intersection","bit_xor":"symmetric_difference","subtract":"difference"}[kind],type=left_type,owned=True);return left_type,None
            if kind in {"bit_and","bit_or","bit_xor","shift_left","shift_right","power"}:
                if left_type not in {"i16","u8"} or right_type not in {"i16","u8"}:raise CompileError("Integer operator requires integers",item)
                if kind in {"shift_left","shift_right","power"} and right is not None and right<0:raise CompileError("Shift count and integer exponent cannot be negative",item)
                item["type"]="i16"
                if left is not None and right is not None:
                    constant={"bit_and":lambda:left&right,"bit_or":lambda:left|right,"bit_xor":lambda:left^right,"shift_left":lambda:0 if right>=16 else left<<right,"shift_right":lambda:left>>min(right,16),"power":lambda:pow(left,right,65536)}[kind]()
                    constant=(constant+32768)%65536-32768
                return "i16",constant
            if is_set(left_type) and is_set(right_type) and kind=="compare":
                if left_type!=right_type:raise CompileError("Set comparison requires matching element types",item)
                item.update(kind="set_compare",type="bool");return "bool",None
            if kind=="add" and left_type==right_type and not is_tuple(left_type) and (left_type=="str" or left_type.endswith("[]")):
                item.update(kind="sequence_binary",name="concat",type=left_type,owned=True);return left_type,None
            if kind=="add" and is_tuple(left_type) and is_tuple(right_type):
                t="tuple["+",".join(tuple_types(left_type)+tuple_types(right_type))+"]"
                item.update(kind="sequence_binary",name="concat",type=t,owned=True);return t,None
            if kind=="multiply":
                def sequence(t):return t=="str" or t.endswith("[]") or is_tuple(t)
                if sequence(left_type) and right_type in {"i16","u8"} or sequence(right_type) and left_type in {"i16","u8"}:
                    t=left_type if sequence(left_type) else right_type
                    if is_tuple(t):
                        count=right if sequence(left_type) else left
                        if count is None or dynamic_tuple(t):t="tuple_cycle["+",".join(tuple_types(t))+"]"
                        else:
                            if len(tuple_types(t))*max(0,count)>4096:raise CompileError("Tuple repetition exceeds the container limit",item)
                            t="tuple["+",".join(tuple_types(t)*max(0,count))+"]"
                    item.update(kind="sequence_binary",name="repeat" if sequence(left_type) else "repeat_reverse",type=t,owned=True);return t,None
            if kind=="compare" and (managed(left_type) or managed(right_type)):
                if left_type!=right_type and item["operator"] not in {"==","!="}:raise CompileError("Ordering requires matching collection types",item)
                if is_dict(left_type) and item["operator"] not in {"==","!="}:raise CompileError("Dictionaries only support equality comparisons",item)
                item.update(kind="collection_compare",type="bool",same_type=left_type==right_type)
                return "bool",None
            if is_dict(left_type) or is_dict(right_type): raise CompileError("Dictionary arithmetic/comparison is not supported", item)
            if "fixed" in {left_type, right_type}:
                if kind not in {"add", "subtract", "multiply", "compare"} or any(t not in {"fixed", "i16", "u8"} for t in (left_type, right_type)):
                    raise CompileError("Unsupported fixed-point operation", item)
                if kind == "multiply" and left_type == right_type == "fixed":
                    raise CompileError("Multiplication of two fixed-point values is not supported yet", item)
                if kind != "multiply":
                    for field, t in (("left", left_type), ("right", right_type)):
                        if t != "fixed": item[field] = {"kind": "to_fixed", "value": item[field], "type": "fixed"}
                item["type"] = "bool" if kind == "compare" else "fixed"
                return item["type"], None
            if left_type.endswith("[]") or right_type.endswith("[]"):
                raise CompileError("Use indexing or array methods instead of scalar operators on arrays", item)
            if "str" in {left_type, right_type}:
                raise CompileError("String arithmetic and comparison are not supported yet", item)
            if kind in {"and", "or"}:
                if left_type != "bool" or right_type != "bool":
                    raise CompileError("and/or require bool operands", item)
                value_type = "bool"
            elif kind == "compare":
                if (left_type == "bool") != (right_type == "bool"):
                    raise CompileError("Cannot compare bool with an integer", item)
                op = item["operator"]
                if left_type == "bool" and op not in {"==", "!="}:
                    raise CompileError("Bool values only support == and != comparisons", item)
                value_type = "bool"
                if left is not None and right is not None:
                    constant = {"==": left == right, "!=": left != right, "<": left < right,
                                ">": left > right, "<=": left <= right, ">=": left >= right}[op]
            else:
                if "bool" in {left_type, right_type}:
                    raise CompileError("Addition, subtraction and remainder require integers", item)
                value_type = "i16"
                if kind in {"modulo", "floor_divide"} and right == 0 and not checked["tracing_heap"]:
                    raise CompileError("Remainder divisor cannot be zero" if kind == "modulo" else "Division divisor cannot be zero", item)
                if left is not None and right is not None and not (kind in {"modulo","floor_divide"} and right==0):
                    constant = {"modulo": lambda: left % right, "floor_divide": lambda: left // right,
                                "multiply": lambda: left * right, "add": lambda: left + right,
                                "subtract": lambda: left - right}[kind]()
        if value_type == "void" and not allow_void:
            raise CompileError("A void function does not produce a value", item)
        item["type"] = value_type
        if value_type == "i16" and constant is not None and kind != "number" and not -32768 <= constant <= 32767:
            raise CompileError(f"Constant result {constant} is outside i16 range", item)
        return value_type, constant

    def statements(body, loop_depth=0, top_level=False):
        reachable = True
        for statement in body:
            kind = statement["kind"]
            if kind=="attribute_assign":
                target=statement["target"];t,_=expression(target["base"])
                if not is_object(t):raise CompileError("Attribute assignment requires an object",statement)
                fields=classes[t[7:-1]]["fields"];name=target["name"]
                if name not in fields:raise CompileError(f"Cannot infer field type for {name!r}",statement)
                expected=fields[name];actual,c=expression(statement["value"],expected_element=expected)
                compatible(expected,actual,c,statement)
                target.update(type=expected,index={"kind":"number","value":list(fields).index(name),"type":"i16"})
                if statement["operator"]!="=" and expected not in {"i16","u8"}:raise CompileError("Compound field assignment requires integers",statement)
                statement.update(kind="object_assign",type=expected);continue
            if kind=="yield":
                function=context["function"]
                if not function or not function.get("generator"):raise CompileError("yield requires a function",statement)
                t,c=expression(statement["value"],expected_element=function["yield_type"])
                compatible(function["yield_type"],t,c,statement);continue
            if kind in {"try_except","try_finally"}:
                before=context["initialized"].copy();old=context.get("handler_depth",0);context["handler_depth"]=old+1
                falls=statements(statement["body"],loop_depth)
                context["handler_depth"]=old
                paths=[]
                if kind=="try_except":
                    if falls and statements(statement["alternative"],loop_depth):paths.append(context["initialized"].copy())
                    from .exception_runtime import EXCEPTIONS
                    for handler in statement["handlers"]:
                        context["initialized"]=before.copy()
                        for t in handler["types"]:
                            if t not in EXCEPTIONS and t not in {"Exception","BaseException"}:raise CompileError(f"Unknown exception type {t!r}",handler)
                        handler["code_slot"]=scratch("i16",100+old)
                        if handler["name"]:
                            name=handler["name"];slot=context["locals"].get(name)
                            if slot is None:
                                slot={"type":"exception","size":2,"label":f"__var_{len(globals_)}"};globals_[name]=slot
                            slot["type"]="exception";context["locals"][name]=slot;context["initialized"].add(name)
                            handler["target"]={**slot,"symbol":slot["label"]}
                        previous=context.get("active_exception");context["active_exception"]=handler["code_slot"]
                        if statements(handler["body"],loop_depth):paths.append(context["initialized"].copy())
                        context["active_exception"]=previous
                    context["initialized"]=set.intersection(*paths) if paths else before
                    reachable=bool(paths)
                else:
                    statement["code_slot"]=scratch("i16",110+old)
                    context["initialized"]=before
                    final_falls=statements(statement["final"],loop_depth)
                    reachable=falls and final_falls
                continue
            if kind in {"raise","assert"}:
                value=statement.get("value")
                if kind=="assert":
                    t,_=expression(value)
                    if t!="bool":raise CompileError("assert requires a bool condition",statement)
                    statement["exception_code"]=15
                elif value is None:
                    if not context.get("active_exception"):raise CompileError("Bare raise requires an active exception handler",statement)
                    statement["code_slot"]=context["active_exception"]
                else:
                    from .exception_runtime import EXCEPTIONS
                    if value["kind"] in {"call","name"} and value.get("name") in EXCEPTIONS:
                        statement["exception_code"]=EXCEPTIONS[value["name"]][0]
                        args=value.get("arguments",[])
                        if len(args)>1:raise CompileError("An exception accepts one message",statement)
                        statement["message"]=args[0] if args else None
                    else:
                        t,_=expression(value)
                        if t!="exception":raise CompileError("raise requires an exception",statement)
                if statement.get("message") and expression(statement["message"])[0]!="str":raise CompileError("Exception messages must be strings",statement)
                if kind=="raise":reachable=False
                continue
            if kind == "function":
                if not top_level:
                    raise CompileError("Function definitions must be at the top level", statement)
                continue
            if not reachable:
                statement["unreachable"] = True
                continue
            if kind == "pass":
                continue
            if kind == "expression_statement":
                expression(statement["value"], allow_void=True)
                continue
            if kind == "collect":
                expected = statement.get("expected_element")
                element, constant = expression(statement["value"],expected_element=expected)
                if element == "void":
                    raise CompileError("Comprehensions require scalar elements", statement)
                if expected:
                    leaf=element
                    while leaf.endswith("[]"):leaf=leaf[:-2]
                    if leaf!=expected:compatible(expected, element, constant, statement)
                statement["type"] = element
                if statement.get("collection")=="dict":
                    key,_=expression(statement["key"])
                    if key not in {"i16","u8","str"}:raise CompileError("Dictionary keys must be integers or strings",statement)
                    statement["key_type"]="i16" if key=="u8" else key
                if statement.get("collection")=="set" and element not in {"i16","u8","str"}:raise CompileError("Set elements must be integers or strings",statement)
                continue
            if kind=="unpack_assign":
                source_type,_=expression(statement["value"])
                names=statement["names"]
                if is_tuple(source_type):
                    fields=tuple_types(source_type)
                    if dynamic_tuple(source_type):fields=[fields[i%len(fields)] for i in range(len(names))]
                    elif len(fields)!=len(names):raise CompileError("Wrong number of values to unpack",statement)
                elif source_type.endswith("[]"):fields=[source_type[:-2]]*len(names)
                else:raise CompileError("Unpacking requires a list or tuple",statement)
                targets=[]
                for name,t in zip(names,fields):
                    symbol=context["locals"].get(name)
                    if symbol is None:
                        symbol={"type":t,"size":2,"label":f"__var_{len(globals_)}"};globals_[name]=symbol
                    elif symbol["type"]=="auto":symbol["type"]=t
                    else:compatible(symbol["type"],t,None,statement)
                    context["locals"][name]=symbol;context["initialized"].add(name)
                    targets.append({**symbol,"symbol":symbol["label"]})
                statement["targets"]=targets;continue
            if kind == "index_assign":
                target_type, _ = expression(statement["target"])
                base_type=statement["target"]["base"]["type"]
                if base_type=="str" or is_tuple(base_type):raise CompileError("Strings and tuples are immutable",statement)
                source_type, value = expression(statement["value"], expected_element=target_type)
                if statement["operator"] != "=":
                    if target_type not in {"i16", "u8"} or source_type not in {"i16", "u8"}:
                        raise CompileError("Array arithmetic requires integers", statement)
                    source_type, value = "i16", None
                compatible(target_type, source_type, value, statement)
                statement["type"] = target_type
                if statement["target"]["kind"] == "dict_index": statement["kind"] = "dict_assign"
                continue
            if kind == "dict_delete":
                expression(statement["target"])
                if statement["target"]["kind"] != "dict_index": raise CompileError("del currently requires a dictionary key", statement)
                continue
            if kind == "return":
                function = context["function"]
                if function is None:
                    raise CompileError("return must be inside a function", statement)
                target = function["return_type"]
                if target == "void":
                    if statement["value"] is not None:
                        raise CompileError("A void function cannot return a value", statement)
                else:
                    if statement["value"] is None:
                        raise CompileError("This function must return a value", statement)
                    value_type, value = expression(statement["value"], expected_element=target[:-2] if target.endswith("[]") else target)
                    compatible(target, value_type, value, statement)
                reachable = False
                if checked["tracing_heap"] and target!="void":statement["completion_slot"]=scratch(target,120)
                continue
            if kind in {"break", "continue"}:
                if loop_depth == 0:
                    raise CompileError(f"{kind} must be inside a while loop", statement)
                reachable = False
                continue
            if kind == "for":
                iterable = statement["iterable"]
                names = statement.get("names", [statement["name"]])
                def primitive_field(value):
                    if value["kind"] in {"number","boolean","decimal"}:return True
                    if value["kind"]=="name":
                        symbol=context["locals"].get(value["name"],globals_.get(value["name"],{}))
                        return not managed(symbol.get("type","i16"))
                    if value["kind"]=="negate":return primitive_field(value["value"])
                    if value["kind"] in {"add","subtract","multiply","floor_divide","modulo"}:return primitive_field(value["left"]) and primitive_field(value["right"])
                    return False
                literal_rows=iterable.get("values",[])
                fast_rows=all(row["kind"]=="tuple" and all(primitive_field(v) for v in row["values"]) for row in literal_rows)
                if iterable["kind"] == "list" and len(names)>1 and (any(v["kind"] == "tuple" for v in literal_rows) and fast_rows or not literal_rows):
                    # Scalar rows have a known shape; no Python tuple objects or
                    # heap descriptors are needed for this iteration.
                    rows, constants, elements = iterable["values"], [], [None] * len(names)
                    for row in rows:
                        if row["kind"] != "tuple" or len(row["values"]) != len(names):
                            raise CompileError(f"Every tuple row must have {len(names)} values to unpack", row)
                        values = []
                        for index, value in enumerate(row["values"]):
                            value_type, constant = expression(value)
                            if value_type not in {"i16", "u8", "bool", "fixed"}:
                                raise CompileError("Tuple loop components must be scalar values", value)
                            if elements[index] is None:
                                elements[index] = value_type if value_type in {"bool", "fixed"} else "i16"
                            compatible(elements[index], value_type, constant, value)
                            values.append(constant)
                        row["type"] = "tuple"
                        constants.append(values)
                    elements = [t or "i16" for t in elements]
                    before = context["initialized"].copy()
                    targets = []
                    for name, element in zip(names, elements):
                        if name in functions or name in GRAPHICS_BUILTINS or name == "len":
                            raise CompileError("Loop variable conflicts with a function name", statement)
                        target = context["locals"].get(name)
                        if target is None:
                            target = {"type": element, "size": 1 if element == "bool" else 2, "label": f"__var_{len(globals_)}"}
                            globals_[name] = target
                            if name.startswith("$comp_"): checked["temporaries"][target["label"]] = target
                        if target.pop("loop_inferred", False): target["type"] = element
                        compatible(target["type"], element, None, statement)
                        targets.append({**target, "symbol": target["label"]})
                        context["initialized"].add(name)
                    for field in ("iterator_0", "iteration_index"):
                        if context["function"]:
                            symbol = statement[field]
                            symbol["type"] = "i16"
                        else:
                            symbol = {"type": "i16", "size": 2, "label": f"__tmp_{len(checked['temporaries'])}"}
                            checked["temporaries"][symbol["label"]] = symbol
                        statement[field] = {**symbol, "symbol": symbol["label"]}
                    if all(value is not None for row in constants for value in row):
                        statement["constant_rows"] = constants
                    iterable.update(kind="tuple_sequence", type="tuple_sequence")
                    statement.update(iteration_kind="tuple", targets=targets, target=targets[-1], sources=[iterable])
                    statements(statement["body"], loop_depth + 1)
                    context["initialized"] = before
                    continue
                if iterable["kind"] == "call" and iterable.get("name") == "range" and "range" not in functions:
                    expression(iterable)
                    names = statement.get("names", [statement["name"]])
                    if len(names) != 1:
                        raise CompileError("range requires one target name", statement)
                    name = names[0]
                    if name in functions or name in GRAPHICS_BUILTINS or name == "len":
                        raise CompileError("Loop variable conflicts with a function name", statement)
                    before = context["initialized"].copy()
                    fields = {}
                    for field in ("range_current", "range_stop", "range_step"):
                        if context["function"]:
                            symbol = statement[field]
                        else:
                            symbol = {"type": "i16", "size": 2, "label": f"__tmp_{len(checked['temporaries'])}"}
                            checked["temporaries"][symbol["label"]] = symbol
                        fields[field] = {**symbol, "symbol": symbol["label"]}
                    target = context["locals"].get(name)
                    if target is None:
                        target = {"type": "i16", "size": 2, "label": f"__var_{len(globals_)}"}
                        globals_[name] = target
                        if name.startswith("$comp_"):
                            checked["temporaries"][target["label"]] = target
                    if target.pop("loop_inferred", False):
                        target["type"] = "i16"
                    compatible(target["type"], "i16", None, statement)
                    statement.update(fields)
                    statement["iteration_kind"] = "range"
                    statement["target"] = {**target, "symbol": target["label"]}
                    context["initialized"].add(name)
                    statements(statement["body"], loop_depth + 1)
                    context["initialized"] = before
                    continue
                mode = iterable.get("name") if iterable["kind"] == "call" and iterable.get("name") in {"enumerate", "zip"} and iterable.get("name") not in functions else "array"
                view = iterable["name"] if iterable["kind"] == "method" and iterable["name"] in {"keys", "values", "items"} else None
                if view and iterable["arguments"]: raise CompileError("Dictionary views take no arguments", iterable)
                sources = [iterable["base"]] if view else iterable["arguments"] if mode == "zip" else iterable["arguments"][:1] if mode == "enumerate" else [iterable]
                if mode == "zip" and not sources or mode == "enumerate" and len(iterable["arguments"]) not in {1, 2}:
                    raise CompileError(f"Invalid arguments to {mode}", iterable)
                types = []
                iterator_source=None
                for source in sources:
                    iterable_type, _ = expression(source)
                    if is_iterator(iterable_type) and mode=="array":iterator_source=(source,iterable_type);break
                    if is_view(iterable_type) and mode=="array":
                        view,mapping_type=view_types(iterable_type)
                        sources[0]={"kind":"view_target","base":source,"type":mapping_type,"owned":True}
                        iterable_type=mapping_type
                    if not iterable_type.endswith("[]") and not is_tuple(iterable_type) and not ((is_dict(iterable_type) or is_set(iterable_type)) and mode == "array"):
                        raise CompileError("A for loop requires an array or range", source)
                    types.append(iterable_type)
                if iterator_source:
                    source,t=iterator_source;value=iterable_element(t);fields=tuple_types(value) if len(names)>1 and is_tuple(value) else [value]
                    if len(fields)!=len(names):raise CompileError("Wrong number of iterator targets",statement)
                    before=context["initialized"].copy();targets=[]
                    for name,field in zip(names,fields):
                        slot=context["locals"].get(name)
                        if slot is None:
                            slot={"type":field,"size":2,"label":f"__var_{len(globals_)}"};globals_[name]=slot
                            if name.startswith("$comp_"):checked["temporaries"][slot["label"]]=slot
                        if slot.pop("loop_inferred",False):slot["type"]=field
                        compatible(slot["type"],field,None,statement);context["initialized"].add(name)
                        targets.append({**slot,"symbol":slot["label"]})
                    slot=statement.get("iterator_0") if context["function"] else None
                    if slot is None:
                        slot={"type":t,"size":2,"label":f"__tmp_{len(checked['temporaries'])}"};checked["temporaries"][slot["label"]]=slot
                    slot["type"]=t
                    statement.update(iteration_kind="iterator",sources=[source],targets=targets,target=targets[-1],iterator_0={**slot,"symbol":slot["label"]})
                    statements(statement["body"],loop_depth+1);context["initialized"]=before;continue
                if len(types) == 1 and (is_dict(types[0]) or is_set(types[0])):
                    key_type, value_type = (element_type(types[0]),"i16") if is_set(types[0]) else dict_types(types[0])
                    if is_set(types[0]) and view:raise CompileError("Sets have no dictionary views",statement)
                    elements = [key_type, value_type] if view == "items" else [value_type] if view == "values" else [key_type]
                    names = statement.get("names", [statement["name"]])
                    if len(names) != len(elements): raise CompileError(f"This dictionary iteration requires {len(elements)} target name(s)", statement)
                    before = context["initialized"].copy()
                    targets = []
                    for name, t in zip(names, elements):
                        if name in functions or name in GRAPHICS_BUILTINS or name == "len": raise CompileError("Loop variable conflicts with a function name", statement)
                        symbol = context["locals"].get(name)
                        if symbol is None:
                            symbol = {"type": t, "size": 2, "label": f"__var_{len(globals_)}"}
                            globals_[name] = symbol
                            if name.startswith("$comp_"): checked["temporaries"][symbol["label"]] = symbol
                        if symbol.pop("loop_inferred", False): symbol["type"] = t
                        compatible(symbol["type"], t, None, statement)
                        targets.append({**symbol, "symbol": symbol["label"]})
                        context["initialized"].add(name)
                    for field, t in (("iterator_0", types[0]), ("iteration_index", "i16"), ("dict_version", "i16")):
                        if context["function"]:
                            if field not in statement:
                                function = context["function"]
                                function["frame_size"] += 2
                                if function["frame_size"] > 4096: raise CompileError("Function frame exceeds 4096 bytes", statement)
                                symbol = {"type": t, "size": 2, "storage": "frame", "offset": -function["frame_size"], "label": f"__local_{len(context['locals'])}"}
                                context["locals"][f"${field}{len(context['locals'])}"] = symbol
                            else:
                                symbol = statement[field]; symbol["type"] = t
                        else:
                            symbol = {"type": t, "size": 2, "label": f"__tmp_{len(checked['temporaries'])}"}
                            checked["temporaries"][symbol["label"]] = symbol
                        statement[field] = {**symbol, "symbol": symbol["label"]}
                    statement.update(iteration_kind="dict", dict_view=view or "keys", sources=sources, targets=targets, target=targets[-1])
                    if view and iterable["kind"]=="method": iterable["kind"] = "dict_view"
                    statements(statement["body"], loop_depth + 1)
                    context["initialized"] = before
                    continue
                if view: raise CompileError("Dictionary views require a dictionary", iterable)
                elements=[]
                for t in types:
                    if is_tuple(t):
                        fields=tuple_types(t)
                        if fields and len(set(fields))>1:raise CompileError("Tuple iteration requires matching field types",statement)
                        elements.append(fields[0] if fields else "i16")
                    else:elements.append(t[:-2])
                if mode == "enumerate":
                    start = iterable["arguments"][1] if len(iterable["arguments"]) == 2 else {"kind": "number", "value": 0, "line": statement["line"], "column": statement["column"]}
                    if expression(start)[0] not in {"i16", "u8"}:
                        raise CompileError("enumerate start must be an integer", start)
                    statement["enumeration_start"] = start
                    elements.insert(0, "i16")
                names = statement.get("names", [statement["name"]])
                unpack_rows=mode=="array" and len(elements)==1 and is_tuple(elements[0]) and len(names)>1
                if unpack_rows:elements=tuple_types(elements[0])
                if len(names) != len(elements):
                    raise CompileError(f"This iteration requires {len(elements)} target name(s)", statement)
                before = context["initialized"].copy()
                def slot(field, type_name):
                    if context["function"]:
                        symbol = statement[field]
                        symbol["type"] = type_name
                    else:
                        symbol = {"type": type_name, "size": 2, "label": f"__tmp_{len(checked['temporaries'])}"}
                        checked["temporaries"][symbol["label"]] = symbol
                    return {**symbol, "symbol": symbol["label"]}
                statement["iterators"] = [slot(f"iterator_{i}", t) for i, t in enumerate(types)]
                statement["iteration_index"] = slot("iteration_index", "i16")
                if mode == "enumerate":
                    statement["enumeration"] = slot("enumeration", "i16")
                statement["sources"], statement["iteration_kind"] = sources, "unpack_array" if unpack_rows else mode
                targets = []
                for name, element in zip(names, elements):
                    if name in functions or name == "len" or name in GRAPHICS_BUILTINS:
                        raise CompileError("Loop variable conflicts with a function name", statement)
                    target = context["locals"].get(name)
                    if target is None:
                        target = {"type": element, "size": 1 if element in {"u8", "bool"} else 2,
                                  "label": f"__var_{len(globals_)}"}
                        globals_[name] = target
                        if name.startswith("$comp_"):
                            checked["temporaries"][target["label"]] = target
                    if target.pop("loop_inferred", False):
                        target["type"] = element
                    compatible(target["type"], element, None, statement)
                    targets.append({**target, "symbol": target["label"]})
                    context["initialized"].add(name)
                statement["targets"] = targets
                statement["iterator"], statement["target"] = statement["iterators"][0], targets[-1]
                statements(statement["body"], loop_depth + 1)
                context["initialized"] = before
                continue
            if kind in {"if", "while"}:
                condition_type, _ = expression(statement["condition"])
                if condition_type != "bool":
                    raise CompileError(f"{'An if' if kind == 'if' else 'A while'} condition must be bool", statement["condition"])
                before = context["initialized"].copy()
                old=context.get("refinements",set());context["refinements"]=refined(statement["condition"])
                then_falls = statements(statement["body"], loop_depth + (kind == "while"))
                then_initialized = context["initialized"].copy()
                context["initialized"] = before.copy()
                context["refinements"]=old
                if kind == "while":
                    continue
                context["refinements"]=refined(statement["condition"],False)
                else_falls = statements(statement["alternative"], loop_depth)
                context["refinements"]=old
                if then_falls and else_falls:
                    context["initialized"] &= then_initialized
                elif then_falls:
                    context["initialized"] = then_initialized
                reachable = then_falls or else_falls
                continue
            name = statement["name"]
            if kind == "assign" and statement.get("inferred"):
                if name in functions or name == "len" or name in GRAPHICS_BUILTINS:
                    raise CompileError(f"{name!r} is already a function name", statement)
                symbol = lookup(name,statement,writing=True) if context["function"] and name in context["function"].get("captures",[]) else context["locals"].get(name)
                expected = symbol["type"] if symbol and symbol["type"] != "auto" else statement.get("inferred_type")
                if expected and expected.endswith("[]"): expected = expected[:-2]
                source_type, constant = expression(statement["value"], expected_element=expected)
                if symbol is None:
                    symbol = {"type": source_type, "size": 1 if source_type in {"u8", "bool"} else 2,
                              "label": f"__var_{len(globals_)}"}
                    context["locals"][name] = symbol
                    statement["kind"] = "declare"
                elif symbol["type"] == "auto":
                    symbol["type"] = source_type
                    statement["kind"] = "declare"
                else:
                    compatible(symbol["type"], source_type, constant, statement)
                context["initialized"].add(name)
                statement["type"] = symbol["type"]
                bind(statement, symbol)
                continue
            if kind == "declare":
                target = normalize(statement["type"])
                if target == "void":
                    raise CompileError("A variable cannot be void", statement)
                if name in functions or name == "len" or name in GRAPHICS_BUILTINS:
                    raise CompileError(f"{name!r} is already a function name", statement)
                if context["function"]:
                    symbol = context["locals"][name]
                else:
                    if name in globals_:
                        raise CompileError(f"Variable {name!r} is already declared", statement)
                    symbol = {"type": target, "label": f"__var_{len(globals_)}",
                              "size": 1 if target in {"u8", "bool"} else 2}
                source_type, constant = expression(statement["value"], expected_element=target)
                if source_type.endswith("[]"):
                    leaf=source_type
                    while leaf.endswith("[]"):leaf=leaf[:-2]
                    if target==leaf:target=source_type
                    symbol["type"] = target
                    symbol["size"] = 2
                compatible(target, source_type, constant, statement)
                if not context["function"]:
                    # Comprehensions may allocate private globals while the
                    # initializer is checked. Assign this label afterwards.
                    symbol["label"] = f"__var_{len(globals_)}"
                context["locals"][name] = symbol
                context["initialized"].add(name)
            else:
                symbol = lookup(name, statement, writing=True)
                source_type, constant = expression(statement["value"], expected_element=symbol["type"][:-2] if symbol["type"].endswith("[]") else symbol["type"])
                compatible(symbol["type"], source_type, constant, statement)
            statement["type"] = symbol["type"]
            bind(statement, symbol)
        return reachable

    statements(checked["body"], top_level=True)

    def allocate_locals(body, local_symbols, size):
        for statement in body:
            def comprehension_slots(item):
                nonlocal size
                if isinstance(item, dict):
                    if item.get("kind") == "comprehension":
                        size = allocate_locals([item["loop"]], local_symbols, size)
                        return
                    for key, value in item.items():
                        if key not in {"body", "alternative", "loop"}:
                            comprehension_slots(value)
                elif isinstance(item, list):
                    for value in item:
                        comprehension_slots(value)
            comprehension_slots(statement)
            kind = statement["kind"]
            if kind=="unpack_assign":
                for name in statement["names"]:
                    if name not in local_symbols:
                        size+=2
                        local_symbols[name]={"type":"auto","size":2,"storage":"frame","offset":-size,"label":f"__local_{len(local_symbols)}"}
                if size>4096:raise CompileError("Function frame exceeds 4096 bytes",statement)
                continue
            if kind == "function":
                raise CompileError("Nested functions are not supported", statement)
            if kind == "declare" or kind == "assign" and statement.get("inferred"):
                name = statement["name"]
                if name in local_symbols:
                    if kind == "assign": continue
                    raise CompileError(f"Variable {name!r} is already declared in this function", statement)
                type_name = normalize(statement.get("type",statement.get("inferred_type", "auto")))
                # Reserve word slots for locals; an initializer can infer an
                # array descriptor even when its declared element type is u8.
                width = 2
                size += width
                if size > 4096:
                    raise CompileError("Function frame exceeds 4096 bytes", statement)
                local_symbols[name] = {"type": type_name, "size": width,
                                       "storage": "frame", "offset": -size,
                                       "label": f"__local_{len(local_symbols)}"}
            elif kind == "for":
                iterable = statement["iterable"]
                mode = iterable.get("name") if iterable["kind"] == "call" and iterable.get("name") not in functions else "array"
                count = len(iterable["arguments"]) if mode == "zip" else 1
                fields = [(f"iterator_{i}", "i16[]") for i in range(count)] + [("iteration_index", "i16")]
                if mode == "range":
                    fields = [(field, "i16") for field in ("range_current", "range_stop", "range_step")]
                if mode == "enumerate":
                    fields.append(("enumeration", "i16"))
                for field, type_name in fields:
                    size += 2
                    slot = {"type": type_name, "size": 2, "storage": "frame", "offset": -size,
                            "label": f"__local_{len(local_symbols)}"}
                    local_symbols[f"${field}{len(local_symbols)}"] = slot
                    statement[field] = slot
                for name in statement.get("names", [statement["name"]]):
                    if name not in local_symbols:
                        size += 2
                        local_symbols[name] = {"type": "i16", "size": 2, "storage": "frame", "offset": -size,
                                               "label": f"__local_{len(local_symbols)}", "loop_inferred": True}
                if size > 4096:
                    raise CompileError("Function frame exceeds 4096 bytes", statement)
                size = allocate_locals(statement["body"], local_symbols, size)
            elif kind in {"try_except","try_finally"}:
                size=allocate_locals(statement["body"],local_symbols,size)
                for handler in statement.get("handlers",[]):
                    if handler.get("name") and handler["name"] not in local_symbols:
                        size+=2;local_symbols[handler["name"]]={"type":"exception","size":2,"storage":"frame","offset":-size,"label":f"__local_{len(local_symbols)}"}
                    size=allocate_locals(handler["body"],local_symbols,size)
                size=allocate_locals(statement.get("alternative",statement.get("final",[])),local_symbols,size)
            elif kind in {"if", "while"}:
                size = allocate_locals(statement["body"], local_symbols, size)
                if kind == "if":
                    size = allocate_locals(statement["alternative"], local_symbols, size)
        return size

    for function in functions.values():
        local_symbols = {}
        count = len(function["params"])
        for index, parameter in enumerate(function["params"]):
            if parameter["name"] in functions:
                raise CompileError("Parameter name conflicts with a function", parameter)
            local_symbols[parameter["name"]] = {
                "type": parameter["type"], "size": 2, "storage": "frame",
                "offset": 4 + 2 * (count - index - 1), "label": f"__arg_{index}"}
        function["frame_size"] = allocate_locals(function["body"], local_symbols, 0)
        for name in function.get("cell_names",[]):
            if name in local_symbols:local_symbols[name]["cell"]=True
        function["locals"] = local_symbols
        context = {"locals": local_symbols, "initialized": {p["name"] for p in function["params"]},
                   "function": function}
        falls_through = statements(function["body"])
        if function["return_type"] != "void" and falls_through:
            raise CompileError(f"Function {function['name']!r} must return a value on every path", function)

    # Transitive reads ensure that a call cannot observe a global before its
    # initializer has run, including through mutually recursive functions.
    changed = True
    while changed:
        changed = False
        for function in functions.values():
            before = len(function["requires_globals"])
            for callee in function["calls"]:
                function["requires_globals"].update(functions[callee]["requires_globals"])
            changed |= before != len(function["requires_globals"])
    for call, available in global_calls:
        missing = functions[call["name"]]["requires_globals"] - available
        if missing:
            raise CompileError(f"Call may read uninitialized global {sorted(missing)[0]!r}", call)
    for function in functions.values():
        function["requires_globals"] = sorted(function["requires_globals"])
        function["calls"] = sorted(function["calls"])
    return checked, {name: value for name, value in globals_.items() if not name.startswith("$comp_")}
