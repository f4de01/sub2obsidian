r"""凭据提供者的实现：Playwright 持久化浏览器配置。

- 浏览器配置放在用户配置目录 browser\<平台>\，登录一次后可复用；
- 每次运行中每个平台只从该配置读一次 cookie（启动一次无界面浏览器，结果缓存在内存里），导出为 credentials\<平台>.cookies.txt（Netscape 格式，供 yt-dlp），
  或拼成 cookie 字符串（供 F2）；
- 不读取用户日常使用的 Chrome / Edge（应用绑定加密导致读取不可靠）。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from sub2obsidian.config import UserConfig
from sub2obsidian.credentials import CredentialError, LoginRequired
from sub2obsidian.files import write_text_atomically

Cookie = dict[str, Any]  # Playwright BrowserContext.cookies() 的单个 cookie


@dataclass(frozen=True)
class LoginSite:
    url: str
    domain: str  # cookie 所属的域（含子域）
    session_cookie: str  # 存在且未过期即视为已登录


SITES = {
    "bilibili": LoginSite(
        url="https://passport.bilibili.com/login",
        domain="bilibili.com",
        session_cookie="SESSDATA",
    ),
    "douyin": LoginSite(
        url="https://www.douyin.com/",  # 首页会弹出扫码登录框
        domain="douyin.com",
        session_cookie="sessionid",
    ),
}


class Browser(Protocol):
    def login(self, profile: Path, url: str, logged_in: Callable[[list[Cookie]], bool]) -> list[Cookie]:
        """用该浏览器配置打开登录页，等到 logged_in 为真或用户关掉窗口，返回此时的 cookie。"""
        ...

    def cookies(self, profile: Path) -> list[Cookie]:
        """读出该浏览器配置中保存的全部 cookie。"""
        ...


class BrowserUnavailable(CredentialError):
    pass


INSTALL_HINT = (
    "找不到 Playwright Chromium，请先安装："
    '& "$(uv tool dir)\\sub2obsidian\\Scripts\\python.exe" -m playwright install chromium'
)


class PlaywrightBrowser:
    LOGIN_TIMEOUT = 300  # 秒

    def _launch(self, playwright: Any, profile: Path, *, headless: bool) -> Any:
        from playwright.sync_api import Error

        try:
            return playwright.chromium.launch_persistent_context(
                str(profile), headless=headless, no_viewport=not headless
            )
        except Error as error:
            if "Executable doesn't exist" in str(error):
                raise BrowserUnavailable(INSTALL_HINT) from error
            raise BrowserUnavailable(f"无法启动浏览器：{error}") from error

    def login(self, profile: Path, url: str, logged_in: Callable[[list[Cookie]], bool]) -> list[Cookie]:
        from playwright.sync_api import Error, sync_playwright

        profile.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            context = self._launch(playwright, profile, headless=False)
            cookies: list[Cookie] = []
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(url)
                deadline = time.monotonic() + self.LOGIN_TIMEOUT
                while time.monotonic() < deadline and context.pages:
                    cookies = context.cookies()
                    if logged_in(cookies):
                        break
                    time.sleep(1)
            except Error:
                pass  # 用户关掉了登录窗口
            finally:
                try:
                    context.close()
                except Error:
                    pass
            return cookies

    def cookies(self, profile: Path) -> list[Cookie]:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            context = self._launch(playwright, profile, headless=True)
            try:
                return context.cookies()
            finally:
                context.close()


def _belongs_to(cookie: Cookie, domain: str) -> bool:
    host = cookie["domain"].lstrip(".")
    return host == domain or host.endswith("." + domain)


def _expired(cookie: Cookie) -> bool:
    expires = cookie.get("expires", -1)
    return expires is not None and expires > 0 and expires < time.time()


def _netscape(cookies: list[Cookie]) -> str:
    lines = ["# Netscape HTTP Cookie File", "# 由 sub2obsidian 从其专用浏览器配置导出，勿外传", ""]
    for cookie in cookies:
        domain = cookie["domain"]
        expires = cookie.get("expires", -1)
        lines.append(
            "\t".join(
                [
                    domain,
                    "TRUE" if domain.startswith(".") else "FALSE",
                    cookie.get("path") or "/",
                    "TRUE" if cookie.get("secure") else "FALSE",
                    str(int(expires)) if expires and expires > 0 else "",  # 空 = 会话 cookie
                    cookie["name"],
                    cookie["value"],
                ]
            )
        )
    return "\n".join(lines) + "\n"


class BrowserCredentials:
    def __init__(self, user_config: UserConfig, browser: Browser | None = None) -> None:
        self.user_config = user_config
        self.browser = browser or PlaywrightBrowser()
        # 本次运行内已读出的有效 cookie，按平台缓存；只在内存里，读一次就要启动一次浏览器
        self._sessions: dict[str, list[Cookie]] = {}

    def _profile(self, platform: str) -> Path:
        return self.user_config.browser_profile_dir(platform).resolve()

    def _logged_in(self, site: LoginSite, cookies: list[Cookie]) -> bool:
        return any(
            cookie["name"] == site.session_cookie
            and cookie["value"]
            and _belongs_to(cookie, site.domain)
            and not _expired(cookie)
            for cookie in cookies
        )

    def login(self, platform: str) -> None:
        site = SITES[platform]
        self._sessions.pop(platform, None)  # 重新登录后以浏览器配置中的新 cookie 为准
        cookies = self.browser.login(
            self._profile(platform), site.url, lambda cookies: self._logged_in(site, cookies)
        )
        if not self._logged_in(site, cookies):
            raise LoginRequired(platform)
        self._export(platform, site, cookies)

    def cookies_file(self, platform: str) -> Path:
        return self._export(platform, SITES[platform], self._session(platform))

    def cookie_string(self, platform: str) -> str:
        site = SITES[platform]
        return "; ".join(
            f"{cookie['name']}={cookie['value']}"
            for cookie in self._session(platform)
            if _belongs_to(cookie, site.domain) and not _expired(cookie)
        )

    def _session(self, platform: str) -> list[Cookie]:
        """浏览器配置中当前的 cookie，本次运行内每个平台只读一次；没有登录或登录已失效时抛 LoginRequired。"""
        if platform not in self._sessions:
            self._sessions[platform] = self._read_session(platform)
        return self._sessions[platform]

    def _read_session(self, platform: str) -> list[Cookie]:
        profile = self._profile(platform)
        if not profile.is_dir():
            raise LoginRequired(platform)
        cookies = self.browser.cookies(profile)
        if not self._logged_in(SITES[platform], cookies):
            raise LoginRequired(platform)
        return cookies

    def _export(self, platform: str, site: LoginSite, cookies: list[Cookie]) -> Path:
        target = (self.user_config.credentials_dir / f"{platform}.cookies.txt").resolve()
        relevant = [cookie for cookie in cookies if _belongs_to(cookie, site.domain)]
        write_text_atomically(target, _netscape(relevant))
        return target
