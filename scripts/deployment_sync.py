#!/usr/bin/env python3
"""用稀疏 Git 管理 62，并核对本地与 62 的 HEAD。

``check`` 只读比较两个 HEAD，并要求 62 受控运行时范围工作树 clean。只比较 HEAD 而
不检查 clean 会漏掉“远端文件被手工覆盖但没有 commit”的事故。

``artifact`` 从已提交 HEAD 生成最小 Git 对象包：只包含当前 commit/tree 和运行时范围
blob，不携带仓库历史、患者数据 blob 或凭据。``install`` 在 62 上先备份，再把对象包安装
为稀疏 ``production-62`` 工作树。
"""

from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from typing import Any, Sequence


DEFAULT_HOST = "admin2@192.168.31.62"
DEFAULT_REMOTE_ROOT = "/home/admin2/javert"
DEFAULT_REMOTE_BRANCH = "production-62"
ARTIFACT_MANIFEST = "deployment.json"
META_PACK = "head-trees.pack"
RUNTIME_PACK = "runtime-blobs.pack"

# 只管理运行 Javert 必需且允许版本化部署的文件。禁止纳入 .env、data/ 患者文件和 output/。
DEPLOY_SCOPES = (
    "src",
    "configs",
    "data/router",
    # Promise 运行时会 fail closed 校验去标识案例；它们不是普通测试附件。
    "tests/promise_cases",
    "scripts",
    "pyproject.toml",
    "uv.lock",
)

REMOTE_HEAD_PROBE = r"""
import json
import subprocess
import sys

root = sys.argv[1]
scopes = json.load(sys.stdin)["scopes"]

def git(*args):
    result = subprocess.run(
        ["git", "-C", root, *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()
        print(json.dumps({"error": detail[-1] if detail else "git failed"}))
        raise SystemExit(0)
    return result.stdout.strip()

head = git("rev-parse", "HEAD")
branch = git("symbolic-ref", "--quiet", "--short", "HEAD")
dirty = git("status", "--porcelain", "--untracked-files=no", "--", *scopes)
print(json.dumps({
    "head": head,
    "branch": branch,
    "dirty": [line for line in dirty.splitlines() if line.strip()],
}, separators=(",", ":")))
"""


class DeploymentSyncError(RuntimeError):
    """部署状态无法可靠生成、安装或查询。"""


def _run(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    input_data: str | bytes | None = None,
    text: bool = True,
) -> subprocess.CompletedProcess:
    result = subprocess.run(
        list(command),
        cwd=cwd,
        input=input_data,
        check=False,
        capture_output=True,
        text=text,
    )
    if result.returncode != 0:
        stderr = result.stderr if text else result.stderr.decode(errors="replace")
        detail = stderr.strip().splitlines()
        raise DeploymentSyncError(
            f"命令失败 ({' '.join(command[:3])}): {detail[-1] if detail else '无错误详情'}"
        )
    return result


def _git(repo: Path, *args: str, text: bool = True) -> str | bytes:
    result = _run(["git", "-C", str(repo), *args], text=text)
    return result.stdout


def find_repo_root(start: Path | None = None) -> Path:
    base = (start or Path.cwd()).resolve()
    return Path(str(_git(base, "rev-parse", "--show-toplevel")).strip()).resolve()


def local_head(repo: Path, ref: str = "HEAD") -> str:
    value = str(_git(repo, "rev-parse", ref)).strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", value):
        raise DeploymentSyncError("本地 HEAD 格式非法")
    return value


def runtime_dirty_count(repo: Path) -> int:
    output = str(
        _git(
            repo,
            "status",
            "--porcelain",
            "--untracked-files=no",
            "--",
            *DEPLOY_SCOPES,
        )
    )
    return sum(1 for line in output.splitlines() if line.strip())


def query_remote_head(
    *,
    host: str,
    remote_root: str,
    timeout: float,
) -> dict[str, Any]:
    if not host or host.startswith("-"):
        raise DeploymentSyncError("远端 host 非法")
    if not remote_root.startswith("/"):
        raise DeploymentSyncError("远端目录必须是绝对路径")
    remote_command = f"python3 -c {shlex.quote(REMOTE_HEAD_PROBE)} {shlex.quote(remote_root)}"
    try:
        result = subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=5",
                "-o",
                "ConnectionAttempts=1",
                host,
                remote_command,
            ],
            input=json.dumps({"scopes": DEPLOY_SCOPES}, separators=(",", ":")),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise DeploymentSyncError(f"查询 62 超时（{timeout:g}s）") from exc
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()
        raise DeploymentSyncError(f"SSH 查询失败: {detail[-1] if detail else '无错误详情'}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise DeploymentSyncError("62 返回了非 JSON 状态") from exc
    if payload.get("error"):
        raise DeploymentSyncError(f"62 Git 状态不可用: {payload['error']}")
    if not isinstance(payload.get("dirty"), list):
        raise DeploymentSyncError("62 Git 状态结构非法")
    return payload


def compare_head_state(expected_head: str, remote: dict[str, Any]) -> dict[str, Any]:
    remote_head = str(remote.get("head", ""))
    dirty = list(remote.get("dirty", []))
    return {
        "synced": remote_head == expected_head and not dirty,
        "local_head": expected_head,
        "remote_head": remote_head,
        "remote_branch": str(remote.get("branch", "")),
        "remote_dirty": dirty,
    }


def check_command(args: argparse.Namespace) -> int:
    repo = find_repo_root()
    expected = local_head(repo, args.ref)
    local_dirty = runtime_dirty_count(repo)
    try:
        remote = query_remote_head(
            host=args.host,
            remote_root=args.remote_root,
            timeout=args.timeout,
        )
    except DeploymentSyncError as exc:
        print(f"[62-sync] UNKNOWN {exc}", file=sys.stderr)
        if local_dirty:
            print(f"[62-sync] 本地运行时范围有 {local_dirty} 个未提交改动", file=sys.stderr)
        return 2

    report = compare_head_state(expected, remote)
    if args.json:
        print(json.dumps({**report, "local_dirty": local_dirty}, ensure_ascii=False, indent=2))
        return 0 if report["synced"] else 1

    label = "SYNCED" if report["synced"] else "DRIFT"
    remote_label = report["remote_head"][:12] if report["remote_head"] else "<missing>"
    print(
        f"[62-sync] {label} local={report['local_head'][:12]} remote={remote_label} "
        f"branch={report['remote_branch'] or '<unknown>'} remote_dirty={len(report['remote_dirty'])}"
    )
    if report["remote_head"] != report["local_head"]:
        print("[62-sync] - 本地与 62 HEAD 不一致")
    for line in report["remote_dirty"][: args.max_diffs]:
        print(f"[62-sync] - 62 未提交改动: {line}")
    hidden = len(report["remote_dirty"]) - args.max_diffs
    if hidden > 0:
        print(f"[62-sync] - 另有 {hidden} 个 62 未提交改动")
    if local_dirty:
        print(
            f"[62-sync] 注意：本地运行时范围有 {local_dirty} 个未提交改动；"
            "HEAD 一致不代表这些改动已部署"
        )
    return 0 if report["synced"] else 1


def _object_ids(repo: Path, head: str) -> tuple[list[str], list[str]]:
    root_tree = str(_git(repo, "rev-parse", f"{head}^{{tree}}")).strip()
    tree_rows = str(
        _git(repo, "ls-tree", "-r", "-t", "--format=%(objecttype) %(objectname)", head)
    ).splitlines()
    trees = {root_tree, *(row.split()[1] for row in tree_rows if row.startswith("tree "))}
    blob_rows = str(
        _git(
            repo,
            "ls-tree",
            "-r",
            "--format=%(objecttype) %(objectname)",
            head,
            "--",
            *DEPLOY_SCOPES,
        )
    ).splitlines()
    blobs = {row.split()[1] for row in blob_rows if row.startswith("blob ")}
    return sorted({head, *trees}), sorted(blobs)


def _pack_objects(repo: Path, object_ids: Sequence[str]) -> bytes:
    payload = "".join(f"{oid}\n" for oid in object_ids).encode("ascii")
    result = _run(["git", "pack-objects", "--stdout"], cwd=repo, input_data=payload, text=False)
    assert isinstance(result.stdout, bytes)
    return result.stdout


def artifact_command(args: argparse.Namespace) -> int:
    repo = find_repo_root()
    dirty = runtime_dirty_count(repo)
    if dirty and not args.allow_dirty:
        print(
            f"拒绝生成部署物：运行时范围有 {dirty} 个未提交改动。请先提交，"
            "或明确使用 --allow-dirty（部署物仍只取 HEAD）。",
            file=sys.stderr,
        )
        return 2

    head = local_head(repo, args.ref)
    meta_ids, blob_ids = _object_ids(repo, head)
    meta_pack = _pack_objects(repo, meta_ids)
    runtime_pack = _pack_objects(repo, blob_ids)
    manifest = {
        "schema_version": 2,
        "commit": head,
        "branch": DEFAULT_REMOTE_BRANCH,
        "scopes": list(DEPLOY_SCOPES),
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    output = Path(args.output or f"/tmp/javert-git-{head[:12]}.tgz").expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with tarfile.open(temporary, "w:gz") as archive:
            for name, raw in (
                (META_PACK, meta_pack),
                (RUNTIME_PACK, runtime_pack),
                (
                    ARTIFACT_MANIFEST,
                    (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode(),
                ),
            ):
                member = tarfile.TarInfo(name)
                member.size = len(raw)
                member.mode = 0o600
                member.mtime = int(time.time())
                archive.addfile(member, io.BytesIO(raw))
        os.replace(temporary, output)
        output.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)
    print(
        f"artifact={output}\ncommit={head}\n"
        f"runtime_blobs={len(blob_ids)}\nscopes={','.join(DEPLOY_SCOPES)}"
    )
    return 0


def _safe_artifact_members(archive: tarfile.TarFile) -> dict[str, bytes]:
    expected = {META_PACK, RUNTIME_PACK, ARTIFACT_MANIFEST}
    names = {member.name for member in archive.getmembers() if member.isfile()}
    if names != expected:
        raise DeploymentSyncError("部署物文件集合非法")
    result: dict[str, bytes] = {}
    for name in expected:
        handle = archive.extractfile(name)
        if handle is None:
            raise DeploymentSyncError(f"无法读取部署物: {name}")
        result[name] = handle.read()
    return result


def _validate_manifest(raw: bytes) -> dict[str, Any]:
    try:
        manifest = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeploymentSyncError("deployment.json 非法") from exc
    if manifest.get("schema_version") != 2:
        raise DeploymentSyncError("部署物 schema_version 不支持")
    if not re.fullmatch(r"[0-9a-f]{40,64}", str(manifest.get("commit", ""))):
        raise DeploymentSyncError("部署物 commit 非法")
    if tuple(manifest.get("scopes", ())) != DEPLOY_SCOPES:
        raise DeploymentSyncError("部署物运行范围与安装器不一致")
    return manifest


def _backup_remote(remote_root: Path) -> Path:
    timestamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    backup = Path.home() / "backup" / f"javert-git-{timestamp}-{os.getpid()}"
    backup.mkdir(parents=True, mode=0o700)
    backup.chmod(0o700)
    runtime_backup = backup / "runtime.tgz"
    with tarfile.open(runtime_backup, "w:gz") as archive:
        for relative in DEPLOY_SCOPES:
            path = remote_root / relative
            if path.exists():
                archive.add(path, arcname=relative, recursive=True)
    runtime_backup.chmod(0o600)

    env_path = remote_root / ".env"
    if not env_path.is_file():
        raise DeploymentSyncError("62 缺少 .env，已停止覆盖")
    shutil.copy2(env_path, backup / ".env")
    (backup / ".env").chmod(0o600)

    pid_result = _run(
        ["systemctl", "show", "-p", "MainPID", "--value", "javert-web"],
        text=True,
    )
    pid = pid_result.stdout.strip()
    environ = Path("/proc") / pid / "environ"
    if not pid.isdigit() or pid == "0" or not environ.is_file():
        raise DeploymentSyncError("无法固化旧进程实际环境，已停止覆盖")
    shutil.copyfile(environ, backup / "process.environ")
    (backup / "process.environ").chmod(0o600)
    return backup


def _git_mutate(repo: Path, *args: str, input_data: bytes | None = None) -> str:
    result = _run(
        ["git", "-C", str(repo), *args],
        input_data=input_data,
        text=input_data is None,
    )
    if isinstance(result.stdout, bytes):
        return result.stdout.decode(errors="replace").strip()
    return result.stdout.strip()


def install_command(args: argparse.Namespace) -> int:
    artifact = Path(args.artifact).expanduser().resolve()
    remote_root = Path(args.remote_root).expanduser().resolve()
    if remote_root != Path(DEFAULT_REMOTE_ROOT):
        raise DeploymentSyncError(f"拒绝安装到非标准目录: {remote_root}")
    if not artifact.is_file() or not remote_root.is_dir():
        raise DeploymentSyncError("部署物或 62 项目目录不存在")

    with tarfile.open(artifact, "r:gz") as archive:
        members = _safe_artifact_members(archive)
    manifest = _validate_manifest(members[ARTIFACT_MANIFEST])
    commit = manifest["commit"]
    branch = str(manifest.get("branch") or DEFAULT_REMOTE_BRANCH)
    backup = _backup_remote(remote_root)

    if not (remote_root / ".git").is_dir():
        _git_mutate(remote_root, "init")
    _git_mutate(remote_root, "config", "extensions.partialClone", "origin")
    _git_mutate(remote_root, "config", "remote.origin.promisor", "true")
    _git_mutate(remote_root, "config", "remote.origin.partialclonefilter", "blob:none")
    _git_mutate(remote_root, "index-pack", "--stdin", input_data=members[META_PACK])
    _git_mutate(remote_root, "index-pack", "--stdin", input_data=members[RUNTIME_PACK])
    _git_mutate(remote_root, "update-ref", f"refs/heads/{branch}", commit)
    _git_mutate(remote_root, "symbolic-ref", "HEAD", f"refs/heads/{branch}")

    shallow = remote_root / ".git" / "shallow"
    shallow.write_text(commit + "\n", encoding="ascii")
    shallow.chmod(0o644)
    _git_mutate(remote_root, "sparse-checkout", "init", "--no-cone")
    patterns = tuple(f"/{scope}/" if "." not in Path(scope).name else f"/{scope}" for scope in DEPLOY_SCOPES)
    _git_mutate(remote_root, "sparse-checkout", "set", "--no-cone", *patterns)
    _git_mutate(remote_root, "reset", "--hard", commit)

    installed = _git_mutate(remote_root, "rev-parse", "HEAD")
    dirty = _git_mutate(
        remote_root,
        "status",
        "--porcelain",
        "--untracked-files=no",
        "--",
        *DEPLOY_SCOPES,
    )
    if installed != commit or dirty:
        raise DeploymentSyncError(f"安装后 Git 验证失败；备份位于 {backup}")
    print(f"installed_head={installed}\nbranch={branch}\nbackup={backup}\nworktree=clean")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    check = commands.add_parser("check", help="比较本地与 62 HEAD，并检查 62 工作树 clean")
    check.add_argument("--host", default=os.getenv("JAVERT_DEPLOY_HOST", DEFAULT_HOST))
    check.add_argument("--remote-root", default=os.getenv("JAVERT_DEPLOY_ROOT", DEFAULT_REMOTE_ROOT))
    check.add_argument("--ref", default="HEAD")
    check.add_argument("--timeout", type=float, default=10.0)
    check.add_argument("--max-diffs", type=int, default=8)
    check.add_argument("--json", action="store_true")
    check.set_defaults(func=check_command)

    artifact = commands.add_parser("artifact", help="从 HEAD 生成最小稀疏 Git 部署物")
    artifact.add_argument("--ref", default="HEAD")
    artifact.add_argument("--output")
    artifact.add_argument("--allow-dirty", action="store_true")
    artifact.set_defaults(func=artifact_command)

    install = commands.add_parser("install", help="在 62 上备份并安装稀疏 Git 部署物")
    install.add_argument("--artifact", required=True)
    install.add_argument("--remote-root", default=DEFAULT_REMOTE_ROOT)
    install.set_defaults(func=install_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except DeploymentSyncError as exc:
        print(f"deployment-sync: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
