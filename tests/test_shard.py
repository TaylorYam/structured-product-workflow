"""CI 分份（#69）：每個測試檔剛好分到一份、分法固定、各份大致平均。"""

from __future__ import annotations

import pytest

from shard import assign_shards, parse_shard


def test_every_file_lands_in_exactly_one_shard() -> None:
    counts = {f"tests/checker/test_{n}.py": n for n in range(1, 30)}

    shards = assign_shards(counts, 4)

    assert len(shards) == 4
    assert set().union(*shards) == set(counts)
    assert sum(len(s) for s in shards) == len(counts)


def test_same_input_gives_the_same_split() -> None:
    counts = {"b.py": 5, "a.py": 5, "c.py": 9, "d.py": 1}

    assert assign_shards(counts, 2) == assign_shards(dict(reversed(counts.items())), 2)


def test_shards_are_roughly_balanced_by_test_count() -> None:
    counts = {"big.py": 10, "m1.py": 6, "m2.py": 4, "s1.py": 3, "s2.py": 3, "s3.py": 2}

    loads = [sum(counts[f] for f in s) for s in assign_shards(counts, 2)]

    assert loads == [15, 13]  # 10→甲、6→乙、4→乙、3→甲（同數量取前面那份）、3→乙、2→甲


def test_more_shards_than_files_leaves_some_empty() -> None:
    assert assign_shards({"only.py": 3}, 3) == [{"only.py"}, set(), set()]


@pytest.mark.parametrize("spec", ["2", "0/4", "5/4", "a/4", "2/x", ""])
def test_bad_shard_spec_is_rejected(spec: str) -> None:
    with pytest.raises(ValueError):
        parse_shard(spec)


def test_shard_spec() -> None:
    assert parse_shard("2/4") == (2, 4)
