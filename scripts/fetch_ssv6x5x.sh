#!/bin/sh
# Fetch the pinned upstream ssv6x5x tree into work/ssv6x5x and apply our patches.
# usage: fetch_ssv6x5x.sh [workdir]        (default: work)
# env:   SSV_REPO overrides the repository URL (e.g. a local mirror)
set -eu
top=$(cd "$(dirname "$0")/.." && pwd)
work=${1:-$top/work}
repo=${SSV_REPO:-$(sed -n 's/^SSV6X5X_REPO *:= *//p' "$top/config/ssv6x5x.mk")}
commit=$(sed -n 's/^SSV6X5X_COMMIT *:= *//p' "$top/config/ssv6x5x.mk")
dst=$work/ssv6x5x

[ -e "$dst" ] && { echo "$dst exists; remove it first" >&2; exit 1; }
mkdir -p "$work"
git clone -q "$repo" "$dst"
git -C "$dst" checkout -q "$commit"
got=$(git -C "$dst" rev-parse HEAD)
[ "$got" = "$commit" ] || { echo "unexpected commit $got" >&2; exit 1; }
git -C "$dst" checkout -q -b fh-patched
git -C "$dst" -c user.name=open-cam-fw -c user.email=open-cam-fw@example.invalid \
    am -q --keep-cr "$top"/patches/ssv6x5x/*.patch
echo "OK: $dst at $(git -C "$dst" rev-parse --short HEAD) (upstream $commit + $(ls "$top"/patches/ssv6x5x/*.patch | wc -l) patches)"
