from scripts.deployment_sync import DEPLOY_SCOPES, compare_head_state


def test_runtime_scope_contains_promise_governance_assets():
    assert "tests/promise_cases" in DEPLOY_SCOPES
    assert "截至20260401诊疗项目.xls" in DEPLOY_SCOPES
    assert "截至20260701诊疗项目.xls" in DEPLOY_SCOPES
    assert "docs/templates/260611医保基金监管规则框架总表.xlsx" not in DEPLOY_SCOPES


def test_equal_heads_and_clean_remote_are_synced():
    head = "a" * 40

    report = compare_head_state(
        head,
        {"head": head, "branch": "production-62", "dirty": []},
    )

    assert report["synced"] is True
    assert report["remote_branch"] == "production-62"


def test_different_heads_are_not_synced():
    report = compare_head_state(
        "a" * 40,
        {"head": "b" * 40, "branch": "production-62", "dirty": []},
    )

    assert report["synced"] is False


def test_same_head_with_remote_overwrite_is_not_synced():
    head = "a" * 40

    report = compare_head_state(
        head,
        {
            "head": head,
            "branch": "production-62",
            "dirty": [" M src/javert/data/hub_source.py"],
        },
    )

    assert report["synced"] is False
    assert report["remote_dirty"] == [" M src/javert/data/hub_source.py"]
