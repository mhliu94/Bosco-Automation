"""Transform a narrowly scoped active-account export into portable credentials."""
from datetime import datetime, timezone
import copy
import json
import os
from pathlib import Path
from .defaults import DEFAULTS
from .protocol import xml_parse
from .storage import save_session

def export(data, directory, account_id, account_num_id, bootstrap=None, passwords=None, *, config_path=None):
    directory=Path(directory).expanduser().resolve()
    if config_path is not None:
        config_path=Path(config_path).expanduser().resolve()
        if config_path.is_relative_to(directory):
            raise ValueError('Configuration must be outside the credentials directory.')
        if not config_path.exists() and not bootstrap:
            raise ValueError('Kafka bootstrap is required when creating configuration.')
    login=xml_parse(data['login_xml'].encode('utf8'))
    if login.findtext('result')!='1' or data['user_id']!=login.findtext('user_id') or data['account']!=data['user_id']:
        raise ValueError('The active BSL account/session is inconsistent.')
    if data['account_number']!=login.findtext('default_account'):
        raise ValueError('The selected account differs from the default account; inspect routing before export.')
    host,port=data['server'].split(':')[:2]
    passwords=passwords or {}
    state={
        'schema_version':1,'account_id':account_id,'account_num_id':account_num_id,
        'host':host,'port':int(port),'rsa_modulus':data['rsa_modulus'],
        'app_version':data.get('app_version','1.0.6'),'user_id':data['user_id'],
        'account_number':data['account_number'],'session':login.findtext('session'),
        'internet_session':login.findtext('internet_session'),'internet_expiry_time':login.findtext('internet_expiry_time'),
        'device_name':data['device_name'],'device_code':data['device_code'],
        'trade_password':passwords.get('trading_password',''),
        'exported_at':datetime.now(timezone.utc).isoformat(),
    }
    required=('session','internet_session','internet_expiry_time','device_code','device_name')
    if any(not state.get(k) or state[k] in ('null','undefined') for k in required):
        raise ValueError('The app has no complete verified session.')
    destination=directory/'credentials.json'
    if destination.exists():
        previous=json.loads(destination.read_text('utf8'))
        if any(previous.get(k)!=state[k] for k in ('user_id','account_number','account_id','account_num_id')):
            raise ValueError('Refusing to replace a different account in this folder.')
        if not state['trade_password']: state['trade_password']=previous.get('trade_password','')
    save_session(destination,state)
    if config_path is not None and not config_path.exists():
        config=copy.deepcopy(DEFAULTS)
        config['paths'].update(workDir='../work/'+account_id.lower()+'/state',logDir='../logs/'+account_id.lower())
        config['bosco'].update(accountId=account_id,accountNumId=account_num_id,backend='api')
        try: session_path=os.path.relpath(destination,config_path.parent)
        except ValueError: session_path=str(destination)  # Separate Windows drives.
        config['api']['sessionPath']=Path(session_path).as_posix()
        config['kafka'].update(bootstrapServers=bootstrap,consumerGroupId='bosco-'+account_id.lower(),clientConfig={})
        config['service']['name']='bosco-'+account_id.lower()
        save_session(config_path,config)
    return {'credentials':str(destination),'config':str(config_path) if config_path else None,'account_id':account_id}

def read_passwords(path):
    values={}
    for line in Path(path).read_text('utf-8-sig').splitlines():
        if '=' in line:
            k,v=line.split('=',1)
            if k.strip() in ('account_password','trading_password'): values[k.strip()]=v.strip()
    if not values.get('trading_password'): raise ValueError('Password file needs trading_password=...')
    return values
