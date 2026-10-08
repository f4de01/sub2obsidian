"""公众号文章平台适配器：按链接直接抓取文章网页（无需登录），提取正文转为 Markdown，
图片下载到来源目录并改写为本地引用。

不读取微信「收藏」（ADR-0002）：公众号来源一律由用户推送链接。
网络层（WechatClient）与解析分开：契约测试用录制的网页样本回放网络层，验证解析与判定。
"""

from __future__ import annotations

import datetime as dt
import html as htmllib
import random
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup, Tag
from markdownify import MarkdownConverter

from sub2obsidian.links import SourceRef, wechat_article_ref
from sub2obsidian.platforms import Article, Asset, FetchedSource, FetchFailed, SourceUnavailable

PLATFORM = "wechat"
BEIJING = dt.timezone(dt.timedelta(hours=8))

# 文章已删除、违规下架、账号被封等：转为「已失效」。文字取自微信的提示页。
UNAVAILABLE_NOTICES = [
    "该内容已被发布者删除",
    "此内容因违规无法查看",
    "此内容发送失败无法查看",
    "涉嫌违反相关法律法规和政策",
    "被投诉且经审核涉嫌侵权",
    "涉嫌过度营销、骚扰用户",
    "经审核涉嫌不实信息",
    "冒名侵权",
    "帐号已被屏蔽",
    "账号已被屏蔽",
    "此帐号已自主注销",
    "此账号已自主注销",
    "帐号已迁移",
    "账号已迁移",
]
# 访问过于频繁时微信跳到验证页（环境异常、去验证）：可重试的失败
VERIFICATION_MARKERS = ["wappoc_appmsgcaptcha", "secitptpage/verify", "环境异常", "去验证"]

IMAGE_SUFFIXES = {"jpeg": ".jpg", "jpg": ".jpg", "png": ".png", "gif": ".gif", "webp": ".webp"}


class ResourceGone(FetchFailed):
    """资源已不存在（HTTP 4xx），重试也拿不到。"""


@dataclass(frozen=True)
class Page:
    url: str  # 跟随跳转后的最终地址
    html: str


class WechatClient(Protocol):
    """公众号网络层。"""

    def page(self, url: str) -> Page:
        """打开网页（跟随跳转）；网络失败抛 FetchFailed。"""
        ...

    def download(self, url: str) -> bytes:
        """下载图片；网络失败抛 FetchFailed，资源不存在抛 ResourceGone。"""
        ...


class WechatAdapter:
    platform = PLATFORM

    def __init__(self, client: WechatClient | None = None) -> None:
        self.client = client or HttpWechatClient()
        # 解析短码链接时已经打开过的文章页，采集时直接复用，少一次请求
        self._pages: dict[str, BeautifulSoup] = {}

    def expand_short_link(self, url: str) -> str:
        """短码链接只能打开文章页、读出 __biz/mid/idx/sn，再换成长链接。"""
        try:
            soup = self._article_page(url)
        except SourceUnavailable as error:
            # 读不出文章 ID，无从留失效存根：报告时带上链接
            raise SourceUnavailable(f"公众号文章已失效：{url}，{error}") from error
        values = [_js_var(soup, name) for name in ("biz", "mid", "idx", "sn")]
        if not all(values):
            raise FetchFailed(f"公众号文章页中没有找到文章 ID（可能是微信改版）：{url}")
        ref = wechat_article_ref(*values)
        self._pages[ref.platform_id] = soup
        return ref.url

    def fetch(self, ref: SourceRef) -> FetchedSource:
        soup = self._pages.pop(ref.platform_id, None)
        if soup is None:
            soup = self._article_page(ref.url)
        content = soup.find(id="js_content")
        assert isinstance(content, Tag)
        title = _meta(soup, "og:title") or _text(soup.find(id="activity-name")) or ref.platform_id
        return FetchedSource(
            kind="文章",
            title=title,
            author=_text(soup.find(id="js_name")) or _js_var(soup, "nickname"),
            published=_published(_js_var(soup, "ct")),
            description=_meta(soup, "description") or "",
            cover=self._cover(_js_var(soup, "msg_cdn_url") or _meta(soup, "og:image")),
            article=self._article(content, byline=_meta(soup, "author")),
        )

    def download_audio(self, ref: SourceRef, directory: Path) -> Path:
        raise NotImplementedError("公众号文章没有音频，不需要转写")

    def _article_page(self, url: str) -> BeautifulSoup:
        """打开文章页；验证页报可重试的失败，删除、违规等提示页报来源已失效。"""
        page = self.client.page(url)
        soup = BeautifulSoup(page.html, "html.parser")
        if isinstance(soup.find(id="js_content"), Tag):
            return soup
        if any(marker in page.url or marker in page.html for marker in VERIFICATION_MARKERS):
            message = "微信要求验证（环境异常），请稍后再试"
            if "__biz=" in url:
                message += (
                    "；在微信之外打开长链接常被要求验证，"
                    "可在微信中「复制链接」，改用文章的短链接（https://mp.weixin.qq.com/s/…）重新提交"
                )
            raise FetchFailed(message)
        for notice in UNAVAILABLE_NOTICES:
            if notice in page.html:
                raise SourceUnavailable(_notice_sentence(soup, notice))
        if "参数错误" in page.html:
            raise FetchFailed(f"微信提示「参数错误」，请确认链接完整：{url}")
        title = _text(soup.find("title")) or "无标题"
        raise FetchFailed(f"无法识别的公众号页面（{title}），可能是微信改版：{url}")

    def _cover(self, url: str | None) -> Asset | None:
        if not url:
            return None
        return Asset(name=f"封面{_suffix(url)}", data=self.client.download(url))

    def _article(self, content: Tag, byline: str | None) -> Article:
        for element in content.find_all(["script", "style"]):
            element.decompose()
        images: list[Asset] = []
        names: dict[str, str] = {}  # 图片地址 → 本地文件名（同一张图只存一份）
        for img in content.find_all("img"):
            url = _image_url(img)
            if url is None:
                img.decompose()
                continue
            if url not in names:
                name = f"图{len(images) + 1:02d}{_suffix(url)}"
                try:
                    images.append(Asset(name=name, data=self.client.download(url)))
                except ResourceGone:
                    name = url  # 图床上已经没有这张图：保留原地址，不阻塞整篇文章
                names[url] = name
            img.attrs = {"src": names[url], "alt": img.get("alt", "")}
        return Article(markdown=_markdown(content), images=images, byline=byline or None)


def _js_var(soup: BeautifulSoup, name: str) -> str | None:
    """文章页内联脚本中的 `var name = "…" || "…"`（可能包在 htmlDecode() 里）：取第一个非空值。"""
    pattern = re.compile(
        rf"""var {name} = (?:htmlDecode\()?((?:"[^"]*"|'[^']*')(?:\s*\|\|\s*(?:"[^"]*"|'[^']*'))*)"""
    )
    for script in soup.find_all("script"):
        for match in pattern.finditer(script.get_text()):
            for value in re.findall(r""""([^"]*)"|'([^']*)'""", match.group(1)):
                if text := htmllib.unescape("".join(value)).strip():
                    return text
    return None


def _meta(soup: BeautifulSoup, name: str) -> str | None:
    tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
    if not isinstance(tag, Tag):
        return None
    content = tag.get("content")
    return content.strip() or None if isinstance(content, str) else None


def _text(tag: Any) -> str | None:
    return tag.get_text(strip=True) or None if isinstance(tag, Tag) else None


def _published(ct: str | None) -> str | None:
    if not ct or not ct.isdigit():
        return None
    return dt.datetime.fromtimestamp(int(ct), BEIJING).isoformat()


def _notice_sentence(soup: BeautifulSoup, notice: str) -> str:
    """提示页上包含该提示的那句话（如「该内容已被发布者删除」）。"""
    for element in soup.find_all(string=re.compile(re.escape(notice))):
        if element.find_parent(["script", "style", "title"]) is None:
            return " ".join(element.split())
    return notice


def _image_url(img: Tag) -> str | None:
    """正文图片懒加载，真实地址在 data-src；src 多为占位图。"""
    for attribute in ("data-src", "src"):
        value = img.get(attribute)
        if isinstance(value, str):
            for prefix in ("https://", "http://", "//"):
                if value.startswith(prefix):
                    return "https://" + value.removeprefix(prefix)
    return None


def _suffix(url: str) -> str:
    """微信图床用 wx_fmt 参数标明图片格式。"""
    fmt = parse_qs(urlsplit(url).query).get("wx_fmt", [""])[0].lower()
    return IMAGE_SUFFIXES.get(fmt, ".jpg")


class _Converter(MarkdownConverter):
    def convert_pre(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        """微信代码块每行一个 <code>：逐行拼接，避免各行粘成一行。"""
        lines = [code.get_text() for code in el.find_all("code", recursive=False)]
        if len(lines) > 1:
            text = "\n".join(lines)
        return super().convert_pre(el, text, parent_tags)


def _code_language(pre: Tag) -> str:
    language = pre.get("data-lang")
    return language if isinstance(language, str) else ""


def _markdown(content: Tag) -> str:
    converter = _Converter(
        heading_style="ATX", bullets="-", code_language_callback=_code_language, escape_misc=False
    )
    text = converter.convert_soup(content).replace("\u00a0", " ")
    text = text.replace("****", "")  # 相邻或嵌套的加粗
    lines = [line.rstrip() for line in text.splitlines()]
    lines = [line for line in lines if not re.fullmatch(r"#{1,6}|\*\*|\*", line.strip())]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + "\n"


class HttpWechatClient:
    """真实网络层：urllib 打开文章页与图片；文章页之间随机间隔数秒，图片间隔较短。"""

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    TIMEOUT = 20

    def __init__(
        self,
        page_interval: tuple[float, float] = (3.0, 6.0),
        image_interval: tuple[float, float] = (0.3, 1.0),
    ) -> None:
        self.page_interval = page_interval
        self.image_interval = image_interval
        self._last_page = 0.0
        self._last_image = 0.0

    @staticmethod
    def _wait(last: float, interval: tuple[float, float]) -> float:
        wait = last + random.uniform(*interval) - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        return time.monotonic()

    def _open(self, url: str, referer: str | None = None):
        headers = dict(self.HEADERS)
        if referer:
            headers["Referer"] = referer
        request = urllib.request.Request(url, headers=headers)
        return urllib.request.urlopen(request, timeout=self.TIMEOUT)

    def page(self, url: str) -> Page:
        self._last_page = self._wait(self._last_page, self.page_interval)
        try:
            with self._open(url) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return Page(url=response.url, html=response.read().decode(charset, "replace"))
        except (urllib.error.URLError, TimeoutError) as error:
            raise FetchFailed(f"打开公众号文章失败：{url}：{error}") from error

    def download(self, url: str) -> bytes:
        self._last_image = self._wait(self._last_image, self.image_interval)
        try:
            with self._open(url, referer="https://mp.weixin.qq.com/") as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if 400 <= error.code < 500:
                raise ResourceGone(f"图片已不存在：{url}：HTTP {error.code}") from error
            raise FetchFailed(f"下载图片失败：{url}：HTTP {error.code}") from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise FetchFailed(f"下载图片失败：{url}：{error}") from error
