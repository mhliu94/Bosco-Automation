"""BSL 1.0.6 CMSP: RSA bootstrap and framed AES-256-CBC XML.

Read-only enrollment verified on 2026-11-10. No Android runtime is involved.
The fixed IV and PKCS1v15 padding reproduce the broker's wire protocol.
"""
from dataclasses import dataclass, field
import re
import secrets
import socket
import struct
import time
import xml.etree.ElementTree as ET
from cryptography.hazmat.primitives.asymmetric import rsa, padding as asymmetric_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding

class SessionError(RuntimeError): pass
class AuthenticationRequired(SessionError): pass
class TransportError(SessionError): pass
class SessionRenewalDeferred(TransportError):
    """A valid login was recently replaced; retry reads after a bounded delay."""
    def __init__(self, retry_after):
        self.retry_after=max(0.0,float(retry_after))
        super().__init__('Broker session renewal is cooling down; automatic retry remains enabled.')

@dataclass
class ApiResult:
    path: str
    http_status: int
    api_code: object
    elapsed_ms: float
    request_id: str
    payload: dict = field(repr=False)
    @property
    def ok(self): return self.http_status == 200 and self.api_code in (0, '1')

class ApiError(RuntimeError):
    def __init__(self, result):
        self.result = result
        super().__init__(f'Broker refused request (code={result.api_code}).')

def xml_parse(data):
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise TransportError('Unexpected XML declaration.')
    # The app emits a nonstandard, unquoted XML declaration.
    if data.startswith(b'<?xml'): data = data.split(b'?>', 1)[-1]
    try: return ET.fromstring(data)
    except ET.ParseError: raise TransportError('Malformed broker XML.') from None

def encrypt(key, data):
    pad = padding.PKCS7(128).padder()
    raw = pad.update(data) + pad.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(bytes(range(16)))).encryptor()
    return cipher.update(raw) + cipher.finalize()

def decrypt(key, data):
    try:
        cipher = Cipher(algorithms.AES(key), modes.CBC(bytes(range(16)))).decryptor()
        raw = cipher.update(data) + cipher.finalize()
        pad = padding.PKCS7(128).unpadder()
        return pad.update(raw) + pad.finalize()
    except ValueError: raise TransportError('Invalid encrypted broker frame.') from None

def frame(code, payload):
    return struct.pack('<IH', len(payload)+6, code) + payload

class Connection:
    def __init__(self, credentials, timeout=15, max_bytes=4194304, socket_factory=socket.create_connection):
        self.credentials = credentials
        self.timeout, self.max_bytes = timeout, max_bytes
        self.factory = socket_factory
        self.socket = None
        self.key = None

    def close(self):
        if self.socket:
            self.socket.close()
        self.socket = self.key = None

    def _take(self, n, deadline):
        result = bytearray()
        while len(result) < n:
            remaining = deadline-time.monotonic()
            if remaining <= 0: raise TimeoutError
            self.socket.settimeout(remaining)
            part = self.socket.recv(n-len(result))
            if not part: raise EOFError
            result.extend(part)
        return bytes(result)

    def _receive(self, deadline):
        size, = struct.unpack('<I', self._take(4, deadline))
        if not 6 <= size <= self.max_bytes:
            raise TransportError('Broker frame exceeds configured bounds.')
        payload = self._take(size-4, deadline)
        return struct.unpack('<H', payload[:2])[0], payload[2:]

    def connect(self, *, reconnect=True):
        self.close()
        c = self.credentials
        try:
            self.socket = self.factory((c['host'], int(c['port'])), timeout=self.timeout)
            temporary_key = bytes(secrets.randbelow(127)+1 for _ in range(24)) + b'0'*8
            challenge = b'asia' + bytes(36) + temporary_key + b'asia'
            public = rsa.RSAPublicNumbers(65537, int(c['rsa_modulus'],16)).public_key()
            self.socket.sendall(frame(31304, public.encrypt(challenge, asymmetric_padding.PKCS1v15())))
            code, payload = self._receive(time.monotonic()+self.timeout)
            if code != 31305: raise TransportError('Unexpected handshake response.')
            plain = decrypt(temporary_key, payload)
            if len(plain) != 76 or plain[:4] != b'asia' or plain[72:] != b'asia':
                raise TransportError('Invalid broker handshake.')
            self.key = plain[40:72]
            self.socket.sendall(frame(31306, encrypt(self.key,plain)))
            if reconnect:
                self.request('reconnect', {'terminal_type':'ANDROID'}, {
                    'app_version':'normal','client_version':c['app_version'],'org':'HMS',
                    'internet_expiry_time':c['internet_expiry_time']})
        except (OSError, EOFError, ValueError) as exc:
            self.close()
            raise TransportError('Broker connection failed; no order was sent.') from None
        except Exception:
            self.close()
            raise

    def request(self, tag, fields=None, extra=None, attributes=None, before_send=None):
        if self.socket is None: raise TransportError('Broker is disconnected.')
        c = self.credentials
        msg = tag+'_response_'+str(time.time_ns())
        root = ET.Element(tag, {'msgnum':msg, **(attributes or {})})
        common = {'user_id':c['user_id'], 'password':' ', 'ae':c['account_number'],
                  'session':c['session'], 'language':'EN', 'mts':str(time.time_ns()//1000000)}
        common.update(fields or {})
        extras = {'internet_session':c['internet_session'], **(extra or {})}
        if tag == 'login':
            # Native password login does not carry the old session, internet
            # token or observation timestamp. Reconnect is a separate request.
            common['session'] = ''
            common.pop('mts')
            extras.pop('internet_session', None)
        for key, value in common.items(): ET.SubElement(root,key).text = str(value)
        ET.SubElement(root,'extra',extras)
        xml = ET.tostring(root,encoding='utf-8')
        data = frame(31307, encrypt(self.key,struct.pack('<IHI',len(xml)+10,51000,len(xml))+xml))
        try:
            # Validation/authentication completes before the journal marks a send.
            if before_send: before_send()
            self.socket.sendall(data)
            deadline = time.monotonic()+self.timeout
            while True:
                code,payload = self._receive(deadline)
                if code in (10012,10013):
                    if code == 10012: self.socket.sendall(frame(10013,payload))
                    continue
                if code != 31307: raise TransportError('Unexpected broker protocol code.')
                decoded = decrypt(self.key,payload)
                if len(decoded)<10: raise TransportError('Truncated broker envelope.')
                length, inner_code, xml_length = struct.unpack('<IHI',decoded[:10])
                if length!=len(decoded) or inner_code!=51001 or xml_length!=len(decoded)-10:
                    raise TransportError('Invalid broker envelope length.')
                response = xml_parse(decoded[10:])
                if response.tag in ('kickoff','logout_notify'):
                    raise AuthenticationRequired('Broker revoked the session; sign in and export again.')
                if response.tag != tag+'_response' or response.get('msgnum') != msg:
                    continue  # asynchronous order notifications are reconciled by exact ID
                result = response.findtext('result')
                if result != '1':
                    safe_code = result if result and re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',result) else 'unrecognized'
                    auth = ('password','session','login','2fa','otp','verification','device')
                    if tag in ('reconnect','login_trade') or any(x in safe_code.lower() for x in auth) or safe_code.startswith('TSCI_110_') or safe_code=='TSCI_010_00007':
                        raise AuthenticationRequired(f'Broker authentication required (code={safe_code}).')
                    # No native rejection-code allowlist has been verified with a
                    # live submission. Preserve the send guard for every failure;
                    # only exact order status can establish a terminal rejection.
                    raise ApiError(ApiResult(tag,503,safe_code,0,msg,{}))
                return response
        except (OSError, EOFError):
            self.close()
            raise TransportError('Broker response was lost; a dispatched order must be reconciled.') from None
        except (TransportError, AuthenticationRequired):
            self.close()
            raise
