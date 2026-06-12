# -*- coding: utf-8 -*-
"""tests for web/auth.py: bcrypt hash/verify."""

from __future__ import annotations

from javert.web.auth import hash_password, verify_password


def test_hash_then_verify_roundtrip():
    h = hash_password("correct-horse-battery-staple")
    assert h.startswith("$2")
    assert verify_password("correct-horse-battery-staple", h)


def test_verify_wrong_password():
    h = hash_password("right")
    assert not verify_password("wrong", h)


def test_verify_garbage_hash_returns_false():
    assert not verify_password("anything", "not-a-bcrypt-hash")


def test_chinese_password_roundtrip():
    pw = "中文密码123"
    h = hash_password(pw)
    assert verify_password(pw, h)
    assert not verify_password("中文密码1234", h)


def test_different_runs_produce_different_hashes():
    a = hash_password("same")
    b = hash_password("same")
    assert a != b  # salt 差异
    assert verify_password("same", a)
    assert verify_password("same", b)
