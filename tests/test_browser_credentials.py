"""凭据提供者（Playwright 持久化浏览器配置）的契约测试。

浏览器本身由回放 Playwright `context.cookies()` 形态样本的假浏览器替代，不启动 Chromium。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from http.cookiejar import MozillaCookieJar
from pathlib import Path

import pytest

from sub2obsidian.browser_credentials import BrowserCredentials
from sub2obsidian.config import UserConfig
from sub2obsidian.credentials import LoginRequired

IN_A_MONTH = time.time() + 30 * 86400


def playwright_cookie(name: str, value: str, *, domain=".bilibili.com", expires=IN_A_MONTH, **extra):
    """Playwright BrowserContext.cookies() 返回的单个 cookie 形态。"""
    return {
        "name": name,
        "value": value,
        "domain": domain,
        "path": "/",
        "expires": expires,
        "httpOnly": name == "SESSDATA",
        "secure": name == "SESSDATA",
        "sameSite": "Lax",
        **extra,
    }


LOGGED_IN = [
    playwright_cookie("SESSDATA", "abc%2C123%2Cdef"),
    playwright_cookie("bili_jct", "csrf-token"),
    playwright_cookie("DedeUserID", "10000001"),
    playwright_cookie("buvid3", "visitor", expires=-1),  # 会话 cookie
    playwright_cookie("other", "x", domain=".example.com"),
]


@dataclass
class FakeBrowser:
    """记录登录与读取所用的浏览器配置目录；cookies 按配置目录保存。"""

    stored: dict[Path, list[dict]] = field(default_factory=dict)
    on_login: list[dict] = field(default_factory=lambda: list(LOGGED_IN))
    logins: list[tuple[Path, str]] = field(default_factory=list)
    reads: list[Path] = field(default_factory=list)  # 每次读取都会启动一次无界面浏览器

    def login(self, profile: Path, url: str, logged_in) -> list[dict]:
        self.logins.append((profile, url))
        profile.mkdir(parents=True, exist_ok=True)
        self.stored[profile] = self.on_login
        assert logged_in(self.on_login)
        return self.on_login

    def cookies(self, profile: Path) -> list[dict]:
        self.reads.append(profile)
        return self.stored.get(profile, [])


@pytest.fixture
def user_config() -> UserConfig:
    return UserConfig.default()  # %APPDATA% 已由 conftest 指向临时目录


def load_jar(path: Path) -> MozillaCookieJar:
    jar = MozillaCookieJar(str(path))
    jar.load(ignore_discard=True, ignore_expires=True)
    return jar


def test_login_uses_persistent_profile_in_user_config_dir(user_config):
    browser = FakeBrowser()

    BrowserCredentials(user_config, browser).login("bilibili")

    profile, url = browser.logins[0]
    assert profile == user_config.browser_profile_dir("bilibili")
    assert url.startswith("https://passport.bilibili.com/login")


def test_cookies_file_exports_netscape_cookies_for_ytdlp(user_config):
    browser = FakeBrowser()
    credentials = BrowserCredentials(user_config, browser)
    credentials.login("bilibili")

    path = credentials.cookies_file("bilibili")

    assert path.parent == user_config.credentials_dir
    jar = load_jar(path)
    cookies = {cookie.name: cookie for cookie in jar}
    assert set(cookies) == {"SESSDATA", "bili_jct", "DedeUserID", "buvid3"}
    assert cookies["SESSDATA"].value == "abc%2C123%2Cdef"
    assert cookies["SESSDATA"].domain == ".bilibili.com"
    assert cookies["SESSDATA"].secure
    assert cookies["buvid3"].expires is None  # 会话 cookie


def test_cookies_are_read_from_the_profile_only_once_per_run(user_config):
    """一次 sync 会多次取 cookie：每个平台只启动一次浏览器。"""
    browser = FakeBrowser()
    credentials = BrowserCredentials(user_config, browser)
    credentials.login("bilibili")

    first = credentials.cookies_file("bilibili")
    second = credentials.cookies_file("bilibili")
    credentials.cookie_string("bilibili")

    assert browser.reads == [user_config.browser_profile_dir("bilibili")]
    assert first == second
    assert {cookie.name for cookie in load_jar(second)} == {"SESSDATA", "bili_jct", "DedeUserID", "buvid3"}


@pytest.mark.parametrize(
    ("platform", "rotated", "read"),
    [
        pytest.param(
            "bilibili",
            playwright_cookie("SESSDATA", "rotated"),
            lambda credentials: {c.name: c.value for c in load_jar(credentials.cookies_file("bilibili"))},
            id="B站cookies文件",
        ),
        pytest.param(
            "douyin",
            playwright_cookie("sessionid", "rotated", domain=".douyin.com"),
            lambda credentials: dict(pair.split("=", 1) for pair in credentials.cookie_string("douyin").split("; ")),
            id="抖音cookie字符串",
        ),
    ],
)
def test_each_run_rereads_cookies_from_the_profile(user_config, platform, rotated, read):
    """平台会轮换 cookie：每次运行（新的凭据提供者）都以浏览器配置为准，不沿用上一次运行读到的。"""
    browser = FakeBrowser(on_login=DOUYIN_LOGGED_IN if platform == "douyin" else LOGGED_IN)
    BrowserCredentials(user_config, browser).login(platform)
    assert read(BrowserCredentials(user_config, browser)) != {rotated["name"]: "rotated"}  # 上一次运行
    browser.stored[user_config.browser_profile_dir(platform)] = [rotated]

    assert read(BrowserCredentials(user_config, browser)) == {rotated["name"]: "rotated"}


def test_logging_in_again_replaces_the_cookies_cached_in_this_run(user_config):
    """重新登录后，同一次运行里再取 cookie 拿到的是新登录的。"""
    browser = FakeBrowser()
    credentials = BrowserCredentials(user_config, browser)
    credentials.login("bilibili")
    credentials.cookie_string("bilibili")
    browser.on_login = [playwright_cookie("SESSDATA", "fresh")]

    credentials.login("bilibili")

    assert credentials.cookie_string("bilibili") == "SESSDATA=fresh"


def test_each_platform_caches_its_own_cookies(user_config):
    """两个平台交替取 cookie：各自只读一次自己的浏览器配置。"""
    browser = FakeBrowser()
    credentials = BrowserCredentials(user_config, browser)
    credentials.login("bilibili")
    browser.on_login = DOUYIN_LOGGED_IN
    credentials.login("douyin")

    for _ in range(2):
        assert credentials.cookie_string("bilibili").startswith("SESSDATA=abc")
        assert "sessionid=0123abcd" in credentials.cookie_string("douyin")

    assert browser.reads == [
        user_config.browser_profile_dir("bilibili"),
        user_config.browser_profile_dir("douyin"),
    ]


def test_never_logged_in_asks_to_log_in(user_config):
    with pytest.raises(LoginRequired, match="请重新登录 B站：sub2obsidian login bilibili"):
        BrowserCredentials(user_config, FakeBrowser()).cookies_file("bilibili")


@pytest.mark.parametrize(
    "cookies",
    [
        pytest.param([], id="cookie被清空"),
        pytest.param([playwright_cookie("SESSDATA", "old", expires=time.time() - 60)], id="已过期"),
        pytest.param([playwright_cookie("buvid3", "visitor")], id="只有访客cookie"),
    ],
)
def test_expired_or_missing_session_cookie_asks_to_log_in_again(user_config, cookies):
    browser = FakeBrowser()
    credentials = BrowserCredentials(user_config, browser)
    credentials.login("bilibili")
    browser.stored[user_config.browser_profile_dir("bilibili")] = cookies

    with pytest.raises(LoginRequired, match="请重新登录 B站"):
        credentials.cookies_file("bilibili")


def test_expired_login_is_read_once_and_reported_on_every_use(user_config):
    """登录失效时 sync 的每个来源都会来取 cookie：每次都报「请重新登录」，但只启动一次浏览器。"""
    browser = FakeBrowser()
    credentials = BrowserCredentials(user_config, browser)
    credentials.login("bilibili")
    browser.stored[user_config.browser_profile_dir("bilibili")] = [
        playwright_cookie("SESSDATA", "old", expires=time.time() - 60)
    ]

    for _ in range(3):
        with pytest.raises(LoginRequired, match="请重新登录 B站"):
            credentials.cookies_file("bilibili")

    assert len(browser.reads) == 1


def test_login_that_never_completes_is_reported(user_config):
    browser = FakeBrowser(on_login=[playwright_cookie("buvid3", "visitor")])

    def login(profile, url, logged_in):
        assert not logged_in(browser.on_login)
        return browser.on_login

    browser.login = login  # 用户关掉了登录窗口，没有扫码

    with pytest.raises(LoginRequired):
        BrowserCredentials(user_config, browser).login("bilibili")


def test_credentials_are_written_only_to_user_config_dir_even_when_run_inside_vault(
    user_config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """agent 在知识库目录中调用 CLI：相对路径也绝不能把凭据写进知识库。"""
    vault = tmp_path / "知识库"
    vault.mkdir()
    monkeypatch.chdir(vault)
    credentials = BrowserCredentials(user_config, FakeBrowser())

    credentials.login("bilibili")
    credentials.cookies_file("bilibili")

    assert list(vault.rglob("*")) == []
    assert (user_config.credentials_dir / "bilibili.cookies.txt").is_file()


DOUYIN_LOGGED_IN = [
    playwright_cookie("sessionid", "0123abcd", domain=".douyin.com"),
    playwright_cookie("sid_guard", "0123abcd%7C1727000000", domain=".douyin.com"),
    playwright_cookie("ttwid", "1%7Cvisitor", domain=".douyin.com", expires=-1),  # 会话 cookie
    playwright_cookie("s_v_web_id", "verify_xyz", domain="www.douyin.com"),
    playwright_cookie("old", "gone", domain=".douyin.com", expires=time.time() - 60),
    playwright_cookie("SESSDATA", "bili", domain=".bilibili.com"),
]


def test_login_douyin_opens_douyin_in_its_own_profile(user_config):
    browser = FakeBrowser(on_login=DOUYIN_LOGGED_IN)

    BrowserCredentials(user_config, browser).login("douyin")

    profile, url = browser.logins[0]
    assert profile == user_config.browser_profile_dir("douyin")
    assert url.startswith("https://www.douyin.com")


def test_cookie_string_exports_douyin_cookies_for_f2(user_config):
    browser = FakeBrowser(on_login=DOUYIN_LOGGED_IN)
    credentials = BrowserCredentials(user_config, browser)
    credentials.login("douyin")

    cookie = credentials.cookie_string("douyin")

    pairs = dict(pair.split("=", 1) for pair in cookie.split("; "))
    assert pairs == {
        "sessionid": "0123abcd",
        "sid_guard": "0123abcd%7C1727000000",
        "ttwid": "1%7Cvisitor",
        "s_v_web_id": "verify_xyz",
    }


@pytest.mark.parametrize(
    "cookies",
    [
        pytest.param([], id="cookie被清空"),
        pytest.param(
            [playwright_cookie("sessionid", "old", domain=".douyin.com", expires=time.time() - 60)],
            id="已过期",
        ),
        pytest.param([playwright_cookie("ttwid", "visitor", domain=".douyin.com")], id="只有访客cookie"),
    ],
)
def test_douyin_without_live_session_asks_to_log_in_again(user_config, cookies):
    browser = FakeBrowser(on_login=DOUYIN_LOGGED_IN)
    credentials = BrowserCredentials(user_config, browser)
    credentials.login("douyin")
    browser.stored[user_config.browser_profile_dir("douyin")] = cookies

    with pytest.raises(LoginRequired, match="请重新登录 抖音：sub2obsidian login douyin"):
        credentials.cookie_string("douyin")


def test_douyin_never_logged_in_asks_to_log_in(user_config):
    with pytest.raises(LoginRequired, match="请重新登录 抖音"):
        BrowserCredentials(user_config, FakeBrowser()).cookie_string("douyin")


def test_douyin_cookie_string_is_never_written_inside_the_vault(
    user_config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    vault = tmp_path / "知识库"
    vault.mkdir()
    monkeypatch.chdir(vault)
    credentials = BrowserCredentials(user_config, FakeBrowser(on_login=DOUYIN_LOGGED_IN))

    credentials.login("douyin")
    credentials.cookie_string("douyin")

    assert list(vault.rglob("*")) == []
