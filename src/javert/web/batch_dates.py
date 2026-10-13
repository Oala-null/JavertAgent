"""审计批次的只读日期展示；原始 tag 及审计结果保持不变。"""
from __future__ import annotations

from datetime import date, datetime
import re


UNKNOWN_DATE = "日期未知"
_DATE_TOKEN = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2}|\d{14}|\d{12}|\d{8}|\d{6})(?!\d)")


def batch_date(tag, created_at=None) -> str:
    """tag 内有效日期优先；没有时保留 created_at 原有日历日期，不转换时区。"""
    for match in _DATE_TOKEN.finditer(str(tag or "")):
        token = match.group(1)
        try:
            if "-" in token:
                return date.fromisoformat(token).isoformat()
            if len(token) in (6, 12):
                token = "20" + token[:6]
            else:
                token = token[:8]
            return date(int(token[:4]), int(token[4:6]), int(token[6:8])).isoformat()
        except ValueError:
            continue
    if isinstance(created_at, (date, datetime)):
        return created_at.date().isoformat() if isinstance(created_at, datetime) else created_at.isoformat()
    try:
        return datetime.fromisoformat(str(created_at)).date().isoformat()
    except (ValueError, TypeError):
        return UNKNOWN_DATE


def sidebar_dates(patient) -> list[str]:
    """日期集合含历史归属；旧调用未填集合时仍显示最近一次日期。"""
    return sorted(set(getattr(patient, "batch_dates", None) or [batch_date(patient.batch_tag, patient.updated_at)]), reverse=True)


def date_choices(patients) -> list[str]:
    values = {value for patient in patients for value in sidebar_dates(patient)}
    return sorted(values - {UNKNOWN_DATE}, reverse=True) + ([UNKNOWN_DATE] if UNKNOWN_DATE in values else [])


def all_patient_dates(store) -> dict[str, list[str]]:
    """一次窄列查询覆盖全部历史；不按 latest、verdict 或当前批次裁剪。"""
    from sqlalchemy import text

    engine = store.get_engine()
    if engine is None:
        raise RuntimeError("RESULT_SOURCE_UNAVAILABLE")
    with engine.connect() as connection:
        rows = connection.execute(text("""
            SELECT DISTINCT patient_id, batch_tag,
                   CONVERT(char(10), created_at, 23) AS created_day
            FROM javert_audit_runs
        """)).fetchall()
    dates: dict[str, set[str]] = {}
    for patient_id, tag, created_day in rows:
        dates.setdefault(str(patient_id), set()).add(batch_date(tag, created_day))
    return {pid: sorted(values, reverse=True) for pid, values in dates.items()}
