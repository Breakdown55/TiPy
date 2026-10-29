"""Small allocation-free kernels for the public game helpers."""


def routines():
    return [
        """__gfx_mesh_game_saved_cpu: db 0
__gfx_mesh_game_previous_tick: db 0
__gfx_mesh_game_begin:
        in a,(0x20)
        ld (__gfx_mesh_game_saved_cpu),a
        ld a,1
        out (0x20),a
        call __native_frame_ticks
        ld a,l
        ld (__gfx_mesh_game_previous_tick),a
        ret
__gfx_mesh_game_end:
        ld a,(__gfx_mesh_game_saved_cpu)
        out (0x20),a
        ret
__gfx_mesh_frame_step:
        call __native_frame_ticks
        ld a,(__gfx_mesh_game_previous_tick)
        ld b,a
        ld a,l
        ld (__gfx_mesh_game_previous_tick),a
        sub b
        cp 17
        jr c,__gfx_mesh_frame_step_ready
        ld a,16
__gfx_mesh_frame_step_ready:
        ld l,a
        ld h,0
        ret""",
        """; The compiler clips these constant row ranges to the LCD bounds.
__gfx_mesh_clear_rows:
        push ix
        ld ix,0
        add ix,sp
        ld l,(ix+6)
        ld h,(ix+7)
        add hl,hl
        add hl,hl
        ld d,h
        ld e,l
        add hl,hl
        add hl,de
        ld de,0x9340
        add hl,de
        push hl
        ld l,(ix+4)
        ld h,(ix+5)
        add hl,hl
        add hl,hl
        ld d,h
        ld e,l
        add hl,hl
        add hl,de
        dec hl
        ld b,h
        ld c,l
        pop hl
        ld d,h
        ld e,l
        inc de
        ld (hl),0
        ldir
        pop ix
        ret""",
    ]
