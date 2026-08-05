#!/usr/bin/env python3
"""Bootstrap a fresh checkout or deployment.

Copies the example config and env files if they are missing (they are
gitignored, so a fresh clone has neither), then creates every directory the
pipeline writes to. Safe to re-run.
"""

import argparse
import shutil
import sys
from pathlib import Path

import yaml

from articles import feed_dir_name


def bootstrap(config_path: Path, project_root: Path) -> list[str]:
    config_path = Path(config_path)
    project_root = Path(project_root)
    messages: list[str] = []

    if not config_path.exists():
        example = project_root / "config.example.yaml"
        if not example.exists():
            sys.exit(f"No {config_path.name} and no config.example.yaml to copy from.")
        shutil.copy(example, config_path)
        messages.append(f"Created {config_path.name} from config.example.yaml — edit it before running.")

    env_path = project_root / ".env"
    env_example = project_root / ".env.example"
    if env_example.exists() and not env_path.exists():
        shutil.copy(env_example, env_path)
        messages.append("Created .env from .env.example — set EREADER_DATA_DIR and PUBLIC_BASE_URL.")

    try:
        cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        sys.exit(f"Could not parse {config_path}: {e}")

    if not isinstance(cfg, dict):
        sys.exit(f"{config_path} must contain a YAML mapping.")

    def resolve(key: str, default: str) -> Path:
        value = Path(cfg.get(key, default)).expanduser()
        return value if value.is_absolute() else project_root / value

    output_dir = resolve("output_dir", "ereader-news")
    for path in (output_dir, resolve("public_dir", "public"), resolve("image_cache_dir", ".image-cache")):
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            sys.exit(f"Cannot create {path}: {e}")
        messages.append(f"Ensured {path}")

    for feed in cfg.get("feeds", []):
        name = feed.get("name")
        if name:
            (output_dir / feed_dir_name(name)).mkdir(parents=True, exist_ok=True)
    messages.append(f"Ensured {len(cfg.get('feeds', []))} feed directories")

    return messages


def main():
    parser = argparse.ArgumentParser(description="Prepare directories and config for the news pipeline")
    parser.add_argument("--config", default="config.yaml", help="Path to the config file")
    args = parser.parse_args()

    project_root = Path(__file__).parent
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = project_root / config_path

    for message in bootstrap(config_path, project_root):
        print(message)


if __name__ == "__main__":
    main()
