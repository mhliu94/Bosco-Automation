from __future__ import annotations
import argparse
import json
import logging
from pathlib import Path
import sys

from .config import load_settings
from .api import ApiClient, load_session, AuthenticationRequired
from .api_trading import ApiTrading
from .diagnostics import command_context, error_detail
from .lock import OperationLock
from .storage import save_session

def parser():
    p=argparse.ArgumentParser(description='Bosco direct trading and KTrader Kafka worker')
    p.add_argument('--config')
    p.add_argument('--verbose',action='store_true')
    sub=p.add_subparsers(dest='command',required=True)
    for name in ('check-config','session-check','holdings','authenticate','runner','runner-status','runner-stop','render-service','kafka-check'):
        sub.add_parser(name)
    cancel=sub.add_parser('cancel-all');cancel.add_argument('--dry-run',action='store_true')
    for name in ('market-order','limit-order'):
        o=sub.add_parser(name)
        o.add_argument('--symbol',required=True);o.add_argument('--quantity',required=True)
        o.add_argument('--side',required=True,choices=('Buy','Sell','BUY','SELL'))
        o.add_argument('--dry-run',action='store_true');o.add_argument('--no-submit',action='store_true')
        o.add_argument('--command-id',help='Stable idempotency key; reuse only for the identical order')
        if name=='limit-order': o.add_argument('--price',required=True);o.add_argument('--kill',action='store_true')
    return p

def service(settings):
    if sys.platform!='linux':
        raise ValueError('Render the service on Linux so its Python and config paths are correct.')
    def quote(s): return '"'+str(s).replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'
    name=settings.get('service.name','bosco-runner')
    import re
    if not re.fullmatch(r'[A-Za-z0-9_.-]+',name): raise ValueError('Invalid service name.')
    path=settings.work_dir/'systemd'/f'{name}.service'
    path.parent.mkdir(parents=True,exist_ok=True)
    content='\n'.join([
        '[Unit]','Description=Bosco Securities Kafka worker','After=network-online.target','Wants=network-online.target','',
        '[Service]','Type=simple',f'WorkingDirectory={quote(settings.path.parent)}',
        'ExecStart='+' '.join(map(quote,(sys.executable,'-m','bosco','--config',str(settings.path),'runner'))),
        'UMask=0077','Restart=on-failure','RestartPreventExitStatus=3',
        f'RestartSec={settings.get("service.restartSeconds",10)}','TimeoutStopSec=360','NoNewPrivileges=true','',
        '[Install]','WantedBy=default.target',''])
    path.write_text(content,encoding='utf8')
    return {'service':str(path),'installed':False}

def main(argv=None):
    args=parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s',stream=sys.stderr)
    client=None
    settings=None
    token=None
    try:
        settings=load_settings(args.config)
        command=args.command
        if command=='check-config':
            credentials=load_session(settings.session_path)
            if credentials['account_id']!=settings.require('bosco.accountId'): raise ValueError('Credential account mismatch.')
            if credentials.get('account_num_id')!=settings.get('bosco.accountNumId'): raise ValueError('Credential numeric account mismatch.')
            result={'ok':True,'account_id':settings.require('bosco.accountId'),'account_num_id':settings.get('bosco.accountNumId'),
                'bootstrap_servers':settings.require('kafka.bootstrapServers'),'backend':'api','credentials_present':True}
        elif command=='render-service': result=service(settings)
        elif command=='kafka-check':
            from confluent_kafka.admin import AdminClient
            admin=AdminClient({**settings.kafka_client_config,'bootstrap.servers':settings.require('kafka.bootstrapServers')})
            metadata=admin.list_topics(timeout=10)
            names=[settings.require('kafka.'+k) for k in ('commandTopic','accountDetailsTopic','orderStatusTopic','priceBooksTopic')]
            result={'reachable':True,'topics':{n:n in metadata.topics and metadata.topics[n].error is None for n in names},
                    'consumer_started':False,'messages_published':False}
        elif command.startswith('runner'):
            from .runner import TradingRunner,runner_status,request_runner_stop
            if command=='runner-status': result=runner_status(settings)
            elif command=='runner-stop': result=request_runner_stop(settings)
            else:
                runner=TradingRunner(settings)
                try: runner.run()
                finally:
                    if runner.api_executor: runner.api_executor.client.close()
                return 3 if runner.authentication_failed else 0
        elif command in ('session-check','authenticate'):
            client=ApiClient(settings)
            with OperationLock(settings.work_dir/'api/operation.lock'):
                if command=='authenticate': result=client.authenticate_trade()
                else:
                    client.read('cash')
                    result={'authenticated':True,'account_id':settings.require('bosco.accountId'),'operation':'cash read'}
        else:
            api=ApiTrading(settings);client=api.client
            if command=='holdings': result=api.query()
            elif command=='cancel-all': result=api.cancel_all(dry_run=args.dry_run)
            else:
                token=command_context.set(args.command_id)
                arguments={k:getattr(args,k) for k in ('symbol','quantity','side','dry_run','no_submit')}
                if command=='limit-order': arguments.update(price=args.price,kill=args.kill)
                result=getattr(api,command.replace('-','_'))(**arguments)
        print(json.dumps(result,ensure_ascii=True,indent=2))
        return 0
    except AuthenticationRequired as error:
        print(str(error),file=sys.stderr)
        return 3
    except Exception as error:
        print(error_detail(error,settings) if settings else str(error),file=sys.stderr)
        return 2
    finally:
        if token is not None: command_context.reset(token)
        if client: client.close()
