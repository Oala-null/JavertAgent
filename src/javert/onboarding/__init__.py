# -*- coding: utf-8 -*-
"""onboarding capability — 数据接入地基.

add-visual-schema-onboarding:
- manifest_loader  schema_manifest.yaml 校验加载 (数据模型唯一真相源)
- profiler         按需逐列剖析 + 日期格式逐列探测 (纯计算)
- join_preflight   连接预检 (键交集覆盖率 + 时间窗口 → 三态)
- alias_matcher    国标别名种子 fuzzy 命中 (自动预填映射)

GUI (/onboarding) 与 CLI (etl_import 校验阶段) 共用同一套实现 (设计 D6).
"""
