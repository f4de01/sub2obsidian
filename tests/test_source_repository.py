"""来源仓储：来源状态按状态机转换，非法转换被拒绝且不改动原始材料。

状态机（规格 #1）：待筛 → 已通过 / 已拒绝；已通过 → 已采集；视频：已采集 → 已转写；
可编译（文章或图文已采集，或视频已转写）→ 已编译；任意状态 → 已失效。
"""

from itertools import product
from pathlib import Path

import pytest

from raw import read_metadata, source_dir
from sub2obsidian.links import SourceRef
from sub2obsidian.sources import IllegalTransition, Kind, Origin, SourceRepository, Status

S = Status
REF = SourceRef("bilibili", "BV1GJ411x7h7", "https://www.bilibili.com/video/BV1GJ411x7h7")

# 到达每个状态的合法路径
PATHS = {
    S.PENDING: (S.PENDING, []),
    S.REJECTED: (S.PENDING, [S.REJECTED]),
    S.APPROVED: (S.APPROVED, []),
    S.COLLECTED: (S.APPROVED, [S.COLLECTED]),
    S.TRANSCRIBED: (S.APPROVED, [S.COLLECTED, S.TRANSCRIBED]),
    S.UNAVAILABLE: (S.APPROVED, [S.UNAVAILABLE]),
}

LEGAL = {
    Kind.VIDEO: {
        (S.PENDING, S.APPROVED),
        (S.PENDING, S.REJECTED),
        (S.APPROVED, S.COLLECTED),
        (S.COLLECTED, S.TRANSCRIBED),
        (S.TRANSCRIBED, S.COMPILED),
        (S.PENDING, S.UNAVAILABLE),
        (S.REJECTED, S.UNAVAILABLE),
        (S.APPROVED, S.UNAVAILABLE),
        (S.COLLECTED, S.UNAVAILABLE),
        (S.TRANSCRIBED, S.UNAVAILABLE),
        (S.COMPILED, S.UNAVAILABLE),
    },
    Kind.ARTICLE: {
        (S.PENDING, S.APPROVED),
        (S.PENDING, S.REJECTED),
        (S.APPROVED, S.COLLECTED),
        (S.COLLECTED, S.COMPILED),
        (S.PENDING, S.UNAVAILABLE),
        (S.REJECTED, S.UNAVAILABLE),
        (S.APPROVED, S.UNAVAILABLE),
        (S.COLLECTED, S.UNAVAILABLE),
        (S.COMPILED, S.UNAVAILABLE),
    },
}


def source_in(vault: Path, kind: Kind, status: Status):
    repo = SourceRepository(vault)
    if status is S.COMPILED:
        before = S.TRANSCRIBED if kind is Kind.VIDEO else S.COLLECTED
        source = source_in(vault, kind, before)
        repo.transition(source, S.COMPILED)
        return source
    initial, steps = PATHS[status]
    source = repo.create(REF, kind=kind, origin=Origin.PULL, status=initial)
    for step in steps:
        repo.transition(source, step)
    return source


def reachable(kind: Kind) -> list[Status]:
    return [s for s in Status if not (kind is Kind.ARTICLE and s is S.TRANSCRIBED)]


CASES = [
    pytest.param(kind, old, new, id=f"{kind}-{old}→{new}")
    for kind in (Kind.VIDEO, Kind.ARTICLE)
    for old, new in product(reachable(kind), Status)
]


@pytest.mark.parametrize(("kind", "old", "new"), CASES)
def test_status_transitions_follow_the_state_machine(tmp_path: Path, kind, old, new):
    vault = tmp_path / "知识库"
    source = source_in(vault, kind, old)
    repo = SourceRepository(vault)

    if (old, new) in LEGAL[kind]:
        repo.transition(source, new)
        assert read_metadata(vault, "bilibili", REF.platform_id)["来源状态"] == new
    else:
        before = (source_dir(vault, "bilibili", REF.platform_id) / "元数据.md").read_bytes()
        with pytest.raises(IllegalTransition):
            repo.transition(source, new)
        after = (source_dir(vault, "bilibili", REF.platform_id) / "元数据.md").read_bytes()
        assert after == before


@pytest.mark.parametrize("status", [s for s in Status if s not in (S.PENDING, S.APPROVED)])
def test_new_source_can_only_start_pending_or_approved(tmp_path: Path, status):
    with pytest.raises(IllegalTransition):
        SourceRepository(tmp_path).create(REF, kind=Kind.VIDEO, origin=Origin.PUSH, status=status)
    assert not source_dir(tmp_path, "bilibili", REF.platform_id).exists()


def test_raw_material_files_are_never_overwritten(tmp_path: Path):
    repo = SourceRepository(tmp_path)
    source = repo.create(REF, kind=Kind.VIDEO, origin=Origin.PUSH, status=S.APPROVED)
    repo.add_file(source, "封面.jpg", b"original")

    with pytest.raises(FileExistsError):
        repo.add_file(source, "封面.jpg", b"different")

    assert (source_dir(tmp_path, "bilibili", REF.platform_id) / "封面.jpg").read_bytes() == b"original"
