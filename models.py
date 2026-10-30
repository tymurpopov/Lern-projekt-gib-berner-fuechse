from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timezone

db = SQLAlchemy()

member_teams = db.Table(
    "member_teams",

    db.Column(
        "member_id",
        db.Integer,
        db.ForeignKey("members.id"),
        primary_key=True
    ),

    db.Column(
        "team_id",
        db.Integer,
        db.ForeignKey("teams.id"),
        primary_key=True
    ),
)

class Event(db.Model):
    __tablename__ = "events"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    date = db.Column(db.Date, nullable=False)
    time = db.Column(db.Time, nullable=False)
    location = db.Column(db.String(200), nullable=False)
    game = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=False, default="")


class Team(db.Model):
    __tablename__ = "teams"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    game = db.Column(db.String(100), nullable=False)
    level = db.Column(db.String(100), nullable=False)
    member_count = db.Column(db.Integer, nullable=False, default=0)
    description = db.Column(db.Text, nullable=False, default="")
    image = db.Column(db.String(255), nullable=True)

    __table_args__ = (
        db.CheckConstraint(
            "member_count >= 0",
            name="check_team_member_count"
        ),
    )

class Admin(db.Model):
    __tablename__ = "admins"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class TeamApplication(db.Model):
    __tablename__ = "team_applications"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    game = db.Column(db.String(100), nullable=False)
    player_count = db.Column(db.Integer, nullable=False)
    captain_name = db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(254), nullable=False)
    notes = db.Column(db.Text, nullable=False, default="")
    photo_filename = db.Column(
        db.String(255),
        nullable=True
    )

    team_id = db.Column(
        db.Integer,
        db.ForeignKey("teams.id"),
        nullable=True
    )
    status = db.Column(db.String(20), nullable=False, default="pending")

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        db.CheckConstraint(
            "player_count >= 1 AND player_count <= 1000",
            name="check_application_player_count"
        ),
    )

class MemberApplication(db.Model):
    __tablename__ = "member_applications"

    id = db.Column(db.Integer, primary_key=True)

    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    birth_date = db.Column(db.Date, nullable=False)

    email = db.Column(db.String(254), nullable=False)
    phone = db.Column(db.String(40), nullable=False, default="")

    nickname = db.Column(db.String(80), nullable=False)
    game = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(100), nullable=False, default="")

    external_profile = db.Column(
        db.String(500),
        nullable=False,
        default=""
    )

    notes = db.Column(db.Text, nullable=False, default="")

    photo_filename = db.Column(
        db.String(255),
        nullable=True
    )

    photo_filename = db.Column(
        db.String(255),
        nullable=True
    )

    publish_profile = db.Column(
        db.Boolean,
        nullable=False,
        default=False
    )

    status = db.Column(
        db.String(20),
        nullable=False,
        default="pending"
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )

class Member(db.Model):
    __tablename__ = "members"

    id = db.Column(db.Integer, primary_key=True)

    application_id = db.Column(
        db.Integer,
        db.ForeignKey("member_applications.id"),
        unique=True,
        nullable=False
    )

    nickname_key = db.Column(
        db.String(255),
        unique=True,
        nullable=False
    )

    application = db.relationship(
        "MemberApplication",
        backref=db.backref("member", uselist=False)
    )

    teams = db.relationship(
        "Team",
        secondary=member_teams,
        backref=db.backref("members"),
        order_by="Team.name"
    )