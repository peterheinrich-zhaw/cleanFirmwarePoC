#!/bin/sh
# usage: prepare_tree.sh <name> <tarball> <workdir> <patchdir>
# Unpacks the pristine release into <workdir>, commits it as "fh-vanilla" in a
# purely local git repo (never pushed anywhere), then applies <patchdir>/*.patch
# with git am, one commit per patch. The result is the development tree.
set -e
name=$1; tarball=$2; dir=$3; pdir=$4
[ -f "$tarball" ] || { echo "ERROR: $tarball missing (run: make fetch)"; exit 1; }
if [ -e "$dir/.git" ]; then
	echo "$dir already exists (use 'make reprepare' to rebuild it from the patches)"
	exit 0
fi
tmp=$dir.tmp
rm -rf "$tmp"; mkdir -p "$tmp"
echo "unpacking $tarball ..."
tar -xf "$tarball" -C "$tmp" --strip-components=1
cd "$tmp"
export GIT_AUTHOR_NAME="upstream release" GIT_AUTHOR_EMAIL="upstream@invalid"
export GIT_COMMITTER_NAME="fh8626 build"  GIT_COMMITTER_EMAIL="build@invalid"
export GIT_AUTHOR_DATE="2000-01-01T00:00:00+0000" GIT_COMMITTER_DATE="2000-01-01T00:00:00+0000"
git init -q .
git config core.untrackedCache true
git config gc.auto 0
# Bind mounts (Docker Desktop on macOS) change inode/ctime behind git's back,
# so git am sees a stale index and fails with "does not match index".
git config core.checkStat minimal
git config core.trustctime false
git add -f -A
git commit -q -m "Pristine $name release"
git tag fh-vanilla
unset GIT_AUTHOR_DATE GIT_COMMITTER_DATE
cd - >/dev/null
n=$(ls "$pdir"/*.patch 2>/dev/null | wc -l)
if [ "$n" -gt 0 ]; then
	echo "applying $n patch(es) from $pdir ..."
	if ! (cd "$tmp" && git am -q "$OLDPWD"/"$pdir"/*.patch); then
		echo "ERROR: a patch does not apply. The partial tree is left in $tmp"
		echo "       (cd $tmp && git am --abort) to start over; fix or regenerate the patch."
		exit 1
	fi
fi
mv "$tmp" "$dir"
echo "ready: $dir ($(git -C "$dir" rev-list --count fh-vanilla..HEAD) commit(s) on top of the release)"
