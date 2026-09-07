from copy import deepcopy

import pytest

from javert.onboarding.reviewed_pdf import validate_reviewed_pdf_bundle


@pytest.fixture
def bundle():
    return {
        'page_kinds': {'1': 'clinical', '2': 'fee', '3': 'financial'},
        'fee_page_counts': {'2': 2}, 'expected_fee_total': '30.00',
        'notes': [{'source_page': 1, '内容': '治疗计划，不等同实际执行'}],
        'fees': [
            {'source_page': 2, 'feedetl_sn': 'F1', 'medins_list_name': '同一药品',
             'med_list_codg': '00123', 'cnt': '1', 'pric': '10', 'det_item_fee_sumamt': '10'},
            {'source_page': 2, 'feedetl_sn': 'F2', 'medins_list_name': '同一药品',
             'med_list_codg': '00123', 'cnt': '1', 'pric': '20', 'det_item_fee_sumamt': '20'},
        ],
    }


def test_complete_bundle_keeps_separate_same_name_rows(bundle):
    assert validate_reviewed_pdf_bundle(bundle)['fee_rows'] == 2


@pytest.mark.parametrize('problem', ['subset', 'financial_page', 'financial_text', 'duplicate', 'amount', 'code', 'clinical_missing'])
def test_rejects_incomplete_or_corrupted_publication(bundle, problem):
    b = deepcopy(bundle)
    if problem == 'subset': b['fees'].pop()
    elif problem == 'financial_page': b['notes'].append({'source_page': 3, '内容': '伪装成病历附件'})
    elif problem == 'financial_text': b['notes'][0]['内容'] = '住院病人费用总账明细单'
    elif problem == 'duplicate': b['fees'][1]['feedetl_sn'] = 'F1'
    elif problem == 'amount': b['fees'][0]['det_item_fee_sumamt'] = 'NaN'
    elif problem == 'code': b['fees'][0]['med_list_codg'] = '001N0123456789abcdef99'
    elif problem == 'clinical_missing': b['notes'] = []
    with pytest.raises(ValueError): validate_reviewed_pdf_bundle(b)
