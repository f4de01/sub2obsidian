r"""用户配置目录 %APPDATA%\sub2obsidian\ 的读写约定。"""

import tomllib
from pathlib import Path

import tomli_w


def read_config(user_config_dir: Path) -> dict:
    return tomllib.loads((user_config_dir / "config.toml").read_text(encoding="utf-8"))


def test_init_without_path_uses_vault_from_user_config(run, user_config_dir: Path, vault: Path):
    user_config_dir.mkdir(parents=True)
    (user_config_dir / "config.toml").write_text(
        tomli_w.dumps({"vault": str(vault)}), encoding="utf-8"
    )

    result = run.run("init")

    assert result.exit_code == 0, result.output
    assert (vault / "CLAUDE.md").is_file()


def test_init_records_vault_in_user_config(run, user_config_dir: Path, vault: Path):
    run.run("init", str(vault))

    assert read_config(user_config_dir)["vault"] == str(vault.resolve())


def test_init_keeps_vault_already_in_user_config(
    run, user_config_dir: Path, vault: Path, tmp_path: Path
):
    run.run("init", str(vault))
    other = tmp_path / "另一个库"

    run.run("init", str(other))

    assert read_config(user_config_dir)["vault"] == str(vault.resolve())


def test_user_config_never_lands_in_vault(run, vault: Path):
    run.run("init", str(vault))

    assert not list(vault.rglob("config.toml"))
