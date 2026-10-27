"""Held keys read through the TI-84 Plus active-low keypad port."""
KEYS={'down':1,'left':2,'right':3,'up':4,'enter':9,'+':10,'-':11,
      '*':12,'/':13,'^':14,'clear':15,'escape':15,'negate':17,
      '3':18,'6':19,'9':20,')':21,'tan':22,'vars':23,'.':25,
      '2':26,'5':27,'8':28,'(':29,'cos':30,'prgm':31,'stat':32,
      '0':33,'1':34,'4':35,'7':36,',':37,'sin':38,'apps':39,
      'x':40,'sto':42,'ln':43,'log':44,'square':45,'inverse':46,
      'math':47,'alpha':48,'y=':49,'window':50,'zoom':51,'trace':52,
      'graph':53,'2nd':54,'mode':55,'delete':56}


def assembly():
    return '''__native_key_down:
        ld a,h
        or a
        jp nz,__native_key_false
        ld a,l
        dec a
        cp 56
        jp nc,__native_key_false
        ld c,a
        and 7
        inc a
        ld e,a
        ld a,c
        srl a
        srl a
        srl a
        ld b,a
        ld a,254
        inc b
        jr __native_key_select_first
__native_key_select:
        rlca
__native_key_select_first:
        djnz __native_key_select
        ld c,a
        ld a,i
        push af
        di
        ld a,255
        out (1),a
        ld a,c
        out (1),a
        ex (sp),hl
        ex (sp),hl
        ld b,12
__native_key_settle:
        djnz __native_key_settle
        in a,(1)
        ld c,a
        ld a,255
        out (1),a
        ld a,c
        ld b,e
__native_key_bit:
        rrca
        djnz __native_key_bit
        ld hl,0
        jr c,__native_key_restore
        inc l
__native_key_restore:
        pop af
        jp po,__native_key_return
        ei
__native_key_return:
        ret
__native_key_false:
        ld hl,0
        ret'''
