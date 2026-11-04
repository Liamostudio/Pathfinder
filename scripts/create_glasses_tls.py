#!/usr/bin/env python3
"""Create a short-lived development CA and HTTPS certificate for a phone relay.

Only ca.crt goes on the iPhone. Install/trust it explicitly in iPhone Settings;
keep both private keys on the laptop. Existing output directories are refused.
"""
import argparse
import ipaddress
import os
from pathlib import Path
import re
import subprocess


def create(host, output):
    if not re.fullmatch(r'[A-Za-z0-9.-]{1,253}', host):
        raise ValueError('Use the laptop IPv4 address or DNS hostname')
    try:
        value = ipaddress.ip_address(host)
        if value.version != 4: raise ValueError('Use IPv4 or a DNS hostname')
        san = f'IP:{value}'
    except ValueError:
        if re.fullmatch(r'[0-9.]+', host): raise ValueError('Invalid IPv4 address') from None
        san = f'DNS:{host}'
    output = Path(output).resolve()
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    old_mask = os.umask(0o077)
    def run(*args):
        subprocess.run(['openssl', *args], cwd=output, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        run('req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-sha256', '-days', '30',
            '-keyout', 'ca.key', '-out', 'ca.crt', '-subj', '/CN=Pathfinder local development CA',
            '-addext', 'basicConstraints=critical,CA:TRUE,pathlen:0', '-addext', 'keyUsage=critical,keyCertSign,cRLSign')
        run('req', '-new', '-newkey', 'rsa:2048', '-nodes', '-keyout', 'server.key', '-out', 'server.csr', '-subj', '/CN=Pathfinder phone relay')
        (output/'server.ext').write_text(f'basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName={san}\n')
        run('x509', '-req', '-in', 'server.csr', '-CA', 'ca.crt', '-CAkey', 'ca.key', '-CAcreateserial',
            '-out', 'server.crt', '-days', '30', '-sha256', '-extfile', 'server.ext')
    finally:
        os.umask(old_mask)
    return output


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('host', help='Exact IPv4/DNS host the iPhone will connect to')
    parser.add_argument('--output', type=Path, default=Path('recordings/glasses-gateway/tls'))
    args=parser.parse_args()
    try: result=create(args.host,args.output)
    except (ValueError,OSError,subprocess.CalledProcessError) as exc: parser.error(str(exc))
    print(f'Created development certificates in {result}.\nInstall only ca.crt on your iPhone. Never share ca.key or server.key.')
if __name__=='__main__':main()
