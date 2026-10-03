# Results

Two numbers, and a performance gate needs both before anybody lets it fail a build.

    false alarms   run identical code against itself. Every regression found is wrong.
    power          inject a slowdown of known size. Every one missed is a regression shipped.

A gate tuned never to cry wolf detects nothing; one tuned to catch 1% regressions fails every
build by Thursday. Either number alone is marketing.

Reproduce with:

```bash
perf-hunter self-check --repeats 40 --power-repeats 12 --json bench.json
```

Raw output is in `bench.json` beside this file.

---

## The machine, which is part of the result

```
Windows-11-10.0.26200-SP0, 16 cpus, Python 3.14.7
25 samples per side, 2% threshold, 5 workloads, 379s
```

**It was busy.** A GPU job and roughly three dozen other Python processes were running
throughout. That is why so many trials came back unreadable, and it is deliberately not
tidied away: a shared CI runner looks far more like this than a quiet laptop does, and a
false-alarm rate measured on an idle machine is not the rate anybody will get.

The five workloads are chosen to be unalike — a tight arithmetic loop that barely allocates,
dictionary building that leans on the allocator, sorting that is memory-bound, a regex scan
that spends its time inside C, and string joining.

---

## 1. False alarms: identical code on both sides

`a` and `b` are **the same function object**. There is nothing to find, so every `REGRESSION`
or `FASTER` verdict is wrong by construction — no modelling assumption is needed to say so.

| schedule | trials | false alarms | rate | noisy | worst reading |
|---|---:|---:|---:|---:|---:|
| `sequential` | 200 | **41** | **20.5%** | 61 | **+68.2%** |
| `interleaved` | 200 | **1** | **0.5%** | 98 | +19.9% |
| `paired` | 200 | **0** | **0%** | 94 | +33.9% |

These numbers are from the corrected verdict rule (a wide interval clear of the threshold is
called, not abstained on). Under the old rule many sequential drifts were hidden as `NOISY`;
they are real measured differences, just not about the software, and now they show.

### What the schedules are

| | |
|---|---|
| `sequential` | all 25 samples of A, then all 25 of B — what people write |
| `interleaved` | A, B, A, B — drift hits both sides equally |
| `paired` | A and B once per round, with the order inside each round shuffled |

### Reading it

**Sequential sampling reported a 68% slowdown on code that had not changed**, and one trial in five was a false alarm. That is the
headline. Everything that drifts on a machine drifts *between* the two blocks and lands
entirely on one side: the CPU clocks down as it heats, another process starts or finishes,
the allocator's arena grows. The statistics are not being fooled — they are being handed a
real difference that simply is not about the software.

**Interleaving all but removes it**: 1 false alarm in 200 trials (paired: 0), against 41.

**`paired` is not measurably better than `interleaved` here**, and this run does not claim it
is. 1 vs 0 in 200 is no difference. `paired` additionally randomises which side runs first within a round,
which guards against a systematic cost of going first; that effect is evidently below what
200 trials can resolve on this machine.

**The noisy counts are large** (61–98 of 200; this run's machine was busier than the last). Those are
trials where the interval was too wide to say anything. They are not failures of the
schedule; they are the loaded machine, showing up the same way in all three.

---

## 2. Power: a slowdown of known size

| asked for | **achieved** | trials | detected | power | missed | noisy |
|---:|---:|---:|---:|---:|---:|---:|
| 1% | 3.5% | 60 | 9 | 15% | 29 | 22 |
| 2% | 5.7% | 60 | 12 | 20% | 26 | 22 |
| 5% | 7.3% | 60 | 15 | 25% | 20 | 25 |
| 10% | 13.8% | 60 | 19 | 32% | 10 | 31 |
| 25% | 28.1% | 60 | 37 | 62% | 5 | 18 |

### Why there are two columns for the effect size

"Asked for" is the nominal factor. "Achieved" is what the injection actually cost, measured
separately with more samples than a trial uses.

They disagree, and not by a little: asking for 1% produced 3.5%, and asking for 2% produced
5.7% (a previous run got 2.9% and 0.9%). The injection repeats a fraction of the same work, and the wrapper's own bookkeeping —
a call, a counter, a comparison — is a meaningful share of a 1% target, differently so on
each workload. Reporting power against the nominal figure would have mislabelled the entire
curve, so the achieved column is the one that means anything.

### Reading it

**At a real 28% slowdown, 5 of 60 were missed** (called `SAME` or `FASTER`) on this loaded
machine.

**62% were *called*.** The other 18 came back `NOISY` — the interval was too wide
to support a verdict, so the tool refused rather than guessed. That is why `detected` and
`missed` are separate columns and why power alone would misrepresent it: this is not a gate
that lets regressions through, it is a gate that abstains when the machine is too loud.

**Below about 14% achieved, this machine cannot reliably see it.** Power sits at a third or less, and most of
the failures are genuine misses rather than abstentions. On a quiet, pinned machine the floor
would be lower; the honest statement is that the floor is a property of the hardware and has
to be measured there.

---

## 3. What these numbers do not say

- **They are this machine's, under load.** A false-alarm rate is a property of the hardware,
  the OS scheduler and what else is running. `self-check` exists so the number can be taken
  on the machine that will actually run the gate.
- **Five synthetic workloads are not a program.** They are small, they allocate little, and
  none of them does I/O. A real benchmark suite has slower, lumpier members where the noise
  behaves differently.
- **The injected slowdowns perturb only duration.** They repeat the same work, so they do not
  change cache behaviour or allocation patterns the way a real regression usually does. A
  real 5% regression may be easier to detect than a synthetic one, or harder.
- **200 trials resolve a 6% difference from 0%, and not much finer.** The claim that
  interleaving beats sequential is well supported. The claim that `paired` beats
  `interleaved` is not made, because this run cannot support it.
- **2% is a judgement call**, not a discovery. It is the default threshold, it is printed
  with every result, and it is a flag.

## Bugs these runs caught

| what it reported | what was true |
|---|---|
| sequential 3% vs interleaved 0%, from 30 trials | one false alarm out of thirty — not a comparison. At 200 trials it was 6.5% vs 0% (20.5% vs 0.5% after the verdict fix, on a busier machine) |
| power against injected effects of 1% and 2% | the injection actually delivered 2.9% and 0.9%; the curve was labelled with numbers that were not true |
| a 2x slowdown, interval +79% to +155%, reported `NOISY` and `--fail-on-regression` exited 0 | the width rule ran before the threshold rule; a wide interval clear of the threshold is now `REGRESSION`, and `NOISY` exits 3 under `--fail-on-regression` |
| a passing test of seed behaviour | `assert a != b or True` cannot fail |
| the permutation test returning p = 0.84 on an obvious difference | a two-valued fixture makes the median a step function; the test was measuring itself |
