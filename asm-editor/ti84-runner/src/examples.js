export const EXAMPLES = {
    'Move a pixel': `#include "ti84.inc"
        org userMem
        rst 0x28
        dw _GrBufClr
loop:
        rst 0x28
        dw _GrBufClr
; Locate the pixel: plotSScreen + y*12 + x/8
        ld a,(y)
        ld l,a
        ld h,0
        add hl,hl
        add hl,hl
        ld d,h
        ld e,l
        add hl,hl
        add hl,de
        ld de,plotSScreen
        add hl,de
        ld a,(x)
        srl a
        srl a
        srl a
        ld e,a
        ld d,0
        add hl,de
; Pixels are packed MSB first, 8 per byte
        ld a,(x)
        and 7
        ld b,a
        inc b
        ld a,1
mask:   rrca
        djnz mask
        ld (hl),a
        rst 0x28
        dw _GrBufCpy
; Wait for six ~118 Hz timer interrupts
        ld b,6
wait:   halt
        djnz wait
; Select the arrow-key group on real keypad port 1
        ld a,0xFE
        out (1),a
        in a,(1)
        ld c,a
        bit 0,c
        jr nz,left
        ld a,(y)
        cp 63
        jr z,left
        inc a
        ld (y),a
left:   bit 1,c
        jr nz,right
        ld a,(x)
        or a
        jr z,right
        dec a
        ld (x),a
right:  bit 2,c
        jr nz,up
        ld a,(x)
        cp 95
        jr z,up
        inc a
        ld (x),a
up:     bit 3,c
        jr nz,loop
        ld a,(y)
        or a
        jr z,loop
        dec a
        ld (y),a
        jp loop
x:      db 48
y:      db 32
`,
    'Hello screen': `#include "ti84.inc"
        org userMem
        rst 0x28
        dw _ClrLCDFull
        rst 0x28
        dw _HomeUp
        ld hl,message
        rst 0x28
        dw _PutS
        ret
message: db "HELLO PYTIE!",13,"96 X 64 PIXELS",13,"NO ROM NEEDED",0
`,
    'Direct LCD ports': `#include "ti84.inc"
        org userMem
; This draws directly through ports. Graphics RAM stays blank.
        di
        ld a,1
        out (0x10),a
        ld a,7
        out (0x10),a
        ld d,0
row:    ld a,d
        or 0x80
        out (0x10),a
        ld a,0x20
        out (0x10),a
        ld b,12
        ld a,d
        and 8
        ld a,0xAA
        jr z,draw
        cpl
draw:   out (0x11),a
        djnz draw
        inc d
        ld a,d
        cp 64
        jr nz,row
        ret
`
}
