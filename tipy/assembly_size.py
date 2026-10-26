"""Conservative byte sizing of compiler-emitted Z80 for RAM reservation.

The browser assembler remains authoritative. Unknown instructions reserve the
maximum ordinary instruction length, so they can only reduce the chosen heap.
"""
import re


def estimate(assembly):
    total=0
    registers={"a","b","c","d","e","h","l","(hl)","ixh","ixl","iyh","iyl"}
    pairs={"bc","de","hl","sp","ix","iy"}
    for source in assembly.splitlines():
        line=source.split(";",1)[0].strip().lower()
        if ":" in line:line=line.split(":",1)[1].strip()
        if not line:continue
        op,*rest=line.split(None,1);args=rest[0].replace(" ","") if rest else ""
        operands=args.split(",") if args else []
        indexed=bool(re.search(r"\(i[xy][+-]",args))
        if op in {"org","equ"}:continue
        if op in {"db","dw"}:total+=len(operands)*(2 if op=="dw" else 1);continue
        if op in {"defs","ds"}:total+=int(operands[0],0);continue
        if op=="ld" and len(operands)==2:
            a,b=operands
            if indexed:size=3 if b in registers or a in registers else 4
            elif a in {"ix","iy"} or b in {"ix","iy"}:size=2 if a=="sp" else 4
            elif a in pairs:
                size=1 if a=="sp" and b=="hl" else (3 if a=="hl" else 4) if b.startswith("(") else 3
            elif b in pairs:size=3 if b=="hl" else 4
            elif a in {"i","r"} or b in {"i","r"}:size=2
            elif a.startswith("(") and a not in {"(hl)","(bc)","(de)"} or b.startswith("(") and b not in {"(hl)","(bc)","(de)"}:size=3
            elif b in registers or b in {"(bc)","(de)"}:size=1
            else:size=2
        elif op in {"push","pop"}:size=2 if args in {"ix","iy"} else 1
        elif op=="jp":size=1 if args=="(hl)" else 2 if args in {"(ix)","(iy)"} else 3
        elif op in {"call"}:size=3
        elif op in {"jr","djnz"}:size=2
        elif op in {"bit","set","res","rl","rr","rlc","rrc","sla","sra","srl","sll"}:size=4 if indexed else 2
        elif op in {"inc","dec"}:size=3 if indexed else 2 if args in {"ix","iy"} else 1
        elif op in {"add","adc","sbc"} and operands and operands[0] in pairs:size=2 if op!="add" or operands[0] in {"ix","iy"} else 1
        elif op in {"add","adc","sub","sbc","and","or","xor","cp"}:size=3 if indexed else 1 if operands[-1] in registers else 2
        elif op in {"reti","retn","neg","im","ldir","lddr","ldi","ldd","cpir","cpdr","cpi","cpd","inir","indr","otir","otdr"}:size=2
        elif op in {"in","out"}:size=2
        elif op=="ex":size=2 if "ix" in args or "iy" in args else 1
        elif op in {"ret","rst","nop","halt","ei","di","scf","ccf","cpl","daa","rlca","rla","rrca","rra","exx"}:size=1
        else:size=4
        total+=size
    return total
