from flask import Flask, render_template
from models import db, Event, Team, Admin
from werkzeug.security import generate_password_hash, check_password_hash
import click
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    abort,
)
import os
import secrets
import hmac
from datetime import timedelta
from functools import wraps

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY")

if not app.config["SECRET_KEY"]:
    raise RuntimeError(
        "SECRET_KEY fehlt. Setze die Umgebungsvariable vor dem Start."
    )

app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=2)

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///berner_fuechse.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

def admin_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        admin_id = session.get("admin_id")

        if not isinstance(admin_id, int):
            return redirect(url_for("admin_login"))

        admin = db.session.get(Admin, admin_id)

        if admin is None:
            session.clear()
            return redirect(url_for("admin_login"))

        return view(*args, **kwargs)

    return wrapped_view


def csrf_token():
    token = session.get("csrf_token")

    if token is None:
        token = secrets.token_hex(32)
        session["csrf_token"] = token

    return token


@app.context_processor
def security_helpers():
    return {"csrf_token": csrf_token}


def check_csrf():
    expected = session.get("csrf_token")
    received = request.form.get("csrf_token")

    if not expected or not received:
        abort(400)

    if not hmac.compare_digest(
        expected.encode("utf-8"),
        received.encode("utf-8"),
    ):
        abort(400)

@app.cli.command("init-db")
def init_db():
    db.create_all()
    print("Datenbank bereit: instance/berner_fuechse.db")

@app.cli.command("create-admin")
@click.option("--username", prompt="Benutzername")
@click.password_option(
    prompt="Passwort",
    confirmation_prompt="Passwort wiederholen"
)
def create_admin(username, password):
    username = username.strip().lower()

    if not username or len(username) > 80:
        raise click.ClickException(
            "Der Benutzername muss zwischen 1 und 80 Zeichen lang sein."
        )

    if len(password) < 12:
        raise click.ClickException(
            "Das Passwort muss mindestens 12 Zeichen lang sein."
        )

    statement = db.select(Admin).where(Admin.username == username)
    existing_admin = db.session.execute(statement).scalar_one_or_none()

    if existing_admin is not None:
        raise click.ClickException(
            "Dieser Benutzername ist bereits vergeben."
        )

    admin = Admin(username=username)
    admin.set_password(password)

    db.session.add(admin)
    db.session.commit()

    click.echo(f"Administrator '{username}' wurde erstellt.")

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/teams")
def teams():
    return render_template("teams.html")

@app.route("/events")
def events():
    statement = db.select(Event).order_by(Event.date, Event.time)
    all_events = db.session.execute(statement).scalars().all()

    return render_template("events.html", events=all_events)

@app.route("/membership")
def membership():
    return render_template("membership.html")

@app.route("/shop")
def shop():
    return render_template("shop.html")

@app.route("/rules")
def rules():
    return render_template("rules.html")

@app.route("/join")
def join():
    return render_template("join.html")

@app.route("/teamform")
def teamform():
    return render_template("teamform.html")


@app.route("/register")
def register():
    return render_template("register.html")


@app.route("/event/<int:event_id>")
def event(event_id):
    selected_event = db.get_or_404(Event, event_id)

    return render_template("event.html", event=selected_event)

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    error = None

    if request.method == "POST":
        check_csrf()

        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")

        admin = None

        if 1 <= len(username) <= 80 and 1 <= len(password) <= 1024:
            statement = db.select(Admin).where(
                Admin.username == username
            )
            admin = db.session.execute(statement).scalar_one_or_none()

        if admin is not None and admin.check_password(password):
            session.clear()
            session["admin_id"] = admin.id
            session.permanent = True

            return redirect(url_for("admin_dashboard"))

        error = "Benutzername oder Passwort ist falsch."

    return render_template("admin_login.html", error=error)


@app.route("/admin")
@admin_required
def admin_dashboard():
    return render_template("admin.html")


@app.route("/admin/logout", methods=["POST"])
@admin_required
def admin_logout():
    check_csrf()
    session.clear()

    return redirect(url_for("admin_login"))

if __name__ == "__main__":
    app.run(debug=True)