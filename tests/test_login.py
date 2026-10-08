"""登录与凭据：`login <平台>` 扫码登录；凭据只存放在用户配置目录，绝不进入知识库。"""

from pathlib import Path

from test_capture import BV, CANONICAL, SUBTITLES, video
from vault_git import git


def test_login_bilibili_asks_credential_provider_to_log_in(run, credentials):
    result = run.run("login", "bilibili")

    assert result.exit_code == 0, result.output
    assert credentials.logins == ["bilibili"]
    assert "已登录 B站" in result.output


def test_login_rejects_unsupported_platform(run, credentials):
    result = run.run("login", "weibo")

    assert result.exit_code != 0
    assert credentials.logins == []


def test_credentials_never_land_in_vault(run, credentials, bilibili, vault: Path, user_config_dir):
    run.run("init", str(vault))
    bilibili.videos[BV] = video(SUBTITLES)

    run.run("login", "bilibili")
    result = run.run("capture", CANONICAL)

    assert result.exit_code == 0, result.output
    assert any(user_config_dir.rglob("*.cookies.txt"))  # 凭据在用户配置目录里
    assert not [path for path in vault.rglob("*") if "cookie" in path.name.lower()]
    for path in vault.rglob("*"):
        if path.is_file() and ".git" not in path.relative_to(vault).parts:
            assert credentials.secret.encode() not in path.read_bytes(), path
    assert git(vault, "grep", "-l", "-F", credentials.secret, "HEAD") == ""
