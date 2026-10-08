r"""录制公众号文章契约测试样本（无需登录）。

    uv run python scripts/record_wechat_fixtures.py <样本名> <文章链接>

打开链接（跟随跳转），把网页裁剪后写入 tests/fixtures/wechat/<样本名>.html，并打印最终地址
（验证页的判定要用到它，记进 tests/fixtures/wechat/README.md）。

文章页很大（数 MB 的内联脚本），只删与解析无关的部分：<style>、<link>、外链脚本，以及不含
文章变量（biz、mid、idx、sn、ct、nickname、msg_cdn_url）的内联脚本；正文与 <meta> 原样保留。
验证页、提示页等其他页面原样保存。请求经 HttpWechatClient 限速；连续录制多条时微信可能
弹出验证页，隔一段时间再录。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from sub2obsidian.wechat import HttpWechatClient

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "wechat"
ARTICLE_VARIABLES = re.compile(
    r"""var (?:biz|mid|idx|sn|ct|nickname|msg_cdn_url) = (?:htmlDecode\()?["']"""
)


def trim(html: str) -> str:
    if 'id="js_content"' not in html:
        return html

    def keep_script(match: re.Match[str]) -> str:
        script = match.group(0)
        opening = script[: script.index(">") + 1]
        return script if "src=" not in opening and ARTICLE_VARIABLES.search(script) else ""

    html = re.sub(r"<style\b.*?</style>", "", html, flags=re.S | re.I)
    html = re.sub(r"<link\b[^>]*>", "", html, flags=re.I)
    html = re.sub(r"<script\b.*?</script>", keep_script, html, flags=re.S | re.I)
    return re.sub(r"\n\s*\n+", "\n", html)


def main(name: str, url: str) -> None:
    page = HttpWechatClient().page(url)
    target = FIXTURES / f"{name}.html"
    target.write_text(trim(page.html), encoding="utf-8", newline="\n")
    print(f"已写入 {target.name}（{target.stat().st_size} 字节），最终地址：{page.url}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
