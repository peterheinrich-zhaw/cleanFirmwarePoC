ENTRY(_start)

/* Confirmed load address from the vendor 2BL section table's "ram"
 * entry (load_addr == exec_addr == 0x10008000). Reserved size in the
 * vendor table is 0x4000 (16KB) -- keep the final .bin under that
 * unless you also patch the section-table entry (see README). */
MEMORY
{
    sram (rwx) : ORIGIN = 0x10008000, LENGTH = 0x4000
}

SECTIONS
{
    . = 0x10008000;

    .text : {
        *(.text.start)
        *(.text.vectors)   /* explicit: vector_table needs its own .align 5,
                             * placed right after start.S's small entry code */
        *(.text*)
    } > sram

    .rodata : {
        *(.rodata*)
    } > sram

    .data : {
        *(.data*)
    } > sram

    .bss (NOLOAD) : {
        *(.bss*)
        *(COMMON)
    } > sram

    /DISCARD/ : { *(.comment) *(.note*) *(.ARM.attributes) }
}
