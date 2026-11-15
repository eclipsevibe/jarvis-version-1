"""Chrome native host: framed messages on stdio, local private socket to Jarvis."""
import argparse
import json
import socket
import struct
import sys
import threading
import time

from browser_control import socket_path


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--browser',choices=['chrome','chromium'],required=True)
    args,_=parser.parse_known_args()
    connection=socket.socket(socket.AF_UNIX)
    # Jarvis is normally running; reconnecting the extension handles restarts.
    connection.connect(str(socket_path()))
    connection.sendall((json.dumps({'browser':args.browser})+'\n').encode())
    def receive():
        try:
            for line in connection.makefile('rb'):
                data=json.loads(line)
                raw=json.dumps(data).encode()
                if len(raw)>1024*1024:break
                sys.stdout.buffer.write(struct.pack('<I',len(raw))+raw);sys.stdout.buffer.flush()
        except (OSError,ValueError,BrokenPipeError):pass
        finally:
            # Closing input ends Chrome's native port and triggers reconnection.
            import os
            os._exit(0)
    threading.Thread(target=receive,daemon=True).start()
    while True:
        header=sys.stdin.buffer.read(4)
        if len(header)!=4:return
        length=struct.unpack('<I',header)[0]
        if length>2*1024*1024:return
        raw=sys.stdin.buffer.read(length)
        if len(raw)!=length:return
        message=json.loads(raw)
        connection.sendall((json.dumps(message)+'\n').encode())


if __name__=='__main__':
    try:main()
    except (OSError,ValueError):sys.exit(1)
