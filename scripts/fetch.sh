#!/bin/sh
# usage: fetch.sh <dest> <url> <sha256>
# Downloads <url> to <dest> and refuses to keep it unless the SHA-256 matches.
# With an empty <sha256> it prints the hash of the download so you can check it
# against the upstream's published checksum and pin it in config/versions.mk.
set -e
dest=$1; url=$2; want=$3
mkdir -p "$(dirname "$dest")"
part=$dest.part
if [ ! -f "$part" ] || [ -z "$want" ] || [ "$(sha256sum "$part" | cut -d' ' -f1)" != "$want" ]; then
	echo "downloading $url"
	if command -v curl >/dev/null 2>&1; then
		curl -fL --retry 3 -o "$part" "$url"
	else
		wget -q -O "$part" "$url"
	fi
fi
got=$(sha256sum "$part" | cut -d' ' -f1)
if [ -z "$want" ]; then
	echo "sha256 $got  $(basename "$dest")"
	echo "ERROR: no checksum pinned. Compare the hash above with upstream's published"
	echo "       checksum/signature, then set it in config/versions.mk and run make again."
	exit 1
fi
if [ "$got" != "$want" ]; then
	echo "ERROR: checksum mismatch for $url"
	echo "  expected $want"
	echo "  got      $got"
	exit 1
fi
mv "$part" "$dest"
echo "ok: $dest"
