from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json

import pytest

from belowone.meter import BudgetExceeded, Meter


def test_provider_totals_and_call_records():
    meter = Meter('15')
    reservation = meter.reserve('OpenRouter', Decimal('3'))
    meter.record('openrouter', 'served-a', 10, 20, Decimal('1.25'), 0.4, reservation)
    meter.record('kimi', 'served-b', 4, 7, '0', 0.2)
    second = meter.reserve('openrouter', '1')
    meter.record('openrouter', 'served-c', 2, 3, '0.75', 0.1, second)
    report = meter.report()
    assert report['providers']['openrouter']['cost_usd'] == '2.00'
    assert report['providers']['openrouter']['input_tokens'] == 12
    assert report['providers']['openrouter']['output_tokens'] == 23
    assert report['providers']['openrouter']['calls'] == 2
    assert report['providers']['kimi']['calls'] == 1
    assert report['reserved_usd'] == '0'
    assert [call['model'] for call in report['calls']] == ['served-a', 'served-b', 'served-c']
    assert report['calls'][0]['latency'] == 0.4
    assert json.loads(json.dumps(report)) == report
    report['calls'][0]['model'] = 'mutated'
    assert meter.report()['calls'][0]['model'] == 'served-a'


def test_reservation_refuses_before_send_and_releases_unused_budget():
    meter = Meter('15')
    first = meter.reserve('openrouter', '14.9999999999999999999999999999')
    with pytest.raises(BudgetExceeded):
        meter.reserve('openrouter', '0.0000000000000000000000000002')
    assert meter.report()['calls'] == []
    meter.cancel(first)
    exact = meter.reserve('openrouter', '15')
    meter.record('openrouter', 'synthetic', 1, 1, '15', 0.1, exact)
    with pytest.raises(BudgetExceeded):
        meter.reserve('openrouter', '0.0000000000000000000000000001')


def test_actual_cost_settlement_frees_budget():
    meter = Meter('15')
    first = meter.reserve('openrouter', '14')
    meter.record('openrouter', 'synthetic', 1, 1, '2', 0.1, first)
    second = meter.reserve('openrouter', '13')
    meter.cancel(second)
    assert meter.report()['providers']['openrouter']['cost_usd'] == '2'
    assert meter.report()['reserved_usd'] == '0'


def test_concurrent_worst_case_reservations_never_exceed_cap():
    meter = Meter('15')
    def reserve(_):
        try:
            return meter.reserve('openrouter', '1')
        except BudgetExceeded:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        reservations = list(pool.map(reserve, range(40)))
    accepted = [hold for hold in reservations if hold is not None]
    assert len(accepted) == 15
    assert meter.report()['reserved_usd'] == '15'
    def settle(hold):
        meter.record('openrouter', 'synthetic', 1, 2, '0.5', 0.01, hold)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(settle, accepted))
    assert meter.report()['providers']['openrouter']['cost_usd'] == '7.5'
    assert meter.report()['reserved_usd'] == '0'


def test_unreserved_foreign_double_and_mismatched_settlement_rejected():
    meter = Meter('15')
    with pytest.raises(ValueError, match='reservation'):
        meter.record('openrouter', 'm', 1, 1, '1', 0.1)
    hold = meter.reserve('openrouter', '1')
    with pytest.raises(ValueError):
        Meter('15').cancel(hold)
    with pytest.raises(ValueError):
        meter.record('kimi', 'm', 1, 1, '0', 0.1, hold)
    meter.record('openrouter', 'm', 1, 1, '1', 0.1, hold)
    with pytest.raises(ValueError):
        meter.record('openrouter', 'm', 1, 1, '1', 0.1, hold)
    with pytest.raises(ValueError):
        meter.cancel(hold)


def test_invalid_values_and_underestimated_charge_are_honest():
    for cap in ('-1', 'NaN', 'Infinity'):
        with pytest.raises(ValueError):
            Meter(cap)
    meter = Meter('1')
    for amount in ('-1', 'NaN', 'Infinity'):
        with pytest.raises(ValueError):
            meter.reserve('openrouter', amount)
    with pytest.raises(ValueError):
        meter.record('kimi', 'm', -1, 2, '0', 0.1)
    with pytest.raises(ValueError):
        meter.record('kimi', 'm', 1, 2, '0', float('nan'))
    hold = meter.reserve('openrouter', '0.5')
    with pytest.raises(BudgetExceeded, match='exceeded reservation'):
        meter.record('openrouter', 'm', 1, 1, '1.2', 0.1, hold)
    assert meter.report()['providers']['openrouter']['cost_usd'] == '1.2'
    assert meter.report()['budget_breached'] is True
    with pytest.raises(BudgetExceeded):
        meter.reserve('openrouter', '0')


def test_model_and_provider_strings_never_expose_credentials(monkeypatch):
    monkeypatch.setenv('KIMI_API_KEY', 'synthetic-private-value')
    meter = Meter('15')
    meter.record('kimi', 'synthetic-private-value', 1, 1, '0', 0.1)
    assert 'synthetic-private-value' not in json.dumps(meter.report())
