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

另有 `nav_rate_limited.json`（构造）：风控拦截时接口返回 `-412 请求被拦截`，属于可重试的失败，不是登录失效。

## 列出收藏（拉取）

收藏夹与稍后再看只对登录的账号本人可见，录制不了真实样本，以下样本均按 B站 公开接口形态构造（账号与收藏内容为假）。#12 人工验收时用真实账号回填，核对解析结果。

| 文件 | 接口 | 说明 |
| --- | --- | --- |
| `fav_folders.json` | `x/v3/fav/folder/created/list-all?up_mid=<nav 的 mid>` | 账号创建的全部收藏夹 |
| `fav_resources_page1.json` | `x/v3/fav/resource/list?media_id=…&pn=1&ps=20&order=mtime` | 第 1 页（`has_more: true`）：一个正常视频、一个已失效视频（`attr: 9`，标题「已失效视频」）、一个音频（`type: 12`，不是视频来源） |
| `fav_resources_page2.json` | 同上，`pn=2` | 最后一页（`has_more: false`） |
| `fav_resources_private.json` | 同上 | 错误码 `-403 访问权限不足`：可重试的失败 |
| `toview.json` | `x/v2/history/toview/web` | 稍后再看（一次返回全部） |
