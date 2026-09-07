#!/usr/bin/env python3
"""gnome-243 完整运行包与受控覆盖。仅stdlib，不需要院内Git/联网/pip。"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import tempfile

BRANCH = "gnome-243"
PROTECTED = {"configs/llm.yaml", "configs/hospital_config.yaml"}
META = {".243-release.json", "DEPLOY_COMMIT"}


def allowed(name):
    p = PurePosixPath(name)
    if p.is_absolute() or p.as_posix() != name or any(x in {"..", "."} for x in p.parts) or "\\" in name:
        return False
    if name in PROTECTED or any(x.startswith(".") or x == "__pycache__" for x in p.parts):
        return False
    return (name in {"pyproject.toml", "uv.lock", "README.md"}
            or name.startswith(("src/", "configs/", "scripts/", "data/router/"))
            or name in {"docs/243_release_cookbook.md", "docs/243_overlay_rollback.txt"})


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.DEVNULL)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def pack(repo, destination):
    repo, destination = Path(repo).resolve(), Path(destination).resolve()
    if git(repo, "branch", "--show-current").decode().strip() != BRANCH:
        raise ValueError("必须在gnome-243分支打包")
    if git(repo, "status", "--porcelain").strip():
        raise ValueError("工作目录有未提交内容；请先检查、提交和推送")
    sha = git(repo, "rev-parse", "HEAD").decode().strip()
    remote = git(repo, "ls-remote", "origin", f"refs/heads/{BRANCH}").decode().split()
    if not remote or remote[0] != sha:
        raise ValueError("origin/gnome-243与HEAD不一致，禁止正式打包")
    files = {}
    modes = {}
    for entry in git(repo, "ls-tree", "-r", "-z", sha).split(b"\0"):
        if not entry:
            continue
        header, raw_name = entry.split(b"\t", 1)
        mode, kind, blob = header.decode().split()
        name = raw_name.decode()
        if not allowed(name):
            continue
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ValueError("运行包不允许链接或子模块")
        files[name] = git(repo, "cat-file", "blob", blob)
        modes[name] = 0o755 if mode == "100755" else 0o644
    manifest = {"version": 1, "branch": BRANCH, "commit": sha,
                "files": {n: {"sha256": digest(v), "mode": modes[n]} for n, v in files.items()}}
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / f"javert-243-{sha[:12]}.tar.gz"
    if archive.exists():
        raise ValueError("发布包已存在，不覆盖")
    payloads = {**files, "MANIFEST.json": json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()}
    with tarfile.open(archive, "w:gz") as tf:
        for name, data in payloads.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), modes.get(name, 0o644), 0
            tf.addfile(info, io.BytesIO(data))
    checksum = digest(archive.read_bytes())
    archive.with_suffix(archive.suffix + ".sha256").write_text(f"{checksum}  {archive.name}\n")
    # 单独提供同一提交的安装器，便于院内不解包覆盖就先验包。
    installer = destination / "gnome_release.py"
    installer.write_bytes(files["scripts/gnome_release.py"])
    print(f"COMMIT={sha}\nPACKAGE={archive}\nSHA256={checksum}")
    return archive


def verify(archive):
    files, total = {}, 0
    with tarfile.open(archive, "r:gz") as tf:
        for member in tf:
            if not member.isfile() or member.name in files:
                raise ValueError("发布包含链接、目录或重复成员")
            if member.name != "MANIFEST.json" and not allowed(member.name):
                raise ValueError("发布包含越界或受保护文件")
            total += member.size
            if total > 256 * 1024 * 1024:
                raise ValueError("发布包超出256MiB上限")
            files[member.name] = tf.extractfile(member).read()
    manifest = json.loads(files.pop("MANIFEST.json"))
    sha = manifest.get("commit", "")
    if (manifest.get("version") != 1 or manifest.get("branch") != BRANCH
            or len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha)):
        raise ValueError("无效的发布版本")
    if set(files) != set(manifest["files"]):
        raise ValueError("文件清单不一致")
    for name, data in files.items():
        item = manifest["files"][name]
        if digest(data) != item["sha256"] or item["mode"] not in {0o644, 0o755}:
            raise ValueError("文件摘要或权限不符")
    for required in ("src/javert/config.py", "scripts/run_243_patient.sh", "pyproject.toml", "uv.lock"):
        if required not in files:
            raise ValueError("运行包缺少必要文件")
    return manifest, files


def safe_target(target):
    target = Path(target).absolute()
    if target.name != "Javert" or target.is_symlink() or not target.is_dir():
        raise ValueError("目标必须是已有的Javert目录，不能是符号链接")
    return target.resolve()


def local_path(target, name):
    if name not in META and not allowed(name):
        raise ValueError("不能写入非受控路径")
    path = target / name
    for candidate in [path, *path.parents]:
        if candidate == target:
            break
        if candidate.is_symlink():
            raise ValueError("目标路径存在符号链接，停止")
    if path.exists() and not path.is_file():
        raise ValueError("目标路径不是普通文件")
    return path


def atomic_write(path, data, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".243-write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def rollback(target, backup):
    target = safe_target(target)
    backup = Path(backup).resolve()
    if backup.parent != target / ".243-backups":
        raise ValueError("只能使用该目标目录自己的备份")
    state = json.loads((backup / "backup.json").read_text())
    if state["target"] != str(target):
        raise ValueError("备份目标不匹配")
    # 先验证所有备份，不能还原到一半才发现摘要损坏。
    for name, item in state["files"].items():
        local_path(target, name)
        if item is not None:
            data = (backup / "files" / name).read_bytes()
            if digest(data) != item["sha256"]:
                raise ValueError("备份文件摘要不符")
    for name, item in state["files"].items():
        path = local_path(target, name)
        if item is None:
            if path.exists():
                path.unlink()  # 仅移除本次部署新增的清单内文件，原包可恢复。
        else:
            atomic_write(path, (backup / "files" / name).read_bytes(), item["mode"])
    print("代码已回滚；env、venv、数据和数据库未改动；请按现场方式重启并验收")


def install(target, archive):
    target = safe_target(target)
    manifest, files = verify(archive)
    old = local_path(target, ".243-release.json")
    previous = json.loads(old.read_text())["files"] if old.exists() else {}
    names = set(files) | set(previous) | META
    for name in names:
        local_path(target, name)
    backup_root = target / ".243-backups"
    if backup_root.is_symlink():
        raise ValueError("备份目录不能是符号链接")
    backup_root.mkdir(mode=0o700, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix="before-" + manifest["commit"][:12] + "-", dir=backup_root))
    state = {"target": str(target), "files": {}}
    for name in sorted(names):
        path = local_path(target, name)
        if path.exists():
            data = path.read_bytes()
            state["files"][name] = {"sha256": digest(data), "mode": path.stat().st_mode & 0o777}
            atomic_write(backup / "files" / name, data, 0o600)
        else:
            state["files"][name] = None
    atomic_write(backup / "backup.json", json.dumps(state).encode(), 0o600)
    try:
        for name, data in files.items():
            atomic_write(local_path(target, name), data, manifest["files"][name]["mode"])
        for name in set(previous) - set(files):
            path = local_path(target, name)
            if path.exists():
                path.unlink()  # 已备份的旧版受控文件；不删除未知现场文件。
        atomic_write(target / ".243-release.json", json.dumps(manifest).encode(), 0o644)
        atomic_write(target / "DEPLOY_COMMIT", (manifest["commit"] + "\n").encode(), 0o644)
    except Exception:
        rollback(target, backup)
        raise
    print(f"已安装代码 COMMIT={manifest['commit']}\n回滚备份：{backup}")
    print("未自动重启、未更新依赖、未修改数据库；待现场验收。")
    return backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("pack")
    build.add_argument("--repo", default=".")
    build.add_argument("--output", required=True)
    check = commands.add_parser("verify")
    check.add_argument("archive")
    upgrade = commands.add_parser("install")
    upgrade.add_argument("archive")
    upgrade.add_argument("--target", required=True)
    upgrade.add_argument("--services-stopped", action="store_true", required=True)
    restore = commands.add_parser("rollback")
    restore.add_argument("backup")
    restore.add_argument("--target", required=True)
    restore.add_argument("--services-stopped", action="store_true", required=True)
    args = parser.parse_args()
    if args.command == "pack":
        pack(args.repo, args.output)
    elif args.command == "verify":
        manifest, files = verify(args.archive)
        print(f"校验通过 COMMIT={manifest['commit']} FILES={len(files)}")
    elif args.command == "install":
        install(args.target, args.archive)
    else:
        rollback(args.target, args.backup)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, tarfile.TarError, subprocess.CalledProcessError) as error:
        # 不回显git远端URL、凭据或文件内容。
        raise SystemExit(f"发布未完成：{type(error).__name__}: " +
                         (str(error) if isinstance(error, ValueError) else "请核对路径/权限/连接与清单"))
