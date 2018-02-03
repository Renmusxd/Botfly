from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
import uuid
from server.flaskdb import db


class UserManager:
    instance = None

    def __init__(self):
        self.users = {}
        self.unames = {}

    @staticmethod
    def get(user_id):
        return User.query.filter_by(uid=user_id).first()

    @staticmethod
    def getbyname(uname):
        return User.query.filter_by(uname=uname).first()

    @staticmethod
    def validate(uname, passwd):
        user = User.query.filter_by(uname=uname).first()
        if user:
            return user.validate(passwd)

    @staticmethod
    def create_user(uname, email, passwd):
        newuser = User(uname, email, passwd)
        db.session.add(newuser)
        db.session.commit()

    @staticmethod
    def change_password(uname, newpassword):
        user = User.query.filter_by(uname=uname).first()
        if user:
            user.change_password(newpassword)
            db.session.commit()
            return True
        else:
            return False


class User(UserMixin, db.Model):
    __tablename__ = 'Users'
    uid = db.Column(db.String(40), unique=True, primary_key=True)
    uname = db.Column(db.String(80), unique=True)
    email = db.Column(db.String(120), unique=True)
    pwhash = db.Column(db.String(80))

    def __init__(self, uname, email, passwd, uid=None):
        self.uname = uname
        self.email = email
        if uid is None:
            uid = str(uuid.uuid4())
        self.uid = uid
        self.pwhash = generate_password_hash(passwd)

    def get_id(self):
        return self.uid

    def validate(self, passwd):
        return check_password_hash(self.pwhash, passwd)

    def change_password(self, newpasswd):
        self.pwhash = generate_password_hash(newpasswd)
        return True

    def __repr__(self):
        return '<User {}>'.format(self.uname)
