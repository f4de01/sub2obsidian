# B站 契约测试样本

| 文件 | 来源 | 说明 |
| --- | --- | --- |
| `ytdlp_info_no_login.json` | 录制（2026-10，未登录） | yt-dlp 2026.08.19 对公开视频 BV1GJ411x7h7 的 info，裁剪掉格式列表等无关字段；未登录只有弹幕、没有字幕 |
| `ytdlp_error_unavailable.txt` | 录制（未登录） | yt-dlp 对不存在的稿件报的错误：并不说明「已删除」，所以适配器另查 view 接口判定 |
| `view_ok.json` | 录制（未登录） | `api.bilibili.com/x/web-interface/view?bvid=BV1GJ411x7h7` |
| `view_unavailable.json` | 录制（未登录） | 不存在的稿件：`62002 稿件不可见` |
| `nav_logged_out.json` | 录制（未登录） | `x/web-interface/nav`：`-101 账号未登录` |
| `nav_logged_in.json` | 构造 | 按公开接口形态构造的登录态 nav（账号信息为假） |
| `ytdlp_info_with_subtitles.json` | 构造 | 在录制样本上加入 CC（zh-CN）与 AI（ai-zh）字幕；字幕形态按 yt-dlp `BilibiliBaseIE.json2srt` 内嵌 SRT |
| `ytdlp_info_ai_subtitles_only.json` | 构造 | 同上，只有 AI 字幕 |
| `ytdlp_info_logged_in.json` | 构造（待 #12 替换） | 登录后 info 的占位样本 |

字幕接口需要登录，录制不了真实样本，所以 `nav_logged_in.json` 与 `ytdlp_info_logged_in.json` 先用构造样本。
#12 人工验收时登录后运行 `uv run python scripts/record_bilibili_fixtures.py <带字幕的BV号>` 覆盖这两份，契约测试应仍然通过。
