"""行为测试的公共夹具：测试只通过「CLI 命令 + 知识库目录」观察行为。

四个外部端口（平台适配器、收件箱、转写引擎、凭据提供者）在这里都有假实现。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from sub2obsidian.cli import Ports, cli
from sub2obsidian.config import UserConfig
from sub2obsidian.credentials import LoginRequired
from sub2obsidian.inbox import InboxBatch, InboxError, InboxNotConfigured
from sub2obsidian.links import SourceRef
from sub2obsidian.platforms import (
    Favorite,
    FavoriteList,
    FavoritesPage,
    FetchedSource,
    FetchFailed,
    SourceUnavailable,
)
from sub2obsidian.transcript import Segment
from sub2obsidian.transcription import TranscriptionFailed


@dataclass
class FakeLauncher:
    """替换真实的 Obsidian 启动器，记录被打开的 URI。"""

    opened: list[str] = field(default_factory=list)

    def open(self, uri: str) -> None:
        self.opened.append(uri)


@dataclass
class FakeCredentials:
    """替换 Playwright 凭据提供者：登录即在用户配置目录写一份 cookies.txt。"""

    secret: str = "SESSDATA-假的会话密钥-0123456789"
    logged_in: set[str] = field(default_factory=set)
    logins: list[str] = field(default_factory=list)

    def _cookies_path(self, platform: str) -> Path:
        return UserConfig.default().credentials_dir / f"{platform}.cookies.txt"

    def login(self, platform: str) -> None:
        self.logins.append(platform)
        self.logged_in.add(platform)
        path = self._cookies_path(platform)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f".bilibili.com\tTRUE\t/\tTRUE\t0\tSESSDATA\t{self.secret}\n", encoding="utf-8")

    def cookies_file(self, platform: str) -> Path:
        if platform not in self.logged_in:
            raise LoginRequired(platform)
        return self._cookies_path(platform)

    def cookie_string(self, platform: str) -> str:
        if platform not in self.logged_in:
            raise LoginRequired(platform)
        return f"sessionid={self.secret}"


@dataclass
class FakeBilibili:
    """替换 B站 适配器：按 BV 号返回预置的采集结果，并记录每次采集。

    要求登录：未经 FakeCredentials 登录时，采集报「请重新登录」。
    """

    credentials: FakeCredentials
    platform: str = "bilibili"
    videos: dict[str, FetchedSource] = field(default_factory=dict)
    unavailable: dict[str, str] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)
    short_links: dict[str, str] = field(default_factory=dict)
    fetched: list[str] = field(default_factory=list)
    audio_failures: dict[str, str] = field(default_factory=dict)
    audio_unavailable: dict[str, str] = field(default_factory=dict)
    audio_files: list[Path] = field(default_factory=list)  # 每次下载的临时音频
    # 拉取：收藏列表名 → 其中的收藏（从新到旧），按 page_size 分页，游标是页码
    favorite_folders: dict[str, list[Favorite]] = field(default_factory=dict)
    page_size: int = 3
    # (收藏列表名, 游标) → 读这一页时抛出的异常（只抛一次）
    page_failures: dict[tuple[str, str | None], BaseException] = field(default_factory=dict)
    pages_read: list[tuple[str, str | None]] = field(default_factory=list)

    def favorite_lists(self) -> list[FavoriteList]:
        self.credentials.cookies_file(self.platform)
        return [FavoriteList(id=name, title=name) for name in self.favorite_folders]

    def favorites(self, list_id: str, cursor: str | None) -> FavoritesPage:
        self.credentials.cookies_file(self.platform)
        if (list_id, cursor) in self.page_failures:
            raise self.page_failures.pop((list_id, cursor))
        self.pages_read.append((list_id, cursor))
        page = int(cursor or 0)
        items = self.favorite_folders[list_id]
        start = page * self.page_size
        more = start + self.page_size < len(items)
        return FavoritesPage(items[start : start + self.page_size], str(page + 1) if more else None)

    def expand_short_link(self, url: str) -> str:
        if url not in self.short_links:
            raise FetchFailed(f"短链解析失败：{url}")
        return self.short_links[url]

    def fetch(self, ref: SourceRef) -> FetchedSource:
        self.credentials.cookies_file(self.platform)
        self.fetched.append(ref.platform_id)
        if ref.platform_id in self.failures:
            raise FetchFailed(self.failures.pop(ref.platform_id))
        if ref.platform_id in self.unavailable:
            raise SourceUnavailable(self.unavailable[ref.platform_id])
        return self.videos[ref.platform_id]

    def download_audio(self, ref: SourceRef, directory: Path) -> Path:
        if ref.platform_id in self.audio_failures:
            raise FetchFailed(self.audio_failures.pop(ref.platform_id))
        if ref.platform_id in self.audio_unavailable:
            raise SourceUnavailable(self.audio_unavailable[ref.platform_id])
        audio = directory / f"{ref.platform_id}.m4a"
        audio.write_bytes(f"audio:{ref.platform_id}".encode())
        self.audio_files.append(audio)
        return audio


@dataclass
class FakeWechat:
    """替换公众号文章适配器：按平台内 ID 返回预置的文章，并记录每次采集。无需登录。"""

    platform: str = "wechat"
    articles: dict[str, FetchedSource] = field(default_factory=dict)
    unavailable: dict[str, str] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)
    short_links: dict[str, str] = field(default_factory=dict)  # 短码链接 → 长链接
    unavailable_short_links: dict[str, str] = field(default_factory=dict)  # 短码链接 → 失效原因
    fetched: list[str] = field(default_factory=list)

    def expand_short_link(self, url: str) -> str:
        if url in self.unavailable_short_links:
            raise SourceUnavailable(self.unavailable_short_links[url])
        if url not in self.short_links:
            raise FetchFailed(f"无法解析公众号短链：{url}")
        return self.short_links[url]

    def fetch(self, ref: SourceRef) -> FetchedSource:
        self.fetched.append(ref.platform_id)
        if ref.platform_id in self.failures:
            raise FetchFailed(self.failures.pop(ref.platform_id))
        if ref.platform_id in self.unavailable:
            raise SourceUnavailable(self.unavailable[ref.platform_id])
        return self.articles[ref.platform_id]

    def download_audio(self, ref: SourceRef, directory: Path) -> Path:  # pragma: no cover
        raise AssertionError("文章没有音频")


@dataclass
class FakeDouyin:
    """替换抖音适配器：按作品 ID 返回预置的视频或图文，并记录每次采集与音频下载。

    要求登录：未经 FakeCredentials 登录时，采集报「请重新登录 抖音」。
    """

    credentials: FakeCredentials
    platform: str = "douyin"
    posts: dict[str, FetchedSource] = field(default_factory=dict)
    unavailable: dict[str, str] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)
    short_links: dict[str, str] = field(default_factory=dict)  # 短链 → 跳转到的完整链接
    fetched: list[str] = field(default_factory=list)
    audio_files: list[Path] = field(default_factory=list)  # 每次下载的临时音频

    def expand_short_link(self, url: str) -> str:
        if url not in self.short_links:
            raise FetchFailed(f"短链解析失败：{url}")
        return self.short_links[url]

    def fetch(self, ref: SourceRef) -> FetchedSource:
        self.credentials.cookie_string(self.platform)
        self.fetched.append(ref.platform_id)
        if ref.platform_id in self.failures:
            raise FetchFailed(self.failures.pop(ref.platform_id))
        if ref.platform_id in self.unavailable:
            raise SourceUnavailable(self.unavailable[ref.platform_id])
        return self.posts[ref.platform_id]

    def download_audio(self, ref: SourceRef, directory: Path) -> Path:
        assert self.posts[ref.platform_id].kind == "视频", "图文没有音频"
        audio = directory / f"{ref.platform_id}.wav"
        audio.write_bytes(f"audio:{ref.platform_id}".encode())
        self.audio_files.append(audio)
        return audio


@dataclass
class FakeTranscriber:
    """替换 faster-whisper：按音频内容返回预置分段，并记录每次调用收到的音频与术语。"""

    origin: str = "假转写引擎 v1"
    segments: dict[str, list[Segment]] = field(default_factory=dict)  # 音频内容 → 分段
    failures: dict[str, str] = field(default_factory=dict)  # 音频内容 → 失败原因
    calls: list[tuple[str, list[str]]] = field(default_factory=list)  # (音频内容, 术语)

    def transcribe(self, audio: Path, terms: Sequence[str]) -> list[Segment]:
        content = audio.read_text(encoding="utf-8")
        self.calls.append((content, list(terms)))
        if content in self.failures:
            raise TranscriptionFailed(self.failures[content])
        return self.segments[content]


@dataclass
class FakeInbox:
    """替换飞书收件箱：messages 是用户按先后推送的消息；游标是已读到的条数。"""

    messages: list[str] = field(default_factory=list)
    failure: str | None = None  # 读取失败的原因
    configured: bool = True

    def push(self, *texts: str) -> None:
        self.messages.extend(texts)

    def read(self, cursor: str | None) -> InboxBatch:
        if not self.configured:
            raise InboxNotConfigured("飞书收件箱尚未配置")
        if self.failure is not None:
            raise InboxError(self.failure)
        start = int(cursor) if cursor else 0
        return InboxBatch(self.messages[start:], str(len(self.messages)))


@dataclass
class Cli:
    launcher: FakeLauncher
    credentials: FakeCredentials
    bilibili: FakeBilibili
    wechat: FakeWechat
    douyin: FakeDouyin
    transcriber: FakeTranscriber
    inbox: FakeInbox

    def run(self, *args: str) -> Result:
        ports = Ports(
            launcher=self.launcher,
            credentials=self.credentials,
            adapters={
                "bilibili": self.bilibili,
                "wechat": self.wechat,
                "douyin": self.douyin,
            },
            transcriber=self.transcriber,
            inbox=self.inbox,
        )
        return CliRunner().invoke(cli, list(args), obj=ports, catch_exceptions=False)


@pytest.fixture(autouse=True)
def user_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 %APPDATA% 指向临时目录，测试绝不碰真实的用户配置目录。"""
    appdata = tmp_path / "AppData" / "Roaming"
    appdata.mkdir(parents=True)
    monkeypatch.setenv("APPDATA", str(appdata))
    return appdata / "sub2obsidian"


@pytest.fixture
def launcher() -> FakeLauncher:
    return FakeLauncher()


@pytest.fixture
def credentials() -> FakeCredentials:
    return FakeCredentials()


@pytest.fixture
def bilibili(credentials: FakeCredentials) -> FakeBilibili:
    return FakeBilibili(credentials=credentials)


@pytest.fixture
def wechat() -> FakeWechat:
    return FakeWechat()


@pytest.fixture
def douyin(credentials: FakeCredentials) -> FakeDouyin:
    return FakeDouyin(credentials=credentials)


@pytest.fixture
def transcriber() -> FakeTranscriber:
    return FakeTranscriber()


@pytest.fixture
def inbox() -> FakeInbox:
    return FakeInbox()


@pytest.fixture
def run(
    launcher: FakeLauncher,
    credentials: FakeCredentials,
    bilibili: FakeBilibili,
    wechat: FakeWechat,
    douyin: FakeDouyin,
    transcriber: FakeTranscriber,
    inbox: FakeInbox,
) -> Cli:
    return Cli(
        launcher=launcher,
        credentials=credentials,
        bilibili=bilibili,
        wechat=wechat,
        douyin=douyin,
        transcriber=transcriber,
        inbox=inbox,
    )


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """一个位于中文路径下、尚不存在的知识库目录。"""
    return tmp_path / "我的库" / "知识库"
