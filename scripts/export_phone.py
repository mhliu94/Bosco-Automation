"""One-time active BSL account enrollment from an already logged-in rooted phone."""
import argparse
import json
from pathlib import Path
import subprocess
import threading
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bosco.enrollment import export, read_passwords

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--adb',default='adb');p.add_argument('--serial',required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--account-id',required=True)
    p.add_argument('--account-num-id',type=int,required=True);p.add_argument('--bootstrap')
    p.add_argument('--config-output',type=Path,help='Optional config path outside the credentials directory')
    p.add_argument('--password-file',type=Path)
    args=p.parse_args()
    import frida,frida_tools
    pid=subprocess.run([args.adb,'-s',args.serial,'shell','pidof','com.tsci.hms'],capture_output=True,check=True,timeout=15).stdout.decode().strip()
    if not pid.isdecimal(): raise ValueError('Open BSL and select the intended account before export.')
    bridge=(Path(frida_tools.__file__).parent/'bridges/java.js').read_text('utf8')
    source=bridge+'\nconst Java=bridge;\n'+Path(__file__).with_name('enroll.js').read_text('utf8')
    device=frida.get_device(args.serial,timeout=10)
    session=device.attach(int(pid))
    result=[];errors=[];done=threading.Event()
    def message(m, data):
        if m['type']=='send': result.append(m['payload'])
        else: errors.append(m.get('description','Enrollment failed.'))
        done.set()
    try:
        script=session.create_script(source);script.on('message',message);script.load()
        if not done.wait(20) or errors or len(result)!=1:
            raise ValueError('App session extraction failed; no credentials were exported.')
        passwords=read_passwords(args.password_file) if args.password_file else {}
        summary=export(result[0],args.output,args.account_id,args.account_num_id,args.bootstrap,passwords,
                       config_path=args.config_output)
        print(json.dumps(summary))
    finally: session.detach()

if __name__=='__main__':
    try: main()
    except Exception as e:
        print(str(e),file=sys.stderr)
        raise SystemExit(2)
