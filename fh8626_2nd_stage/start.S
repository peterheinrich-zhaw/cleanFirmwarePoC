    .section .text.start
    .global _start
    .arm

_start:
    /* Checkpoint 0, in raw assembly, before ANYTHING else -- mode
     * switch, stack setup, C runtime, all of it. This answers one
     * isolated question: did the CPU actually reach 0x10008000 and
     * can it talk to the UART at all, independent of every other
     * assumption in this codebase.
     */
    ldr     r0, =0xf0700000
1:  ldr     r1, [r0, #0x7c]
    tst     r1, #0x2
    beq     1b

    /* Force SVC32 mode, IRQ/FIQ disabled. Vendor stub does the same
     * (mrs/bic/orr/msr cpsr_c) before touching anything else. */
    mrs     r0, cpsr
    bic     r0, r0, #0x1f
    orr     r0, r0, #0xd3
    msr     cpsr_c, r0

    /* Stack pointer = our own load address, growing down into the
     * SRAM window the vendor's own DDR_0 table was temporarily
     * loaded into (0x10000800-ish) -- confirmed safe because the
     * vendor stub itself initializes SP to this exact same value
     * (0x10008000) before doing anything else. */
    ldr     sp, =_start

    /*
     * No JTAG available on this board, so software fault reporting
     * is the substitute: set up banked stack pointers for each
     * exception mode, then install our own vector table via VBAR --
     * reusing the EXACT mechanism confirmed present in the vendor's
     * own real U-Boot startup code (clear SCTLR.V, then
     * mcr p15,0,r0,c12,c0,0).
     */
    mrs     r0, cpsr
    bic     r0, r0, #0x1f
    orr     r0, r0, #0xd7          /* ABT32, IRQ/FIQ disabled */
    msr     cpsr_c, r0
    ldr     sp, =abt_stack_top

    mrs     r0, cpsr
    bic     r0, r0, #0x1f
    orr     r0, r0, #0xdb          /* UND32 */
    msr     cpsr_c, r0
    ldr     sp, =und_stack_top

    mrs     r0, cpsr
    bic     r0, r0, #0x1f
    orr     r0, r0, #0xd2          /* IRQ32 */
    msr     cpsr_c, r0
    ldr     sp, =irq_stack_top

    mrs     r0, cpsr
    bic     r0, r0, #0x1f
    orr     r0, r0, #0xd1          /* FIQ32 */
    msr     cpsr_c, r0
    ldr     sp, =fiq_stack_top

    mrs     r0, cpsr                /* back to SVC32 */
    bic     r0, r0, #0x1f
    orr     r0, r0, #0xd3
    msr     cpsr_c, r0

    mrc     p15, 0, r0, c1, c0, 0   /* SCTLR */
    bic     r0, r0, #0x2000         /* clear V bit so VBAR is honored */
    mcr     p15, 0, r0, c1, c0, 0
    ldr     r0, =vector_table
    mcr     p15, 0, r0, c12, c0, 0  /* VBAR = vector_table */

    bl      main

hang:
    b       hang

    .bss
    .align 4
abt_stack_bottom: .space 256
abt_stack_top:
und_stack_bottom: .space 256
und_stack_top:
irq_stack_bottom: .space 256
irq_stack_top:
fiq_stack_bottom: .space 256
fiq_stack_top:

    .end
