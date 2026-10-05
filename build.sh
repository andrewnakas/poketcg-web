#!/usr/bin/env bash
# Build Pokemon Trading Card Game from pret/poketcg and check it byte-matches.
#
# Nothing here needs root. rgbds comes from the upstream project's own statically
# linked Linux release, because this machine has no bison and no sudo, so building
# rgbds from source (what the gbdev INSTALL instructions tell you to do) cannot
# work here.
#
# Usage: ./build.sh            build and verify
#        ./build.sh clean      remove the clone (leaves the shared toolchain)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHARED="$HOME/decompfleet/shared"

# Pinned upstream revision. pret/poketcg has no tags, so the commit hash is the
# only pin available. This is the master commit this project was verified at.
PRET_REV=4afd40f47153d37f0ba88bcc3bb2ce41034c6c92

# The version pret's own CI checks out (.github/workflows/main.yml: it clones
# gbdev/rgbds at ref v1.0.4 and builds it). src/rgbdscheck.asm only demands
# ">= 1.0.0", which is NOT enough to guarantee a byte match: rgbgfx output and
# the LZ settings move between minor versions. CI is the authority.
RGBDS_VER=v1.0.4
RGBDS_TARBALL_SHA256=3e19e50ea602e316be709160acaf0a708b1fa6d32d219ec541f244442895dbb7

RGBDS_DIR="$SHARED/rgbds-${RGBDS_VER#v}"

if [ "${1:-}" = "clean" ]; then
  rm -rf "$HERE/upstream"
  echo "cleaned (shared rgbds left in place)"
  exit 0
fi

# ---------------------------------------------------------------- toolchain --
if [ ! -x "$RGBDS_DIR/bin/rgbasm" ]; then
  echo ">> installing rgbds $RGBDS_VER into $RGBDS_DIR"
  mkdir -p "$SHARED/dl" "$RGBDS_DIR/bin"
  tarball="$SHARED/dl/rgbds-$RGBDS_VER-linux-x86_64.tar.xz"
  if [ ! -f "$tarball" ]; then
    curl -fsSL -o "$tarball" \
      "https://github.com/gbdev/rgbds/releases/download/$RGBDS_VER/rgbds-linux-x86_64.tar.xz"
  fi
  echo "$RGBDS_TARBALL_SHA256  $tarball" | sha256sum -c -
  tmp=$(mktemp -d)
  tar xJf "$tarball" -C "$tmp"
  install -m755 "$tmp"/rgbasm "$tmp"/rgblink "$tmp"/rgbfix "$tmp"/rgbgfx "$RGBDS_DIR/bin/"
  rm -rf "$tmp"
fi
export PATH="$RGBDS_DIR/bin:$PATH"
echo ">> $(rgbasm --version)"

# ----------------------------------------------------------------- sources --
if [ ! -d "$HERE/upstream/.git" ]; then
  echo ">> cloning pret/poketcg"
  git clone --quiet https://github.com/pret/poketcg.git "$HERE/upstream"
fi
cd "$HERE/upstream"
git fetch --quiet origin
git checkout --quiet "$PRET_REV"
echo ">> pret/poketcg at $(git rev-parse HEAD)"

# ------------------------------------------------------------------- build --
# DEBUG=1 is what pret's CI compares with. It only adds -E to rgbasm, which
# exports every symbol to the object files; the ROM is byte-identical either way
# (build.sh verified that), but the richer poketcg.sym is what the browser test
# decodes game state with, so build it that way here.
make DEBUG=1 -j"$(nproc)" compare

echo
echo ">> built"
sha1sum poketcg.gbc poketcg.patch

staged="$HOME/decompfleet/roms/poketcg.gbc"
if [ -f "$staged" ]; then
  echo
  echo ">> byte-for-byte against the staged dump"
  cmp "$staged" poketcg.gbc && echo "   identical to $staged"
fi
