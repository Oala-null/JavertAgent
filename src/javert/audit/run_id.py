# -*- coding: utf-8 -*-
"""run_id 生成 — `aud_` + 12 char nanoid."""

from __future__ import annotations

from nanoid import generate

_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
_LEN = 12


def new_run_id() -> str:
    """返回 aud_<nanoid>."""
    return f"aud_{generate(_ALPHABET, _LEN)}"
