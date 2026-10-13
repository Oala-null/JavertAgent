"""合成 /proc：不得读取真实进程或发送真实信号。"""
from contextlib import ExitStack
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


@pytest.fixture(params=['stop_web', 'installer'])
def guard_env(tmp_path, monkeypatch, request):
    script = ROOT / 'delivery/web_performance_v2' / (request.param + '.py')
    spec = importlib.util.spec_from_file_location('synthetic_' + request.param, script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    root = tmp_path / 'hospital'
    root.mkdir()
    proc = tmp_path / 'proc'
    proc.mkdir()
    monkeypatch.setattr(mod, 'Path', lambda value: proc if str(value) == '/proc' else Path(value))
    denied = set()
    resolve, read_bytes = Path.resolve, Path.read_bytes

    def guarded_resolve(path, *args, **kwargs):
        if path in denied:
            raise PermissionError(13, 'Permission denied', str(path))
        return resolve(path, *args, **kwargs)

    def guarded_read(path):
        if path in denied:
            raise PermissionError(13, 'Permission denied', str(path))
        return read_bytes(path)

    monkeypatch.setattr(Path, 'resolve', guarded_resolve)
    monkeypatch.setattr(Path, 'read_bytes', guarded_read)
    signals = []

    def signal_process(pid, sig):
        signals.append((pid, sig))
        path = proc / str(pid) / 'stat'
        path.write_text(path.read_text().replace('(python) S', '(python) Z'))

    monkeypatch.setattr(mod.os, 'kill', signal_process)

    def add(pid, command=b'python\0-m\0javert.cli\0web\0', cwd=None):
        entry = proc / str(pid)
        entry.mkdir()
        (entry / 'cwd').symlink_to(cwd or root, target_is_directory=True)
        (entry / 'cmdline').write_bytes(command)
        (entry / 'stat').write_text(f'{pid} (python) S ' + '0 ' * 18 + '42')
        return entry

    def run():
        if request.param == 'stop_web':
            mod.stop(root)
        else:
            with ExitStack() as stack:
                mod.idle_locks(root, stack)

    return request.param, root, add, denied, signals, run


def test_unrelated_unreadable_cwd_does_not_block(guard_env):
    _, _, add, denied, signals, run = guard_env
    entry = add(51001, b'/usr/lib/systemd/systemd\0--user\0')
    denied.add(entry / 'cwd')
    run()
    assert signals == []


@pytest.mark.parametrize('field', ['cwd', 'cmdline'])
def test_unverifiable_application_still_blocks_before_any_signal(guard_env, field):
    mode, _, add, denied, signals, run = guard_env
    if mode == 'stop_web':
        add(51001)
    entry = add(51002)
    denied.add(entry / field)
    with pytest.raises(RuntimeError, match='无法核验 PID 51002.*权限不足'):
        run()
    assert signals == []


def test_readable_application_still_stops_or_blocks(guard_env):
    mode, _, add, _, signals, run = guard_env
    add(51001)
    if mode == 'stop_web':
        run()
        assert len(signals) == 1 and signals[0][0] == 51001
    else:
        with pytest.raises(RuntimeError, match='仍有 Web'):
            run()
        assert signals == []


def test_other_instance_is_not_stopped_or_blocked(guard_env, tmp_path):
    _, _, add, _, signals, run = guard_env
    other = tmp_path / 'other'
    other.mkdir()
    add(51001, cwd=other)
    run()
    assert signals == []


def test_unrelated_permission_denial_does_not_hide_active_app(guard_env):
    mode, _, add, denied, signals, run = guard_env
    unrelated = add(51001, b'/usr/lib/systemd/systemd\0--user\0')
    denied.add(unrelated / 'cwd')
    add(51002)
    if mode == 'stop_web':
        run()
        assert len(signals) == 1 and signals[0][0] == 51002
    else:
        with pytest.raises(RuntimeError, match='仍有 Web'):
            run()
        assert signals == []


def test_multiple_web_processes_are_never_signalled(guard_env):
    _, _, add, _, signals, run = guard_env
    add(51001)
    add(51002)
    with pytest.raises(RuntimeError):
        run()
    assert signals == []
