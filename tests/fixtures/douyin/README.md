# 抖音契约测试样本

抖音适配器经 F2 的 `DouyinCrawler.fetch_post_detail` 取作品详情，契约测试回放的就是它的返回值：抖音网页版作品详情接口（`aweme/v1/web/aweme/detail/`）的 JSON。字段路径与 F2 `PostDetailFilter`（提交 `f6be8c0`）一致，例如 `$.aweme_detail.desc`、`$.aweme_detail.author.nickname`、`$.aweme_detail.video.bit_rate[*].play_addr.url_list`、`$.aweme_detail.images[*].url_list`；作品被删除或设为私密时 `aweme_detail` 为 null，原因写在 `filter_detail.detail_msg`（F2 `fetch_one_video` 即据此报错）。

| 文件 | 来源 | 说明 |
| --- | --- | --- |
| `post_detail_video.json` | 构造 | 一条视频：多档码率（`bit_rate`）、`play_addr`、封面；作者与 ID 均为假数据 |
| `post_detail_note.json` | 构造 | 一条图文（`aweme_type` 68）：三张图，每张有 WebP 与 JPEG 两种地址 |
| `post_detail_deleted.json` | 构造 | 已删除的作品：`aweme_detail` 为 null，`filter_detail` 说明原因 |
| `collection_page.json` | 构造 | 「收藏」页全部收藏的一页（`DouyinCrawler.fetch_user_collection`，接口 `aweme/v1/web/aweme/listcollection/`）：视频、图文、已删除（`status.is_delete`）各一条；`has_more` 为 1，`cursor` 是 16 位时间戳 |
| `collects_list.json` | 构造 | 收藏夹列表（`fetch_user_collects`，接口 `aweme/v1/web/collects/list/`）：两个收藏夹，`collects_id` 与 `collects_id_str` |
| `collects_video_page.json` | 构造 | 一个收藏夹中作品的最后一页（`fetch_user_collects_video`，接口 `aweme/v1/web/collects/video/list/`），`has_more` 为 false |

收藏列表的字段路径与 F2 `UserCollectionFilter`（继承 `UserPostFilter`：`$.aweme_list[*]` 中每条作品的结构与 `aweme_detail` 相同，`$.has_more`，下一页游标 `$.cursor`）和 `UserCollectsFilter`（`$.collects_list[*].collects_id`、`collects_name`，`$.has_more`、`$.cursor`）一致；三份收藏样本由详情样本改写而成。

抖音接口需要登录 cookie 与签名，录制不了真实样本，以上样本均按 F2 返回结构构造。
#12 人工验收时登录后录制真实样本（脱敏后）替换它们，契约测试应仍然通过。

cookie 或签名失效时抖音返回空响应，F2 重试后抛 `APIRetryExhaustedError`；网络层把它表示为 `None`，契约测试直接回放 `None`。
