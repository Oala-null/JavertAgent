#!/usr/bin/env python3
"""只停止应用所属账号、指定目录的唯一 javert web；不读环境或停止批跑。"""
import argparse
import os
from pathlib import Path
import signal
import time


def stop(root):
    if not Path('/proc').is_dir():
        raise RuntimeError('仅支持院内 Linux')
    root = root.resolve()
    found = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != os.getuid() or (entry / 'cwd').resolve() != root:
                continue
            command = (entry / 'cmdline').read_bytes()
            args = command.split(b'\0')
            if b'web' in args and (b'javert.cli' in args or any(Path(a.decode(errors='replace')).name == 'javert' for a in args)):
                found.append((entry, command))
        except (FileNotFoundError, ProcessLookupError):
            continue
    if not found:
        print('NO_MATCHING_WEB；未停止任何进程；安装器还会验证活动任务')
        return
    if len(found) != 1:
        raise RuntimeError('匹配到多个 Web，请按原运维手册核查，未停止任何进程')
    entry, command = found[0]
    identity = (entry / 'stat').read_text().rsplit(')', 1)[1].split()[19]
    if (entry / 'cmdline').read_bytes() != command or (entry / 'cwd').resolve() != root:
        raise RuntimeError('Web 进程身份发生变化，未发送信号')
    os.kill(int(entry.name), signal.SIGTERM)
    for _ in range(150):
        try:
            state = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            if state[19] != identity or state[0] == 'Z':
                print('WEB_STOPPED'); return
        except FileNotFoundError:
            print('WEB_STOPPED'); return
        time.sleep(0.2)
    raise RuntimeError('Web 尚未退出，未发送 SIGKILL；请检查服务管理器是否自动重启')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/home/admin2/Javert'))
    try:
        stop(parser.parse_args().root)
    except (RuntimeError, OSError) as exc:
        raise SystemExit('STOP: ' + str(exc))
