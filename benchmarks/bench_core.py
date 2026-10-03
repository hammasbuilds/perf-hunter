"""Example benchmarks for `perf-hunter run` / `compare`: zero-argument callables named bench_*.

Each one returns its result so the work cannot be optimised away.
"""


def bench_sum_of_squares():
    return sum(i * i for i in range(20_000))


def bench_sort_shuffled():
    data = [(i * 7919) % 10_007 for i in range(5_000)]
    return sorted(data)


def bench_dict_build():
    return {str(i): i for i in range(5_000)}


def bench_string_join():
    return ",".join(str(i) for i in range(5_000))
