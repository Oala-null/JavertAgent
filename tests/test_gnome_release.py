import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile

import pytest

spec = importlib.util.spec_from_file_location("gnome_release", Path(__file__).parents[1] / "scripts/gnome_release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def archive(tmp_path, extra=None, corrupt=False):
    files = {"src/javert/config.py": b"new-code",
             "scripts/run_243_patient.sh": b"run", "pyproject.toml": b"project",
             "uv.lock": b"lock", **(extra or {})}
    metadata = {"version": 1, "branch": "gnome-243", "commit": "a" * 40,
                "files": {p: {"sha256": hashlib.sha256(v).hexdigest(), "mode": 0o644}
                          for p, v in files.items()}}
    if corrupt:
        files["src/javert/config.py"] = b"tampered"
    path = tmp_path / "release.tar.gz"
    with tarfile.open(path, "w:gz") as tf:
        for name, data in {**files, "MANIFEST.json": json.dumps(metadata).encode()}.items():
            item = tarfile.TarInfo(name)
            item.size = len(data)
            tf.addfile(item, io.BytesIO(data))
    return path


def target(tmp_path):
    root = tmp_path / "Javert"
    for name, content in {
        "src/javert/config.py": "old-code",
        ".env": "SQL_PASSWORD=TEST_SECRET",
        "configs/llm.yaml": "onsite-model-config",
        ".venv/keep": "onsite-environment",
        "data/case_notes.csv": "TEST_ONLY_RECORD",
        "output/audit.sqlite": "TEST_ONLY_DATABASE",
    }.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return root


def test_install_and_rollback_preserve_site(tmp_path):
    root = target(tmp_path)
    protected = {name: (root / name).read_bytes() for name in (
        ".env", "configs/llm.yaml", ".venv/keep", "data/case_notes.csv", "output/audit.sqlite")}
    backup = release.install(root, archive(tmp_path))
    assert (root / "src/javert/config.py").read_text() == "new-code"
    assert (root / "DEPLOY_COMMIT").read_text().strip() == "a" * 40
    for name, data in protected.items():
        assert (root / name).read_bytes() == data
    release.rollback(root, backup)
    assert (root / "src/javert/config.py").read_text() == "old-code"
    assert not (root / "scripts/run_243_patient.sh").exists()
    for name, data in protected.items():
        assert (root / name).read_bytes() == data


@pytest.mark.parametrize("name", ["../escape", "/etc/passwd", ".env", "configs/llm.yaml",
                                  "data/patient.csv", ".venv/bin/python"])
def test_reject_non_runtime_files(tmp_path, name):
    root = target(tmp_path)
    with pytest.raises(ValueError):
        release.install(root, archive(tmp_path, {name: b"bad"}))
    assert (root / "src/javert/config.py").read_text() == "old-code"


def test_corrupt_payload_does_not_write(tmp_path):
    root = target(tmp_path)
    with pytest.raises(ValueError):
        release.install(root, archive(tmp_path, corrupt=True))
    assert (root / "src/javert/config.py").read_text() == "old-code"


def test_destination_symlink_rejected(tmp_path):
    root = target(tmp_path)
    (root / "scripts").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(ValueError):
        release.install(root, archive(tmp_path))


def test_pack_requires_pushed_clean_head(tmp_path, monkeypatch):
    def git(repo, *args):
        if args[0] == "branch":
            return b"gnome-243"
        if args[0] == "status":
            return b" M src/test.py"
        raise AssertionError("dirty tree must stop early")
    monkeypatch.setattr(release, "git", git)
    with pytest.raises(ValueError, match="未提交"):
        release.pack(tmp_path, tmp_path / "out")


def test_pack_requires_same_remote_sha(tmp_path, monkeypatch):
    outputs = {"branch": b"gnome-243", "status": b"", "rev-parse": b"a" * 40,
               "ls-remote": b"b" * 40 + b" refs/heads/gnome-243"}
    monkeypatch.setattr(release, "git", lambda repo, *a: outputs[a[0]])
    with pytest.raises(ValueError, match="不一致"):
        release.pack(tmp_path, tmp_path / "out")


def test_failed_install_restores_old_files(tmp_path, monkeypatch):
    root = target(tmp_path)
    original = release.atomic_write
    failed = []
    def write(path, data, mode):
        if path == root / "uv.lock" and not failed:
            failed.append(True)
            raise OSError("artificial disk error")
        return original(path, data, mode)
    monkeypatch.setattr(release, "atomic_write", write)
    with pytest.raises(OSError):
        release.install(root, archive(tmp_path))
    assert (root / "src/javert/config.py").read_text() == "old-code"
    assert (root / ".env").read_text() == "SQL_PASSWORD=TEST_SECRET"
    assert not (root / "scripts/run_243_patient.sh").exists()
