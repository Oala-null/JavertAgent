"""RuleRouter — 病案进来时, 决定哪些 Javert yaml 规则需要 LLM 详细审计.

两阶段管线的 Stage A:
  Stage A (这里, 秒级): 14372 字典 keyword 命中 + 高层 prune → 输出 javert rule subset
  Stage B (现有 audit-rule loop, 分钟级): 仅对 subset 跑 LLM agent

入口:
  from javert.routing import RuleRouter, PatientRecord
  router = RuleRouter.from_defaults()
  decision = router.route(record)
  decision.final_rules   # List[str], 应当跑的 javert rule ids
"""

from .adapter import build_patient_record_for_router, default_shi_zd_path
from .router import RuleRouter
from .types import FeeItem, PatientRecord, RouterDecision, TriggerEvidence

__all__ = [
    "RuleRouter",
    "PatientRecord",
    "FeeItem",
    "RouterDecision",
    "TriggerEvidence",
    "build_patient_record_for_router",
    "default_shi_zd_path",
]
