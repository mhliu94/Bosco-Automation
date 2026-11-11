"""Map the inspected CMSP fields into the common execution/KTrader model."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

STATUS = {
    '-1':'RejectedStatus', '0':'UNKNOWN', '1':'NewStatus', '2':'PartialFilledStatus',
    '3':'FilledStatus', '4':'CanceledStatus', '5':'WaitToNew', '6':'WaitToNew',
    '7':'NewStatus', '8':'WaitToNew', '9':'ExpiredStatus', '10':'PendingCancelStatus',
    '11':'NotReported', '12':'NotReported', '13':'PendingReplaceStatus',
    '14':'NotReported', '16':'NotReported', '17':'PendingCancelStatus',
    '20':'CanceledStatus', '21':'NotReported', '22':'NotReported',
    '23':'PendingCancelStatus', '24':'WaitToNew', '25':'RejectedStatus',
    '26':'NewStatus', '27':'PendingReplaceStatus', '28':'PartialWithdrawal',
    '43':'NewStatus', '45':'NewStatus', '46':'CanceledStatus', '48':'NotReported',
    '51':'PendingReplaceStatus', '54':'RejectedStatus', '55':'NewStatus',
    '56':'NotReported', '57':'NotReported', '59':'WaitToNew', '61':'NewStatus',
}

def numeric(value, name):
    try:
        result = Decimal(str(value))
        if not result.is_finite(): raise ValueError
        return result
    except (ValueError, InvalidOperation):
        raise ValueError(f'Broker returned an invalid {name}.') from None

def broker_time(value, date=None, zone='Asia/Hong_Kong'):
    if not value: raise ValueError('Missing broker order timestamp.')
    value = str(value).strip()
    candidates = [value]
    if date: candidates.insert(0, str(date).strip()+' '+value)
    for candidate in candidates:
        for fmt in ('%Y/%m/%d %H:%M:%S','%Y-%m-%d %H:%M:%S','%Y%m%d %H:%M:%S',
                    '%Y%m%d %H%M%S','%Y%m%d%H%M%S','%Y-%m-%dT%H:%M:%S%z'):
            try:
                stamp = datetime.strptime(candidate,fmt)
                if stamp.tzinfo is None: stamp=stamp.replace(tzinfo=ZoneInfo(zone))
                return str(stamp.timestamp())
            except ValueError: pass
    raise ValueError('Unrecognized broker order timestamp; reporting deferred.')

def order(row, context, zone='Asia/Hong_Kong'):
    identifier = row.get('order_id','')
    if not identifier or identifier.strip()!=identifier: raise ValueError('Missing exact broker order ID.')
    market = {'USA':'US','HK':'HK','HKG':'HK','SHA':'SH','SZA':'SZ'}.get(row.get('market_code'))
    if not market: raise ValueError('Unsupported broker order market.')
    quantity = numeric(row.get('order_qty'),'order quantity')
    filled = numeric(row.get('filled_qty'),'filled quantity')
    if quantity<=0 or filled<0 or filled>quantity: raise ValueError('Inconsistent broker quantities.')
    side = row.get('order_side')
    if side not in ('1','2'): raise ValueError('Unsupported broker order side.')
    status = STATUS.get(row.get('order_status'),'UNKNOWN')
    if status=='CanceledStatus' and filled>0: status='PartialWithdrawal'
    result = {**context,'id':identifier,'order_id_str':identifier,
        'counter_id':f'ST/{market}/{row["stock_code"]}', 'action':side,
        'order_type':{'2':'LO','6':'MO'}.get(row.get('trade_type'),'OTHER'),
        'quantity':str(quantity),'executed_qty':str(filled),
        'executed_price':row.get('avg_price') or row.get('filled_price') or None,
        'price':row.get('order_price'), 'status':status,
        'submitted_at':broker_time(row.get('create_time') or row.get('order_time'),row.get('order_date') or row.get('orderdate'),zone),
        'currency':row.get('ccy') or ('USD' if market=='US' else 'HKD'),
        'time_in_force':row.get('order_validity') or '0',
        'native':row,
    }
    if row.get('update_time'):
        result['updated_at']=broker_time(row['update_time'],row.get('order_date') or row.get('orderdate'),zone)
    # Client IDs are not invented from symbol/time. Unknown sends remain guarded.
    return result

def position(row, cost_mode='diluted'):
    qty = numeric(row.get('qty'),'position quantity')
    if qty==0: return None
    market = {'USA':'US','HK':'HK','HKG':'HK','SHA':'SH','SZA':'SZ'}.get(row.get('market_code'))
    if not market: raise ValueError('Unsupported position market; refusing a partial portfolio.')
    cost = row.get('cost_price') if cost_mode=='diluted' else row.get('avg_price')
    cost = numeric(cost,'position cost')
    price = numeric(row.get('latest_price'),'position price')
    available = numeric(row.get('qty_sellable'),'sellable quantity')
    value = numeric(row.get('market_value'),'position market value')
    return {'counter_id':f'ST/{market}/{row["stock_code"]}', 'name':row.get('stock_name',''),
        'quantity':str(qty),'available_quantity':str(available),
        'average_cost':str(cost),'diluted_avg_cost':str(cost),'market_price':str(price),
        'last_done':str(price),'native_market_value':str(value), 'native':row}
