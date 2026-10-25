"""Small, flag-preserving assembly rewrites shared by user code and runtime."""

import re


def optimize(assembly):
    previous = None
    while assembly != previous:
        previous = assembly
        assembly = _pass(assembly)
    return assembly


def _pass(assembly):
    lines = assembly.splitlines()
    result = []
    index = 0
    while index < len(lines):
        instructions = [line.strip() for line in lines[index:index + 5]]
        # POP immediately restores the same pair; neither operation sets flags.
        if len(instructions) >= 2 and instructions[0] in {"push hl", "push de", "push bc"} and instructions[1] == "pop " + instructions[0][5:]:
            index += 2
            continue
        # A simple RHS can load DE directly, leaving the LHS in HL. Unlike
        # evaluating a call or nested expression, these loads cannot clobber HL.
        if instructions[0] == "push hl":
            if len(instructions) >= 4 and instructions[2:4] == ["ex de,hl", "pop hl"]:
                match = re.fullmatch(r"ld hl,([A-Za-z_][A-Za-z_0-9]*|[0-9]+|\([A-Za-z_][A-Za-z_0-9]*\))", instructions[1])
                if match:
                    result.append("        ld de," + match[1])
                    index += 4
                    continue
            if len(instructions) == 5 and instructions[3:] == ["ex de,hl", "pop hl"]:
                low = re.fullmatch(r"ld l,(\(ix[+-][0-9]+\))", instructions[1])
                high = re.fullmatch(r"ld h,(\(ix[+-][0-9]+\)|0)", instructions[2])
                if low and high:
                    result.extend(["        ld e," + low[1], "        ld d," + high[1]])
                    index += 5
                    continue
        # Look through comments and consecutive labels, but never an instruction.
        jump = re.fullmatch(r"jp (__[A-Za-z_0-9]+)", instructions[0])
        if jump:
            following = index + 1
            while following < len(lines):
                line = lines[following].strip()
                if line == jump[1] + ":":
                    break
                if line and not line.startswith(";") and not line.endswith(":"):
                    break
                following += 1
            if following < len(lines) and lines[following].strip() == jump[1] + ":":
                index += 1
                continue
        result.append(lines[index])
        index += 1
    return "\n".join(result) + "\n"
