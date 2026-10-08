"""链接规范化：从分享文本中提取链接，得到（平台, 平台内 ID, 规范链接）。"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

PLATFORM_NAMES = {"bilibili": "B站", "douyin": "抖音", "wechat": "公众号"}


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


# 中文分享文本里链接常紧挨着中文与全角标点：链接只由可见的 ASCII 字符组成，遇到其他字符
# 即视为结束。方括号也不算在内，这样飞书消息里 [文字](链接) 写法的超链接也能取出。
# （字符类不能忽略大小写：i、k、s 等 ASCII 字母有非 ASCII 的大小写对应字符。）
_URL = re.compile(r"(?i:https?)://[^\x00-\x20\x7f-\U0010ffff<>\"'\[\]]+")

_BV = re.compile(r"(?i:bv)([0-9A-Za-z]{10})")
_BILIBILI_HOSTS = {"bilibili.com", "www.bilibili.com", "m.bilibili.com"}
# 短链域名 → 平台
SHORT_LINK_HOSTS = {"b23.tv": "bilibili", "bili2233.cn": "bilibili", "v.douyin.com": "douyin"}
WECHAT_HOST = "mp.weixin.qq.com"
_DOUYIN_HOSTS = {
    "douyin.com",
    "www.douyin.com",
    "m.douyin.com",
    "iesdouyin.com",
    "www.iesdouyin.com",
}
_AWEME_ID = r"(\d{15,21})"

# expand(platform, url)：由该平台的适配器把短链解析为完整链接（需要网络）。
Expander = Callable[[str, str], str]
# parts(platform, video_id)：由该平台的适配器查出视频有几个分P（需要网络）；
# 只对没指明分P的 B站 链接调用。
PartCounter = Callable[[str, str], int]


def extract_urls(text: str) -> list[str]:
    """文本中的链接，按出现先后；同一链接出现多次只算一次。"""
    return list(dict.fromkeys(match.rstrip(".,;:!?)") for match in _URL.findall(text)))


def bilibili_ref(bvid: str, part: int = 1) -> SourceRef:
    """B站 视频（的一个分P）→ 来源身份；不是 BV 号时抛 UnsupportedLink。

    多P视频的每个分P是一条来源：第 1 P（以及单P视频）的平台内 ID 就是 BV 号，规范链接不带
    参数；第 N P（N≥2）为「BV号_pN」，规范链接带 `?p=N`。BV 号统一为大写 BV 前缀。
    """
    match = _BV.fullmatch(bvid)
    if not match:
        raise UnsupportedLink(f"不是 B站 视频的 BV 号：{bvid}")
    bvid = "BV" + match.group(1)
    url = f"https://www.bilibili.com/video/{bvid}"
    if part == 1:
        return SourceRef("bilibili", bvid, url)
    return SourceRef("bilibili", f"{bvid}_p{part}", f"{url}?p={part}")


def bilibili_part(ref: SourceRef) -> tuple[str, int]:
    """B站 来源 → （BV 号, 分P序号）。"""
    bvid, _, part = ref.platform_id.partition("_p")
    return bvid, int(part) if part else 1


def _bilibili_parts(bv_suffix: str, query: str, parts: PartCounter) -> list[SourceRef]:
    """链接指明了分P（`?p=N`）就只是那一P；没指明时展开为该视频的全部分P。"""
    bvid = "BV" + bv_suffix
    part = (parse_qs(query).get("p") or [""])[0]
    if part.isdigit() and int(part) >= 1:
        return [bilibili_ref(bvid, int(part))]
    return [bilibili_ref(bvid, n) for n in range(1, parts("bilibili", bvid) + 1)]


def _bilibili_video(url: str, parts: PartCounter) -> list[SourceRef] | None:
    split = urlsplit(url)
    if (split.hostname or "").lower() not in _BILIBILI_HOSTS:
        return None
    match = re.fullmatch(r"/video/" + _BV.pattern + r"/?", split.path)
    if not match:
        raise UnsupportedLink(f"不是 B站 视频链接：{url}")
    return _bilibili_parts(match.group(1), split.query, parts)


def _bilibili_short(url: str, expand: Expander, parts: PartCounter) -> list[SourceRef]:
    short = urlsplit(url)
    if match := re.fullmatch(r"/" + _BV.pattern + r"/?", short.path):
        return _bilibili_parts(match.group(1), short.query, parts)
    target = expand("bilibili", url)
    refs = _bilibili_video(target, parts)
    if refs is None:
        raise UnsupportedLink(f"B站 短链没有指向视频：{url} → {target}")
    return refs


def _douyin_post(url: str) -> SourceRef | None:
    """抖音作品（视频或图文）由作品 ID（aweme_id）唯一确定。

    完整链接有 /video/<ID>、/note/<ID>（图文）、短链跳转到的 iesdouyin.com/share/<video|note|slides>/<ID>，
    以及网页版弹窗的 ?modal_id=<ID>。规范链接：图文为 /note/<ID>，其余为 /video/<ID>。
    """
    parts = urlsplit(url)
    if (parts.hostname or "").lower() not in _DOUYIN_HOSTS:
        return None
    if match := re.fullmatch(r"(?:/share)?/(video|note|slides)/" + _AWEME_ID + r"/?", parts.path):
        kind, aweme_id = match.groups()
    elif (modal := parse_qs(parts.query).get("modal_id")) and re.fullmatch(_AWEME_ID, modal[0]):
        kind, aweme_id = "video", modal[0]
    else:
        raise UnsupportedLink(f"不是抖音作品链接：{url}")
    return douyin_post_ref(aweme_id, note=kind in ("note", "slides"))


def douyin_post_ref(aweme_id: str, *, note: bool) -> SourceRef:
    """抖音作品的来源身份；规范链接：图文为 /note/<ID>，其余为 /video/<ID>。"""
    path = "note" if note else "video"
    return SourceRef("douyin", aweme_id, f"https://www.douyin.com/{path}/{aweme_id}")


def _douyin_short(url: str, expand: Expander) -> SourceRef:
    target = expand("douyin", url)
    try:
        ref = _douyin_post(target)
    except UnsupportedLink:
        ref = None
    if ref is None:
        raise UnsupportedLink(f"抖音短链没有指向作品：{url} → {target}")
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


def normalize(url: str, expand: Expander, parts: PartCounter) -> list[SourceRef]:
    """把一个链接规范化为它指向的来源；无法识别时抛 UnsupportedLink。

    短链交给 expand 解析一次；规范链接不带追踪参数与锚点。B站 多P视频的每个分P是一条来源：
    链接指明分P（`?p=N`）时只是那一P，没指明时由 parts 查出分P数、展开为全部分P。
    其他平台的链接只指向一条来源。
    """
    host = (urlsplit(url).hostname or "").lower()
    if SHORT_LINK_HOSTS.get(host) == "bilibili":
        return _bilibili_short(url, expand, parts)
    if SHORT_LINK_HOSTS.get(host) == "douyin":
        return [_douyin_short(url, expand)]
    if host == WECHAT_HOST:
        return [_wechat_article(url, expand)]
    refs = _bilibili_video(url, parts)
    if refs is not None:
        return refs
    ref = _douyin_post(url)
    if ref is None:
        raise UnsupportedLink(f"无法识别的链接：{url}")
    return [ref]
