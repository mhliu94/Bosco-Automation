"""Encrypted in-memory peer: real framing/crypto with no network or broker writes."""
import struct
import xml.etree.ElementTree as ET
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa, padding

from bosco.protocol import (Connection, TransportError, AuthenticationRequired,
                            ApiError, ApiResult, frame, encrypt, decrypt, xml_parse)


@pytest.fixture(scope='module')
def private_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class Peer:
    def __init__(self, private):
        self.private = private
        self.buffer = bytearray()
        self.key = bytes(range(32))
        self.closed = False
        self.requests = []
        self.events = []
        self.result = '1'
        self.drop_reply = False
        self.wrong_reply = False
        self.heartbeat = False

    def settimeout(self, timeout):
        assert timeout > 0

    def close(self):
        self.closed = True

    def recv(self, size):
        if not self.buffer:
            raise TimeoutError('synthetic lost reply')
        size = min(size, 3)  # Require the client to handle partial TCP reads.
        data = bytes(self.buffer[:size])
        del self.buffer[:size]
        return data

    def enqueue(self, root, inner=51001):
        xml = ET.tostring(root)
        raw = struct.pack('<IHI', len(xml)+10, inner, len(xml)) + xml
        self.buffer.extend(frame(31307, encrypt(self.key, raw)))

    def sendall(self, data):
        length, code = struct.unpack('<IH', data[:6])
        assert length == len(data)
        if code == 31304:
            challenge = self.private.decrypt(data[6:], padding.PKCS1v15())
            assert len(challenge) == 76
            assert challenge[:4] == challenge[-4:] == b'asia'
            self.answer = challenge[:40] + self.key + challenge[72:]
            self.buffer.extend(frame(31305, encrypt(challenge[40:72], self.answer)))
        elif code == 31306:
            assert decrypt(self.key, data[6:]) == self.answer
        elif code == 10013:
            self.events.append('heartbeat_ack')
        else:
            assert code == 31307
            decoded = decrypt(self.key, data[6:])
            assert struct.unpack('<IHI', decoded[:10]) == (len(decoded), 51000, len(decoded)-10)
            root = xml_parse(decoded[10:])
            self.requests.append(root)
            self.events.append(root.tag)
            if self.drop_reply:
                return
            if self.heartbeat:
                self.buffer.extend(frame(10012, b'ping'))
            if self.wrong_reply:
                self.enqueue(ET.Element('order_notify'))
                wrong = ET.Element(root.tag+'_response', msgnum='different-request')
                ET.SubElement(wrong, 'result').text = '1'
                self.enqueue(wrong)
            reply = ET.Element(root.tag+'_response', msgnum=root.get('msgnum'))
            ET.SubElement(reply, 'result').text = self.result
            ET.SubElement(reply, 'order_id').text = 'BSL-9007199254740993123'
            self.enqueue(reply)


@pytest.fixture
def connected(private_key):
    peer = Peer(private_key)
    state = dict(host='offline.invalid', port=10001,
                 rsa_modulus=format(private_key.public_key().public_numbers().n, 'x'),
                 app_version='1.0.6', internet_expiry_time='2099/01/01 00:00:00',
                 user_id='TEST', account_number='TEST-A', session='fake-session', internet_session='fake-internet')
    connection = Connection(state, socket_factory=lambda *args, **kwargs: peer)
    connection.connect()
    return connection, peer


def test_handshake_reconnect_and_exact_response_matching(connected):
    connection, peer = connected
    assert peer.requests[0].tag == 'reconnect'
    assert peer.requests[0].findtext('ae') == 'TEST-A'
    assert peer.requests[0].find('extra').get('org') == 'HMS'
    peer.wrong_reply = peer.heartbeat = True
    response = connection.request('cash', {'market_code': ''})
    assert response.tag == 'cash_response'
    assert 'heartbeat_ack' in peer.events

def test_password_login_omits_expired_session_fields(connected):
    connection,peer=connected
    connection.request('login',{'password':'synthetic-secret','encoding':'UTF8','terminal_type':'ANDROID'},
                       {'new_version':'1','os_version':'16'})
    request=peer.requests[-1]
    assert request.findtext('session')==''
    assert request.find('mts') is None
    assert 'internet_session' not in request.find('extra').attrib
    assert request.findtext('password')=='synthetic-secret'

def test_login_handshake_does_not_try_expired_reconnect(connected):
    connection,peer=connected
    peer.requests.clear()
    connection.connect(reconnect=False)
    assert peer.requests==[]


def test_marker_precedes_only_one_send_when_ack_is_lost(connected):
    connection, peer = connected
    peer.drop_reply = True
    with pytest.raises(TransportError, match='lost'):
        connection.request('place_order', before_send=lambda: peer.events.append('journal_fsynced'))
    assert peer.events[-2:] == ['journal_fsynced', 'place_order']
    assert sum(r.tag == 'place_order' for r in peer.requests) == 1
    assert connection.socket is None and peer.closed


def test_failed_journal_write_prevents_socket_send(connected):
    connection, peer = connected
    def fail():
        raise OSError('disk full')
    with pytest.raises(TransportError):
        connection.request('place_order', before_send=fail)
    assert [r.tag for r in peer.requests] == ['reconnect']


def test_unclassified_error_retains_ambiguous_send_status(connected):
    connection, peer = connected
    peer.result = 'Some.Unverified.Error'
    with pytest.raises(ApiError) as caught:
        connection.request('place_order')
    assert caught.value.result.http_status == 503


def test_auth_rejection_closes_connection(connected):
    connection, peer = connected
    peer.result = 'Error.InvalidPassword'
    with pytest.raises(AuthenticationRequired):
        connection.request('login_trade', {'password': 'synthetic-secret'})
    assert peer.closed


@pytest.mark.parametrize('size', [0, 5, 4194305])
def test_invalid_frame_length_rejected_before_allocation(connected, size):
    connection, peer = connected
    peer.buffer.extend(struct.pack('<I', size))
    with pytest.raises(TransportError, match='bounds'):
        connection.request('cash')


@pytest.mark.parametrize('inner', [51000, 0])
def test_wrong_inner_protocol_code_rejected(connected, inner):
    connection, peer = connected
    peer.enqueue(ET.Element('cash_response'), inner=inner)
    with pytest.raises(TransportError, match='envelope'):
        connection.request('cash')


def test_logout_notification_closes_connection(connected):
    connection, peer = connected
    peer.enqueue(ET.Element('logout_notify'))
    with pytest.raises(AuthenticationRequired):
        connection.request('cash')
    assert peer.closed


def test_xml_normalization_and_entity_rejection():
    assert xml_parse(b'<?xml version=1.0 encoding=UTF-8?><cash/>').tag == 'cash'
    with pytest.raises(TransportError):
        xml_parse(b'<!DOCTYPE x [<!ENTITY y "bad">]><cash>&y;</cash>')
    with pytest.raises(TransportError):
        xml_parse(b'<cash>')


def test_result_repr_does_not_disclose_payload():
    assert 'secret-value' not in repr(ApiResult('cash', 200, 0, 0, 'id', {'token': 'secret-value'}))
