/* SPDX-License-Identifier: GPL-3.0-or-later
 * Original, freestanding AArch64 fixture for the FagriShot CPU check.
 * All addresses and contents belong to this test image.
 */
.text
.global test_main
.type test_main,%function
test_main:
    mov x28, x30
    mov x0, #-1
    adds x0, x0, #1
    adc x1, xzr, xzr
    mov x2, #0x13
    bl mix
    adr x3, mix
    blr x3
    adrp x4, results
    add x4, x4, :lo12:results
    str x2, [x4]
    mrs x6, tpidr_el0
    str x1, [x6, #16]
    ldr x7, [x6, #16]
    movi v0.16b, #0x7f
    movi v1.16b, #2
    add v2.16b, v0.16b, v1.16b
    str q2, [x4, #16]
    fmov d3, #1.5
    fadd d4, d3, d3
    str d4, [x4, #8]
    subs x8, x7, #1
    b.ne unexpected
    mov x9, #0x44
    mov x30, x28
    ret
unexpected:
    brk #1
.size test_main, .-test_main

.type mix,%function
mix:
    add x2, x2, x1, lsl #3
    eor x2, x2, #0xff
    ret
.size mix, .-mix

.data
.balign 16
.type results,%object
results:
    .zero 32
.size results, .-results
