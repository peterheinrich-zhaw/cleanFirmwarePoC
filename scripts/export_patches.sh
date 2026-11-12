#!/bin/sh
# usage: export_patches.sh <name> <workdir> <patchdir> [--check]
# Writes every commit on top of "fh-vanilla" in <workdir> to <patchdir>/NNNN-*.patch.
# Output is made reproducible (no signature, zeroed commit hash) so that
# regenerating unchanged commits gives byte-identical files.
# --check only verifies that <patchdir> already matches the tree (exit 1 if not).
set -e
name=$1; dir=$2; pdir=$3; mode=$4
if [ ! -d "$dir/.git" ]; then
	[ "$mode" = "--check" ] && exit 0
	echo "ERROR: $dir does not exist (run: make prepare)"; exit 1
fi
git -C "$dir" rev-parse -q --verify fh-vanilla >/dev/null || { echo "ERROR: $dir has no fh-vanilla tag"; exit 1; }
if ! git -C "$dir" diff --quiet HEAD || [ -n "$(git -C "$dir" ls-files --others --exclude-standard)" ]; then
	echo "ERROR: $dir has uncommitted or untracked files; commit them first:"
	git -C "$dir" status --short | head -15
	exit 1
fi
out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT
git -C "$dir" format-patch -q --no-signature --zero-commit -o "$out" fh-vanilla..HEAD
mkdir -p "$pdir"
if diff -rq "$out" "$pdir" >/dev/null 2>&1 || { [ -z "$(ls "$out")" ] && [ -z "$(ls "$pdir"/*.patch 2>/dev/null)" ]; }; then
	echo "$name: patches in $pdir are up to date ($(ls "$out" | wc -l) patch(es))"
	exit 0
fi
if [ "$mode" = "--check" ]; then
	echo "ERROR: $dir has commits that are not exported to $pdir (run: make export)"
	exit 1
fi
rm -f "$pdir"/*.patch
cp "$out"/*.patch "$pdir"/ 2>/dev/null || true
echo "$name: wrote $(ls "$pdir"/*.patch 2>/dev/null | wc -l) patch(es) to $pdir"
