from datetime import datetime, timezone
import json
import xml.etree.ElementTree as ET
import pytest

from bosco.api import ApiClient
from bosco.config import load_settings
from bosco.enrollment import export, read_passwords
from bosco.errors import ConfigurationError
from bosco.mapping import order, position, broker_time
from bosco.market import market_session
from bosco.protocol import AuthenticationRequired, SessionError
from bosco.runner import TradingRunner
from bosco.storage import save_session


def login_export():
    return dict(user_id='TEST', account='TEST', account_number='TEST-A', server='offline.invalid:10001',
        rsa_modulus='ab'*256, app_version='1.0.6', device_name='test-device', device_code='test-device-code',
        login_xml='<login_response><result>1</result><user_id>TEST</user_id><default_account>TEST-A</default_account>'
                  '<session>synthetic-session</session><internet_session>synthetic-internet</internet_session>'
                  '<internet_expiry_time>2099/01/01 00:00:00</internet_expiry_time></login_response>')


def native_order(**changes):
    return dict(order_id='BSL-9007199254740993123', market_code='USA', stock_code='TEST', order_qty='10',
        filled_qty='0', order_side='1', order_status='1', trade_type='2', order_price='12.34',
        order_time='2026/11/10 23:00:00', ccy='USD', **changes)


def rows_response(rows, total=None):
    root = ET.Element('response', all_count=str(len(rows) if total is None else total))
    records = ET.SubElement(root, 'records')
    for row in rows:
        ET.SubElement(records, 'row', row)
    return root


class NativePeer:
    def __init__(self, state, **kwargs):
        self.credentials = state
        self.socket = None
        self.calls = []
        self.events = []
        self.pages = {}
        self.reject_auth = False

    def connect(self):
        self.socket = True
        self.events.append('connect')

    def close(self):
        self.socket = None

    def request(self, tag, fields=None, extra=None, attributes=None, before_send=None):
        if before_send:
            before_send()
        self.events.append(tag)
        self.calls.append((tag, fields or {}, extra or {}, attributes or {}))
        if tag == 'login_trade':
            if self.reject_auth:
                raise AuthenticationRequired('synthetic rejection')
            return ET.fromstring('<login_trade_response><trade_session>synthetic-trade</trade_session>'
                '<trade_expiry_time>2099/01/01 00:00:00</trade_expiry_time></login_trade_response>')
        if tag == 'place_order':
            return ET.fromstring('<place_order_response><order_id>BSL-9007199254740993123</order_id></place_order_response>')
        if tag == 'cancel_order':
            return ET.Element('cancel_order_response')
        pages = self.pages.get(tag, [rows_response([])])
        return pages.pop(0) if len(pages) > 1 else pages[0]


@pytest.fixture
def client(tmp_path):
    config=tmp_path/'config/bosco.config.json'
    export(login_export(), tmp_path/'credentials', 'BOSCO-TEST', 29, 'offline.invalid:9092',
           {'trading_password': 'synthetic-password'}, config_path=config)
    return ApiClient(load_settings(config), connection_factory=NativePeer)

def test_explicit_session_renewal_validates_identity_and_does_not_store_login_password(client):
    from unittest.mock import Mock
    from bosco.enrollment import renew_session
    connection=Mock()
    connection.request.return_value=ET.fromstring('<login_response><user_id>TEST</user_id>'
        '<default_account>TEST-A</default_account><session>renewed</session>'
        '<internet_session>renewed-internet</internet_session><internet_expiry_time>2099/01/01 00:00:00</internet_expiry_time></login_response>')
    result=renew_session(client.settings,'synthetic-login-secret','16',connection_factory=lambda state:connection)
    assert result=={'account_id':'BOSCO-TEST','session_renewed':True}
    connection.connect.assert_called_once_with(reconnect=False)
    assert connection.request.call_args.args[0]=='login'
    assert connection.request.call_args.args[1]['encoding']=='UTF8'
    assert connection.request.call_args.args[2]['new_version']=='1'
    raw=client.settings.session_path.read_text()
    assert 'synthetic-login-secret' not in raw
    assert json.loads(raw)['session']=='renewed'
    connection.close.assert_called_once()

def test_cash_uses_actual_currency_ledgers_instead_of_converted_account_totals(client):
    client.connection.pages['currency']=[rows_response([
        {'ccy_code':'USD','balance':'100.00','static_cash_by_rate':'109.70'},
        {'ccy_code':'HKD','balance':'50.00','static_cash_by_rate':'855.66'},
        {'ccy_code':'CNY','balance':'25.00','static_cash_by_rate':'750.00'}])]
    client.connection.pages['cash']=[rows_response([
        {'ccy_code':'USD','balance':'109.70'},{'ccy_code':'HKD','balance':'855.66'}])]
    data=client.read('cash').payload['data']
    assert data['cash_balance']==[{'currency':'USD','balance':'100.00'},
        {'currency':'HKD','balance':'50.00'},{'currency':'CNY','balance':'25.00'}]
    assert [call[0] for call in client.connection.calls]==['currency']

def test_cash_missing_actual_balance_cannot_fall_back_to_converted_value(client):
    client.connection.pages['currency']=[rows_response([{'ccy_code':'HKD','static_cash_by_rate':'780'}])]
    with pytest.raises(KeyError):client.read('cash')

def test_read_authentication_renews_once_without_replaying_writes(client, monkeypatch, tmp_path):
    from unittest.mock import Mock
    password=tmp_path/'passwords.txt'
    password.write_text('account_password=synthetic-login-secret\ntrading_password=synthetic-trade\n')
    client.settings.data['api'].update(passwordFile=str(password),deviceOsVersion='16')
    client.connection.connect=Mock()
    response=ET.fromstring('<login_response><user_id>TEST</user_id><default_account>TEST-A</default_account>'
        '<session>new-session</session><internet_session>new-internet</internet_session>'
        '<internet_expiry_time>2099/01/01 00:00:00</internet_expiry_time></login_response>')
    expected=ET.Element('query_response')
    client.connection.request=Mock(side_effect=[AuthenticationRequired('expired'),response,expected])
    assert client._request('query_account') is expected
    assert [call.args[0] for call in client.connection.request.call_args_list]==['query_account','login','query_account']
    assert 'synthetic-login-secret' not in client.settings.session_path.read_text()
    assert json.loads(client.settings.session_path.read_text())['session']=='new-session'
    # A failed financial response is propagated; only pre-dispatch authentication
    # may renew. The request cannot be sent a second time.
    monkeypatch.setattr(client,'_trade_ready',lambda:None)
    client.state['trade_session']='synthetic-trade-session'
    client.connection.request=Mock(side_effect=AuthenticationRequired('expired after send'))
    with pytest.raises(AuthenticationRequired):
        client.write('submit',{'counter_id':'ST/US/TEST','action':'1','order_type':'LO','quantity':'1','price':'2'},before_send=lambda:None)
    assert client.connection.request.call_count==1

def test_read_renewal_rejects_wrong_identity_without_overwriting_session(client, tmp_path):
    from unittest.mock import Mock
    password=tmp_path/'passwords.txt';password.write_text('account_password=synthetic-login\ntrading_password=synthetic-trade\n')
    client.settings.data['api'].update(passwordFile=str(password),deviceOsVersion='16')
    before=client.settings.session_path.read_bytes()
    client.connection.connect=Mock()
    client.connection.request=Mock(side_effect=[AuthenticationRequired('expired'),ET.fromstring('<login_response><user_id>OTHER</user_id></login_response>')])
    with pytest.raises(AuthenticationRequired):client._request('query_account')
    assert client.settings.session_path.read_bytes()==before

@pytest.mark.parametrize('response',[
    '<login_response><user_id>OTHER</user_id><default_account>TEST-A</default_account></login_response>',
    '<login_response><user_id>TEST</user_id><default_account>TEST-A</default_account></login_response>',
])
def test_invalid_renewal_does_not_replace_credentials(client,response):
    from unittest.mock import Mock
    from bosco.enrollment import renew_session
    connection=Mock();connection.request.return_value=ET.fromstring(response)
    before=client.settings.session_path.read_bytes()
    with pytest.raises(ValueError):renew_session(client.settings,'synthetic-password','16',connection_factory=lambda state:connection)
    assert client.settings.session_path.read_bytes()==before
    connection.close.assert_called_once()

def test_renewal_refuses_to_interrupt_running_worker(client):
    from unittest.mock import Mock
    from bosco.enrollment import renew_session
    from bosco.lock import OperationLock
    from bosco.errors import SafetyError
    factory=Mock()
    with OperationLock(client.settings.work_dir/'runner.lock',0):
        with pytest.raises(SafetyError):renew_session(client.settings,'synthetic-password','16',connection_factory=factory)
    factory.assert_not_called()


@pytest.mark.parametrize('kind,price,trade_type,extended', [('LO', '12.34', '2', 'Y'), ('MO', None, '6', 'N')])
def test_submission_native_fields_and_auth_before_send_marker(client, kind, price, trade_type, extended):
    body = dict(counter_id='ST/US/TEST', action='1', order_type=kind, quantity='10', client_request_id='internal-only')
    if price:
        body['price'] = price
    result = client.write('submit', body, before_send=lambda: client.connection.events.append('durable-marker'))
    assert result == {'order_id_str': 'BSL-9007199254740993123'}
    assert client.connection.events == ['connect', 'login_trade', 'durable-marker', 'place_order']
    tag, fields, extra, _ = client.connection.calls[-1]
    assert fields == dict(market_code='USA', stock_code='TEST', order_side='1', trade_type=trade_type,
                          order_qty='10', ccy_code='USD', **({'order_price': price} if price else {}))
    assert extra == dict(trade_session='synthetic-trade', terminal_type='ANDROID', extended_trading=extended)
    assert 'internal-only' not in str(client.connection.calls)
    state = json.loads(client.settings.session_path.read_text())
    assert state['trade_session'] == 'synthetic-trade'


def test_auth_failure_does_not_mark_or_send(client):
    client.connection.reject_auth = True
    with pytest.raises(AuthenticationRequired):
        client.write('submit', {}, before_send=lambda: pytest.fail('sent marker before authentication'))
    assert client.connection.events == ['connect', 'login_trade']


def test_cancel_refreshes_exact_order_and_preserves_original_context(client):
    original = native_order()
    original.update(order_side='2', related_order_id='PARENT-1', order_txn_reference='native-ref', extended_trading='Y')
    client.connection.pages['today_order'] = [rows_response([original])]
    client.write('withdraw', {'order_id_str': original['order_id']}, before_send=lambda: client.connection.events.append('marker'))
    assert client.connection.events[-3:] == ['today_order', 'marker', 'cancel_order']
    _, fields, extra, _ = client.connection.calls[-1]
    assert fields['order_id'] == original['order_id']
    assert fields['order_side'] == '2'
    assert fields['related_order_id'] == 'PARENT-1'
    assert extra['order_txn_reference'] == 'native-ref'


def test_exact_id_history_fallback_does_not_match_similar_symbol(client):
    wrong = native_order()
    wrong['order_id'] = 'DIFFERENT'
    right = native_order()
    client.connection.pages['today_order'] = [rows_response([wrong])]
    client.connection.pages['history_order'] = [rows_response([right])]
    result = client.read('order_detail', {'order_id_str': right['order_id']})
    assert result.payload['data']['order']['id'] == right['order_id']
    assert client.connection.calls[-1][2]['order_id'] == right['order_id']


def test_paginated_snapshot_requires_completeness_and_no_duplicates(client):
    client.connection.pages['currency'] = [rows_response([{'ccy_code':'USD','balance':'1'}], 2),
                                     rows_response([{'ccy_code':'HKD','balance':'2'}], 2)]
    assert len(client.read('cash').payload['data']['cash_balance']) == 2
    assert client.connection.calls[-1][3]['begin'] == '1'
    client.connection.pages['currency'] = [rows_response([{'ccy_code':'USD','balance':'1'}], 2)]
    with pytest.raises(ValueError, match='repeated'):
        client.read('cash')
    client.connection.pages['currency'] = [rows_response([], 2)]
    with pytest.raises(ValueError, match='incomplete'):
        client.read('cash')


def test_credentials_cannot_switch_account_during_reconnect(client):
    state = dict(client.state, account_number='DIFFERENT-ACCOUNT')
    save_session(client.settings.session_path, state)
    with pytest.raises(SessionError, match='changed account'):
        client.read('cash')


def test_numeric_control_account_must_match_enrollment(client):
    client.settings.data['bosco']['accountNumId'] = 30
    with pytest.raises(SessionError, match='numeric account'):
        ApiClient(client.settings, connection_factory=NativePeer)


def test_native_alphanumeric_id_survives_polling_restart(client):
    from bosco.order_polling import OrderPollingState
    from bosco.order_status import order_snapshot
    now = datetime.now(timezone.utc)
    row = native_order()
    row['order_time'] = now.isoformat(timespec='seconds')
    snapshot = order_snapshot(order(row, client.account_context()), client.settings, observed_at=now)
    state = OrderPollingState(client.settings)
    state.observe(snapshot)
    state.save()
    restored = OrderPollingState(client.settings)
    assert snapshot['order_id'] in restored.data['pending']
    assert restored.data['snapshots'][snapshot['order_id']] == snapshot


def test_export_validates_active_account_and_retains_private_password(tmp_path):
    data = login_export()
    export(data, tmp_path, 'BOSCO-TEST', 29, 'offline.invalid:9092', {'trading_password':'synthetic-secret'})
    export(data, tmp_path, 'BOSCO-TEST', 29, 'offline.invalid:9092')
    state = json.loads((tmp_path/'credentials.json').read_text())
    assert state['trade_password'] == 'synthetic-secret'
    assert 'account_password' not in state
    with pytest.raises(ValueError, match='different account'):
        export(data, tmp_path, 'OTHER', 30, 'offline.invalid:9092')
    data['account_number'] = 'OTHER'
    with pytest.raises(ValueError, match='selected account'):
        export(data, tmp_path, 'BOSCO-TEST', 29, 'offline.invalid:9092')


def test_password_file_and_portable_relative_paths(tmp_path):
    credentials = tmp_path/'credentials'
    credentials.mkdir()
    file = credentials/'passwords.txt'
    file.write_text('account_password=unused\ntrading_password=synthetic=with-equals\n')
    assert read_passwords(file)['trading_password'] == 'synthetic=with-equals'
    config = tmp_path/'config/bosco.config.json'
    export(login_export(), credentials, 'TEST', 29, 'offline.invalid:9092', config_path=config)
    settings = load_settings(config)
    assert settings.session_path == credentials/'credentials.json'
    assert settings.work_dir == tmp_path/'work/test/state'
    assert settings.kafka_client_config == {}
    assert sorted(p.name for p in credentials.iterdir()) == ['credentials.json','passwords.txt']


def test_enrollment_writes_only_credentials_unless_config_is_explicit(tmp_path):
    result = export(login_export(), tmp_path, 'TEST', 29)
    assert result['config'] is None
    assert [p.name for p in tmp_path.iterdir()] == ['credentials.json']


def test_enrollment_rejects_config_inside_credentials_folder_before_writing(tmp_path):
    with pytest.raises(ValueError, match='outside the credentials'):
        export(login_export(), tmp_path, 'TEST', 29, 'offline.invalid:9092', config_path=tmp_path/'nested/config.json')
    assert list(tmp_path.iterdir()) == []


def test_enrollment_preserves_existing_external_configuration(tmp_path):
    config=tmp_path/'config/bosco.config.json'
    config.parent.mkdir()
    config.write_text('{"operator_setting":"preserved"}')
    export(login_export(), tmp_path/'credentials', 'TEST', 29, config_path=config)
    assert config.read_text() == '{"operator_setting":"preserved"}'


@pytest.mark.parametrize('setting,value', [('readAttempts',0), ('pageSize',1.5), ('requestTimeoutSeconds',0),
                                         ('positionCost','invented'), ('cancelPollDelaysSeconds',[-1])])
def test_invalid_execution_settings_fail_early(tmp_path, setting, value):
    path = tmp_path/'config.json'
    path.write_text(json.dumps({'api':{setting:value}}))
    with pytest.raises(ConfigurationError):
        load_settings(path)


def test_corrupt_saved_enable_state_does_not_reenable(client):
    runner = TradingRunner(client.settings)
    assert runner._trading_enabled()
    for content in ('invalid-json', '{"tradingEnabled":"false"}', '{}'):
        runner.state_path.write_text(content)
        assert runner._trading_enabled() is False


@pytest.mark.parametrize('native,filled,expected', [('1','0','NewStatus'), ('2','3','PartialFilledStatus'),
    ('3','10','FilledStatus'), ('4','0','CanceledStatus'), ('4','3','PartialWithdrawal'), ('-1','0','RejectedStatus'),
    ('9','0','ExpiredStatus'), ('10','2','PendingCancelStatus'), ('999','0','UNKNOWN')])
def test_native_statuses_exact_ids_and_timestamp(native, filled, expected):
    row = native_order()
    row.update(order_status=native, filled_qty=filled)
    result = order(row, {'aaid':'TEST','org_id':'HMS','account_channel':'TEST-A'})
    assert result['id'] == row['order_id']
    assert result['status'] == expected
    assert float(result['submitted_at']) == datetime(2026,11,10,15,tzinfo=timezone.utc).timestamp()
    assert 'client_request_id' not in result


def test_invalid_order_quantities_cannot_be_reported_as_completed():
    row = native_order()
    for value in ('11','-1','NaN'):
        row['filled_qty'] = value
        with pytest.raises(ValueError):
            order(row, {})


def test_position_values_require_native_fields_and_zero_rows_are_ignored():
    assert position({'qty':'0'}) is None
    row = dict(qty='10', qty_sellable='7', market_code='USA', stock_code='TEST', cost_price='10',
               avg_price='11', latest_price='12', market_value='120')
    assert position(row)['native_market_value'] == '120'
    assert position(row, 'average')['average_cost'] == '11'
    del row['qty_sellable']
    with pytest.raises(ValueError):
        position(row)


@pytest.mark.parametrize('stamp,expected', [('2026-11-10T09:00:00+00:00','premarket'),
    ('2026-11-10T14:30:00+00:00','regular'), ('2026-11-10T21:00:00+00:00','afterhours'),
    ('2026-11-11T01:00:00+00:00',None), ('2026-07-03T15:00:00+00:00',None),
    ('2026-11-07T16:00:00+00:00',None), ('2026-11-27T17:00:00+00:00','regular'),
    ('2026-11-27T18:00:00+00:00',None), ('2026-06-01T13:30:00+00:00','regular')])
def test_calendar_holidays_dst_and_early_close(stamp, expected):
    assert market_session(datetime.fromisoformat(stamp)) == expected
