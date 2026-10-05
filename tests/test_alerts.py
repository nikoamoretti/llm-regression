from runner.alerts import quality_alert, render_status_line


def test_critical_requires_ci_and_sample_size() -> None:
    decision = quality_alert(absolute_delta=-0.12, ci_high=-0.01, n_tasks=50)
    assert decision.severity == "critical"
    assert "CONFIRMED" in render_status_line(decision)


def test_small_n_is_not_confirmed() -> None:
    decision = quality_alert(absolute_delta=-0.12, ci_high=-0.01, n_tasks=10)
    assert decision.severity != "critical"


def test_infrastructure_suppresses() -> None:
    decision = quality_alert(
        absolute_delta=-0.5,
        ci_high=-0.4,
        n_tasks=50,
        infrastructure_incident=True,
    )
    assert decision.status == "suppressed"
