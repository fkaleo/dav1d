# dav1d benchmark and correctness harness

A self-contained, profiler-guided measurement loop for dav1d optimisation
work. The goal is to make every proposed performance patch answerable with
three questions: *is the output still bit-exact?*, *did the work per frame go
down?*, and *is the wall-clock win stable across content classes?*

Nothing in this directory affects the dav1d build or library; it is test and
measurement infrastructure only.

## Components

| file | purpose |
| --- | --- |
| `fetch_conformance.sh` | downloads a 22-vector subset of the libaom AV1 test vectors with per-frame MD5 ground truth (features, quantizer extremes, odd frame sizes, film grain, monochrome, 8/10-bit) |
| `generate_corpus.sh` | generates a 12-clip benchmark corpus with ffmpeg synthetic sources + SVT-AV1 (480p–4K, 8/10-bit, animation/film-detail/screen/smooth/noisy/high-motion/film-grain classes) |
| `bench.py` | driver: correctness checks, wall-clock benchmarks, deterministic perf counters, callgrind profiles, result diffing |

Generated data lives in `data/` (gitignored): the corpus is reproducible from
the scripts, and reference MD5s are regenerated from a trusted baseline build
per environment, so no binary data is committed.

## Workflow

```sh
# one-time setup
tests/bench/fetch_conformance.sh
tests/bench/generate_corpus.sh
python3 tests/bench/bench.py make-refs        # from a TRUSTED BASELINE build

# correctness gate (run on every change)
python3 tests/bench/bench.py check
#  - decodes the libaom vectors per-frame and compares against libaom's
#    own MD5 ground truth (true external conformance, not self-reference)
#  - runs both the full-SIMD path and the pure-C path (--cpumask 0)
#  - verifies the whole corpus against the stored baseline MD5s on both paths

# wall-clock benchmark (median of N runs, first run discarded as warmup)
python3 tests/bench/bench.py bench --runs 7 --threads 1,4 --out before.json

# deterministic energy proxies (single-thread, under cachegrind):
# instructions/frame, L1d misses/frame, LLd misses/frame, branch misses/frame
python3 tests/bench/bench.py counters --limit 60 --out before-counters.json

# function-level hotspot profile of one clip (callgrind)
python3 tests/bench/bench.py profile detail-1080p-8bit --limit 40
python3 tests/bench/bench.py profile detail-1080p-8bit --limit 40 --cpumask 0

# after a change: rebuild, re-run, diff
python3 tests/bench/bench.py compare before.json after.json
python3 tests/bench/bench.py compare before-counters.json after-counters.json
```

## Measurement notes

- **Wall time is noisy in shared/virtualised environments.** The harness
  reports the spread (max−min)/median per clip; treat wall-clock deltas
  smaller than the spread as noise. The cachegrind instruction counts are
  deterministic to ~0.01% and are the primary signal for small wins.
- **Counters are energy proxies.** Instructions retired, cache misses and
  branch misses correlate with CPU energy but are not package-energy
  measurements (RAPL is typically unavailable in containers). Label them as
  proxies in any report.
- **Correctness is dual-anchored.** The conformance vectors compare against
  *libaom's* ground truth, so a harness bug cannot silently bless a broken
  decoder; the corpus MD5s additionally pin bit-exactness on realistic
  content, and every check runs the pure-C and SIMD paths so they can never
  diverge unnoticed.
- The corpus is synthetic (deterministic ffmpeg lavfi sources) so it can be
  regenerated anywhere without shipping binary clips. It exercises decoder
  tools in stable proportions but absolute bitrates are not representative of
  any particular streaming service; validate headline claims on real content
  before publishing them.
- `checkasm` (the upstream SIMD unit test) is the preferred tool for
  validating and benchmarking individual SIMD kernels; this harness
  complements it with whole-decoder, whole-stream checks. If
  code.videolan.org is unreachable, the framework subproject can be
  fetched from the GitHub mirror instead:
  `curl -L https://codeload.github.com/haasn/checkasm/tar.gz/refs/tags/v1.2.0 | tar xz -C subprojects && mv subprojects/checkasm-1.2.0 subprojects/checkasm`
  then reconfigure with `-Dtrim_dsp=false` and build the
  `tests/checkasm` target.

## Recommended release build flags (for packagers/CI)

PGO and LTO combined measured −1.8…−3.7% instructions/frame and
−18…−43% L1i misses versus a plain `-O3` build of the same sources
(bit-exact output; see reports/). Recipe:

```sh
meson setup build --buildtype release -Db_lto=true -Db_pgo=generate
ninja -C build
# train on representative content, e.g. the corpus at 1 and N threads
for c in tests/bench/data/corpus/*.ivf; do
    build/tools/dav1d -q -i "$c" --muxer null --threads 1 -l 60
    build/tools/dav1d -q -i "$c" --muxer null --threads 4 -l 60
done
meson configure build -Db_pgo=use && ninja -C build
# IMPORTANT: confirm instrumentation is gone and profiles matched:
nm build/src/libdav1d.so* | grep -c gcov   # must print 0
```

If sources changed since training, gcc fails with coverage-mismatch
errors: retrain rather than suppressing the error.
