"""feishu-connect：配置事件订阅时临时保持飞书长连接。

长连接换成假实现（conftest 的 FakeFeishuEvents），不连真实的飞书。
"""

from __future__ import annotations

from pathlib import Path

import pytest

APP_ID = "cli_a1b2c3d4e5f60708"
APP_SECRET = "假的应用密钥-0123456789abcdef"


@pytest.fixture
def feishu_env(user_config_dir: Path) -> Path:
    """配置向导到「复制应用凭据」为止写下的 feishu.env：还没有 open_id。"""
    path = user_config_dir / "credentials" / "feishu.env"
    path.parent.mkdir(parents=True)
    path.write_text(f"FEISHU_APP_ID={APP_ID}\nFEISHU_APP_SECRET={APP_SECRET}\n", encoding="utf-8")
    return path


def test_connects_with_the_app_credentials_and_says_what_to_do_in_the_console(
    run, feishu_events, feishu_env
):
    result = run.run("feishu-connect")

    assert result.exit_code == 0, result.output
    assert feishu_events.connections == [(APP_ID, APP_SECRET)]
    assert feishu_events.waits == [600]  # 缺省保持 10 分钟
    assert feishu_events.closed
    output = result.output
    assert APP_ID in output
    assert APP_SECRET not in output
    for step in ("使用长连接接收事件", "im.message.receive_v1", "发布", "Ctrl+C"):
        assert step in output
    assert "已保持 10 分钟" in output


def test_ctrl_c_disconnects_and_exits_cleanly(run, feishu_events, feishu_env):
    feishu_events.interrupted = True

    result = run.run("feishu-connect")

    assert result.exit_code == 0, result.output
    assert "已断开长连接" in result.output
    assert "已保持" not in result.output
    assert feishu_events.closed


def test_minutes_sets_how_long_the_connection_is_held(run, feishu_events, feishu_env):
    result = run.run("feishu-connect", "--minutes", "3")

    assert result.exit_code == 0, result.output
    assert feishu_events.waits == [180]
    assert "3 分钟后自动结束" in result.output


def test_each_message_received_while_connected_is_reported(run, feishu_events, feishu_env):
    feishu_events.messages = 2

    result = run.run("feishu-connect")

    assert result.output.count("收到一条发给机器人的消息") == 2


def test_without_credentials_points_at_the_setup_wizard_and_does_not_connect(run, feishu_events):
    result = run.run("feishu-connect")

    assert result.exit_code == 1
    assert "feishu-inbox-setup.sh" in result.output
    assert feishu_events.connections == []


def test_missing_app_secret_is_named(run, feishu_events, feishu_env):
    feishu_env.write_text(f"FEISHU_APP_ID={APP_ID}\n", encoding="utf-8")

    result = run.run("feishu-connect")

    assert result.exit_code == 1
    assert "FEISHU_APP_SECRET" in result.output
    assert feishu_events.connections == []


def test_connection_failure_is_reported_without_the_console_steps(run, feishu_events, feishu_env):
    feishu_events.failure = "连不上飞书长连接：app secret invalid"

    result = run.run("feishu-connect")

    assert result.exit_code == 1
    assert "app secret invalid" in result.output
    assert "im.message.receive_v1" not in result.output
    assert feishu_events.waits == []


def test_connection_lost_while_held_is_an_error(run, feishu_events, feishu_env):
    feishu_events.lost = "飞书长连接断开了：server unreachable"

    result = run.run("feishu-connect")

    assert result.exit_code == 1
    assert "server unreachable" in result.output
    assert feishu_events.closed


def test_ctrl_c_while_connecting_exits_cleanly(run, feishu_events, feishu_env):
    feishu_events.interrupted_while_connecting = True

    result = run.run("feishu-connect")

    assert result.exit_code == 0, result.output
    assert "已断开长连接" in result.output
    assert "im.message.receive_v1" not in result.output
    assert feishu_events.closed
