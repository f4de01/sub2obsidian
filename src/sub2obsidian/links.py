"""链接规范化：从分享文本中提取链接，得到（平台, 平台内 ID, 规范链接）。"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

PLATFORM_NAMES = {"bilibili": "B站", "wechat": "公众号"}


@dataclass(frozen=True)
class SourceRef:
    """来源的身份：「平台 + 平台内 ID」唯一标识一个来源。"""

    platform: str
    platform_id: str
    url: str  # 规范链接

    @property
    def key(self) -> str:
        """「平台/平台内ID」：命令行中指称来源的写法，也是它在原始材料区的相对目录。"""
        return f"{self.platform}/{self.platform_id}"

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
WECHAT_HOST = "mp.weixin.qq.com"

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


def wechat_article_ref(biz: str, mid: str, idx: str, sn: str) -> SourceRef:
    """公众号文章由 __biz（公众号）、mid（群发）、idx（群发中的第几篇）唯一确定。

    平台内 ID 为「公众号数字 ID_mid_idx」（__biz 是数字 ID 的 base64）；规范链接还要带上
    sn 签名，缺了它微信不给看文章。
    """
    try:
        account = base64.b64decode(biz, validate=True).decode("ascii")
    except (binascii.Error, UnicodeDecodeError):
        account = ""
    if not account.isdigit():
        account = biz
    url = f"https://{WECHAT_HOST}/s?__biz={biz}&mid={mid}&idx={idx}&sn={sn}"
    return SourceRef("wechat", f"{account}_{mid}_{idx}", url)


def _wechat_long(url: str) -> SourceRef | None:
    """`/s?__biz=…&mid=…&idx=…&sn=…` 形态；从网页复制的链接里 & 可能写作 &amp;。"""
    parts = urlsplit(url.replace("&amp;", "&"))
    if parts.path not in ("/s", "/s/"):
        return None
    query = {key: values[0].strip() for key, values in parse_qs(parts.query).items()}
    if "__biz" not in query:
        return None
    biz, mid, idx, sn = (query.get(key, "") for key in ("__biz", "mid", "idx", "sn"))
    if not (biz and mid.isdigit() and idx.isdigit() and re.fullmatch(r"[0-9a-f]+", sn)):
        raise UnsupportedLink(f"公众号文章链接不完整（需要 __biz、mid、idx、sn）：{url}")
    return wechat_article_ref(biz, mid, idx, sn)


def _wechat_article(url: str, expand: Expander) -> SourceRef:
    """公众号文章的两种链接形态（短码与 __biz/mid/idx）统一为同一个平台内 ID。

    短码与 ID 之间没有固定的换算，只能打开短码链接、从文章页读出 __biz/mid/idx。
    在微信之外打开长链接常被要求验证（环境异常），短码链接则能直接打开，所以有短码链接时
    规范链接用（去掉参数的）短码链接，没有时才用长链接。
    """
    if (ref := _wechat_long(url)) is not None:
        return ref
    match = re.fullmatch(r"/s/([0-9A-Za-z_-]{6,})/?", urlsplit(url).path)
    if not match:
        raise UnsupportedLink(f"不是公众号文章链接：{url}")
    short = f"https://{WECHAT_HOST}/s/{match.group(1)}"
    target = expand("wechat", short)
    ref = _wechat_long(target)
    if ref is None:
        raise UnsupportedLink(f"公众号短链没有指向文章：{short} → {target}")
    return SourceRef(ref.platform, ref.platform_id, short)


def normalize(url: str, expand: Expander) -> SourceRef:
    """把一个链接规范化为来源身份；无法识别时抛 UnsupportedLink。

    短链交给 expand 解析一次；规范链接不带任何查询参数与锚点。
    """
    host = (urlsplit(url).hostname or "").lower()
    if SHORT_LINK_HOSTS.get(host) == "bilibili":
        return _bilibili_short(url, expand)
    if host == WECHAT_HOST:
        return _wechat_article(url, expand)
    ref = _bilibili_video(url)
    if ref is None:
        raise UnsupportedLink(f"无法识别的链接：{url}")
    return ref
