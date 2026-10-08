"""同步 (`sync`)：读取收件箱 → 采集已通过的来源 → 转写 → 原始材料单独提交 git → 输出汇总。

收件箱、平台适配器与转写引擎都是假实现（见 conftest.py）；飞书收件箱的契约测试见
test_feishu_inbox.py。
"""

from pathlib import Path

import pytest

from raw import read_metadata, source_dir, source_dirs
from vault_git import commit_subjects, git
from sub2obsidian.platforms import Article, Asset, FetchedSource
from sub2obsidian.transcript import Segment

BV = "BV1GJ411x7h7"
BV_LINK = f"https://www.bilibili.com/video/{BV}"
BV_TITLE = "【大模型】RAG 到底是什么？10 分钟讲清楚"
WX_ID = "3888064333_2247499360_1"
WX_LINK = (
    "https://mp.weixin.qq.com/s?__biz=Mzg4ODA2NDMzMw==&mid=2247499360&idx=1"
    "&sn=7f578d217699fabba9d56e29354ce065"
)
WX_TITLE = "通过增强PDF结构识别，革新检索增强生成技术(RAG)"
ASR = [Segment(start=0.0, end=2.6, text="大家好，今天聊 RAG")]


def video(title: str = BV_TITLE) -> FetchedSource:
    return FetchedSource(
        kind="视频",
        title=title,
        author="某知识区UP主",
        published="2024-05-01T20:00:00+08:00",
        duration=612,
        cover=Asset(name="封面.jpg", data=b"\xff\xd8 fake jpeg"),
    )


def article() -> FetchedSource:
    return FetchedSource(
        kind="文章",
        title=WX_TITLE,
        author="北京庖丁科技",
        published="2024-01-31T14:37:04+08:00",
        article=Article(markdown="检索增强生成（RAG）可以更好地利用领域专家知识。\n", images=[]),
    )


@pytest.fixture
def initialized(run, vault: Path, credentials) -> Path:
    run.run("init", str(vault))
    credentials.login("bilibili")
    credentials.login("douyin")  # 抖音也会被拉取收藏
    return vault


def offer_video(bilibili, transcriber, bv: str = BV, title: str = BV_TITLE) -> None:
    """平台上有这个没有字幕的视频，转写引擎能转写它的音频。"""
    bilibili.videos[bv] = video(title)
    transcriber.segments[f"audio:{bv}"] = ASR


def test_sync_collects_pushed_bilibili_and_wechat_links_as_approved_and_transcribes(
    run, inbox, bilibili, wechat, transcriber, initialized
):
    vault = initialized
    offer_video(bilibili, transcriber)
    wechat.articles[WX_ID] = article()
    inbox.push(BV_LINK, WX_LINK)

    result = run.run("sync")

    assert result.exit_code == 0, result.output
    video_meta = read_metadata(vault, "bilibili", BV)
    assert video_meta["采集途径"] == "推送"
    assert video_meta["标题"] == BV_TITLE
    assert video_meta["来源状态"] == "已转写"
    assert "[00:00:00] 大家好，今天聊 RAG" in (
        source_dir(vault, "bilibili", BV) / "口播稿.md"
    ).read_text(encoding="utf-8")
    article_meta = read_metadata(vault, "wechat", WX_ID)
    assert article_meta["采集途径"] == "推送"
    assert article_meta["类型"] == "文章"
    assert article_meta["来源状态"] == "已采集"
    assert (source_dir(vault, "wechat", WX_ID) / "正文.md").is_file()


def test_sync_handles_pushed_douyin_share_text_video_and_note(
    run, inbox, douyin, transcriber, credentials, initialized
):
    from test_capture_douyin import (
        ASR as DOUYIN_ASR,
        NOTE_ID,
        NOTE_LINK,
        SHARE_TEXT,
        SHORT,
        VIDEO_ID,
        douyin_note,
        douyin_video,
    )

    vault = initialized
    douyin.posts[VIDEO_ID] = douyin_video()
    douyin.posts[NOTE_ID] = douyin_note()
    douyin.short_links[SHORT] = f"https://www.iesdouyin.com/share/video/{VIDEO_ID}/?region=CN"
    transcriber.segments[f"audio:{VIDEO_ID}"] = DOUYIN_ASR
    inbox.push(SHARE_TEXT, f"这篇图文不错 {NOTE_LINK}")

    result = run.run("sync")

    assert result.exit_code == 0, result.output
    video_meta = read_metadata(vault, "douyin", VIDEO_ID)
    assert video_meta["采集途径"] == "推送"
    assert video_meta["来源状态"] == "已转写"
    assert (source_dir(vault, "douyin", VIDEO_ID) / "口播稿.md").is_file()
    note_meta = read_metadata(vault, "douyin", NOTE_ID)
    assert note_meta["类型"] == "图文"
    assert note_meta["来源状态"] == "已采集"
    assert (source_dir(vault, "douyin", NOTE_ID) / "正文.md").is_file()
    assert [content for content, _ in transcriber.calls] == [f"audio:{VIDEO_ID}"]
    assert "新增来源 2" in result.output
    assert git(vault, "log", "-1", "--format=%s").startswith("sync:")
    assert git(vault, "status", "--porcelain") == ""


def test_links_pushed_while_offline_are_read_by_next_sync_and_read_messages_are_not_reprocessed(
    run, inbox, bilibili, transcriber, initialized
):
    vault = initialized
    other, later = "BV1Ab411c7De", "BV1Xy411z7W9"
    for bv, title in [(BV, BV_TITLE), (other, "第二个视频"), (later, "第三个视频")]:
        offer_video(bilibili, transcriber, bv, title)
    inbox.push(BV_LINK)
    run.run("sync")
    # 电脑关机期间又推送了两条
    inbox.push(f"https://www.bilibili.com/video/{other}", f"https://www.bilibili.com/video/{later}")

    result = run.run("sync")

    assert result.exit_code == 0, result.output
    assert source_dirs(vault) == [
        source_dir(vault, "bilibili", other),
        source_dir(vault, "bilibili", BV),
        source_dir(vault, "bilibili", later),
    ]
    assert read_metadata(vault, "bilibili", other)["来源状态"] == "已转写"
    assert read_metadata(vault, "bilibili", later)["来源状态"] == "已转写"
    assert "新消息 2 条" in result.output
    assert BV not in result.output  # 已读的消息不再处理
    assert bilibili.fetched == [BV, other, later]


def test_sync_with_nothing_new_changes_nothing(run, inbox, bilibili, transcriber, initialized):
    vault = initialized
    offer_video(bilibili, transcriber)
    inbox.push(BV_LINK)
    run.run("sync")
    head = git(vault, "rev-parse", "HEAD")

    result = run.run("sync")

    assert result.exit_code == 0, result.output
    assert "新消息 0 条" in result.output
    assert git(vault, "rev-parse", "HEAD") == head
    assert bilibili.fetched == [BV]


B23 = "https://b23.tv/AbCd123"


@pytest.mark.parametrize(
    "message",
    [
        pytest.param(BV_LINK, id="纯链接"),
        pytest.param(f"这个讲得好 {BV_LINK}?spm_id_from=333.1007&vd_source=0123abcd 推荐", id="前后有文字"),
        pytest.param(f"这个讲得好{BV_LINK}，推荐", id="紧挨全角标点"),
        pytest.param(f"{BV_LINK}这个讲得好", id="紧挨中文"),
        pytest.param(f"推荐（{BV_LINK}）", id="全角括号"),
        pytest.param(f"推荐({BV_LINK})。", id="半角括号"),
        pytest.param(f"[{BV_LINK}]({BV_LINK})", id="飞书超链接写法"),
        pytest.param(f"[B站视频]({BV_LINK})", id="带文字的超链接"),
        pytest.param(f"【{BV_TITLE}-哔哩哔哩】 {B23}", id="App分享文本"),
        pytest.param(f"先记一下\n\n{B23}\n回头看", id="多行"),
    ],
)
def test_link_is_extracted_from_message_mixed_with_text(
    run, inbox, bilibili, transcriber, initialized, message: str
):
    vault = initialized
    offer_video(bilibili, transcriber)
    bilibili.short_links[B23] = f"{BV_LINK}?share_source=copy_link"
    inbox.push(message)

    result = run.run("sync")

    assert result.exit_code == 0, result.output
    assert source_dirs(vault) == [source_dir(vault, "bilibili", BV)]
    assert read_metadata(vault, "bilibili", BV)["来源状态"] == "已转写"


def test_every_link_in_one_message_becomes_a_source(run, inbox, bilibili, wechat, transcriber, initialized):
    vault = initialized
    offer_video(bilibili, transcriber)
    wechat.articles[WX_ID] = article()
    inbox.push(f"两篇一起看：{BV_LINK}\n还有这篇 {WX_LINK}")

    result = run.run("sync")

    assert result.exit_code == 0, result.output
    assert source_dirs(vault) == [source_dir(vault, "bilibili", BV), source_dir(vault, "wechat", WX_ID)]


def test_bad_links_do_not_block_the_batch_and_summary_lists_failures_with_reasons(
    run, inbox, bilibili, wechat, transcriber, initialized
):
    vault = initialized
    gone, flaky = "BV1Ab411c7De", "BV1Xy411z7W9"
    offer_video(bilibili, transcriber)
    bilibili.unavailable[gone] = "稿件不可见（62002）"
    offer_video(bilibili, transcriber, flaky, "风控的视频")
    bilibili.failures[flaky] = "请求过于频繁（412），请稍后再试"
    wechat.articles[WX_ID] = article()
    inbox.push(
        "https://example.com/some/page",
        f"https://www.bilibili.com/video/{gone}",
        f"https://www.bilibili.com/video/{flaky}",
        BV_LINK,
        "收藏一下，忘了贴链接",
        WX_LINK,
    )

    result = run.run("sync")

    assert result.exit_code != 0
    assert read_metadata(vault, "bilibili", BV)["来源状态"] == "已转写"
    assert read_metadata(vault, "wechat", WX_ID)["来源状态"] == "已采集"
    assert read_metadata(vault, "bilibili", gone)["来源状态"] == "已失效"
    failed = read_metadata(vault, "bilibili", flaky)
    assert failed["来源状态"] == "已通过"  # 失败的来源保持原状态
    assert failed["失败原因"] == "请求过于频繁（412），请稍后再试"
    assert "消息里没有链接，跳过：收藏一下，忘了贴链接" in result.output
    summary = result.output[result.output.index("sync 汇总") :]
    assert "新增来源 4" in summary
    assert "已转写 1" in summary
    assert "已采集 1" in summary
    assert "已失效 1" in summary
    assert f"B站 {gone}：稿件不可见（62002）" in summary
    assert "失败 2" in summary
    assert "无法识别的链接：https://example.com/some/page" in summary
    assert f"B站 {flaky}" in summary and "请求过于频繁（412）" in summary


def test_source_that_failed_to_collect_is_retried_by_the_next_sync(
    run, inbox, bilibili, transcriber, initialized
):
    vault = initialized
    offer_video(bilibili, transcriber)
    bilibili.failures[BV] = "请求过于频繁（412），请稍后再试"
    inbox.push(BV_LINK)
    run.run("sync")

    result = run.run("sync")  # 收件箱里没有新消息

    assert result.exit_code == 0, result.output
    meta = read_metadata(vault, "bilibili", BV)
    assert meta["来源状态"] == "已转写"
    assert meta["失败原因"] is None
    assert bilibili.fetched == [BV, BV]


def test_short_link_that_failed_to_resolve_is_retried_by_the_next_sync(
    run, inbox, bilibili, transcriber, initialized
):
    vault = initialized
    offer_video(bilibili, transcriber)
    inbox.push(f"【{BV_TITLE}-哔哩哔哩】 {B23}")

    failed = run.run("sync")  # 短链暂时解析不了

    assert failed.exit_code != 0
    assert B23 in failed.output
    assert source_dirs(vault) == []

    bilibili.short_links[B23] = BV_LINK
    retried = run.run("sync")

    assert retried.exit_code == 0, retried.output
    assert "新消息 0 条" in retried.output
    assert read_metadata(vault, "bilibili", BV)["来源状态"] == "已转写"


def test_failed_transcription_keeps_source_collected_and_is_listed(
    run, inbox, bilibili, transcriber, initialized
):
    vault = initialized
    offer_video(bilibili, transcriber)
    transcriber.failures[f"audio:{BV}"] = "音频解码失败"
    inbox.push(BV_LINK)

    result = run.run("sync")

    assert result.exit_code != 0
    assert read_metadata(vault, "bilibili", BV)["来源状态"] == "已采集"
    summary = result.output[result.output.index("sync 汇总") :]
    assert "失败 1" in summary
    assert "音频解码失败" in summary


def test_raw_material_changes_get_their_own_commit_without_wiki_changes(
    run, inbox, bilibili, wechat, transcriber, initialized
):
    vault = initialized
    offer_video(bilibili, transcriber)
    wechat.articles[WX_ID] = article()
    (vault / "Wiki" / "概念" / "草稿.md").write_text("未提交的 Wiki 改动", encoding="utf-8")
    (vault / "index.md").write_text("编辑中的 index", encoding="utf-8")
    inbox.push(BV_LINK, WX_LINK)
    commits_before = len(commit_subjects(vault))

    run.run("sync")

    assert len(commit_subjects(vault)) == commits_before + 1
    message = git(vault, "log", "-1", "--format=%B")
    assert message.startswith("sync: 同步 2 个来源")
    assert f"- B站 {BV} {BV_TITLE}" in message
    assert f"- 公众号 {WX_ID} {WX_TITLE}" in message
    committed = git(vault, "show", "--name-only", "--format=", "HEAD").split()
    assert committed and all(path.startswith("原始材料/") for path in committed)
    assert f"原始材料/bilibili/{BV}/口播稿.md" in committed
    assert f"原始材料/wechat/{WX_ID}/正文.md" in committed
    assert sorted(git(vault, "status", "--porcelain").splitlines()) == [" M index.md", "?? Wiki/"]


def test_inbox_cursor_is_kept_in_user_config_dir_not_in_the_vault(
    run, inbox, bilibili, transcriber, initialized, user_config_dir: Path
):
    vault = initialized
    offer_video(bilibili, transcriber)
    inbox.push(BV_LINK)

    run.run("sync")

    assert (user_config_dir / "state" / "inbox.toml").is_file()
    assert git(vault, "status", "--porcelain", "--ignored") == ""


def test_inbox_read_failure_is_reported_and_approved_sources_are_still_collected(
    run, inbox, bilibili, transcriber, initialized
):
    vault = initialized
    offer_video(bilibili, transcriber)
    bilibili.failures[BV] = "网络错误"
    inbox.push(BV_LINK)
    run.run("sync")  # 来源已登记，采集失败
    inbox.push(f"https://www.bilibili.com/video/BV1Ab411c7De")
    inbox.failure = "连接飞书失败：timed out"

    failed = run.run("sync")

    assert failed.exit_code != 0
    assert "读取收件箱失败：连接飞书失败：timed out" in failed.output
    assert read_metadata(vault, "bilibili", BV)["来源状态"] == "已转写"

    inbox.failure = None
    offer_video(bilibili, transcriber, "BV1Ab411c7De", "第二个视频")
    recovered = run.run("sync")

    assert recovered.exit_code == 0, recovered.output
    assert "新消息 1 条" in recovered.output  # 读取失败时游标不动，这一条没有丢
    assert read_metadata(vault, "bilibili", "BV1Ab411c7De")["来源状态"] == "已转写"


def test_sync_without_configured_inbox_still_collects_approved_sources(
    run, inbox, bilibili, transcriber, initialized
):
    vault = initialized
    offer_video(bilibili, transcriber)
    bilibili.failures[BV] = "网络错误"
    run.run("capture", BV_LINK)  # 保底入口提交的来源，采集失败
    inbox.configured = False

    result = run.run("sync")

    assert result.exit_code == 0, result.output
    assert "跳过收件箱：飞书收件箱尚未配置" in result.output
    assert read_metadata(vault, "bilibili", BV)["来源状态"] == "已转写"


def test_missing_ffmpeg_stops_transcription_but_collected_sources_are_committed(
    run, inbox, bilibili, transcriber, initialized
):
    from sub2obsidian.tools import MissingTool

    vault = initialized
    offer_video(bilibili, transcriber)

    def no_ffmpeg(ref, directory):
        raise MissingTool("找不到 ffmpeg")

    bilibili.download_audio = no_ffmpeg
    inbox.push(BV_LINK)

    result = run.run("sync")

    assert result.exit_code != 0
    assert "sync 中止：找不到 ffmpeg" in result.output
    assert read_metadata(vault, "bilibili", BV)["来源状态"] == "已采集"
    assert git(vault, "log", "-1", "--format=%s").startswith("sync:")
    assert git(vault, "status", "--porcelain") == ""


def test_sync_requires_an_initialized_vault(run, inbox, vault: Path):
    inbox.push(BV_LINK)

    result = run.run("sync", "--vault", str(vault))

    assert result.exit_code != 0
    assert "请先执行 sub2obsidian init" in result.output
    assert not vault.exists()
