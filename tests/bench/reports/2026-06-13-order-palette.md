# order_palette() to DSP: measured rejection on this platform (2026-06-13)

ROADMAP item 4 listed `order_palette()` (the wavefront palette-index
ranker in decode.c) as a SIMD-DSP candidate, quoting "~2.6% on screen
content". Investigated on the M4 Pro before committing to a kernel;
the data says don't.

## What it does (vectorization shape)

Per palette block, `read_pal_indices` walks top-left→bottom-right
diagonals. For each diagonal it calls `order_palette`, which for every
cell on the diagonal computes a context (0–4) and an 8-entry colour
ordering from the left / top / top-left neighbours, then the diagonal's
cells are entropy-decoded (msac) using that ordering.

- Across diagonals it is strictly serial: diagonal i reads cells
  written by the msac decode of diagonals i−1/i−2, so it cannot be
  precomputed ahead of the entropy decode.
- Within a diagonal the cells are independent — that is the only
  vectorization axis. A kernel would process a diagonal's cells (up to
  64) at once: branchy 4-case neighbour comparison (vectorizable via
  compares/selects) plus a per-cell **variable-length order
  construction** — append the 1–3 candidate colours, then fill the
  remaining colours ascending by skipping a mask (an 8-bit
  compaction/expand, the hard part in SIMD).

## Why it is rejected here (measurement)

1. **Realistic content never exercises it.** Every SVT-AV1-encoded clip
   in the corpus (and the long screen/anim clips) calls `order_palette`
   **0 times** — SVT does not emit palette-mode blocks for this content
   at preset 8. Palette only appeared after re-encoding a screen source
   with `aomenc --tune-content=screen --enable-palette=1`.

2. **Even on a forced-palette aomenc clip its share is ~0.65%.**
   In-decoder timing (720p aomenc screen, palette on, 120 frames):
   `order_palette` = **0.26 ms over 142 450 cells**, against ~40 ms of
   decode — **0.65%**. The roadmap's "2.6%" is the *inclusive* palette
   cost (`read_pal_indices` + `order_palette` + the msac symbol decode);
   the msac decode dominates and is the irreducible serial-entropy part.
   `order_palette` alone is well under 1% even on palette-only content.

3. **A 3× kernel would buy ~0.4% on palette-only content and 0%
   everywhere else**, for a high-effort, high-risk data-dependent
   permutation (the mask-compaction fill has no cheap NEON form).

4. **The "to DSP" refactor without asm would regress.** `order_palette`
   is a fine static C function; routing it through a `pal_dsp` function
   pointer with only a C implementation adds an indirect call per
   diagonal for zero gain — the same indirect-call trap the
   `memset_pow2` table study rejected with data.

## Verdict

Not worth a kernel on this platform: realistic (SVT) content doesn't
use palette, and even forced-palette content spends <1% here. It stays
a future item **only** if real screen-share content (VNC / screen
recording / call screen-share, where palette mode is genuinely common)
is profiled on real hardware and shows a meaningful standalone share.
At that point the within-diagonal cell parallelism above is the design
to pursue; the msac decode it feeds remains the larger, irreducible
palette cost.
