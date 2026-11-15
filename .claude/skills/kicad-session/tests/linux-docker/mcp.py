#!/usr/bin/env python3
"""Drive a Copperpour MCP server over stdio: mcp.py '<json list of {name, arguments}>'.

The server binary is $COPPERPOUR (default /opt/cp/copperpour), a Linux build mounted into the
container."""
import json
import os
import subprocess
import sys
import tempfile

calls = json.loads(sys.argv[1])
config = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False)
json.dump({'ipc_address': 'ipc:///tmp/kicad/api.sock', 'kicad_cli': '/usr/bin/kicad-cli', 'eager_toolsets': True}, config)
config.close()
server = subprocess.Popen([os.environ.get('COPPERPOUR', '/opt/cp/copperpour'), '--config', config.name], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
next_id = 0


def send(method, params):
    global next_id
    next_id += 1
    server.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': next_id, 'method': method,
                                   'params': params}) + '\n')
    server.stdin.flush()
    while True:
        line = server.stdout.readline()
        if not line:
            raise SystemExit('server exited')
        reply = json.loads(line)
        if reply.get('id') == next_id:
            return reply


send('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
                    'clientInfo': {'name': 'test', 'version': '0'}})
for call in calls:
    reply = send('tools/call', {'name': call['name'], 'arguments': call.get('arguments', {})})
    if 'error' in reply:
        print('== %s RPC ERROR %s' % (call['name'], reply['error']))
        continue
    text = reply['result']['content'][0]['text']
    print('== %s isError=%s' % (call['name'], reply['result'].get('isError')))
    print(text[:600])
server.stdin.close()
server.wait(timeout=10)
os.unlink(config.name)
