"""真实端口的组装：命令行入口按用户配置构造平台适配器等外部端口。"""

from __future__ import annotations

from pathlib import Path

from sub2obsidian.cli import real_ports
from sub2obsidian.config import UserConfig
from sub2obsidian.platforms import FavoritesAdapter


def test_bilibili_requests_use_the_configured_interval(user_config_dir: Path):
    user_config_dir.mkdir(parents=True)
    (user_config_dir / "config.toml").write_text(
        "[backfill]\nbatch_size = 20\ninterval = [2, 5.5]\n", encoding="utf-8"
    )

    ports = real_ports(UserConfig.default())

    assert ports.adapters["bilibili"].client.interval == (2.0, 5.5)


def test_bilibili_requests_default_to_one_to_three_seconds_apart():
    ports = real_ports(UserConfig.default())

    assert ports.adapters["bilibili"].client.interval == (1.0, 3.0)


def test_only_bilibili_supports_pulling_favorites_so_far():
    adapters = real_ports(UserConfig.default()).adapters

    assert [name for name, adapter in adapters.items() if isinstance(adapter, FavoritesAdapter)] == [
        "bilibili"
    ]
