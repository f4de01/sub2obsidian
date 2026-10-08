# 公众号文章契约测试样本

全部无需登录，用 `scripts/record_wechat_fixtures.py` 的方式录制（2026-10，未登录，限速）。

| 文件 | 来源 | 说明 |
| --- | --- | --- |
| `article.html` | 录制 | 公开文章「通过增强PDF结构识别，革新检索增强生成技术(RAG)」（北京庖丁科技，署名「创新而务实的」），由短码链接 `https://mp.weixin.qq.com/s/JJHlJsWEqFG77LdzhvzDNw` 打开；按录制脚本裁剪：去掉 `<style>`、`<link>`、外链脚本和不含文章变量的内联脚本，正文与 `<meta>` 原样 |
| `verification.html` | 录制 | 验证页（环境异常、去验证）：在微信之外用浏览器 UA 打开**长链接**（`/s?__biz=…`）时被跳转到 `https://mp.weixin.qq.com/mp/wappoc_appmsgcaptcha?poc_token=…&target_url=…`；录制期间对多篇正常文章的长链接都如此，而短码链接都能直接打开 |
| `parameter_error.html` | 录制 | 不存在的短码链接（`/s/AbCdEfGhIjKlMnOpQrStUv`）返回的「参数错误」提示页：可重试的失败，不是已失效 |
| `deleted.html` | 构造 | 在 `parameter_error.html` 上把提示文字 `<div class="weui-msg__title warn">参数错误</div>` 换成「该内容已被发布者删除」 |
| `violation.html` | 构造 | 同上，提示文字换成「此内容因违规无法查看」 |

已删除、违规下架的文章录制不到真实样本：已知的失效文章都只有长链接，而长链接在微信之外一律跳到验证页。
微信的各类提示页共用同一个 `weui-msg` 模板（提示文字取自
[we-extract 的错误表](https://github.com/airyland/we-extract/blob/master/errors.js)），所以用录制到的「参数错误」页构造。
日后遇到真实的已删除文章（短码链接）时，用录制脚本覆盖 `deleted.html`，契约测试应仍然通过：

    uv run python scripts/record_wechat_fixtures.py deleted https://mp.weixin.qq.com/s/<短码>
