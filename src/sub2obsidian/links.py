"""链接规范化：从分享文本中提取链接，得到（平台, 平台内 ID, 规范链接）。"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

PLATFORM_NAMES = {"bilibili": "B站"}


@dataclass(frozen=True)
class SourceRef:
    """来源的身份：「平台 + 平台内 ID」唯一标识一个来源。"""

    platform: str
    platform_id: str
    url: str  # 规范链接

    @property
    def display(self) -> str:
        return f"{PLATFORM_NAMES.get(self.platform, self.platform)} {self.platform_id}"


class UnsupportedLink(ValueError):
    """无法识别平台，或该平台的这类链接不是可采集的来源。"""


# 中文分享文本里链接常紧挨着全角标点，遇到它们即视为链接结束。
_URL = re.compile(r"https?://[^\s<>\"'，。！？；：、（）【】《》「」]+", re.IGNORECASE)

_BV = re.compile(r"(?i:bv)([0-9A-Za-z]{10})")
_BILIBILI_HOSTS = {"bilibili.com", "www.bilibili.com", "m.bilibili.com"}
# 短链域名 → 平台
SHORT_LINK_HOSTS = {"b23.tv": "bilibili", "bili2233.cn": "bilibili"}

# expand(platform, url)：由该平台的适配器把短链解析为完整链接（需要网络）。
Expander = Callable[[str, str], str]


def extract_urls(text: str) -> list[str]:
    return [match.rstrip(".,;:!?)]") for match in _URL.findall(text)]


def _bilibili_ref(bv_suffix: str) -> SourceRef:
    """BV 号统一为大写 BV 前缀；规范链接不带任何查询参数与锚点。"""
    bvid = "BV" + bv_suffix
    return SourceRef("bilibili", bvid, f"https://www.bilibili.com/video/{bvid}")


def _bilibili_video(url: str) -> SourceRef | None:
    parts = urlsplit(url)
    if (parts.hostname or "").lower() not in _BILIBILI_HOSTS:
        return None
    match = re.fullmatch(r"/video/" + _BV.pattern + r"/?", parts.path)
    if not match:
        raise UnsupportedLink(f"不是 B站 视频链接：{url}")
    return _bilibili_ref(match.group(1))


def _bilibili_short(url: str, expand: Expander) -> SourceRef:
    path = urlsplit(url).path
    if match := re.fullmatch(r"/" + _BV.pattern + r"/?", path):
        return _bilibili_ref(match.group(1))
    target = expand("bilibili", url)
    ref = _bilibili_video(target)
    if ref is None:
        raise UnsupportedLink(f"B站 短链没有指向视频：{url} → {target}")
    return ref


def normalize(url: str, expand: Expander) -> SourceRef:
    """把一个链接规范化为来源身份；无法识别时抛 UnsupportedLink。

    短链交给 expand 解析一次；规范链接不带任何查询参数与锚点。
    """
    host = (urlsplit(url).hostname or "").lower()
    if SHORT_LINK_HOSTS.get(host) == "bilibili":
        return _bilibili_short(url, expand)
    ref = _bilibili_video(url)
    if ref is None:
        raise UnsupportedLink(f"无法识别的链接：{url}")
    return ref
