"""Renew an enrolled account from a private password file with the worker stopped."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bosco.config import load_settings
from bosco.enrollment import read_passwords,renew_session

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True)
    parser.add_argument('--password-file',type=Path,required=True)
    parser.add_argument('--os-version',required=True,help='Android version of the enrolled device')
    args=parser.parse_args()
    try:
        passwords=read_passwords(args.password_file)
        result=renew_session(load_settings(args.config),passwords.get('account_password'),args.os_version)
        print(json.dumps(result))
    except Exception as error:
        # Broker payloads/passwords are never included in the diagnostic.
        print('Session renewal failed: '+type(error).__name__+'. Check login/verification and confirm the worker is stopped.',file=sys.stderr)
        raise SystemExit(2)
