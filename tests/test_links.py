"""链接规范化：B站 多P视频的每个分P是一条独立的来源。

表驱动：同一张表里写明「链接 → 规范化后的来源（平台内 ID、规范链接）」；短链与分P数由假的
平台适配器给出（规范化本身不访问网络）。
"""

from __future__ import annotations

import pytest

from sub2obsidian.links import normalize

BV = "BV1bK411W797"  # 23P 的公开视频
SINGLE = "BV1GJ411x7h7"  # 单P视频
VIDEO = f"https://www.bilibili.com/video/{BV}"

PARTS = {BV: 23, "BV1Ab411c7De": 3}  # 其余视频都是单P
SHORT_LINKS = {
    "https://b23.tv/Part002": f"{VIDEO}?p=2&share_source=copy_link&share_medium=android",
    "https://b23.tv/Whole01": f"{VIDEO}?share_source=copy_link",
}


class FakeAdapter:
    """假的平台适配器：解析短链、报告视频有几个分P，并记录被问过的视频。"""

    def __init__(self) -> None:
        self.counted: list[str] = []

    def expand(self, platform: str, url: str) -> str:
        return SHORT_LINKS[url]

    def parts(self, platform: str, video_id: str) -> int:
        self.counted.append(video_id)
        return PARTS.get(video_id, 1)


def keys(url: str) -> list[tuple[str, str]]:
    """规范化后的（平台内 ID, 规范链接）。"""
    adapter = FakeAdapter()
    return [(ref.platform_id, ref.url) for ref in normalize(url, adapter.expand, adapter.parts)]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        pytest.param(
            f"https://www.bilibili.com/video/{SINGLE}?spm_id_from=333.1007",
            [(SINGLE, f"https://www.bilibili.com/video/{SINGLE}")],
            id="单P视频：平台内ID就是BV号",
        ),
        pytest.param(
            f"https://www.bilibili.com/video/{SINGLE}?p=1",
            [(SINGLE, f"https://www.bilibili.com/video/{SINGLE}")],
            id="单P视频带p=1：同一来源",
        ),
        pytest.param(f"{VIDEO}?p=1", [(BV, VIDEO)], id="多P第1P：BV号本身"),
        pytest.param(
            f"{VIDEO}/?p=2&vd_source=0123abcd#reply1",
            [(f"{BV}_p2", f"{VIDEO}?p=2")],
            id="多P第2P：BV号_p2，规范链接保留p",
        ),
        pytest.param(
            f"https://m.bilibili.com/video/bv{BV[2:]}?p=23",
            [(f"{BV}_p23", f"{VIDEO}?p=23")],
            id="移动端小写bv带p",
        ),
        pytest.param(
            "https://b23.tv/Part002", [(f"{BV}_p2", f"{VIDEO}?p=2")], id="短链跳转到带p的链接"
        ),
        pytest.param(
            "https://www.bilibili.com/video/BV1Ab411c7De",
            [
                ("BV1Ab411c7De", "https://www.bilibili.com/video/BV1Ab411c7De"),
                ("BV1Ab411c7De_p2", "https://www.bilibili.com/video/BV1Ab411c7De?p=2"),
                ("BV1Ab411c7De_p3", "https://www.bilibili.com/video/BV1Ab411c7De?p=3"),
            ],
            id="不带p的多P链接展开为全部分P",
        ),
        pytest.param(
            f"{VIDEO}?p=abc",
            [(BV, VIDEO)] + [(f"{BV}_p{n}", f"{VIDEO}?p={n}") for n in range(2, 24)],
            id="p不是正整数时当作不带p",
        ),
        pytest.param(
            f"{VIDEO}?p=0",
            [(BV, VIDEO)] + [(f"{BV}_p{n}", f"{VIDEO}?p={n}") for n in range(2, 24)],
            id="p为0时当作不带p",
        ),
    ],
)
def test_bilibili_links_normalize_to_one_source_per_part(url: str, expected):
    assert keys(url) == expected


def test_short_link_without_p_to_a_multi_part_video_expands_to_every_part():
    assert [key for key, _ in keys("https://b23.tv/Whole01")] == [BV] + [
        f"{BV}_p{n}" for n in range(2, 24)
    ]


def test_part_count_is_only_asked_for_links_that_name_no_part():
    adapter = FakeAdapter()

    normalize(f"{VIDEO}?p=2", adapter.expand, adapter.parts)
    normalize(f"https://www.bilibili.com/video/{SINGLE}", adapter.expand, adapter.parts)

    assert adapter.counted == [SINGLE]


@pytest.mark.parametrize(
    "url",
    [
        pytest.param("https://www.douyin.com/video/7312345678901234567", id="抖音"),
        pytest.param(
            "https://mp.weixin.qq.com/s?__biz=MzA3MDM3NjE5NQ==&mid=2650000001&idx=1&sn=abcdef0123",
            id="公众号",
        ),
    ],
)
def test_other_platforms_still_normalize_to_exactly_one_source(url: str):
    adapter = FakeAdapter()

    [ref] = normalize(url, adapter.expand, adapter.parts)

    assert ref.platform in ("douyin", "wechat")
    assert adapter.counted == []

