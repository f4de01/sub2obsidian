"""公众号文章适配器的契约测试：用网页样本（文章页按真实页面结构构造，其余为录制）回放网络层。

样本说明见 tests/fixtures/wechat/README.md。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from sub2obsidian.links import SourceRef
from sub2obsidian.platforms import FetchFailed, SourceUnavailable
from sub2obsidian.wechat import Page, ResourceGone, WechatAdapter

FIXTURES = Path(__file__).parent / "fixtures" / "wechat"
SHORT = "https://mp.weixin.qq.com/s/ExampleShortLinkCode01"
LONG = (
    "https://mp.weixin.qq.com/s?__biz=MzAwMDAwMDAwMQ==&mid=2247480001&idx=1"
    "&sn=0123456789abcdef0123456789abcdef"
)
REF = SourceRef("wechat", "3000000001_2247480001_1", LONG)
CAPTCHA_URL = (
    "https://mp.weixin.qq.com/mp/wappoc_appmsgcaptcha?poc_token=HBExamplePocTokenForContractTests0000000"
    "&target_url=https%3A%2F%2Fmp.weixin.qq.com%2Fs%3F__biz%3DMzAwMDAwMDAwMg%3D%3D%26mid%3D2247490002"
    "%26idx%3D3%26sn%3Dfedcba9876543210fedcba9876543210"
)


def fixture(name: str) -> str:
    return (FIXTURES / f"{name}.html").read_text(encoding="utf-8")


@dataclass
class ReplayClient:
    """按样本回放公众号网络层：每次打开网页都返回同一份样本；记录请求以便断言。"""

    sample: str = "article"
    html: str | None = None  # 直接给出网页，代替样本
    final_url: str | None = None  # 跳转后的最终地址；缺省为请求的地址
    gone_images: set[int] = field(default_factory=set)  # 第几次下载的图片已不存在（从 1 数起）
    pages: list[str] = field(default_factory=list)
    downloads: list[str] = field(default_factory=list)

    def page(self, url: str) -> Page:
        self.pages.append(url)
        return Page(url=self.final_url or url, html=self.html or fixture(self.sample))

    def download(self, url: str) -> bytes:
        self.downloads.append(url)
        if len(self.downloads) in self.gone_images:
            raise ResourceGone(f"图片已不存在：{url}：HTTP 404")
        return b"image:" + url.encode()


def test_fetch_maps_article_page_to_source_metadata():
    fetched = WechatAdapter(ReplayClient()).fetch(REF)

    assert fetched.kind == "文章"
    assert fetched.title == "示例主题：给阳台菜园做一份浇水日志"
    assert fetched.author == "示例园艺笔记"  # 公众号名
    # ct = 1717200000 = 2024-06-01T00:00:00Z
    assert fetched.published == "2024-06-01T08:00:00+08:00"
    assert fetched.description == "这是一篇用于契约测试的虚构示例文章：给阳台菜园做一份浇水日志。"
    assert fetched.duration is None
    assert fetched.transcript is None
    assert fetched.byline == "示例作者"
    assert fetched.article is not None


def test_cover_is_downloaded_from_the_article_cover_image():
    client = ReplayClient()

    fetched = WechatAdapter(client).fetch(REF)

    cover_url = "https://mmbiz.qpic.cn/sz_mmbiz_jpg/ExampleFixtureCover00/0?wx_fmt=jpeg"
    assert fetched.cover is not None
    assert fetched.cover.name == "封面.jpg"
    assert fetched.cover.data == b"image:" + cover_url.encode()
    assert client.downloads[0] == cover_url


def test_body_is_markdown_with_images_rewritten_to_local_files():
    client = ReplayClient()

    fetched = WechatAdapter(client).fetch(REF)

    article = fetched.article
    assert article is not None
    markdown = article.markdown
    assert "<section" not in markdown and "<span" not in markdown
    assert "mmbiz.qpic.cn" not in markdown  # 没有残留微信图床地址
    assert "浇水日志" in markdown
    assert "**摘要**" in markdown
    # 小节标题各占一行；排版工具留下的空装饰标题（<h2>&nbsp;</h2>）不留空的「##」
    assert re.search(r"^\*\*记录与整理\*\*$", markdown, re.M)
    assert re.search(r"^常见问题和应对办法$", markdown, re.M)
    assert not re.search(r"^#+\s*$", markdown, re.M)
    assert re.search(r"^- \*\*土壤湿度记录", markdown, re.M)  # 列表
    names = [image.name for image in article.images]
    assert len(names) == len(set(names)) >= 10
    assert names[:2] == ["图01.png", "图02.jpg"]  # 后缀取自图床的 wx_fmt
    for image in article.images:
        assert f"![]({image.name})" in markdown
        assert image.data.startswith(b"image:https://mmbiz.qpic.cn/")
    # 每张正文图片按出现顺序下载一次（第一次下载是封面）
    assert len(client.downloads) == 1 + len(names)
    positions = [markdown.index(f"]({name})") for name in names]
    assert positions == sorted(positions)


def test_code_snippet_keeps_one_line_per_code_element():
    """微信编辑器的代码块每行一个 <code>，语言写在 data-lang。"""
    page = fixture("article")
    start = page.index('id="js_content"')
    start = page.index(">", start) + 1
    snippet = (
        '<section class="code-snippet__fix code-snippet__js"><pre class="code-snippet__js" '
        'data-lang="python"><code><span class="code-snippet_outer">import os</span></code>'
        '<code><span class="code-snippet_outer">print(os.getcwd())</span></code></pre></section>'
    )
    client = ReplayClient(html=page[:start] + snippet + page[start:])

    fetched = WechatAdapter(client).fetch(REF)

    assert fetched.article is not None
    assert "```python\nimport os\nprint(os.getcwd())\n```" in fetched.article.markdown


def test_image_gone_from_the_image_host_keeps_its_original_address():
    client = ReplayClient(gone_images={2})  # 第 1 次下载是封面，第 2 次是正文第一张图

    fetched = WechatAdapter(client).fetch(REF)

    article = fetched.article
    assert article is not None
    gone = client.downloads[1]
    assert f"![]({gone})" in article.markdown
    assert article.images[0].name == "图01.jpg"  # 后面的图照常编号


def test_short_link_expands_to_the_long_link_and_the_page_is_reused_for_fetching():
    client = ReplayClient()
    adapter = WechatAdapter(client)

    target = adapter.expand_short_link(SHORT)
    fetched = adapter.fetch(REF)

    assert target == LONG
    assert fetched.title == "示例主题：给阳台菜园做一份浇水日志"
    assert client.pages == [SHORT]  # 采集时不再打开第二次


def test_verification_page_is_a_retryable_failure_not_unavailable():
    client = ReplayClient(sample="verification", final_url=CAPTCHA_URL)

    with pytest.raises(FetchFailed, match="微信要求验证（环境异常）") as raised:
        WechatAdapter(client).fetch(REF)

    assert not isinstance(raised.value, SourceUnavailable)


def test_verification_page_on_a_long_link_suggests_submitting_the_short_link():
    """在微信之外打开长链接常被要求验证（录制时即如此），短码链接则能直接打开。"""
    client = ReplayClient(sample="verification", final_url=CAPTCHA_URL)

    with pytest.raises(FetchFailed, match=r"改用文章的短链接（https://mp\.weixin\.qq\.com/s/…）"):
        WechatAdapter(client).fetch(REF)


def test_verification_page_on_a_short_link_is_a_retryable_failure():
    client = ReplayClient(sample="verification", final_url=CAPTCHA_URL)

    with pytest.raises(FetchFailed, match="微信要求验证") as raised:
        WechatAdapter(client).expand_short_link(SHORT)

    assert "短链接" not in str(raised.value)


@pytest.mark.parametrize(
    ("sample", "notice"),
    [("deleted", "该内容已被发布者删除"), ("violation", "此内容因违规无法查看")],
)
def test_deleted_or_removed_article_is_reported_unavailable(sample: str, notice: str):
    client = ReplayClient(sample=sample)

    with pytest.raises(SourceUnavailable) as raised:
        WechatAdapter(client).fetch(REF)

    assert str(raised.value) == notice


def test_deleted_article_behind_a_short_link_is_reported_unavailable():
    """短码链接打开就是删除提示页：读不出文章 ID，也无从留存根。"""
    client = ReplayClient(sample="deleted")

    with pytest.raises(SourceUnavailable) as raised:
        WechatAdapter(client).expand_short_link(SHORT)

    assert str(raised.value) == f"公众号文章已失效：{SHORT}，该内容已被发布者删除"


def test_parameter_error_page_is_a_retryable_failure_naming_the_link():
    client = ReplayClient(sample="parameter_error")

    with pytest.raises(FetchFailed, match="参数错误") as raised:
        WechatAdapter(client).fetch(REF)

    assert not isinstance(raised.value, SourceUnavailable)
    assert LONG in str(raised.value)
