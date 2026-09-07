"""人工逐页复核后的 PDF 导入门禁；不把 OCR 或规则命中切片冒充完整病例。"""

from collections import Counter
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re


def _number(value) -> Decimal:
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError('费用数字不可解析') from exc
    if not number.is_finite():
        raise ValueError('费用数字必须有限')
    return number


def validate_reviewed_pdf_bundle(bundle: dict) -> dict:
    """核对独立复核的页分类/行数/原单总额；调用方必须在任何发布写入前调用。"""
    kinds = {int(k): v for k, v in bundle['page_kinds'].items()}
    counts = {int(k): int(v) for k, v in bundle['fee_page_counts'].items()}
    notes, fees = bundle['notes'], bundle['fees']
    if not kinds or set(kinds.values()) - {'clinical', 'fee', 'financial', 'administrative'}:
        raise ValueError('来源页分类缺失或未知')
    if set(kinds) != set(range(1, max(kinds) + 1)):
        raise ValueError('来源页分类不完整')
    if set(counts) != {p for p, kind in kinds.items() if kind == 'fee'} or any(n <= 0 for n in counts.values()):
        raise ValueError('费用页复核行数不完整')
    for note in notes:
        if kinds.get(int(note['source_page'])) != 'clinical':
            raise ValueError('非临床页混入文书')
        if re.search(r'费用总[帐账].*明细|基金支付|个人账户支付', str(note.get('内容', ''))):
            raise ValueError('财务正文混入文书')
    if {int(n['source_page']) for n in notes} != {p for p, kind in kinds.items() if kind == 'clinical'}:
        raise ValueError('临床页缺失')
    if Counter(int(f['source_page']) for f in fees) != Counter(counts):
        raise ValueError('费用页漏行或多行')
    ids = [f['feedetl_sn'] for f in fees]
    if any(not i for i in ids) or len(set(ids)) != len(ids):
        raise ValueError('费用源行ID缺失或重复')
    total = Decimal('0')
    for fee in fees:
        if not str(fee.get('medins_list_name', '')).strip():
            raise ValueError('收费名称缺失')
        code = fee.get('med_list_codg', '')
        if not isinstance(code, str) or (code and not re.fullmatch(r'[A-Za-z0-9.+/\-]+', code)):
            raise ValueError('收费编码不是原始文本')
        if re.search(r'[NOPDEJK][0-9a-f]{16}', code):
            raise ValueError('收费编码被隐私文本替换污染')
        if not code and not fee.get('review_note'):
            raise ValueError('编码缺失须显式标注待核')
        amount = _number(fee['det_item_fee_sumamt'])
        calculated = (_number(fee['cnt']) * _number(fee['pric'])).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        if amount != calculated:
            raise ValueError('收费数量乘单价不等于金额')
        total += amount
    if total != _number(bundle['expected_fee_total']):
        raise ValueError('费用明细与原单总额不平')
    return {'note_rows': len(notes), 'fee_rows': len(fees), 'fee_total': str(total), 'fee_pages': len(counts)}
