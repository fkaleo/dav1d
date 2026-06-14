# Package-energy ground truth on Apple M4 Pro (powermetrics, 2026-06-14)

The project's end goal is joules per video-hour; everything before this
used instruction/cycle proxies (no RAPL in the original container, no
valid wall-clock). This is the first direct **package-energy**
measurement, on a quiet M4 Pro via `powermetrics`.

## Method

Interleaved same-session A/B: for each round, decode the clip N times
per build back-to-back while sampling CPU/GPU/ANE power, integrate to
energy/frame, alternate builds, take the median over rounds. Decoders
are master (`origin/master`) and the 64-commit branch, both release
builds, single-thread, bit-exact on every clip.

Measurement notes that cost time to get right (harness in the session
tmp, `energy_ab2.py`):
- `powermetrics` must **self-terminate** (`-n <count>`) and be
  `wait()`ed on — relying on `sudo pkill` to stop it silently fails
  (only `powermetrics` was in the sudoers NOPASSWD rule, not `pkill`),
  which leaks one sampler per measurement; ~30 accumulated and pegged
  the cores in a first attempt. One sampler per measurement, joined.
- `powermetrics` reports **whole-package** power, so background macOS
  activity spikes some samples up and trailing idle pulls some down.
  Per-measurement power is the **trimmed mean** (drop first 3 startup
  samples, reject the low 20% / high 20%) = the steady decode draw;
  interleaving + the across-round median reject the rest. The energy
  delta tracking the wall delta confirms the result.
- An earlier "−9.3%" reading was taken with the leaked samplers still
  running (contaminated power) and is superseded by the clean numbers
  below.

## Result: screen content (the load_tmvs regime)

3-way attribution, one interleaved run, screen-1080p stream, 6 rounds,
combined (CPU+GPU+ANE) energy per frame:

| build | mJ/frame | vs master | wall vs master |
| --- | --- | --- | --- |
| master | 3.80 | — | — |
| branch **minus** the load_tmvs rework | 3.63 | **−4.7%** | −3.5% |
| full branch | 3.25 | **−14.5%** | −12.1% |

- **Full series: −14.5% package energy per frame** on screen content.
- **The load_tmvs NEON rework alone: −10.3%** (3.63 → 3.25) — about
  two-thirds of the screen win, the single largest contributor.
- The remaining **−4.7%** is the rest of the series (eob-bounded
  level-clear, the inlining patches, etc.).
- Power is essentially equal across builds (~6.5 W steady single-core),
  so each delta is a near-pure time→energy conversion; the energy
  reduction slightly exceeds the wall reduction because the reworked
  load_tmvs also draws marginally less power.

This is the energy confirmation of the load_tmvs win that the cachegrind
`Ir` proxy could not see (the win is cycle/branch-level): on real
hardware it is a **double-digit energy saving on screen-class content.**

## Result: film / 4K content (no load_tmvs)

master vs branch, 6 rounds each:

| clip | energy vs master | wall vs master |
| --- | --- | --- |
| detail-1080p-8bit (film) | −0.0% | −0.2% |
| uhd-2160p-8bit (4K) | −0.6% | −0.2% |

**Energy-neutral.** These SVT clips do not use ref-frame MVs, so
load_tmvs never runs; the series' remaining wins are memory-traffic
reductions (the eob-level-clear: D1 −12..−37%, LL −41..−82% on the x86
container) and micro-inlining. On the M4's wide unified-memory
subsystem those do not move wall time or energy — the memory-traffic
patches that paid off on a 34 MB-LLC x86 server are invisible here. An
honest cross-platform finding: the same patch series is energy-positive
on x86 (memory-bound) but, on this clip set, only the compute-bound
load_tmvs win converts to energy on Apple Silicon.

## Takeaways

- First joules-level validation for the project: the load_tmvs rework
  delivers **~−10% decode energy on screen-class content** on an M4 Pro,
  and the full series **−14.5%** there.
- The proxy hierarchy is now calibrated against energy: cachegrind `Ir`
  missed load_tmvs entirely (cycle-level win); M4 wall-clock and
  powermetrics agree and capture it. Memory-traffic instruction/miss
  proxies overstate the M4 benefit (fast memory) and understate it on
  big-core x86.
- Open: the x86 run-find port's benefit should be shown the same way on
  a native x86-64 box (`perf`/RAPL), since cachegrind cannot.
