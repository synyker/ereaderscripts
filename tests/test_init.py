from pathlib import Path

import pytest
import yaml

from init import bootstrap

CONFIG = {
    "output_dir": "articles",
    "public_dir": "public",
    "image_cache_dir": ".image-cache",
    "feeds": [{"name": "Yle Tuoreimmat", "url": "https://x/1"}],
}


def write_config(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(CONFIG), encoding="utf-8")
    return path


def test_creates_the_output_directory(tmp_path: Path):
    bootstrap(write_config(tmp_path), tmp_path)

    assert (tmp_path / "articles").is_dir()


def test_creates_a_directory_per_feed(tmp_path: Path):
    bootstrap(write_config(tmp_path), tmp_path)

    assert (tmp_path / "articles" / "Yle_Tuoreimmat").is_dir()


def test_creates_public_and_cache_directories(tmp_path: Path):
    bootstrap(write_config(tmp_path), tmp_path)

    assert (tmp_path / "public").is_dir()
    assert (tmp_path / ".image-cache").is_dir()


def test_is_idempotent(tmp_path: Path):
    config_path = write_config(tmp_path)
    bootstrap(config_path, tmp_path)

    bootstrap(config_path, tmp_path)  # must not raise

    assert (tmp_path / "articles").is_dir()


def test_copies_the_example_config_when_missing(tmp_path: Path):
    (tmp_path / "config.example.yaml").write_text(yaml.safe_dump(CONFIG), encoding="utf-8")

    bootstrap(tmp_path / "config.yaml", tmp_path)

    assert (tmp_path / "config.yaml").exists()


def test_never_overwrites_an_existing_config(tmp_path: Path):
    (tmp_path / "config.example.yaml").write_text(yaml.safe_dump(CONFIG), encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({**CONFIG, "output_dir": "mine"}), encoding="utf-8")

    bootstrap(config_path, tmp_path)

    assert yaml.safe_load(config_path.read_text())["output_dir"] == "mine"


def test_copies_the_example_env_when_missing(tmp_path: Path):
    (tmp_path / ".env.example").write_text("EREADER_DATA_DIR=\n", encoding="utf-8")
    (tmp_path / "config.example.yaml").write_text(yaml.safe_dump(CONFIG), encoding="utf-8")

    bootstrap(tmp_path / "config.yaml", tmp_path)

    assert (tmp_path / ".env").exists()


def test_missing_config_and_no_example_is_an_error(tmp_path: Path):
    with pytest.raises(SystemExit):
        bootstrap(tmp_path / "config.yaml", tmp_path)


def test_unparseable_config_is_an_error(tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("feeds: [unclosed", encoding="utf-8")

    with pytest.raises(SystemExit):
        bootstrap(config_path, tmp_path)


def test_uncreatable_output_dir_exits_cleanly(tmp_path: Path):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")

    config = {**CONFIG, "output_dir": str(blocker / "sub")}
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    with pytest.raises(SystemExit):
        bootstrap(config_path, tmp_path)
