/* SPDX-License-Identifier: GPL-3.0-or-later
 * A valid optional extension instruction without an emitter in the pinned lifter.
 */
.arch armv8.2-a+sha3
.text
.global test_main
.type test_main,%function
test_main:
    sha512h q0, q1, v2.2d
    ret
.size test_main, .-test_main
