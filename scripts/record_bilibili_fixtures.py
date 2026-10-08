r"""用本机已登录的 B站 凭据重新录制契约测试样本（#12 人工验收时运行）。

    uv run python scripts/record_bilibili_fixtures.py BV1xxxxxxxxx

参数为一条带字幕（CC 或 AI 字幕）的公开视频。会覆盖 tests/fixtures/bilibili/ 中的：

    nav_logged_in.json          登录态的 nav 响应（账号信息脱敏）
    ytdlp_info_logged_in.json   登录后 yt-dlp 的 info（含内嵌 SRT 字幕，裁剪无关字段）

之后运行 `uv run pytest tests/test_bilibili_adapter.py`，契约测试应仍然通过。
凭据取自 %APPDATA%\sub2obsidian\（先执行 sub2obsidian login bilibili），绝不写入样本。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from sub2obsidian.bilibili import HttpBilibiliClient
from sub2obsidian.browser_credentials import BrowserCredentials
from sub2obsidian.config import UserConfig

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "bilibili"
KEEP = ["id", "title", "uploader", "uploader_id", "description", "timestamp", "upload_date",
        "duration", "thumbnail", "tags", "chapters", "subtitles", "webpage_url", "extractor", "_type"]


def dump(name: str, value: object) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    (FIXTURES / name).write_text(text, encoding="utf-8", newline="\n")
    print(f"已写入 {name}")


def main(bvid: str) -> None:
    cookies = BrowserCredentials(UserConfig.default()).cookies_file("bilibili")
    client = HttpBilibiliClient()
    nav = client.api("/x/web-interface/nav", {}, cookies)
    data = nav.get("data") or {}
    if not data.get("isLogin"):
        sys.exit("B站 登录已失效，请先执行 sub2obsidian login bilibili")
    # 只保留 isLogin 与无身份信息的字段，账号信息一律替换
    nav["data"] = {"isLogin": True, "mid": 10000001, "uname": "测试用户",
                   "wbi_img": data.get("wbi_img")}
    dump("nav_logged_in.json", nav)
    info = client.video_info(f"https://www.bilibili.com/video/{bvid}", cookies)
    dump("ytdlp_info_logged_in.json", {key: info[key] for key in KEEP if key in info})


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
