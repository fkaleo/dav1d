#!/bin/sh
# Generate a small, reproducible AV1 benchmark corpus using ffmpeg's
# deterministic synthetic sources and the SVT-AV1 encoder.
#
# The corpus covers the content classes that matter for real-world decode
# cost: animation/graphics, high-detail film-like content, smooth gradients,
# high-motion noisy content, screen/text content, film-grain synthesis,
# 8- and 10-bit, and resolutions from 480p to 4K.
#
# The clips are synthetic, so absolute bitrates are not representative of
# any particular streaming service; they are meant to exercise the decoder
# tools (transforms, intra/inter prediction, CDEF, loop restoration, film
# grain) in stable, repeatable proportions. Reference MD5s are generated
# from a trusted baseline build with make-refs, so corpus regeneration on a
# different encoder version only requires regenerating the references.
#
# Usage: generate_corpus.sh [output-dir]

set -e

OUT="${1:-$(dirname "$0")/data/corpus}"
mkdir -p "$OUT"

FFMPEG="${FFMPEG:-ffmpeg}"
PRESET="${SVT_PRESET:-8}"

enc() {
    name=$1; src=$2; frames=$3; pixfmt=$4; crf=$5; params=$6
    if [ -f "$OUT/$name.ivf" ]; then
        echo "exists: $name.ivf"
        return
    fi
    echo "encoding: $name.ivf"
    "$FFMPEG" -y -hide_banner -loglevel error \
        -f lavfi -i "$src" -frames:v "$frames" -pix_fmt "$pixfmt" \
        -c:v libsvtav1 -preset "$PRESET" -crf "$crf" \
        -svtav1-params "${params:+$params:}keyint=120" \
        "$OUT/$name.ivf"
}

# name                      source                                  frames fmt          crf extra-params
enc anim-480p-8bit          "testsrc2=s=854x480:r=30"                  300 yuv420p       45 ""
enc anim-720p-8bit          "testsrc2=s=1280x720:r=30"                 240 yuv420p       35 ""
enc screen-1080p-8bit       "testsrc=s=1920x1080:r=30"                 240 yuv420p       35 ""
enc detail-1080p-8bit       "mandelbrot=s=1920x1080:r=30"              240 yuv420p       30 ""
enc detail-1080p-10bit      "mandelbrot=s=1920x1080:r=30"              240 yuv420p10le   30 ""
enc grain-1080p-8bit        "testsrc2=s=1920x1080:r=30"                240 yuv420p       35 "film-grain=15:film-grain-denoise=0"
enc grain-1080p-10bit       "testsrc2=s=1920x1080:r=30"                240 yuv420p10le   35 "film-grain=15:film-grain-denoise=0"
enc smooth-1080p-10bit      "gradients=s=1920x1080:n=4:speed=0.5"      240 yuv420p10le   30 ""
enc noisy-720p-8bit         "testsrc2=s=1280x720:r=30,noise=alls=12:allf=t" 240 yuv420p  40 ""
enc motion-720p-8bit        "life=s=1280x720:rate=30:mold=10:ratio=0.15" 240 yuv420p     40 ""
enc lowbr-480p-8bit         "testsrc2=s=854x480:r=30,noise=alls=20:allf=t" 300 yuv420p   55 ""
enc uhd-2160p-8bit          "mandelbrot=s=3840x2160:r=30"              120 yuv420p       35 ""

echo "corpus ready in $OUT"
