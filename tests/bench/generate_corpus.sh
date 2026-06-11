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

# Frame super-resolution stresses the resize branches in the CDEF and
# loop-restoration glue that no other clip reaches. SVT-AV1 does not
# implement superres, so this clip needs aomenc and is optional.
if command -v aomenc >/dev/null 2>&1; then
    if [ ! -f "$OUT/superres-720p-8bit.ivf" ]; then
        echo "encoding: superres-720p-8bit.ivf"
        tmpy4m=$(mktemp --suffix=.y4m)
        "$FFMPEG" -y -hide_banner -loglevel error \
            -f lavfi -i "testsrc2=s=1280x720:r=30" -frames:v 60 \
            -pix_fmt yuv420p -f yuv4mpegpipe "$tmpy4m"
        aomenc --ivf -o "$OUT/superres-720p-8bit.ivf" --cpu-used=8 \
            --end-usage=q --cq-level=40 --superres-mode=1 \
            --superres-denominator=12 --enable-restoration=1 --threads=4 \
            "$tmpy4m" >/dev/null 2>&1
        rm -f "$tmpy4m"
    else
        echo "exists: superres-720p-8bit.ivf"
    fi
else
    echo "skipping superres clip (aomenc not found)"
fi

# Real-content clips: raw camera/screen sources from the public
# aom-test-data bucket, encoded locally like the synthetic ones. These
# complement the synthetic corpus with natural noise, motion and texture
# statistics (the synthetic clips remain for reproducibility when the
# bucket is unreachable).
AOM_DATA="https://storage.googleapis.com/aom-test-data"

enc_real() {
    name=$1; srcfile=$2; frames=$3; pixfmt=$4; crf=$5
    if [ -f "$OUT/$name.ivf" ]; then
        echo "exists: $name.ivf"
        return
    fi
    echo "encoding: $name.ivf (source: $srcfile)"
    tmpsrc=$(mktemp --suffix=".${srcfile##*.}")
    curl -sf -o "$tmpsrc" "$AOM_DATA/$srcfile" || { rm -f "$tmpsrc"; \
        echo "skipping $name (download failed)"; return; }
    "$FFMPEG" -y -hide_banner -loglevel error \
        -i "$tmpsrc" -frames:v "$frames" -pix_fmt "$pixfmt" \
        -c:v libsvtav1 -preset "$PRESET" -crf "$crf" \
        -svtav1-params "keyint=120" "$OUT/$name.ivf"
    rm -f "$tmpsrc"
}

# name                       source                        frames fmt         crf
enc_real real-niklas-720p-8bit    niklas_1280_720_30.y4m      240 yuv420p      35
enc_real real-crowdrun-360p-10bit crowd_run_360p_10_150f.y4m  150 yuv420p10le  35
enc_real real-screen-1080p-8bit   screendata.1920_1080.y4m    120 yuv420p      40
enc_real real-rushhour-288p-8bit rush_hour_444.y4m           120 yuv420p      32

echo "corpus ready in $OUT"
