from pathlib import Path

from fsutil import atomic_write_bytes


def test_creates_file_with_content(tmp_path: Path):
    target = tmp_path / "out.bin"

    atomic_write_bytes(target, b"hello")

    assert target.read_bytes() == b"hello"


def test_replaces_existing_file(tmp_path: Path):
    target = tmp_path / "out.bin"
    target.write_bytes(b"old")

    atomic_write_bytes(target, b"new")

    assert target.read_bytes() == b"new"


def test_leaves_no_temp_files_behind(tmp_path: Path):
    atomic_write_bytes(tmp_path / "out.bin", b"data")

    assert [p.name for p in tmp_path.iterdir()] == ["out.bin"]


def test_creates_parent_directories(tmp_path: Path):
    target = tmp_path / "nested" / "deeper" / "out.bin"

    atomic_write_bytes(target, b"data")

    assert target.read_bytes() == b"data"
