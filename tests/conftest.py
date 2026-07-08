# -*- coding: utf-8 -*-
"""全局测试前置.

create_app(with_mssql=True) 自 harden-onsite-redlines 起要求非默认 session secret
(生产 fail-fast). 测试进程给个固定测试值, 不依赖开发机 .env.
"""

from __future__ import annotations

import os

os.environ.setdefault("JAVERT_SESSION_SECRET", "pytest-session-secret-not-for-prod")
