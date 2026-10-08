# 抖音契约测试样本

抖音适配器经 F2 的 `DouyinCrawler.fetch_post_detail` 取作品详情，契约测试回放的就是它的返回值：抖音网页版作品详情接口（`aweme/v1/web/aweme/detail/`）的 JSON。字段路径与 F2 `PostDetailFilter`（提交 `f6be8c0`）一致，例如 `$.aweme_detail.desc`、`$.aweme_detail.author.nickname`、`$.aweme_detail.video.bit_rate[*].play_addr.url_list`、`$.aweme_detail.images[*].url_list`；作品被删除或设为私密时 `aweme_detail` 为 null，原因写在 `filter_detail.detail_msg`（F2 `fetch_one_video` 即据此报错）。

| 文件 | 来源 | 说明 |
| --- | --- | --- |
| `post_detail_video.json` | 构造 | 一条视频：多档码率（`bit_rate`）、`play_addr`、封面；作者与 ID 均为假数据 |
| `post_detail_note.json` | 构造 | 一条图文（`aweme_type` 68）：三张图，每张有 WebP 与 JPEG 两种地址 |
| `post_detail_deleted.json` | 构造 | 已删除的作品：`aweme_detail` 为 null，`filter_detail` 说明原因 |

抖音接口需要登录 cookie 与签名，本工单录制不了真实样本，三份样本均按 F2 返回结构构造。
#12 人工验收时登录后录制真实样本（脱敏后）替换这三份，契约测试应仍然通过。

cookie 或签名失效时抖音返回空响应，F2 重试后抛 `APIRetryExhaustedError`；网络层把它表示为 `None`，契约测试直接回放 `None`。
