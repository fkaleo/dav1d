#!/bin/sh
# Fetch a subset of the libaom AV1 test vectors (plus their per-frame MD5
# ground-truth files) from the public aom-test-data bucket.
#
# The subset covers: codec features (allintra, cdfupdate, mv, mfmv, intrabc,
# film grain, monochrome), 8- and 10-bit quantizer extremes, and a sample of
# odd/even frame sizes that stress edge handling.
#
# Usage: fetch_conformance.sh [output-dir]

set -e

OUT="${1:-$(dirname "$0")/data/conformance}"
mkdir -p "$OUT"

BASE="https://storage.googleapis.com/aom-test-data"

VECTORS="
av1-1-b8-02-allintra
av1-1-b8-04-cdfupdate
av1-1-b8-05-mv
av1-1-b8-06-mfmv
av1-1-b8-16-intra_only-intrabc-extreme-dv
av1-1-b8-23-film_grain-50
av1-1-b8-24-monochrome
av1-1-b10-23-film_grain-50
av1-1-b10-24-monochrome
av1-1-b8-00-quantizer-00
av1-1-b8-00-quantizer-32
av1-1-b8-00-quantizer-63
av1-1-b10-00-quantizer-00
av1-1-b10-00-quantizer-32
av1-1-b10-00-quantizer-63
av1-1-b8-01-size-16x16
av1-1-b8-01-size-16x18
av1-1-b8-01-size-18x16
av1-1-b8-01-size-34x34
av1-1-b8-01-size-66x66
av1-1-b8-01-size-196x198
av1-1-b8-01-size-226x226
"

for v in $VECTORS; do
    for f in "$v.ivf" "$v.ivf.md5"; do
        if [ ! -f "$OUT/$f" ]; then
            echo "fetching: $f"
            curl -sf -o "$OUT/$f" "$BASE/$f"
        fi
    done
done

echo "conformance vectors ready in $OUT"
