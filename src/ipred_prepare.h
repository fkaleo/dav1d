/*
 * Copyright © 2018, VideoLAN and dav1d authors
 * Copyright © 2018, Two Orioles, LLC
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice, this
 *    list of conditions and the following disclaimer.
 *
 * 2. Redistributions in binary form must reproduce the above copyright notice,
 *    this list of conditions and the following disclaimer in the documentation
 *    and/or other materials provided with the distribution.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
 * ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
 * WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
 * DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
 * ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
 * (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
 * LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
 * ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
 * (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
 * SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 */

#ifndef DAV1D_SRC_IPRED_PREPARE_H
#define DAV1D_SRC_IPRED_PREPARE_H

#include <stddef.h>
#include <string.h>
#include <stdint.h>

#include "common/bitdepth.h"
#include "common/intops.h"

#include "src/env.h"
#include "src/intra_edge.h"
#include "src/levels.h"

/*
 * Luma intra edge preparation.
 *
 * x/y/start/w/h are in luma block (4px) units:
 * - x and y are the absolute block positions in the image;
 * - start/w/h are the *dependent tile* boundary positions. In practice, start
 *   is the horizontal tile start, w is the horizontal tile end, the vertical
 *   tile start is assumed to be 0 and h is the vertical image end.
 *
 * edge_flags signals which edges are available for this transform-block inside
 * the given partition, as well as for the partition inside the superblock
 * structure.
 *
 * dst and stride are pointers to the top/left position of the current block,
 * and can be used to locate the top, left, top/left, top/right and bottom/left
 * edge pointers also.
 *
 * angle is the angle_delta [-3..3] on input, and the absolute angle on output.
 *
 * mode is the intra prediction mode as coded in the bitstream. The return value
 * is this same mode, converted to an index in the DSP functions.
 *
 * tw/th are the size of the transform block in block (4px) units.
 *
 * topleft_out is a pointer to scratch memory that will be filled with the edge
 * pixels. The memory array should have space to be indexed in the [-2*w,2*w]
 * range, in the following order:
 * - [0] will be the top/left edge pixel;
 * - [1..w] will be the top edge pixels (1 being left-most, w being right-most);
 * - [w+1..2*w] will be the top/right edge pixels;
 * - [-1..-w] will be the left edge pixels (-1 being top-most, -w being bottom-
 *   most);
 * - [-w-1..-2*w] will be the bottom/left edge pixels.
 * Each edge may remain uninitialized if it is not used by the returned mode
 * index. If edges are not available (because the edge position is outside the
 * tile dimensions or because edge_flags indicates lack of edge availability),
 * they will be extended from nearby edges as defined by the av1 spec.
 */
static const uint8_t av1_mode_conv[N_INTRA_PRED_MODES]
                                  [2 /* have_left */][2 /* have_top */] =
{
    [DC_PRED]    = { { DC_128_PRED,  TOP_DC_PRED },
                     { LEFT_DC_PRED, DC_PRED     } },
    [PAETH_PRED] = { { DC_128_PRED,  VERT_PRED   },
                     { HOR_PRED,     PAETH_PRED  } },
};

static const uint8_t av1_mode_to_angle_map[8] = {
    90, 180, 45, 135, 113, 157, 203, 67
};

static const struct {
    uint8_t needs_left:1;
    uint8_t needs_top:1;
    uint8_t needs_topleft:1;
    uint8_t needs_topright:1;
    uint8_t needs_bottomleft:1;
} av1_intra_prediction_edges[N_IMPL_INTRA_PRED_MODES] = {
    [DC_PRED]       = { .needs_top  = 1, .needs_left = 1 },
    [VERT_PRED]     = { .needs_top  = 1 },
    [HOR_PRED]      = { .needs_left = 1 },
    [LEFT_DC_PRED]  = { .needs_left = 1 },
    [TOP_DC_PRED]   = { .needs_top  = 1 },
    [DC_128_PRED]   = { 0 },
    [Z1_PRED]       = { .needs_top = 1, .needs_topright = 1,
                        .needs_topleft = 1 },
    [Z2_PRED]       = { .needs_left = 1, .needs_top = 1, .needs_topleft = 1 },
    [Z3_PRED]       = { .needs_left = 1, .needs_bottomleft = 1,
                        .needs_topleft = 1 },
    [SMOOTH_PRED]   = { .needs_left = 1, .needs_top = 1 },
    [SMOOTH_V_PRED] = { .needs_left = 1, .needs_top = 1 },
    [SMOOTH_H_PRED] = { .needs_left = 1, .needs_top = 1 },
    [PAETH_PRED]    = { .needs_left = 1, .needs_top = 1, .needs_topleft = 1 },
    [FILTER_PRED]   = { .needs_left = 1, .needs_top = 1, .needs_topleft = 1 },
};

static enum IntraPredMode
bytefn(dav1d_prepare_intra_edges)(const int x, const int have_left,
                                  const int y, const int have_top,
                                  const int w, const int h,
                                  const enum EdgeFlags edge_flags,
                                  const pixel *const dst,
                                  const ptrdiff_t stride,
                                  const pixel *prefilter_toplevel_sb_edge,
                                  enum IntraPredMode mode, int *const angle,
                                  const int tw, const int th, const int filter_edge,
                                  pixel *const topleft_out HIGHBD_DECL_SUFFIX)
{
    const int bitdepth = bitdepth_from_max(bitdepth_max);
    assert(y < h && x < w);

    switch (mode) {
    case VERT_PRED:
    case HOR_PRED:
    case DIAG_DOWN_LEFT_PRED:
    case DIAG_DOWN_RIGHT_PRED:
    case VERT_RIGHT_PRED:
    case HOR_DOWN_PRED:
    case HOR_UP_PRED:
    case VERT_LEFT_PRED: {
        *angle = av1_mode_to_angle_map[mode - VERT_PRED] + 3 * *angle;

        if (*angle <= 90)
            mode = *angle < 90 && have_top ? Z1_PRED : VERT_PRED;
        else if (*angle < 180)
            mode = Z2_PRED;
        else
            mode = *angle > 180 && have_left ? Z3_PRED : HOR_PRED;
        break;
    }
    case DC_PRED:
    case PAETH_PRED:
        mode = av1_mode_conv[mode][have_left][have_top];
        break;
    default:
        break;
    }

    const pixel *dst_top;
    if (have_top &&
        (av1_intra_prediction_edges[mode].needs_top ||
         av1_intra_prediction_edges[mode].needs_topleft ||
         (av1_intra_prediction_edges[mode].needs_left && !have_left)))
    {
        if (prefilter_toplevel_sb_edge) {
            dst_top = &prefilter_toplevel_sb_edge[x * 4];
        } else {
            dst_top = &dst[-PXSTRIDE(stride)];
        }
    }

    if (av1_intra_prediction_edges[mode].needs_left) {
        const int sz = th << 2;
        pixel *const left = &topleft_out[-sz];

        if (have_left) {
            const int px_have = imin(sz, (h - y) << 2);

            for (int i = 0; i < px_have; i++)
                left[sz - 1 - i] = dst[PXSTRIDE(stride) * i - 1];
            if (px_have < sz)
                pixel_set(left, left[sz - px_have], sz - px_have);
        } else {
            pixel_set(left, have_top ? *dst_top : ((1 << bitdepth) >> 1) + 1, sz);
        }

        if (av1_intra_prediction_edges[mode].needs_bottomleft) {
            const int have_bottomleft = (!have_left || y + th >= h) ? 0 :
                                        (edge_flags & EDGE_I444_LEFT_HAS_BOTTOM);

            if (have_bottomleft) {
                const int px_have = imin(sz, (h - y - th) << 2);

                for (int i = 0; i < px_have; i++)
                    left[-(i + 1)] = dst[(sz + i) * PXSTRIDE(stride) - 1];
                if (px_have < sz)
                    pixel_set(left - sz, left[-px_have], sz - px_have);
            } else {
                pixel_set(left - sz, left[0], sz);
            }
        }
    }

    if (av1_intra_prediction_edges[mode].needs_top) {
        const int sz = tw << 2;
        pixel *const top = &topleft_out[1];

        if (have_top) {
            const int px_have = imin(sz, (w - x) << 2);
            pixel_copy(top, dst_top, px_have);
            if (px_have < sz)
                pixel_set(top + px_have, top[px_have - 1], sz - px_have);
        } else {
            pixel_set(top, have_left ? dst[-1] : ((1 << bitdepth) >> 1) - 1, sz);
        }

        if (av1_intra_prediction_edges[mode].needs_topright) {
            const int have_topright = (!have_top || x + tw >= w) ? 0 :
                                      (edge_flags & EDGE_I444_TOP_HAS_RIGHT);

            if (have_topright) {
                const int px_have = imin(sz, (w - x - tw) << 2);

                pixel_copy(top + sz, &dst_top[sz], px_have);
                if (px_have < sz)
                    pixel_set(top + sz + px_have, top[sz + px_have - 1],
                              sz - px_have);
            } else {
                pixel_set(top + sz, top[sz - 1], sz);
            }
        }
    }

    if (av1_intra_prediction_edges[mode].needs_topleft) {
        if (have_left)
            *topleft_out = have_top ? dst_top[-1] : dst[-1];
        else
            *topleft_out = have_top ? *dst_top : (1 << bitdepth) >> 1;

        if (mode == Z2_PRED && tw + th >= 6 && filter_edge)
            *topleft_out = ((topleft_out[-1] + topleft_out[1]) * 5 +
                            topleft_out[0] * 6 + 8) >> 4;
    }

    return mode;
}


// These flags are OR'd with the angle argument into intra predictors.
// ANGLE_USE_EDGE_FILTER_FLAG signals that edges should be convolved
// with a filter before using them to predict values in a block.
// ANGLE_SMOOTH_EDGE_FLAG means that edges are smooth and should use
// reduced filter strength.
#define ANGLE_USE_EDGE_FILTER_FLAG 1024
#define ANGLE_SMOOTH_EDGE_FLAG      512

static inline int sm_flag(const BlockContext *const b, const int idx) {
    if (!b->intra[idx]) return 0;
    const enum IntraPredMode m = b->mode[idx];
    return (m == SMOOTH_PRED || m == SMOOTH_H_PRED ||
            m == SMOOTH_V_PRED) ? ANGLE_SMOOTH_EDGE_FLAG : 0;
}

static inline int sm_uv_flag(const BlockContext *const b, const int idx) {
    const enum IntraPredMode m = b->uvmode[idx];
    return (m == SMOOTH_PRED || m == SMOOTH_H_PRED ||
            m == SMOOTH_V_PRED) ? ANGLE_SMOOTH_EDGE_FLAG : 0;
}

#endif /* DAV1D_SRC_IPRED_PREPARE_H */
