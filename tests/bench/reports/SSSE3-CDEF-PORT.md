# Implementation runway: #305 SSSE3 8-bit fully-edged CDEF filter

Goal: port the 8-bit fully-edged fast path (AVX2 via upstream !915,
also done for arm32/arm64) to SSSE3 in src/x86/cdef_sse.asm. Upstream
issue #305 lists SSSE3 as the remaining TODO.

## Why (measured on this branch's harness)

- On AVX2-less x86 (--cpumask ssse3), CDEF filter kernels are 17.4% of
  1080p film decode (8x8: 12.8%, 4x4: 4.6%); dir is another 4.4% but
  is unaffected by this port.
- Whole-decoder projection: ~2.5-4% on SSSE3-class machines.

## Kernel baselines (checkasm --bench, seed 7, Ice Lake, cycles)

| variant      | sse2  | ssse3 (current) | avx2 (8-bit path) | port target |
|--------------|-------|-----------------|-------------------|-------------|
| 4x4_01       |  94.4 |  71.2           |  61.4             | ~65 (small) |
| 4x4_10       |  64.5 |  52.9           |  49.6             | ~50 (small) |
| 4x4_11       | 144.5 | 106.1           |  73.5             | ~85-95      |
| 4x8_11       | 262.4 | 185.2           | 115.7             | ~145-160    |
| 8x8_01       | 262.8 | 154.6           | 107.9             | ~120        |
| 8x8_10       | 159.1 | 102.6           |  73.7             | ~85         |
| 8x8_11       | 463.1 | 299.5           | 169.3             | ~210-240    |

Start with 8x8 (largest win and decode share), then 4x8, then 4x4.
Accept only if checkasm passes and the per-variant bench beats current
ssse3 on _11 and _01 without regressing _10; gate end-to-end with
`bench.py check` plus counters at --cpumask ssse3.

## Design notes (from reading cdef_avx2.asm lines 94-1500)

- Entry: `cmp edged, 0xf; jne .border_block` — fully-edged falls into
  the 8-bit path; everything else uses the existing 16-bit path
  unchanged. Mirror this structure in cdef_sse.asm under INIT_XMM
  ssse3 (sse2 keeps the old path; sse4 can share the ssse3 body).
- Geometry for 8x8 at 128-bit: two 8-px rows per xmm (vs four in
  ymm); the whole block in 4 regs. LOAD_BLOCK/ADJUST_PIXEL halve
  naturally. ACCUMULATE_TAP_BYTE works as-is at xmm width (all ops
  SSSE3: psubusb/pcmpeqb/psignb/psrlw+pand 8-bit-shift emulation/
  pminub/pmaddubsw).
- The 16 per-(direction,tap) gather blocks (.d0k0...d7k1) are the bulk
  of the work: each loads p0/p1 at the direction offset by qword loads
  from dst/top/bot at -1, psrldq shifts, and blending saved `left`
  columns over the x<0 bytes. AVX2 keeps 3 pre-shifted copies of the
  left columns on the stack (entry code, rsp+0x10/0x28/0x40 for 4x4;
  per-row at rsp+0x60.. for 8x8) - same trick works at xmm width.
- SSE4.1 instructions used by the AVX2 path that MUST be substituted
  for SSSE3: vpblendvb -> pand/pandn/por with the mask in a register
  (masks are constants from blend_4x4/blend_4x8_0/blend_8x8_0 tables -
  reuse those rodata tables); vpblendd -> shufps/por; pinsrd/pextrd ->
  movd/movq + punpckldq / psrldq+movd; vpbroadcastq/d ->
  movddup (SSE3) / pshufd; vinserti128/vextracti128 -> second xmm reg.
- The indirect `call dirjmpq` jump-table dispatch works unchanged;
  add a CDEF_FILTER_JMP_TABLE instantiation for the ssse3 labels.
- Constants: tap_table, pri/sec shift masks and pw_2048 are already in
  cdef_sse.asm or trivially shared; check dav1d_cdef_directions use.
- checkasm fuzzes all (pri,sec,dir,edges) combinations including
  edges=0xf, so correctness iteration is fast; remember --bench is
  seed-sensitive (bench with >=3 seeds; see ROADMAP load_tmvs note).

## Status

Not implemented in this branch: the full port is ~400+ lines of new
assembly; it was scoped out of the original session after the runway
above was prepared (see session notes in 2026-06-11-baseline.md for
the measurement methodology to reuse).
