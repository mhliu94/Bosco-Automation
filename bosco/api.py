"""Native BSL client, adapted to the common execution model."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import time

from .protocol import (Connection, ApiResult, ApiError, SessionError,
                       AuthenticationRequired, TransportError)
from .storage import save_session
from .mapping import order, position, numeric, broker_time
from .market import market_session

def load_session(path):
    try:
        state=json.loads(Path(path).read_text('utf-8-sig'))
    except (OSError,ValueError): raise SessionError('Cannot read the private BSL credentials file.') from None
    required=('host','port','rsa_modulus','user_id','account_number','session',
              'internet_session','internet_expiry_time','device_name','device_code','app_version','account_id')
    if not isinstance(state,dict) or any(not state.get(k) for k in required):
        raise SessionError('BSL credentials are incomplete; export the active account again.')
    if state.get('schema_version')!=1: raise SessionError('Unsupported credentials schema.')
    return state

class ApiClient:
    def __init__(self, settings, *, connection_factory=Connection):
        self.settings=settings
        self.state=load_session(settings.session_path)
        if self.state['account_id']!=settings.require('bosco.accountId'):
            raise SessionError('Credential account differs from the configured Kafka account.')
        if self.state.get('account_num_id')!=settings.get('bosco.accountNumId'):
            raise SessionError('Credential numeric account differs from the configured Kafka account.')
        self.connection=connection_factory(self.state,
            timeout=float(settings.get('api.requestTimeoutSeconds',15)),
            max_bytes=int(settings.get('api.maxResponseBytes',4194304)))
        self.raw_orders={}
        self.closed=False

    @classmethod
    def from_settings(cls, settings, **kwargs): return cls(settings,**kwargs)

    def close(self): self.connection.close()

    def account_context(self):
        return {'aaid':self.state['user_id'], 'org_id':'HMS','account_channel':self.state['account_number']}

    def _ensure(self):
        if self.connection.socket is None:
            latest=load_session(self.settings.session_path)
            if any(latest.get(k)!=self.state.get(k) for k in ('user_id','account_number','account_id','account_num_id')):
                raise SessionError('Credentials changed account; restart with the correct account state directory.')
            self.state=latest
            self.connection.credentials=latest
            self.connection.connect()

    def _request(self, tag, fields=None, extra=None, attributes=None):
        self._ensure()
        return self.connection.request(tag,fields,extra,attributes)

    def _rows(self, tag, fields=None, extra=None):
        result=[]
        seen=set()
        total=None
        cursor=''
        size=int(self.settings.get('api.pageSize',100))
        for page in range(int(self.settings.get('api.maxOrderPages',100))):
            root=self._request(tag,fields,extra,{'begin':str(len(result)),'count':str(size),'str_begin':cursor})
            records=root.find('records')
            if records is None: raise ValueError('Broker omitted the records container.')
            rows=[dict(row.attrib) for row in records if row.tag=='row']
            try: expected=int(root.attrib['all_count'])
            except (KeyError,ValueError): raise ValueError('Broker omitted the complete record count.') from None
            if expected<0 or (total is not None and total!=expected):
                raise ValueError('Broker records changed during pagination; retry the read.')
            total=expected
            for row in rows:
                key=row.get('order_id') or json.dumps(row,sort_keys=True)
                if key in seen: raise ValueError('Broker repeated a page; refusing a partial snapshot.')
                seen.add(key)
                result.append(row)
            if len(result)==total: return result
            if len(result)>total or not rows: raise ValueError('Broker returned an incomplete list.')
            next_cursor=root.get('str_begin','')
            if next_cursor and next_cursor==cursor: raise ValueError('Broker cursor did not advance.')
            cursor=next_cursor
        raise ValueError('Broker pagination exceeded its configured limit.')

    def _orders(self, *, history=False, identifier=None):
        fields={'market_code':'','order_type':''}
        extra={}
        tag='today_order'
        if history:
            tag='history_order'
            now=datetime.now(ZoneInfo('Asia/Hong_Kong'))
            fields.update(begin_date=(now-timedelta(days=30)).strftime('%Y%m%d'),end_date=now.strftime('%Y%m%d'))
            if identifier: extra['order_id']=identifier
        rows=self._rows(tag,fields,extra)
        for row in rows:
            if row.get('order_id'): self.raw_orders[row['order_id']]=row
        return [order(row,self.account_context(),self.settings.get('api.brokerTimezone','Asia/Hong_Kong')) for row in rows]

    def read(self, endpoint, params=None):
        params=params or {}
        context=self.account_context()
        if endpoint=='positions':
            rows=self._rows('holding_stock',{'market_code':''},{'force_data_fetch':'Y'})
            positions=[position(r,self.settings.get('api.positionCost','diluted')) for r in rows]
            data={'account_channel':context['account_channel'],'account':{'account_no':self.state['account_number']},
                  'stockhold':{'holdings':[p for p in positions if p is not None]}}
        elif endpoint=='cash':
            rows=self._rows('cash',{'market_code':''},{'force_data_fetch':'Y'})
            data={'account_channel':context['account_channel'],'account_no':self.state['account_number'],
                  'cash_balance':[{'currency':r['ccy_code'],'balance':str(numeric(r['balance'],'cash balance'))} for r in rows]}
        elif endpoint=='orders':
            # Fetch the complete native list once. The workflow's adapter page is complete.
            if params.get('page',1)!=1: raise ValueError('Unexpected normalized order page.')
            rows=self._orders()
            if params.get('status')==1:
                from .api_trading import TERMINAL
                rows=[r for r in rows if r['status'] not in TERMINAL]
            data={'orders':rows,'has_more':False,'count':len(rows)}
        elif endpoint=='order_detail':
            identifier=params.get('order_id_str') or params['order_id']
            rows=[r for r in self._orders() if r['id']==identifier]
            if not rows: rows=[r for r in self._orders(history=True,identifier=identifier) if r['id']==identifier]
            if len(rows)!=1: raise ValueError('Exact order is not yet confirmed by the broker.')
            data={'order':rows[0]}
        elif endpoint=='order_info':
            # BSL has no verified per-symbol permission endpoint. Confirm the account's
            # USD ledger, use whole-share US stock tickets, and let the broker enforce
            # instrument permissions at submission. No invented quote or fee-card IDs.
            cash=self.read('cash').payload['data']
            usd=any(r['currency']=='USD' for r in cash['cash_balance'])
            data={'counter_id':params['counter_id'],'buy_lot_size':'1','sell_lot_size':'1',
                  'channel_infos':{**context,'support_trade':usd,'restricted':False,'settlement_currency':['USD'] if usd else [],
                    'order_type_config':{'MO':{'support_sessions':[1],'support_time_in_forces':[0]},
                                         'LO':{'support_sessions':[1,2],'support_time_in_forces':[0]}}}}
        elif endpoint=='market_status':
            status={'premarket':201,'regular':202,'afterhours':203}.get(market_session(),0)
            data={'market_time':[{'market':'US','timestamp':str(time.time()),'trade_status':status}]}
        else: raise ValueError('Unsupported BSL read operation.')
        return ApiResult(endpoint,200,0,0,'',{'data':data})

    def authenticate_trade(self):
        self._ensure()
        password=self.settings.get('bosco.tradePassword') or self.state.get('trade_password')
        if not password: raise AuthenticationRequired('Trading password is missing from the private credentials file.')
        response=self.connection.request('login_trade',{'password':password,'terminal_type':'ANDROID'},
            {'device_name':self.state['device_name'],'device_code':self.state['device_code']})
        token=response.findtext('trade_session')
        expiry=response.findtext('trade_expiry_time')
        if not token or not expiry: raise AuthenticationRequired('Trading authentication returned incomplete credentials.')
        self.state.update(trade_session=token,trade_expiry_time=expiry)
        save_session(self.settings.session_path,self.state)
        return {'authenticated':True,'expires_at':expiry}

    def _trade_ready(self):
        self._ensure()
        try: expires=float(broker_time(self.state.get('trade_expiry_time')))
        except ValueError: expires=0
        if not self.state.get('trade_session') or expires-time.time()<60:
            self.authenticate_trade()

    def write(self, operation, body, *, before_send):
        if operation not in ('submit','withdraw'): raise ValueError('Unsupported BSL write operation.')
        self._trade_ready()
        extra={'trade_session':self.state['trade_session'],'terminal_type':'ANDROID'}
        if operation=='submit':
            parts=body['counter_id'].split('/')
            if len(parts)!=3 or parts[:2]!=['ST','US']: raise ValueError('Only US stock order submission is supported.')
            fields={'market_code':'USA','stock_code':parts[2],'order_side':body['action'],
                    'trade_type':{'LO':'2','MO':'6'}[body['order_type']],
                    'order_qty':body['quantity'],'ccy_code':'USD'}
            if body['order_type']=='LO': fields['order_price']=body['price']
            # The app leaves order_validity absent for DAY, adding GTD only for expiry orders.
            extra.update(extended_trading='Y' if body['order_type']=='LO' else 'N')
            # A reference value is not presumed to be an idempotency key.
            response=self.connection.request('place_order',fields,extra,before_send=before_send)
            identifier=response.findtext('order_id')
            if not identifier: raise TransportError('Submission reply has no exact order ID.')
            return {'order_id_str':identifier}
        identifier=body['order_id_str']
        # Refresh before cancel so all fields belong to this exact account/order.
        row=self.read('order_detail',{'order_id_str':identifier}).payload['data']['order']['native']
        fields={k:row[k] for k in ('market_code','stock_code','order_side','trade_type','order_qty','order_price','order_time','related_order_id') if row.get(k)}
        fields.update(order_id=identifier,terminal_type='ANDROID',ccy_code=row.get('ccy','USD'))
        extra.update({k:row[k] for k in ('extended_trading','order_txn_reference','orderdate','sys_date','consign_id','orderGroupID') if row.get(k)})
        self.connection.request('cancel_order',fields,extra,before_send=before_send)
        return {'order_id_str':identifier}

TradingApiClient=ApiClient
