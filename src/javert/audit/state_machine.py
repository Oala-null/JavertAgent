# -*- coding: utf-8 -*-
"""规则 status 状态机校验."""

from __future__ import annotations

from .rule import Status

# 显式列出每个状态可前往的状态 (forward 方向)
FORWARD: dict[Status, set[Status]] = {
    "drafting": {"ready"},
    "ready": {"validated"},
    "validated": set(),
    "abandoned": set(),
}

# abandoned 是 sink: 任意状态都可前往
ALL_TO_ABANDONED = {"drafting", "ready", "validated"}


class StatusTransitionError(ValueError):
    """状态转移被拒绝 (后退且无 force)."""


def is_forward(current: Status, target: Status) -> bool:
    """target 是否是 current 的前向状态."""
    return target in FORWARD.get(current, set())


def is_backward(current: Status, target: Status) -> bool:
    """target 是 current 的后退状态 (validated → ready, ready → drafting)."""
    if target == current:
        return False
    if target == "abandoned":
        return False
    if is_forward(current, target):
        return False
    # abandoned → anything 也是后退
    return True


def validate_transition(current: Status, target: Status, force: bool = False) -> None:
    """校验状态转移. 不合法时抛 StatusTransitionError."""
    if current == target:
        # 同状态视为 noop, 不抛
        return
    if target == "abandoned" and current in ALL_TO_ABANDONED:
        return
    if is_forward(current, target):
        return
    if force:
        return
    raise StatusTransitionError(
        f"{current} → {target} 是后退转移; 加 --force 才能确认"
    )
