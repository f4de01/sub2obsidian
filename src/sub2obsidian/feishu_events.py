"""飞书事件长连接：只在配置飞书收件箱的事件订阅时临时保持（feishu-connect 命令）。

你能给机器人发消息，前提是应用订阅了「接收消息」事件（im.message.receive_v1）；开发者后台保存
「使用长连接接收事件」这一订阅方式时，要求当时有客户端连着长连接。订阅好以后日常 sync 照常
主动拉取消息（feishu.py），不需要这条长连接。

FeishuEvents 是端口：行为测试换成假实现，不连网络。真实实现基于 lark-oapi，只在连接时才导入。
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol


class EventConnectionFailed(Exception):
    """飞书事件长连接连不上或断开了；消息说明原因。"""


class FeishuEvents(Protocol):
    def connect(self, app_id: str, app_secret: str, on_message: Callable[[], None]) -> None:
        """连上长连接后返回，此后在后台保持；每收到一个「接收消息」事件调用一次 on_message。

        连不上抛 EventConnectionFailed；用户按 Ctrl+C 时抛 KeyboardInterrupt。
        """
        ...

    def wait(self, seconds: float) -> None:
        """保持长连接 seconds 秒后返回；用户按 Ctrl+C 时抛 KeyboardInterrupt，
        长连接断开时抛 EventConnectionFailed。"""
        ...

    def close(self) -> None:
        """断开长连接（尽力而为）。"""
        ...


class LarkFeishuEvents:
    """真实长连接：lark-oapi 的 ws.Client。

    ws.Client.start() 会一直阻塞，所以放在后台线程里跑；主线程分段 sleep，Ctrl+C 才打断得了。
    SDK 没有公开「已连上」的状态，也不让调用方停下它：这里读 Client._conn，并停下 start() 所用的
    模块级事件循环（pyproject 因此把 lark-oapi 限在 1.x）。不自动重连：断开就报出来，
    重新运行命令即可。
    """

    CONNECT_TIMEOUT = 30  # 秒
    POLL = 0.5  # 秒
    CLOSE_TIMEOUT = 2  # 秒

    def __init__(self) -> None:
        self._client: Any = None
        self._thread: threading.Thread | None = None
        self._error: BaseException | None = None

    def connect(self, app_id: str, app_secret: str, on_message: Callable[[], None]) -> None:
        import lark_oapi as lark

        handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(lambda _event: on_message())
            .build()
        )
        # 只要 WARNING 以上：INFO 日志会把带票据的长连接地址打到终端
        self._client = lark.ws.Client(
            app_id,
            app_secret,
            event_handler=handler,
            log_level=lark.LogLevel.WARNING,
            auto_reconnect=False,
        )
        self._thread = threading.Thread(target=self._run, name="feishu-events", daemon=True)
        self._thread.start()
        deadline = time.monotonic() + self.CONNECT_TIMEOUT
        while not self._connected():
            if not self._thread.is_alive():
                raise EventConnectionFailed(f"连不上飞书长连接：{self._error}")
            if time.monotonic() > deadline:
                raise EventConnectionFailed(
                    f"{self.CONNECT_TIMEOUT} 秒内没有连上飞书长连接：检查网络后重试"
                )
            time.sleep(self.POLL)

    def wait(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while (left := deadline - time.monotonic()) > 0:
            if not self._connected():
                raise EventConnectionFailed("飞书长连接断开了：还没保存好就重新运行 feishu-connect")
            time.sleep(min(self.POLL, left))

    def close(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            return
        from lark_oapi.ws import client as ws_client

        # start() 跑在这个模块级事件循环上；停下后由 _run 收尾。还没连上就停下时 SDK 可能卡在
        # 自己的清理里，后台线程是守护线程，等一会儿就不再管它
        ws_client.loop.call_soon_threadsafe(ws_client.loop.stop)
        self._thread.join(timeout=self.CLOSE_TIMEOUT)

    def _connected(self) -> bool:
        return (
            self._thread is not None
            and self._thread.is_alive()
            and getattr(self._client, "_conn", None) is not None
        )

    def _run(self) -> None:
        from lark_oapi.ws import client as ws_client

        loop = ws_client.loop
        try:
            self._client.start()
        except BaseException as error:  # 停下事件循环时 start() 也会抛错
            self._error = error
        finally:
            # 取消 SDK 留在事件循环里的任务、关掉连接，退出时才不会打印「Task was destroyed」
            tasks = asyncio.all_tasks(loop)
            for task in tasks:
                task.cancel()
            loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
            loop.run_until_complete(self._disconnect())

    async def _disconnect(self) -> None:
        try:
            await asyncio.wait_for(self._client._disconnect(), self.CLOSE_TIMEOUT)
        except Exception:  # 尽力而为：连接已经断了或关不掉，都不影响退出
            pass
