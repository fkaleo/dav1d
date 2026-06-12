# Session bootstrap runbook (Claude Code web container, Ubuntu 24.04)

Steps to reproduce the working environment of the original optimisation
session from a fresh container. Total time ~25 min, mostly encoding.

```sh
# 1. toolchain (meson is absent; nasm/ffmpeg/aom from apt)
pip install meson
apt-get update; apt-get install -y nasm ffmpeg aom-tools libclang-rt-18-dev

# 2. checkasm subproject (code.videolan.org is blocked by the default
#    network policy; use the maintainer mirror on GitHub)
curl -L https://codeload.github.com/haasn/checkasm/tar.gz/refs/tags/v1.2.0 \
  | tar xz -C subprojects && mv subprojects/checkasm-1.2.0 subprojects/checkasm

# 3. build (release + symbols + untrimmed DSP so checkasm has C refs)
meson setup build --buildtype release -Ddebug=true -Dtrim_dsp=false \
                  -Dtestdata_tests=false
ninja -C build && ninja -C build tests/checkasm

# 4. test data (conformance vectors from the allowed aom bucket; corpus
#    generated locally - synthetic + real-content + superres clips)
tests/bench/fetch_conformance.sh
tests/bench/generate_corpus.sh
python3 tests/bench/bench.py make-refs   # MUST run on a trusted baseline:
    # if local patches are already applied, first verify against master:
    # git stash / build master in a worktree and cross-check md5s.

# 5. verify everything
python3 tests/bench/bench.py check       # expect 22/22 conformance both
                                         # paths + corpus verify PASS
build/tests/checkasm                     # expect all tests passed
```

Environment facts that cost time to rediscover:
- perf and RAPL are unavailable; valgrind (cachegrind/callgrind) is the
  deterministic counter source. valgrind masks AVX-512: counters
  measure AVX2 kernels.
- Wall-clock varies by tens of percent across hours (shared host).
  Only same-session interleaved A/B runs and counters are valid.
- msac SSE2 asm is always active on x86-64 even at --cpumask 0; use an
  -Denable_asm=false build dir to measure the pure-C entropy path.
- checkasm --bench is seed-sensitive (positional seed argument);
  always compare across >=3 seeds (see ROADMAP, load_tmvs).
- gcc PGO: after -Db_pgo=use rebuild, `nm | grep -c gcov` must be 0,
  or you are measuring the instrumented binary.
