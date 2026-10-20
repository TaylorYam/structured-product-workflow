"""把測試分成 n 份給 CI 同時跑（#69）：同一個測試檔一定在同一份，每次分法相同。

依各檔的測試數量由多到少，輪流放進目前測試最少的那一份（同數量時依檔名、份數小的優先），讓各份大致平均。
"""

from __future__ import annotations

from collections.abc import Mapping


def parse_shard(spec: str) -> tuple[int, int]:
    """`"2/4"` → (2, 4)；格式不對或超出範圍丟 ValueError。"""
    index, sep, total = spec.partition("/")
    if not sep or not index.isdigit() or not total.isdigit():
        raise ValueError(f"--shard 要寫成 i/n，例如 2/4：{spec!r}")
    i, n = int(index), int(total)
    if not 1 <= i <= n:
        raise ValueError(f"--shard 的 i 要在 1～n 之間：{spec!r}")
    return i, n


def assign_shards(counts: Mapping[str, int], total: int) -> list[set[str]]:
    """{測試檔: 測試數量} 分成 total 份，回傳每份的測試檔。"""
    shards: list[set[str]] = [set() for _ in range(total)]
    loads = [0] * total
    for path in sorted(counts, key=lambda p: (-counts[p], p)):
        target = min(range(total), key=lambda k: (loads[k], k))
        shards[target].add(path)
        loads[target] += counts[path]
    return shards
