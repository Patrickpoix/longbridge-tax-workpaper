from collections import defaultdict, deque
import csv
from decimal import Decimal as D
import json
import subprocess
import sys

import pytest

from longbridge_tax_workpaper import interim


def event(side, qty, cash, sid='US:ABC'):
    return {'sid': sid, 'side': side, 'quantity': D(qty), 'cash': D(cash), 'reference': 'test'}


def test_fifo_and_average_apply_buy_and_sell_fees():
    results = {}
    for method in ('fifo', 'moving_average'):
        pools = defaultdict(deque)
        interim.apply_event(pools, event('BUY', '10', '-101'), method)
        interim.apply_event(pools, event('BUY', '10', '-201'), method)
        results[method] = interim.apply_event(pools, event('SELL', '10', '179'), method)
    assert results['fifo']['pnl'] == D('78')
    assert results['moving_average']['pnl'] == D('28')


def test_split_preserves_basis_and_option_expiry_realizes_loss():
    pools = defaultdict(deque)
    interim.apply_event(pools, event('BUY', '10', '-101'), 'fifo')
    split = {**event('SPLIT', '10', '0'), 'ratio': D('4')}
    interim.apply_event(pools, split, 'fifo')
    row = interim.apply_event(pools, event('SELL', '40', '0'), 'fifo')
    assert row['cost'] == D('101')
    assert row['pnl'] == D('-101')
    with pytest.raises(ValueError, match='拆股前数量不符'):
        interim.apply_event(pools, split, 'fifo')


def test_split_is_applied_before_same_day_trade():
    trade = {'Position': 'LONG', 'DirectionCode': 'SELL', 'CurrencySettle': 'USD',
             'CurrencyCode': 'USD', 'MarketCode': 'US', 'Code': 'ABC', 'TradeQuantity': '4',
             'TradeDate': '2026.01.02', 'ClearAmountSettle': '40', 'ContractNo': 'sell',
             'TradeDones': [{'TradeDoneTime': '2026-01-02 15:00:00', 'TradeQuantity': '4', 'TradePrice': '10'}]}
    changes = [{'Date': '2026.01.02', 'Code': 'ABC', 'TypeEn': 'Credit Corporate Action Shares', 'Quantity': '4'},
               {'Date': '2026.01.02', 'Code': 'ABC', 'TypeEn': 'Debit Corporate Action Shares', 'Quantity': '-1'}]
    events, _ = interim.trade_events({'StockTradeSums': [{'Trades': [trade]}],
                                     'EquityHoldingChangeSums': [{'MarketCode': 'US', 'EquityHoldingChanges': changes}]})
    pools = defaultdict(deque, {'US:ABC': deque([[D('1'), D('20')]])})
    assert [event['side'] for event in events] == ['SPLIT', 'SELL']
    interim.apply_event(pools, events[0], 'fifo')
    assert interim.apply_event(pools, events[1], 'fifo')['pnl'] == D('20')


def test_unknown_reward_cost_does_not_become_zero_cost():
    pools = defaultdict(deque)
    interim.apply_event(pools, event('UNKNOWN_CHANGE', '0.5', '0'), 'fifo')
    result = interim.apply_event(pools, event('SELL', '0.5', '9'), 'fifo')
    assert result['cost'] is None
    assert result['pnl'] is None


def test_settlement_day_is_reconciled_without_double_counting(tmp_path):
    opening = tmp_path / 'opening.csv'
    opening.write_text('security_id,quantity,total_cost\nUS:ABC,2,20\n', encoding='utf-8')
    def statement(date, quantity, trades=()):
        return {'Date': date, 'EquityHoldingSums': [{'MarketCode': 'US', 'EquityTypeCode': 'ST',
                'EquityHoldings': [{'Code': 'ABC', 'LedgerQuantity': str(quantity)}]}],
                'StockTradeSums': [{'Trades': list(trades)}]}
    trade = {'Position': 'LONG', 'DirectionCode': 'SELL', 'CurrencySettle': 'USD',
             'CurrencyCode': 'USD', 'MarketCode': 'US', 'Code': 'ABC', 'TradeQuantity': '2',
             'TradeDate': '2026.10.30', 'ClearAmountSettle': '29', 'ContractNo': 'contract',
             'TradeDones': [{'TradeDoneTime': '2026-10-30 17:34:33.000000', 'TradeQuantity': '2', 'TradePrice': '15'}]}
    fills = [{'symbol': 'ABC.US', 'side': 'Sell', 'trade_done_at': '2026-10-30T17:34:33Z',
              'quantity': '2', 'price': '15', 'trade_id': 'fill'}]
    statements = [statement('2025.12', 2), statement('2026.10', 2)]
    daily = statement('2026.11.02', 0, [trade])
    rows, issues, missing, extra, _ = interim.analyze(statements, opening, 'fifo', fills, [daily], target_year=2026)
    assert rows[0]['pnl'] == D('9')
    assert issues == missing == extra == []
    with pytest.raises(ValueError, match='重复'):
        interim.analyze(statements, opening, 'fifo', fills, [daily, daily], target_year=2026)


def test_scenario_keeps_unknown_cost_and_uses_explicit_fx():
    pools = {'US:ABC': deque([[D('2'), None]])}
    quotes = [{'symbol': 'ABC.US', 'last_done': '10', 'timestamp': '2026-11-05T16:00:00-05:00'}]
    result = interim.scenario({'USD': D('100'), 'HKD': D('50')}, pools, quotes,
                              {'USD': D('7'), 'HKD': D('0.9')})
    assert result['known_pnl_cny'] == D('745')
    assert result['positions'][0]['estimated_pnl'] is None


def test_scenario_uses_requested_fifo_quantity_without_changing_holdings():
    pools = {'US:ABC': deque([[D('2'), D('20')], [D('3'), D('60')]])}
    quotes = [{'symbol': 'ABC.US', 'last_done': '15', 'timestamp': '2026-11-05T16:00:00-05:00'}]
    result = interim.scenario({'USD': D('100')}, pools, quotes,
                              {'USD': D('7'), 'HKD': D('0.9')}, [('ABC.US', D('3'))])
    assert result['sales'][0]['allocated_cost'] == D('40')
    assert result['sales'][0]['estimated_pnl'] == D('5')
    assert result['known_pnl_after_sales_cny'] == D('735')
    assert list(pools['US:ABC']) == [[D('2'), D('20')], [D('3'), D('60')]]
    with pytest.raises(ValueError, match='持仓不足'):
        interim.scenario({}, pools, quotes, {'USD': D('7'), 'HKD': D('0.9')}, [('ABC.US', D('6'))])


def test_average_combines_opening_lots_before_first_sale(tmp_path):
    opening = tmp_path / 'opening.csv'
    opening.write_text('security_id,quantity,total_cost\nUS:ABC,1,10\nUS:ABC,1,30\n', encoding='utf-8')
    def statement(date, quantity, trades=()):
        return {'Date': date, 'EquityHoldingSums': [{'MarketCode': 'US', 'EquityTypeCode': 'ST',
            'EquityHoldings': [{'Code': 'ABC', 'LedgerQuantity': str(quantity)}]}],
            'StockTradeSums': [{'Trades': list(trades)}]}
    trade = {'Position': 'LONG', 'DirectionCode': 'SELL', 'CurrencySettle': 'USD',
             'CurrencyCode': 'USD', 'MarketCode': 'US', 'Code': 'ABC', 'TradeQuantity': '1',
             'TradeDate': '2026.01.02', 'ClearAmountSettle': '25', 'ContractNo': 'one',
             'TradeDones': [{'TradeDoneTime': '2026-01-02 15:00:00', 'TradeQuantity': '1', 'TradePrice': '25'}]}
    fills = [{'symbol': 'ABC.US', 'side': 'Sell', 'trade_done_at': '2026-01-02T15:00:00Z',
              'quantity': '1', 'price': '25', 'trade_id': 'one'}]
    rows, issues, missing, extra, _ = interim.analyze(
        [statement('2025.12', 2), statement('2026.01', 1, [trade])], opening, 'moving_average', fills,
        target_year=2026)
    assert rows[0]['pnl'] == D('5')
    assert issues == missing == extra == []


def test_fifo_orders_interleaved_fills_and_allocates_order_cost(tmp_path):
    opening = tmp_path / 'opening.csv'
    opening.write_text('security_id,quantity,total_cost\n', encoding='utf-8')
    holdings = lambda qty: [{'MarketCode': 'US', 'EquityTypeCode': 'ST',
                             'EquityHoldings': [{'Code': 'ABC', 'LedgerQuantity': str(qty)}]}]
    def trade(side, ref, cash, fills):
        return {'Position': 'LONG', 'DirectionCode': side, 'CurrencySettle': 'USD',
                'CurrencyCode': 'USD', 'MarketCode': 'US', 'Code': 'ABC',
                'TradeQuantity': str(sum(D(x[1]) for x in fills)), 'TradeDate': '2026.01.02',
                'ClearAmountSettle': cash, 'ContractNo': ref,
                'TradeDones': [{'TradeDoneTime': f'2026-01-02 15:0{t}:00',
                                'TradeQuantity': q, 'TradePrice': price} for t, q, price in fills]}
    buy = trade('BUY', 'buy', '-30.30', [(1, '1', '10'), (3, '1', '20')])
    sell = trade('SELL', 'sell', '14.90', [(2, '1', '15')])
    statements = [{'Date': '2025.12', 'EquityHoldingSums': holdings(0)},
                  {'Date': '2026.01', 'EquityHoldingSums': holdings(1),
                   'StockTradeSums': [{'Trades': [buy, sell]}]}]
    fills = [{'symbol': 'ABC.US', 'side': side, 'trade_done_at': f'2026-01-02T15:0{t}:00Z',
              'quantity': '1', 'price': price, 'trade_id': str(t)}
             for side, t, price in [('Buy', 1, '10'), ('Sell', 2, '15'), ('Buy', 3, '20')]]
    rows, issues, missing, extra, pools = interim.analyze(
        statements, opening, 'fifo', fills, target_year=2026)
    assert rows[0]['cost'] == D('10.1')
    assert rows[0]['pnl'] == D('4.8')
    assert pools['US:ABC'][0][1] == D('20.2')
    assert issues == missing == extra == []


def test_mismatched_holdings_make_later_cost_unknown(tmp_path):
    opening = tmp_path / 'opening.csv'
    opening.write_text('security_id,quantity,total_cost\n', encoding='utf-8')
    def statement(date, qty, trades=()):
        return {'Date': date, 'EquityHoldingSums': [{'MarketCode': 'US', 'EquityTypeCode': 'ST',
                'EquityHoldings': [{'Code': 'ABC', 'LedgerQuantity': str(qty)}]}],
                'StockTradeSums': [{'Trades': list(trades)}]}
    sell = {'Position': 'LONG', 'DirectionCode': 'SELL', 'CurrencySettle': 'USD',
            'CurrencyCode': 'USD', 'MarketCode': 'US', 'Code': 'ABC', 'TradeQuantity': '1',
            'TradeDate': '2026.02.02', 'ClearAmountSettle': '20', 'ContractNo': 'sell',
            'TradeDones': [{'TradeDoneTime': '2026-02-02 15:00:00', 'TradeQuantity': '1', 'TradePrice': '20'}]}
    fills = [{'symbol': 'ABC.US', 'side': 'Sell', 'trade_done_at': '2026-02-02T15:00:00Z',
              'quantity': '1', 'price': '20', 'trade_id': 'one'}]
    rows, issues, missing, extra, _ = interim.analyze(
        [statement('2025.12', 0), statement('2026.01', 1), statement('2026.02', 0, [sell])],
        opening, 'fifo', fills, target_year=2026)
    assert len(issues) == 1
    assert rows[0]['pnl'] is None
    assert rows[0]['cost'] is None
    assert missing == extra == []


def test_unknown_partial_removal_makes_remaining_cost_unknown():
    pools = defaultdict(deque)
    interim.apply_event(pools, event('BUY', '2', '-20'), 'fifo')
    removal = interim.apply_event(pools, event('UNKNOWN_CHANGE', '-1', '0'), 'fifo')
    assert removal['pnl'] is None
    later_sale = interim.apply_event(pools, event('SELL', '1', '15'), 'fifo')
    assert later_sale['pnl'] is None


def test_installed_module_reports_incomplete_cost_in_output(tmp_path):
    statements = tmp_path / 'statements'
    statements.mkdir()
    def make_statement(month, quantity, trades=()):
        return {'Date': month, 'FileType': '1', 'MemberInfo': {'Aaid': 'test'},
                'EquityHoldingSums': [{'MarketCode': 'US', 'EquityTypeCode': 'ST',
                    'EquityHoldings': [{'Code': 'ABC', 'BeginQuantity': '0', 'LedgerQuantity': str(quantity)}]}],
                'StockTradeSums': [{'Trades': list(trades)}]}
    sell = {'Position': 'LONG', 'DirectionCode': 'SELL', 'CurrencySettle': 'USD',
            'CurrencyCode': 'USD', 'MarketCode': 'US', 'Code': 'ABC', 'TradeQuantity': '1',
            'TradeDate': '2026.02.02', 'ClearAmountSettle': '20', 'ContractNo': 'sell',
            'TradeDones': [{'TradeDoneTime': '2026-02-02 15:00:00', 'TradeQuantity': '1', 'TradePrice': '20'}]}
    for name, data in [('202601', make_statement('2026.01', 1)),
                       ('202602', make_statement('2026.02', 0, [sell]))]:
        (statements / (name + '.json')).write_text(json.dumps(data), encoding='utf-8')
    fills = [{'symbol': 'ABC.US', 'side': 'Sell', 'trade_done_at': '2026-02-02T15:00:00Z',
              'quantity': '1', 'price': '20', 'trade_id': 'one'}]
    executions = tmp_path / 'executions.json'
    executions.write_text(json.dumps(fills), encoding='utf-8')
    output = tmp_path / 'result'
    result = subprocess.run([sys.executable, '-m', 'longbridge_tax_workpaper.interim',
        '--statements', str(statements), '--history-start', '202601', '--executions', str(executions),
        '--year', '2026', '--through-month', '2', '--output', str(output)],
        capture_output=True)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    report = json.loads((output / 'summary.json').read_text(encoding='utf-8'))
    assert report['fifo']['review_status'] == '需要复核'
    assert report['fifo']['unknown_disposals'] == 1
    assert report['fifo']['known_pnl'] == {}


def test_cli_writes_sale_scenario_and_disposal_details(tmp_path, monkeypatch, capsys):
    statements = tmp_path / 'statements'
    statements.mkdir()
    def trade(side, ref, date, qty, price, net):
        return {'Position': 'LONG', 'DirectionCode': side, 'CurrencySettle': 'USD',
                'CurrencyCode': 'USD', 'MarketCode': 'US', 'Code': 'ABC', 'TradeQuantity': qty,
                'TradeDate': date.replace('-', '.'), 'ClearAmountSettle': net, 'ContractNo': ref,
                'TradeDones': [{'TradeDoneTime': date + ' 15:00:00', 'TradeQuantity': qty, 'TradePrice': price}]}
    def statement(month, opening, closing, tx):
        return {'Date': month, 'FileType': '1', 'MemberInfo': {'Aaid': 'test'},
                'EquityHoldingSums': [{'MarketCode': 'US', 'EquityTypeCode': 'ST',
                    'EquityHoldings': [{'Code': 'ABC', 'BeginQuantity': opening, 'LedgerQuantity': closing}]}],
                'StockTradeSums': [{'Trades': [tx]}]}
    records = [('202601', statement('2026.01', '0', '2', trade('BUY', 'b', '2026-01-02', '2', '10', '-20'))),
               ('202602', statement('2026.02', '2', '1', trade('SELL', 's', '2026-02-02', '1', '15', '14')))]
    for name, value in records:
        (statements / (name + '.json')).write_text(json.dumps(value), encoding='utf-8')
    fills = [{'symbol': 'ABC.US', 'side': side, 'trade_done_at': date + 'T15:00:00Z',
              'quantity': qty, 'price': price, 'trade_id': side}
             for side, date, qty, price in [('Buy', '2026-01-02', '2', '10'), ('Sell', '2026-02-02', '1', '15')]]
    executions = tmp_path / 'executions.json'
    executions.write_text(json.dumps(fills), encoding='utf-8')
    quotes = tmp_path / 'quotes.json'
    quotes.write_text(json.dumps([{'symbol': 'ABC.US', 'last_done': '16', 'timestamp': '2026-02-03T16:00:00-05:00'}]), encoding='utf-8')
    output = tmp_path / 'output'
    monkeypatch.setattr(sys, 'argv', ['interim', '--statements', str(statements), '--history-start', '202601',
        '--executions', str(executions), '--quotes', str(quotes), '--fx', 'USD=7', '--fx', 'HKD=0.9',
        '--sell', 'ABC.US=1', '--year', '2026', '--through-month', '2', '--output', str(output)])
    interim.main()
    display = json.loads(capsys.readouterr().out)
    report = json.loads((output / 'summary.json').read_text(encoding='utf-8'))
    assert display['fifo']['scenario']['known_pnl_after_sales_cny'] == '70'
    assert report['fifo']['review_status'] == '计算资料未发现差异'
    assert report['fifo']['known_pnl'] == {'USD': '4'}
    with (output / 'fifo_disposals.csv').open(encoding='utf-8-sig', newline='') as stream:
        disposals = list(csv.DictReader(stream))
    assert len(disposals) == 1
    assert disposals[0]['date'] == '2026-02-02'
    assert disposals[0]['pnl'] == '4'
