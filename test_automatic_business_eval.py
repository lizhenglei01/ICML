"""Keep nonfatal provider metadata warnings separate from real run failures."""
from automatic_business_eval import fatal_runtime_events
from automatic_business_diagnose import equal_numeric_paragraph, same_members_wrong_required_order


def test_completed_hosted_run_can_have_a_metadata_warning():
    warning = {'type': 'item.completed', 'item': {'type': 'error', 'message':
        'Model metadata for `qwen4-max` not found. Defaulting to fallback metadata; '
        'this can degrade performance and cause issues.'}}
    assert fatal_runtime_events([warning], 'qwen4-max') == []
    assert fatal_runtime_events([warning], 'deepseek-v4-pro') == [warning]


def test_real_failures_are_not_hidden_by_metadata_warning_filter():
    failures = [
        {'type': 'turn.failed', 'error': {'message': 'insufficient balance'}},
        {'type': 'error', 'message': 'authentication failed'},
        {'type': 'item.completed', 'item': {'type': 'error', 'message': 'tool host failed'}},
    ]
    assert fatal_runtime_events(failures, 'qwen4-max') == failures


def test_diagnostic_accepts_only_equal_decimal_text_and_keeps_other_requirements():
    expected = {'text': '平均值 — 390.329', 'style': 'Normal'}
    assert equal_numeric_paragraph(expected, {**expected, 'text': '平均值 — 390.3290'})
    for actual in (
        {**expected, 'text': '平均值 — 390.3289'},
        {**expected, 'text': '总量 — 390.329'},
        {**expected, 'style': 'Heading'},
        {**expected, 'text': '平均值 — NaN'},
        {**expected, 'text': '平均值 — =390.329'},
    ):
        assert not equal_numeric_paragraph(expected, actual)


def test_order_diagnosis_does_not_excuse_missing_or_duplicate_entities():
    assert same_members_wrong_required_order('A, B', 'B, A')
    for value in ('A, B', 'B', 'B, A, A', 'B, A, C', 'B,A'):
        assert not same_members_wrong_required_order('A, B', value)
