# FH8626V200 open firmware

Loader + U-Boot + Linux + BusyBox for the Fullhan FH8626V200, built from
**official upstream releases plus our patches**. No upstream source code is
stored in this repository.

## Layout

    Makefile  config/versions.mk   pinned upstream versions, URLs, SHA-256
    patches/linux/*.patch          our kernel changes on top of the pinned release
    patches/u-boot/*.patch         our U-Boot changes on top of the pinned release
    fh8626_2nd_stage/              2nd-stage loader (our own code)
    rootfs/build_rootfs.sh         static BusyBox + initramfs description
    scripts/                       fetch / prepare / export helpers
    dl/  work/  build/             generated, git-ignored
      work/linux, work/u-boot      development trees (local git repos, not pushed)

## Workflow

    make fetch          # download + verify tarballs (first time: pin the SHA-256s)
    make prepare        # unpack into work/, git am patches/ on top
    make                # build everything -> flash_image.bin

    cd work/linux       # edit, build, test, then commit as usual
    git commit -am "..."
    cd ../..
    make export         # work/ commits -> patches/   (then: git add patches/ && git commit)

`work/` is a throw-away product of `patches/`: `make reprepare` rebuilds it, and
refuses to run while `work/` holds commits that are not yet exported. Uncommitted
changes in `work/` are not protected, so commit before exporting.

## Provenance (what is whose)

- `patches/linux/*`    our FH8626V200 support for Linux: machine, device tree, clock,
  USB PHY, SC2336 sensor, camera pipeline, stepper and IR-cut drivers, defconfig
- `patches/u-boot/*`   our FH8626 board port
- `patches/ssv6x5x/*`  our Linux 6.18 build fixes for the third-party Wi-Fi driver,
  which is fetched at build time from the commit pinned in `config/ssv6x5x.mk`
- `fh8626_2nd_stage/mkimage.py` contains the `DDR_0` DRAM-init register table copied
  from the stock flash image (register values only, no code)
- Pinned upstream: Linux and U-Boot versions in `config/versions.mk`

No stock firmware, vendor binaries or vendor tuning data are part of this repository.

## Updating upstream

Change the version and URL in `config/versions.mk`, clear the SHA-256, `make fetch`,
verify and pin, then `make reprepare`. Patches that no longer apply are fixed in
`work/` (`git am -3`, resolve) and re-exported.
