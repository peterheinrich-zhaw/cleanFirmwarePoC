/*
 * Minimal exception vector table + stub handlers. Not a general
 * exception framework -- purpose-built to answer one question when
 * something hangs: was it a genuine CPU-detected fault (in which
 * case we can read exactly what kind and where), or a hardware bus
 * stall that never generates an exception at all (in which case
 * this table won't help, and that absence is itself information).
 *
 * Installed via VBAR in start.S, using the same SCTLR.V-clear +
 * VBAR-write sequence confirmed present in the vendor's own real
 * U-Boot startup code.
 */
    .arm
    .section .text.vectors, "ax"
    .align 5
    .global vector_table
vector_table:
    b       .                   /* reset -- unused, nothing should re-enter here */
    b       undef_stub
    b       swi_stub
    b       prefetch_abort_stub
    b       data_abort_stub
    b       .                   /* reserved */
    b       irq_stub
    b       fiq_stub

    /*
     * report_exception(char vector_id, unsigned int lr,
     *                  unsigned int fault_status, unsigned int fault_addr)
     * -- defined in main.c, standard AAPCS r0-r3 args.
     */

undef_stub:
    mov     r0, #'U'
    mov     r1, lr
    mov     r2, #0
    mov     r3, #0
    bl      report_exception
    b       .

swi_stub:
    mov     r0, #'S'
    mov     r1, lr
    mov     r2, #0
    mov     r3, #0
    bl      report_exception
    b       .

prefetch_abort_stub:
    mov     r0, #'P'
    mov     r1, lr
    mrc     p15, 0, r2, c5, c0, 1   /* IFSR */
    mrc     p15, 0, r3, c6, c0, 2   /* IFAR */
    bl      report_exception
    b       .

data_abort_stub:
    /*
     * Recover instead of halting: skip the single faulting
     * instruction and resume, rather than looping forever. Needed
     * for the SPI base-address sweep (main.c) -- probing several
     * candidate addresses in one run means a fault on candidate N
     * must not prevent trying candidate N+1.
     *
     * Correctness matters here in a way it didn't for a halt-only
     * handler: resuming means the interrupted code's register state
     * must come back EXACTLY as it left it. r0-r3/r12 are caller-
     * saved and will be clobbered by the call to report_exception()
     * below, so they're saved/restored explicitly; r4-r11 are
     * callee-saved by AAPCS, so a well-formed C function already
     * leaves those alone -- no need to save those ourselves. lr is
     * saved alongside r0-r3/r12 specifically so the SAME call doesn't
     * clobber the one value (original fault LR) we still need
     * afterward to compute the resume address.
     */
    stmfd   sp!, {r0-r3, r12, lr}
    mov     r0, #'D'
    mov     r1, lr                  /* original fault LR, still intact here */
    mrc     p15, 0, r2, c5, c0, 0   /* DFSR */
    mrc     p15, 0, r3, c6, c0, 0   /* DFAR */
    bl      report_exception
    ldmfd   sp!, {r0-r3, r12, lr}   /* restore everything, including the ORIGINAL fault LR */
    sub     lr, lr, #4              /* LR_abt = fault instruction address + 8; resume at +4 */
    movs    pc, lr                  /* return, restoring CPSR from SPSR_abt */

irq_stub:
    mov     r0, #'I'
    mov     r1, lr
    mov     r2, #0
    mov     r3, #0
    bl      report_exception
    b       .

fiq_stub:
    mov     r0, #'F'
    mov     r1, lr
    mov     r2, #0
    mov     r3, #0
    bl      report_exception
    b       .

    .end
