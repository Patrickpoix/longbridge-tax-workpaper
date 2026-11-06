"""用长桥月结单 JSON 和上年成本底稿生成年中已实现盈亏复核表。"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict, deque
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .text_io import configure_text_io


ZERO = Decimal('0')


def number(value):
    result = Decimal(str(value).replace(',', ''))
    if not result.is_finite():
        raise ValueError(f'数值必须有限: {value}')
    return result


def security(market, code, option=False):
    code = str(code).strip().upper()
    if option:
        return 'OPT:' + code
    return market + ':' + (code.zfill(5) if market == 'HK' else code)


def holdings(statement):
    result = {}
    for group in statement.get('EquityHoldingSums', []):
        market = group['MarketCode']
        for row in group['EquityHoldings']:
            sid = security(market, row['Code'], group['EquityTypeCode'] == 'OP')
            quantity = number(row['LedgerQuantity'])
            if quantity < 0:
                raise ValueError(f'不支持空头持仓: {sid}')
            if quantity:
                if sid in result:
                    raise ValueError(f'重复持仓 {sid}')
                result[sid] = quantity
    return result


def opening_pools(opening_csv, initial):
    pools = defaultdict(deque)
    with opening_csv.open(encoding='utf-8-sig', newline='') as stream:
        for row in csv.DictReader(stream):
            quantity = number(row['quantity'])
            cost = number(row['total_cost'])
            if quantity < 0 or cost < 0 or (quantity == 0 and cost != 0):
                raise ValueError(f"无效期初成本批次: {row['security_id']}")
            if quantity > 0:
                pools[row['security_id']].append([quantity, cost])
    issues = []
    for sid in sorted(set(initial) | set(pools)):
        expected = initial.get(sid, ZERO)
        actual = sum((lot[0] for lot in pools[sid]), ZERO)
        if actual != expected:
            issues.append(f'{sid}: 上年底稿数量 {actual}，月结单数量 {expected}')
            # 不把不相符的底稿成本分配到实际持仓。
            pools[sid].clear()
            if expected > 0:
                pools[sid].append([expected, None])
    return pools, issues


def trade_events(statement):
    events = []
    fills = []
    for section, option in [('StockTradeSums', False), ('OptionTradeSums', True)]:
        for group in statement.get(section, []):
            for row in group['Trades']:
                if row.get('Position') != 'LONG' or row['DirectionCode'] not in ('BUY', 'SELL'):
                    raise ValueError(f"不支持的交易方向/持仓: {row['ContractNo']}")
                if row['CurrencySettle'] != row['CurrencyCode']:
                    raise ValueError(f"跨币种结算需人工处理: {row['ContractNo']}")
                sid = security(row['MarketCode'], row['Code'], option)
                done = row.get('TradeDones', [])
                if not done:
                    raise ValueError(f"缺少成交时间: {row['ContractNo']}")
                qty = number(row['TradeQuantity'])
                if sum((number(x['TradeQuantity']) for x in done), ZERO) != qty:
                    raise ValueError(f"成交数量不一致: {row['ContractNo']}")
                # 月结单只有整笔订单净额；按成交金额比例分摊费用，最后一笔吸收精度余差。
                weights = [number(item['TradeQuantity']) * number(item['TradePrice']) for item in done]
                if any(number(item['TradeQuantity']) <= 0 or number(item['TradePrice']) < 0 for item in done):
                    raise ValueError(f"无效成交数量或价格: {row['ContractNo']}")
                if not sum(weights, ZERO):
                    weights = [number(item['TradeQuantity']) for item in done]
                total_weight = sum(weights, ZERO)
                order_cash = number(row['ClearAmountSettle'])
                assigned_cash = ZERO
                for index, (item, weight) in enumerate(zip(done, weights), start=1):
                    cash = order_cash - assigned_cash if index == len(done) else order_cash * weight / total_weight
                    assigned_cash += cash
                    fills.append((sid, row['DirectionCode'], item['TradeDoneTime'][:19],
                                  number(item['TradeQuantity']), number(item['TradePrice'])))
                    events.append({'sid': sid, 'date': row['TradeDate'].replace('.', '-'),
                        'time': item['TradeDoneTime'], 'side': row['DirectionCode'],
                        'quantity': number(item['TradeQuantity']), 'cash': cash,
                        'currency': row['CurrencyCode'], 'reference': f"{row['ContractNo']}#{index:04d}"})
    for group in statement.get('EquityHoldingChangeSums', []):
        changes = group['EquityHoldingChanges']
        split_pairs = defaultdict(dict)
        for row in changes:
            if row['TypeEn'] in ('Credit Corporate Action Shares', 'Debit Corporate Action Shares'):
                pair = split_pairs[(row['Date'], row['Code'])]
                if row['TypeEn'] in pair:
                    raise ValueError(f"公司行动记录重复: {row['Date']} {row['Code']} {row['TypeEn']}")
                pair[row['TypeEn']] = row
        for (date, code), pair in split_pairs.items():
            if set(pair) != {'Credit Corporate Action Shares', 'Debit Corporate Action Shares'}:
                raise ValueError(f'公司行动缺少对应的增加或减少记录: {date} {code}')
            old = -number(pair['Debit Corporate Action Shares']['Quantity'])
            new = number(pair['Credit Corporate Action Shares']['Quantity'])
            if old <= 0 or new <= 0:
                raise ValueError(f'无效公司行动数量: {date} {code}')
            events.append({'sid': security(group['MarketCode'], code), 'date': date.replace('.', '-'),
                'time': date.replace('.', '-') + ' 00:00:00', 'side': 'SPLIT', 'quantity': old,
                'ratio': new / old, 'cash': ZERO, 'currency': '', 'reference': f'split:{date}:{code}'})
        for row in changes:
            if row['TypeEn'] in ('Credit Corporate Action Shares', 'Debit Corporate Action Shares'):
                continue
            if row['TypeEn'] == 'OptionExpire':
                if group['MarketCode'] != 'US':
                    raise ValueError(f"非美股期权到期币种待确认: {row['Code']}")
                events.append({'sid': security(group['MarketCode'], row['Code'], True),
                    'date': row['Date'].replace('.', '-'), 'time': row['Date'].replace('.', '-') + ' 23:59:59',
                    'side': 'SELL', 'quantity': -number(row['Quantity']), 'cash': ZERO,
                    'currency': 'USD', 'reference': 'expiry:' + row['Code']})
            else:
                events.append({'sid': security(group['MarketCode'], row['Code']),
                    'date': row['Date'].replace('.', '-'), 'time': row['Date'].replace('.', '-') + ' 23:59:59',
                    'side': 'UNKNOWN_CHANGE', 'quantity': number(row['Quantity']),
                    'cash': ZERO, 'currency': '', 'reference': f"{row['TypeEn']}:{row['Date']}:{row['Code']}"})
    events.sort(key=lambda x: (x['date'], x['time'], x['reference']))
    times = defaultdict(set)
    for event in events:
        if event['side'] in ('BUY', 'SELL') and not event['reference'].startswith('expiry:'):
            times[(event['sid'], event['time'])].add(event['side'])
    if any(len(sides) > 1 for sides in times.values()):
        raise ValueError('同一证券的买卖成交时间相同，无法确定成本匹配顺序')
    return events, fills


def apply_event(pools, event, method):
    pool = pools[event['sid']]
    qty = event['quantity']
    if event['side'] == 'SPLIT':
        actual = sum((lot[0] for lot in pool), ZERO)
        if actual != qty:
            raise ValueError(f"拆股前数量不符: {event['sid']} {actual} != {qty}")
        for lot in pool:
            lot[0] *= event['ratio']
        return None
    if event['side'] == 'UNKNOWN_CHANGE' and qty < 0:
        event = {**event, 'side': 'UNKNOWN_REMOVAL', 'quantity': -qty}
        qty = -qty
    if event['side'] in ('BUY', 'UNKNOWN_CHANGE'):
        if qty <= 0:
            raise ValueError(f"不能处理非正数持仓变动: {event}")
        if event['side'] == 'BUY' and event['cash'] > 0:
            raise ValueError(f"买入净额不能为正: {event['reference']}")
        pool.append([qty, -event['cash'] if event['side'] == 'BUY' else None])
        if method == 'moving_average' and len(pool) > 1:
            total_qty = sum((x[0] for x in pool), ZERO)
            total_cost = None if any(x[1] is None for x in pool) else sum((x[1] for x in pool), ZERO)
            pool.clear()
            pool.append([total_qty, total_cost])
        return None
    available = sum((x[0] for x in pool), ZERO)
    if qty <= 0 or qty > available:
        raise ValueError(f"持仓不足: {event['reference']} {event['sid']} 卖出 {qty}，持有 {available}")
    left, allocated, unknown = qty, ZERO, False
    while left:
        lot_qty, cost = pool[0]
        used = min(left, lot_qty)
        share = cost * used / lot_qty if cost is not None else None
        if share is None:
            unknown = True
        else:
            allocated += share
        if used == lot_qty:
            pool.popleft()
        else:
            pool[0] = [lot_qty - used, None if cost is None else cost - share]
        left -= used
    if event['side'] == 'UNKNOWN_REMOVAL':
        # 未知性质的扣减无法证明扣掉哪些成本批次，剩余成本也须复核。
        for lot in pool:
            lot[1] = None
    return {**event, 'cost': None if unknown else allocated,
            'pnl': None if unknown or event['side'] == 'UNKNOWN_REMOVAL' else event['cash'] - allocated}


def reconcile_holdings(pools, statement, issues):
    expected = holdings(statement)
    for sid in sorted(set(pools) | set(expected)):
        actual = sum((lot[0] for lot in pools[sid]), ZERO)
        expected_qty = expected.get(sid, ZERO)
        if actual == expected_qty:
            continue
        if expected_qty < 0:
            raise ValueError(f"不支持空头持仓: {sid}")
        issues.append(f"{statement['Date']} {sid}: 计算持仓 {actual}，对账单 {expected_qty}；后续成本待复核")
        # 数量不符说明有未捕获的事件，不能继续沿用可能错误的批次成本。
        pools[sid].clear()
        if expected_qty:
            pools[sid].append([expected_qty, None])


def analyze(statements, opening_csv, method, executions, daily=(), history_start=False, target_year=None):
    if history_start:
        pools, issues = defaultdict(deque), []
        for group in statements[0].get('EquityHoldingSums', []):
            for holding in group['EquityHoldings']:
                if number(holding['BeginQuantity']) != 0:
                    raise ValueError('历史首月存在期初持仓，不能从空成本开始')
        monthly = statements
    else:
        pools, issues = opening_pools(opening_csv, holdings(statements[0]))
        if method == 'moving_average':
            for sid, pool in pools.items():
                if len(pool) > 1:
                    quantity = sum((lot[0] for lot in pool), ZERO)
                    cost = None if any(lot[1] is None for lot in pool) else sum((lot[1] for lot in pool), ZERO)
                    pools[sid] = deque([[quantity, cost]])
        monthly = statements[1:]
    rows, source_fills, seen_references, latest_trade = [], [], set(), {}
    for statement in monthly:
        for field in ('Corps', 'VirtualTradeSums', 'OtcTradeSums', 'FundTradeSums', 'IpoTradeSums', 'FinancingTransactionSums'):
            if statement.get(field):
                issues.append(f"{statement['Date']}: 未处理 {field}，需人工复核")
        events, fills = trade_events(statement)
        if target_year is None or statement['Date'][:4] == str(target_year):
            source_fills.extend(fills)
        for event in events:
            if event['reference'] in seen_references:
                raise ValueError(f"重复交易或持仓事件: {event['reference']}")
            seen_references.add(event['reference'])
            if event['side'] in ('BUY', 'SELL') and not event['reference'].startswith('expiry:'):
                if event['time'] < latest_trade.get(event['sid'], ''):
                    raise ValueError(f"跨月成交顺序颠倒，需逐笔重排: {event['sid']} {event['reference']}")
                latest_trade[event['sid']] = event['time']
            row = apply_event(pools, event, method)
            if row is not None and (target_year is None or event['date'][:4] == str(target_year)):
                rows.append(row)
            if event['side'] == 'UNKNOWN_CHANGE':
                issues.append(f"{event['date']} {event['sid']}: {event['reference']} 成本待复核")
        reconcile_holdings(pools, statement, issues)
    for statement in daily:
        for field in ('Corps', 'VirtualTradeSums', 'OtcTradeSums', 'FundTradeSums', 'IpoTradeSums', 'FinancingTransactionSums'):
            if statement.get(field):
                issues.append(f"{statement['Date']}: 未处理 {field}，需人工复核")
        events, fills = trade_events(statement)
        if target_year and any(event['date'][:4] != str(target_year) for event in events):
            raise ValueError(f"日结单 {statement['Date']} 含目标年度以外的变动，需先分离")
        new_events = [event for event in events if event['reference'] not in seen_references]
        if len(new_events) != len(events):
            raise ValueError(f"日结单 {statement['Date']} 与已处理交易重复；请仅提供未并入月结单的日结单")
        source_fills.extend(fills)
        for event in new_events:
            seen_references.add(event['reference'])
            if event['side'] in ('BUY', 'SELL') and not event['reference'].startswith('expiry:'):
                if event['time'] < latest_trade.get(event['sid'], ''):
                    raise ValueError(f"日结单成交顺序颠倒: {event['sid']} {event['reference']}")
                latest_trade[event['sid']] = event['time']
            row = apply_event(pools, event, method)
            if row is not None:
                rows.append(row)
            if event['side'] == 'UNKNOWN_CHANGE':
                issues.append(f"{event['date']} {event['sid']}: {event['reference']} 成本待复核")
        reconcile_holdings(pools, statement, issues)
    from collections import Counter
    provider_fills = []
    for row in executions:
        if row['trade_id'].startswith('expire-opt-'):
            continue
        # 边界日前一天仍需显示，供人工核对跨时区成交。
        if target_year and row['trade_done_at'][:10] < f'{target_year-1}-12-31':
            continue
        symbol, market = row['symbol'].rsplit('.', 1)
        option = market == 'US' and bool(re.fullmatch(r'.+\d{6}[CP]\d+', symbol))
        provider_fills.append((security(market, symbol, option), row['side'].upper(),
            row['trade_done_at'][:19].replace('T', ' '), number(row['quantity']), number(row['price'])))
    a, b = Counter(source_fills), Counter(provider_fills)
    return rows, issues, list((a-b).elements()), list((b-a).elements()), pools


def scenario(totals, pools, quotes, rates, sales=()):
    """按指定价格和汇率展示处置情景，不计算未来卖出费用。"""
    if not {'USD', 'HKD'} <= rates.keys():
        raise ValueError('情景测算需要 USD 和 HKD 的人民币汇率')
    positions = []
    for quote in quotes:
        symbol, market = quote['symbol'].rsplit('.', 1)
        sid = security(market, symbol)
        lots = pools.get(sid, ())
        if not lots:
            continue
        if market not in ('US', 'HK'):
            raise ValueError(f'行情情景暂不支持市场: {market}')
        quantity = sum((lot[0] for lot in lots), ZERO)
        cost = None if any(lot[1] is None for lot in lots) else sum((lot[1] for lot in lots), ZERO)
        price = number(quote['last_done'])
        if price <= 0:
            raise ValueError(f"行情价格必须大于零: {quote['symbol']}")
        currency = 'HKD' if market == 'HK' else 'USD'
        pnl = None if cost is None else quantity * price - cost
        positions.append({'symbol': quote['symbol'], 'quantity': quantity, 'cost': cost,
            'price': price, 'price_time': quote['timestamp'], 'currency': currency,
            'estimated_pnl': pnl, 'estimated_pnl_cny': None if pnl is None else pnl * rates[currency]})
    missing_quotes = sorted(sid for sid, lots in pools.items() if lots and not sid.startswith('OPT:')
                            and sid not in {security(x['symbol'].rsplit('.', 1)[1], x['symbol'].rsplit('.', 1)[0]) for x in quotes})
    known_pnl_cny = sum((amount * rates[currency] for currency, amount in totals.items()), ZERO)
    quote_by_symbol = {quote['symbol']: quote for quote in quotes}
    sales_rows = []
    for symbol, quantity in sales:
        if symbol not in quote_by_symbol:
            raise ValueError(f'拟卖出证券缺少行情: {symbol}')
        quote = quote_by_symbol[symbol]
        code, market = symbol.rsplit('.', 1)
        if market not in ('US', 'HK'):
            raise ValueError(f'拟卖出情景暂不支持市场: {market}')
        sid = security(market, code)
        if not pools.get(sid):
            raise ValueError(f'拟卖出证券无持仓: {symbol}')
        currency = 'HKD' if market == 'HK' else 'USD'
        price = number(quote['last_done'])
        # 复制批次，情景计算不得改变原始持仓成本。
        hypothetical = defaultdict(deque, {sid: deque([lot.copy() for lot in pools[sid]])})
        disposal = apply_event(hypothetical, {'sid': sid, 'side': 'SELL', 'quantity': quantity,
            'cash': price * quantity, 'reference': '情景卖出'}, 'fifo')
        pnl = disposal['pnl']
        sales_rows.append({'symbol': symbol, 'quantity': quantity, 'price': price,
            'price_time': quote['timestamp'], 'currency': currency, 'gross_proceeds': price * quantity,
            'allocated_cost': disposal['cost'], 'estimated_pnl': pnl,
            'estimated_pnl_cny': None if pnl is None else pnl * rates[currency]})
    scenario_pnl = (None if any(row['estimated_pnl_cny'] is None for row in sales_rows)
                    else sum((row['estimated_pnl_cny'] for row in sales_rows), ZERO))
    return {'rates': rates, 'known_pnl_cny': known_pnl_cny,
            'positions': positions, 'missing_quotes': missing_quotes,
            'sales': sales_rows, 'sales_pnl_cny': scenario_pnl if sales else None,
            'known_pnl_after_sales_cny': known_pnl_cny + scenario_pnl if sales and scenario_pnl is not None else None,
            'note': '已知处置合计，按给定汇率折算；跨标的与跨市场合并仅为情景，未含未来卖出费用及未知成本。'}


def main():
    configure_text_io()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--statements', type=Path, required=True)
    parser.add_argument('--opening-fifo', type=Path)
    parser.add_argument('--opening-average', type=Path)
    parser.add_argument('--history-start', help='从期初持仓为零的历史首月重建，格式 YYYYMM')
    parser.add_argument('--executions', type=Path, required=True)
    parser.add_argument('--daily', type=Path, action='append', default=[], help='月结单之后的日结单；可重复')
    parser.add_argument('--quotes', type=Path, help='长桥插件行情 JSON')
    parser.add_argument('--fx', action='append', default=[], metavar='币种=人民币汇率')
    parser.add_argument('--sell', action='append', default=[], metavar='证券代码=数量', help='指定拟卖出数量，可重复')
    parser.add_argument('--year', type=int, required=True)
    parser.add_argument('--through-month', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.through_month <= 12:
        parser.error('--through-month 必须在 1—12 之间')
    if args.history_start:
        if len(args.history_start) != 6 or not args.history_start.isdigit():
            parser.error('--history-start 格式应为 YYYYMM')
        start_year, start_month = int(args.history_start[:4]), int(args.history_start[4:])
        if not 1 <= start_month <= 12 or (start_year, start_month) > (args.year, args.through_month):
            parser.error('--history-start 超出有效范围')
        names = [f'{y}{m:02}' for y in range(start_year, args.year + 1) for m in range(1, 13)
                 if (y, m) >= (start_year, start_month) and (y, m) <= (args.year, args.through_month)]
    else:
        if not args.opening_fifo or not args.opening_average:
            parser.error('需要两种期初成本底稿，或使用 --history-start 重建')
        names = [f'{args.year-1}12'] + [f'{args.year}{month:02}' for month in range(1, args.through_month + 1)]
    statements = [json.loads((args.statements / (name + '.json')).read_text(encoding='utf-8-sig')) for name in names]
    accounts = {str(x['MemberInfo']['Aaid']) for x in statements}
    if len(accounts) != 1 or any(x['Date'].replace('.', '').replace('-', '') != name or x.get('FileType') != '1'
                                 for x, name in zip(statements, names)):
        raise ValueError('月结单账户或月份不一致')
    daily = [json.loads(path.read_text(encoding='utf-8-sig')) for path in args.daily]
    if any(str(x['MemberInfo']['Aaid']) not in accounts for x in daily):
        raise ValueError('日结单账户与月结单不一致')
    daily_dates = [datetime.strptime(x['Date'], '%Y.%m.%d').date() for x in daily]
    if any(x.get('FileType') != '0' for x in daily) or daily_dates != sorted(set(daily_dates)):
        raise ValueError('日结单格式或日期顺序不正确')
    if any((day.year, day.month) <= (args.year, args.through_month) for day in daily_dates):
        raise ValueError('日结单日期须晚于最后一份月结单')
    executions = json.loads(args.executions.read_text(encoding='utf-8-sig'))
    rates = {}
    for item in args.fx:
        currency, sep, rate = item.partition('=')
        try:
            parsed_rate = number(rate)
        except (InvalidOperation, ValueError):
            parser.error('--fx 应分别提供正数 USD=汇率 和 HKD=汇率')
        if not sep or currency not in ('USD', 'HKD') or parsed_rate <= 0 or currency in rates:
            parser.error('--fx 应分别提供正数 USD=汇率 和 HKD=汇率')
        rates[currency] = parsed_rate
    if bool(args.quotes) != bool(rates):
        parser.error('--quotes 和 --fx 必须一起提供')
    if args.sell and not args.quotes:
        parser.error('--sell 需要 --quotes 和 --fx')
    sales = []
    for item in args.sell:
        symbol, sep, raw_quantity = item.partition('=')
        symbol = symbol.strip().upper()
        try:
            quantity = number(raw_quantity)
        except (InvalidOperation, ValueError):
            parser.error('--sell 格式应为 证券代码=正数量，且每个证券只能指定一次')
        if not sep or '.' not in symbol or quantity <= 0 or any(x[0] == symbol for x in sales):
            parser.error('--sell 格式应为 证券代码=正数量，且每个证券只能指定一次')
        sales.append((symbol, quantity))
    quotes = json.loads(args.quotes.read_text(encoding='utf-8-sig')) if args.quotes else None
    summary, disposal_rows = {}, {}
    for method, opening in [('fifo', args.opening_fifo), ('moving_average', args.opening_average)]:
        rows, issues, missing, extra, pools = analyze(statements, opening, method, executions, daily,
            history_start=bool(args.history_start), target_year=args.year)
        totals = defaultdict(Decimal)
        for row in rows:
            if row['pnl'] is not None:
                totals[row['currency']] += row['pnl']
        summary[method] = {'known_pnl': dict(totals), 'unknown_disposals': sum(row['pnl'] is None for row in rows),
            'issues': issues, 'statement_fills_not_in_executions': missing,
            'executions_not_in_statements': extra, 'disposal_count': len(rows),
            'remaining': {sid: list(pool) for sid, pool in pools.items() if pool}}
        summary[method]['review_status'] = ('需要复核' if issues or missing or extra
                                            or summary[method]['unknown_disposals'] else '计算资料未发现差异')
        if quotes is not None:
            summary[method]['scenario'] = scenario(totals, pools, quotes, rates, sales)
        disposal_rows[method] = rows
    # 先完成两套计算，避免第二套失败时留下新旧混合的结果。
    args.output.mkdir(parents=True, exist_ok=True)
    for method, rows in disposal_rows.items():
        with (args.output / f'{method}_disposals.csv').open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=['date','sid','quantity','cash','cost','pnl','currency','reference','time','side'])
            writer.writeheader()
            writer.writerows(rows)
    (args.output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    display = {}
    for name, result in summary.items():
        display[name] = {key: value for key, value in result.items() if key not in ('remaining', 'scenario')}
        if 'scenario' in result:
            display[name]['scenario'] = {key: value for key, value in result['scenario'].items() if key != 'positions'}
    print(json.dumps(display, ensure_ascii=False, default=str))


if __name__ == '__main__':
    main()
