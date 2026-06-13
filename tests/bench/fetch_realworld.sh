#!/bin/sh
# Fetch the four industry-standard AV1 streams used by the Phoronix
# `pts/dav1d` profile (Netflix Chimera 8/10-bit 1080p, Summer Nature
# 1080p/4K) and remux them to IVF exactly as that profile does
# (stream copy, audio dropped). ~1.1 GB of downloads.
#
# These complement the synthetic corpus with the clips the community
# publishes dav1d numbers on. They are kept out of data/corpus so the
# routine `bench.py check` gate stays fast; validate them explicitly
# against a trusted build (see reports/). The pts profile decodes them
# with `--muxer null --threads $cores --filmgrain 0`.
#
# Usage: fetch_realworld.sh [output-dir]

set -e

OUT="${1:-$(dirname "$0")/data/realworld}"
mkdir -p "$OUT"

FFMPEG="${FFMPEG:-ffmpeg}"

fetch() {
    url=$1; file=$2; sha=$3; ivf=$4
    if [ -f "$OUT/$ivf" ]; then
        echo "exists: $ivf"
        return
    fi
    if [ ! -f "$OUT/$file" ]; then
        echo "fetching: $file"
        curl -Lf -o "$OUT/$file" "$url"
    fi
    echo "$sha  $OUT/$file" | shasum -a 256 -c - >/dev/null
    "$FFMPEG" -y -hide_banner -loglevel error \
        -i "$OUT/$file" -vcodec copy -an -f ivf "$OUT/$ivf"
    rm -f "$OUT/$file"
}

NETFLIX="http://download.opencontent.netflix.com.s3.amazonaws.com/AV1/Chimera/Old"
PTS="http://www.phoronix-test-suite.com/benchmark-files"

fetch "$NETFLIX/Chimera-AV1-8bit-1920x1080-6736kbps.mp4" \
      Chimera-AV1-8bit-1920x1080-6736kbps.mp4 \
      d566d294e2c18bb274a54aad03352c92312a62c393656d38e1f7dda10c0bf10c \
      chimera_8b_1080p.ivf
fetch "$NETFLIX/Chimera-AV1-10bit-1920x1080-6191kbps.mp4" \
      Chimera-AV1-10bit-1920x1080-6191kbps.mp4 \
      df2080fd77e0dbd9138bd4f172bf008d2ade17da7ab4532fba54ceccf40a9439 \
      chimera_10b_1080p.ivf
fetch "$PTS/Stream2_AV1_HD_6.8mbps.webm" \
      Stream2_AV1_HD_6.8mbps.webm \
      2f23d29750a0663a6df656e8137cf934bddfc96b31e5088db2c3624f19ed14d4 \
      summer_nature_1080p.ivf
fetch "$PTS/Stream2_AV1_4K_22.7mbps.webm" \
      Stream2_AV1_4K_22.7mbps.webm \
      52f3aa1d4b4487af62d37b0f295aabbc4b57f03fdc4c76402c6358193e4aa490 \
      summer_nature_4k.ivf

echo "real-world clips ready in $OUT"
