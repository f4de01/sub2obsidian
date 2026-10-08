"""推送 (Push)：`capture <公众号文章链接或分享文本>` 把一篇公众号文章作为来源落入原始材料。"""

from pathlib import Path

import pytest

from raw import read_metadata, source_dir, source_dirs
from vault_git import git
from sub2obsidian.platforms import Article, Asset, FetchedSource

BIZ = "Mzg4ODA2NDMzMw=="  # base64("3888064333")
MID = "2247499360"
IDX = "1"
SN = "7f578d217699fabba9d56e29354ce065"
ID = "3888064333_2247499360_1"
CANONICAL = f"https://mp.weixin.qq.com/s?__biz={BIZ}&mid={MID}&idx={IDX}&sn={SN}"
SHORT = "https://mp.weixin.qq.com/s/JJHlJsWEqFG77LdzhvzDNw"

COVER = b"\xff\xd8\xff\xe0 fake cover"
FIGURE_1 = b"\x89PNG\r\n\x1a\n fake figure 1"
FIGURE_2 = b"\xff\xd8\xff\xe0 fake figure 2"
TITLE = "通过增强PDF结构识别，革新检索增强生成技术(RAG)"


def article() -> FetchedSource:
    return FetchedSource(
        kind="文章",
        title=TITLE,
        author="北京庖丁科技",
        published="2024-01-31T14:37:04+08:00",
        description="ChatDOC PDF解析器显著提升了RAG系统的回答效果。",
        cover=Asset(name="封面.jpg", data=COVER),
        article=Article(
            markdown=(
                "**摘要**\n\n检索增强生成（RAG）可以更好地利用领域专家知识。\n\n"
                "![](图01.png)\n\n**图 1** 检索增强生成的工作流\n\n"
                "## PDF 解析和分块\n\n![](图02.jpg)\n"
            ),
            images=[Asset(name="图01.png", data=FIGURE_1), Asset(name="图02.jpg", data=FIGURE_2)],
            byline="创新而务实的",
        ),
    )


@pytest.fixture
def initialized(run, vault: Path) -> Path:
    run.run("init", str(vault))
    return vault


def test_capture_article_lands_markdown_body_local_images_and_metadata(run, wechat, initialized):
    vault = initialized
    wechat.articles[ID] = article()

    result = run.run("capture", CANONICAL)

    assert result.exit_code == 0, result.output
    meta = read_metadata(vault, "wechat", ID)
    assert meta["平台"] == "wechat"
    assert meta["平台内ID"] == ID
    assert meta["规范链接"] == CANONICAL
    assert meta["类型"] == "文章"
    assert meta["标题"] == TITLE
    assert meta["作者"] == "北京庖丁科技"  # 公众号名
    assert meta["发布时间"] == "2024-01-31T14:37:04+08:00"
    assert meta["时长"] is None
    assert meta["采集途径"] == "推送"
    assert meta["来源状态"] == "已采集"
    assert meta["失败原因"] is None
    directory = source_dir(vault, "wechat", ID)
    assert (directory / "封面.jpg").read_bytes() == COVER
    assert (directory / "图01.png").read_bytes() == FIGURE_1
    assert (directory / "图02.jpg").read_bytes() == FIGURE_2
    body = (directory / "正文.md").read_text(encoding="utf-8")
    assert body.startswith(f"# {TITLE}\n")
    assert "公众号：北京庖丁科技" in body
    assert "作者：创新而务实的" in body
    assert f"[原文]({CANONICAL})" in body
    assert "检索增强生成（RAG）可以更好地利用领域专家知识。" in body
    assert "![](图01.png)" in body
    assert "![](图02.jpg)" in body
    assert "已采集" in result.output


def test_captured_article_is_compilable_without_transcription(run, wechat, initialized):
    wechat.articles[ID] = article()
    run.run("capture", CANONICAL)

    result = run.run("status")

    assert result.exit_code == 0, result.output
    assert f"wechat/{ID}  文章  {TITLE}" in result.output


def test_capture_commits_body_images_and_metadata_on_their_own(run, wechat, initialized):
    vault = initialized
    wechat.articles[ID] = article()

    run.run("capture", CANONICAL)

    message = git(vault, "log", "-1", "--format=%s").strip()
    assert message == f"capture: 公众号 {ID} {TITLE}"
    committed = git(vault, "show", "--name-only", "--format=", "HEAD").split()
    assert sorted(committed) == sorted(
        f"原始材料/wechat/{ID}/{name}"
        for name in ["元数据.md", "正文.md", "封面.jpg", "图01.png", "图02.jpg"]
    )


@pytest.mark.parametrize(
    "submission",
    [
        pytest.param(CANONICAL, id="长链接"),
        pytest.param(SHORT, id="短码链接"),
        pytest.param(f"{SHORT}?scene=1&poc_token=HBRzx2qjaW4mfCp5vUEqkjfifiAvdVj7", id="短码带参数"),
        pytest.param(
            f"https://mp.weixin.qq.com/s?__biz={BIZ}&mid={MID}&idx={IDX}&sn={SN}"
            "&chksm=c3e0a1b2f4d5e6c7d8&scene=21#wechat_redirect",
            id="长链接带追踪参数",
        ),
        pytest.param(
            f"https://mp.weixin.qq.com/s/?__biz=Mzg4ODA2NDMzMw%3D%3D&amp;mid={MID}&amp;idx={IDX}"
            f"&amp;sn={SN}#rd",
            id="网页复制的转义长链接",
        ),
        pytest.param(
            f"http://mp.weixin.qq.com/s?sn={SN}&idx={IDX}&mid={MID}&__biz={BIZ}", id="http参数乱序"
        ),
        pytest.param(f"【{TITLE}】 {SHORT}", id="分享文本"),
        pytest.param(f"这篇讲得好{SHORT}，推荐", id="夹杂文字"),
    ],
)
def test_same_article_submitted_in_any_link_form_is_kept_once(
    run, wechat, initialized, submission: str
):
    vault = initialized
    wechat.articles[ID] = article()
    wechat.short_links[SHORT] = CANONICAL
    run.run("capture", CANONICAL)

    result = run.run("capture", submission)

    assert result.exit_code == 0, result.output
    assert source_dirs(vault) == [source_dir(vault, "wechat", ID)]
    assert wechat.fetched == [ID]
    assert read_metadata(vault, "wechat", ID)["规范链接"] == CANONICAL
    assert "已存在" in result.output


def test_first_capture_through_short_link_keeps_the_short_link_as_canonical_link(
    run, wechat, initialized
):
    """微信外打开长链接常被要求验证，短码链接则能直接打开：有短码链接时以它为规范链接。"""
    vault = initialized
    wechat.articles[ID] = article()
    wechat.short_links[SHORT] = CANONICAL + "&chksm=c3e0a1b2&scene=21#wechat_redirect"

    result = run.run("capture", f"【{TITLE}】 {SHORT}?scene=1#rd")

    assert result.exit_code == 0, result.output
    meta = read_metadata(vault, "wechat", ID)
    assert meta["平台内ID"] == ID
    assert meta["规范链接"] == SHORT
    assert f"[原文]({SHORT})" in (source_dir(vault, "wechat", ID) / "正文.md").read_text(
        encoding="utf-8"
    )


def test_long_link_that_needs_verification_can_be_retried_with_the_short_link(
    run, wechat, initialized
):
    vault = initialized
    wechat.articles[ID] = article()
    wechat.failures[ID] = "微信要求验证（环境异常）"
    wechat.short_links[SHORT] = CANONICAL
    run.run("capture", CANONICAL)

    result = run.run("capture", SHORT)

    assert result.exit_code == 0, result.output
    assert source_dirs(vault) == [source_dir(vault, "wechat", ID)]
    assert read_metadata(vault, "wechat", ID)["来源状态"] == "已采集"
    assert wechat.fetched == [ID, ID]


def test_deleted_article_becomes_unavailable_stub_and_is_not_retried(run, wechat, initialized):
    vault = initialized
    wechat.unavailable[ID] = "该内容已被发布者删除"

    first = run.run("capture", CANONICAL)
    second = run.run("capture", CANONICAL)

    assert first.exit_code == 0, first.output
    assert "已失效" in first.output
    meta = read_metadata(vault, "wechat", ID)
    assert meta["来源状态"] == "已失效"
    assert meta["类型"] == "文章"
    assert meta["失败原因"] == "该内容已被发布者删除"
    assert sorted(p.name for p in source_dir(vault, "wechat", ID).iterdir()) == ["元数据.md"]
    assert wechat.fetched == [ID]
    assert "已存在" in second.output


def test_verification_page_is_a_retryable_failure(run, wechat, initialized):
    vault = initialized
    wechat.articles[ID] = article()
    wechat.failures[ID] = "微信要求验证（环境异常），请稍后再试"

    failed = run.run("capture", CANONICAL)

    assert failed.exit_code != 0
    assert "微信要求验证（环境异常）" in failed.output
    meta = read_metadata(vault, "wechat", ID)
    assert meta["来源状态"] == "已通过"
    assert meta["失败原因"] == "微信要求验证（环境异常），请稍后再试"

    retried = run.run("capture", CANONICAL)

    assert retried.exit_code == 0, retried.output
    assert read_metadata(vault, "wechat", ID)["来源状态"] == "已采集"
    assert wechat.fetched == [ID, ID]


def test_short_link_that_cannot_be_opened_is_reported_and_nothing_lands(run, wechat, initialized):
    vault = initialized

    result = run.run("capture", SHORT)

    assert result.exit_code != 0
    assert SHORT in result.output
    assert source_dirs(vault) == []


def test_deleted_article_behind_a_short_link_is_reported_without_a_stub(
    run, wechat, initialized
):
    """短码链接打开就是删除提示页时读不出文章 ID，无从留存根，只报告。"""
    vault = initialized
    wechat.unavailable_short_links[SHORT] = "该内容已被发布者删除"

    result = run.run("capture", SHORT)

    assert result.exit_code != 0
    assert "该内容已被发布者删除" in result.output
    assert source_dirs(vault) == []


@pytest.mark.parametrize(
    "submission",
    [
        pytest.param(f"https://mp.weixin.qq.com/s?__biz={BIZ}&mid={MID}&idx={IDX}", id="缺sn"),
        pytest.param(
            f"https://mp.weixin.qq.com/mp/profile_ext?action=home&__biz={BIZ}", id="公众号主页"
        ),
        pytest.param("https://mp.weixin.qq.com/", id="首页"),
    ],
)
def test_link_that_is_not_a_complete_article_link_is_reported(
    run, wechat, initialized, submission: str
):
    vault = initialized

    result = run.run("capture", submission)

    assert result.exit_code != 0
    assert source_dirs(vault) == []
    assert wechat.fetched == []
