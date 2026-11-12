// SPDX-License-Identifier: GPL-3.0-or-later
/*
 * memcat: copy or clear physical memory through an uncached /dev/mem mapping.
 *
 *   memcat ADDR SIZE       write SIZE bytes from ADDR to stdout
 *   memcat -z ADDR SIZE    fill SIZE bytes at ADDR with zeros
 *
 * O_SYNC makes the mapping uncached, so reads see what a DMA master wrote and
 * writes reach DRAM before the DMA master starts. ADDR must be page aligned.
 */
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

int main(int argc, char **argv)
{
	int zero = argc > 1 && !strcmp(argv[1], "-z");
	unsigned long addr, size, done;
	volatile unsigned int *p;
	unsigned int buf[1024];
	int fd;

	if (argc != 3 + zero) {
		fprintf(stderr, "usage: memcat [-z] ADDR SIZE\n");
		return 2;
	}
	addr = strtoul(argv[1 + zero], NULL, 0);
	size = strtoul(argv[2 + zero], NULL, 0);
	if (addr & 4095 || size & 3) {
		fprintf(stderr, "memcat: ADDR must be page aligned, SIZE a multiple of 4\n");
		return 2;
	}

	fd = open("/dev/mem", O_RDWR | O_SYNC);
	if (fd < 0) {
		perror("/dev/mem");
		return 1;
	}
	p = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, addr);
	if (p == MAP_FAILED) {
		perror("mmap");
		return 1;
	}

	for (done = 0; done < size; done += sizeof(buf)) {
		unsigned long n = size - done < sizeof(buf) ? size - done : sizeof(buf);
		unsigned long i;

		for (i = 0; i < n / 4; i++) {
			if (zero)
				p[done / 4 + i] = 0;
			else
				buf[i] = p[done / 4 + i];
		}
		if (!zero && fwrite(buf, 1, n, stdout) != n) {
			perror("write");
			return 1;
		}
	}
	return 0;
}
