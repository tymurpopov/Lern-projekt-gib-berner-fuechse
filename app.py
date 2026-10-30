from flask import Flask, render_template
from models import db, Event, Team, Admin, TeamApplication, MemberApplication, Member
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
    flash,
)
import os
import secrets
import hmac
from datetime import timedelta, date, time, timezone
from functools import wraps
import re
from urllib.parse import urlsplit
from sqlalchemy.exc import IntegrityError

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

def read_event_form():
    title = request.form.get("title", "").strip()
    location = request.form.get("location", "").strip()
    game = request.form.get("game", "").strip()
    description = request.form.get("description", "").strip()

    if not title or len(title) > 150:
        raise ValueError("Der Titel muss 1 bis 150 Zeichen lang sein.")

    if not location or len(location) > 200:
        raise ValueError("Der Ort muss 1 bis 200 Zeichen lang sein.")

    if not game or len(game) > 100:
        raise ValueError("Das Spiel muss 1 bis 100 Zeichen lang sein.")

    if len(description) > 10000:
        raise ValueError("Die Beschreibung ist zu lang.")

    try:
        event_date = date.fromisoformat(
            request.form.get("date", "")
        )
        event_time = time.fromisoformat(
            request.form.get("time", "")
        )
    except ValueError:
        raise ValueError("Bitte ein gültiges Datum und eine gültige Zeit eingeben.")

    if event_time.tzinfo is not None:
        raise ValueError("Bitte eine lokale Uhrzeit eingeben.")

    return {
        "title": title,
        "date": event_date,
        "time": event_time,
        "location": location,
        "game": game,
        "description": description,
    }

def read_team_form():
    name = request.form.get("name", "").strip()
    game = request.form.get("game", "").strip()
    level = request.form.get("level", "").strip()
    description = request.form.get("description", "").strip()
    image = request.form.get("image", "").strip()

    if not name or len(name) > 150:
        raise ValueError("Der Teamname muss 1 bis 150 Zeichen lang sein.")

    if not game or len(game) > 100:
        raise ValueError("Das Spiel muss 1 bis 100 Zeichen lang sein.")

    if not level or len(level) > 100:
        raise ValueError("Das Spielniveau muss 1 bis 100 Zeichen lang sein.")

    try:
        member_count = int(request.form.get("member_count", ""))
    except ValueError:
        raise ValueError("Bitte eine ganze Zahl für die Mitglieder eingeben.")

    if not 0 <= member_count <= 1000:
        raise ValueError("Die Mitgliederzahl muss zwischen 0 und 1000 liegen.")

    if len(description) > 10000:
        raise ValueError("Die Beschreibung ist zu lang.")

    if image:
        valid_image = re.fullmatch(
            r"img/[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp)",
            image,
            flags=re.IGNORECASE
        )

        if len(image) > 255 or not valid_image:
            raise ValueError(
                "Bildpfad ungültig. Beispiel: img/team_alpha.jpg"
            )

    return {
        "name": name,
        "game": game,
        "level": level,
        "member_count": member_count,
        "description": description,
        "image": image or None,
    }

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


@app.route("/admin/teams")
@admin_required
def admin_teams():
    statement = db.select(Team).order_by(Team.name)
    all_teams = db.session.execute(statement).scalars().all()

    return render_template(
        "admin_teams.html",
        teams=all_teams
    )

@app.route("/admin/member-applications")
@admin_required
def admin_member_applications():
    statement = db.select(MemberApplication).order_by(
        MemberApplication.created_at.desc(),
        MemberApplication.id.desc()
    )

    applications = db.session.execute(statement).scalars().all()

    members = db.session.execute(
        db.select(Member)
    ).scalars().all()

    members_by_application = {
        member.application_id: member
        for member in members
    }

    return render_template(
        "admin_members.html",
        applications=applications,
        members_by_application=members_by_application
    )

@app.route(
    "/admin/member-applications/<int:application_id>/approve",
    methods=["POST"]
)
@admin_required
def admin_member_application_approve(application_id):
    check_csrf()

    application = db.get_or_404(
        MemberApplication,
        application_id
    )

    statement = (
        db.update(MemberApplication)
        .where(
            MemberApplication.id == application_id,
            MemberApplication.status == "pending"
        )
        .values(status="approved")
    )

    result = db.session.execute(statement)

    if result.rowcount != 1:
        db.session.rollback()

        flash(
            "Dieser Antrag wurde bereits bearbeitet.",
            "member_application_admin"
        )

        return redirect(url_for("admin_member_applications"))

    member = Member(
        application_id=application.id,
        nickname_key=application.nickname.strip().casefold()
    )

    db.session.add(member)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()

        flash(
            "Der Antrag konnte nicht angenommen werden. "
            "Möglicherweise ist dieser Nickname bereits vergeben. "
            "Der Antrag bleibt offen.",
            "member_application_admin"
        )
    else:
        flash(
            "Der Mitgliedsantrag wurde angenommen.",
            "member_application_admin"
        )

    return redirect(url_for("admin_member_applications"))

@app.route(
    "/admin/member-applications/<int:application_id>/reject",
    methods=["POST"]
)
@admin_required
def admin_member_application_reject(application_id):
    check_csrf()

    db.get_or_404(MemberApplication, application_id)

    statement = (
        db.update(MemberApplication)
        .where(
            MemberApplication.id == application_id,
            MemberApplication.status == "pending"
        )
        .values(status="rejected")
    )

    result = db.session.execute(statement)

    if result.rowcount != 1:
        db.session.rollback()

        flash(
            "Dieser Antrag wurde bereits bearbeitet.",
            "member_application_admin"
        )
    else:
        db.session.commit()

        flash(
            "Der Mitgliedsantrag wurde abgelehnt.",
            "member_application_admin"
        )

    return redirect(url_for("admin_member_applications"))

@app.route("/members/<int:member_id>")
def member_profile(member_id):
    member = db.get_or_404(Member, member_id)
    application = member.application

    if (
        application is None
        or application.status != "approved"
        or not application.publish_profile
    ):
        abort(404)

    return render_template(
        "member.html",
        profile=application
    )

@app.route("/admin/teams/new", methods=["GET", "POST"])
@admin_required
def admin_team_create():
    error = None

    if request.method == "POST":
        check_csrf()

        try:
            data = read_team_form()
        except ValueError as exc:
            error = str(exc)
        else:
            new_team = Team(**data)

            db.session.add(new_team)
            db.session.commit()

            return redirect(url_for("admin_teams"))

    return render_template(
        "admin_team_form.html",
        team=None,
        error=error
    )


@app.route(
    "/admin/teams/<int:team_id>/edit",
    methods=["GET", "POST"]
)
@admin_required
def admin_team_edit(team_id):
    selected_team = db.get_or_404(Team, team_id)
    error = None

    if request.method == "POST":
        check_csrf()

        try:
            data = read_team_form()
        except ValueError as exc:
            error = str(exc)
        else:
            selected_team.name = data["name"]
            selected_team.game = data["game"]
            selected_team.level = data["level"]
            selected_team.member_count = data["member_count"]
            selected_team.description = data["description"]
            selected_team.image = data["image"]

            db.session.commit()

            return redirect(url_for("admin_teams"))

    return render_template(
        "admin_team_form.html",
        team=selected_team,
        error=error
    )


@app.route(
    "/admin/teams/<int:team_id>/delete",
    methods=["POST"]
)
@admin_required
def admin_team_delete(team_id):
    check_csrf()

    selected_team = db.get_or_404(Team, team_id)

    db.session.delete(selected_team)
    db.session.commit()

    return redirect(url_for("admin_teams"))


@app.route("/admin/events")
@admin_required
def admin_events():
    statement = db.select(Event).order_by(
        Event.date,
        Event.time
    )
    all_events = db.session.execute(statement).scalars().all()

    return render_template(
        "admin_events.html",
        events=all_events
    )


@app.route("/admin/events/new", methods=["GET", "POST"])
@admin_required
def admin_event_create():
    error = None

    if request.method == "POST":
        check_csrf()

        try:
            data = read_event_form()
        except ValueError as exc:
            error = str(exc)
        else:
            new_event = Event(**data)

            db.session.add(new_event)
            db.session.commit()

            return redirect(url_for("admin_events"))

    return render_template(
        "admin_event_form.html",
        event=None,
        error=error
    )


@app.route(
    "/admin/events/<int:event_id>/edit",
    methods=["GET", "POST"]
)
@admin_required
def admin_event_edit(event_id):
    selected_event = db.get_or_404(Event, event_id)
    error = None

    if request.method == "POST":
        check_csrf()

        try:
            data = read_event_form()
        except ValueError as exc:
            error = str(exc)
        else:
            selected_event.title = data["title"]
            selected_event.date = data["date"]
            selected_event.time = data["time"]
            selected_event.location = data["location"]
            selected_event.game = data["game"]
            selected_event.description = data["description"]

            db.session.commit()

            return redirect(url_for("admin_events"))

    return render_template(
        "admin_event_form.html",
        event=selected_event,
        error=error
    )


@app.route(
    "/admin/events/<int:event_id>/delete",
    methods=["POST"]
)
@admin_required
def admin_event_delete(event_id):
    check_csrf()

    selected_event = db.get_or_404(Event, event_id)

    db.session.delete(selected_event)
    db.session.commit()

    return redirect(url_for("admin_events"))

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
    statement = db.select(Team).order_by(Team.name)
    all_teams = db.session.execute(statement).scalars().all()

    return render_template("teams.html", teams=all_teams)

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

@app.route("/join", methods=["GET", "POST"])
def join():
    error = None

    if request.method == "POST":
        check_csrf()

        fields = (
            "first_name",
            "last_name",
            "email",
            "phone",
            "nickname",
            "game",
            "role",
            "external_profile",
            "notes",
        )

        data = {
            field: request.form.get(field, "").strip()
            for field in fields
        }

        try:
            required_fields = {
                "first_name": ("Vorname", 100),
                "last_name": ("Nachname", 100),
                "nickname": ("Nickname", 80),
                "game": ("Spiel", 100),
            }

            for field, (label, limit) in required_fields.items():
                if not data[field] or len(data[field]) > limit:
                    raise ValueError(
                        f"{label}: Bitte 1 bis {limit} Zeichen eingeben."
                    )

            if (
                len(data["email"]) > 254
                or not re.fullmatch(
                    r"[^@\s]+@[^@\s]+\.[^@\s]+",
                    data["email"]
                )
            ):
                raise ValueError(
                    "Bitte eine gültige E-Mail-Adresse eingeben."
                )

            if len(data["phone"]) > 40:
                raise ValueError("Die Telefonnummer ist zu lang.")

            if len(data["role"]) > 100:
                raise ValueError("Die Rollenbezeichnung ist zu lang.")

            if len(data["notes"]) > 5000:
                raise ValueError(
                    "Die Bemerkungen dürfen höchstens 5000 Zeichen enthalten."
                )

            try:
                birth_date = date.fromisoformat(
                    request.form.get("birth_date", "")
                )
            except ValueError:
                raise ValueError(
                    "Bitte ein gültiges Geburtsdatum eingeben."
                )

            if birth_date > date.today():
                raise ValueError(
                    "Das Geburtsdatum darf nicht in der Zukunft liegen."
                )

            profile_url = data["external_profile"]

            if profile_url:
                if (
                    len(profile_url) > 500
                    or any(
                        char.isspace() or ord(char) < 32
                        for char in profile_url
                    )
                ):
                    raise ValueError("Der Profil-Link ist ungültig.")

                try:
                    parsed = urlsplit(profile_url)

                    valid_profile = (
                        parsed.scheme == "https"
                        and bool(parsed.hostname)
                        and parsed.username is None
                        and parsed.password is None
                    )
                except ValueError:
                    valid_profile = False

                if not valid_profile:
                    raise ValueError(
                        "Bitte einen vollständigen HTTPS-Profil-Link eingeben."
                    )

        except ValueError as exc:
            error = str(exc)

        else:
            application = MemberApplication(
                **data,
                birth_date=birth_date,
                publish_profile=(
                    request.form.get("publish_profile") == "yes"
                )
            )

            db.session.add(application)
            db.session.commit()

            flash(
                "Vielen Dank! Dein Mitgliedsantrag wurde gespeichert "
                "und wird geprüft.",
                "member_application_success"
            )

            return redirect(url_for("join"))

    return render_template("join.html", error=error)

@app.route("/teamform", methods=["GET", "POST"])
def teamform():
    error = None

    if request.method == "POST":
        check_csrf()

        name = request.form.get("name", "").strip()
        game = request.form.get("game", "").strip()
        captain_name = request.form.get("captain_name", "").strip()
        email = request.form.get("email", "").strip()
        notes = request.form.get("notes", "").strip()

        try:
            if not name or len(name) > 150:
                raise ValueError(
                    "Der Teamname muss 1 bis 150 Zeichen lang sein."
                )

            if not game or len(game) > 100:
                raise ValueError(
                    "Das Spiel muss 1 bis 100 Zeichen lang sein."
                )

            if not captain_name or len(captain_name) > 150:
                raise ValueError(
                    "Bitte den Namen der Kontaktperson eingeben."
                )

            if (
                len(email) > 254
                or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email)
            ):
                raise ValueError(
                    "Bitte eine gültige E-Mail-Adresse eingeben."
                )

            try:
                player_count = int(
                    request.form.get("player_count", "")
                )
            except ValueError:
                raise ValueError(
                    "Bitte eine ganze Zahl für die Spielerzahl eingeben."
                )

            if not 1 <= player_count <= 1000:
                raise ValueError(
                    "Die Spielerzahl muss zwischen 1 und 1000 liegen."
                )

            if len(notes) > 5000:
                raise ValueError(
                    "Die Bemerkungen dürfen höchstens 5000 Zeichen enthalten."
                )

        except ValueError as exc:
            error = str(exc)

        else:
            application = TeamApplication(
                name=name,
                game=game,
                player_count=player_count,
                captain_name=captain_name,
                email=email,
                notes=notes
            )

            db.session.add(application)
            db.session.commit()

            flash(
                "Vielen Dank! Eure Teamanmeldung wurde gespeichert. "
                "Sie wird vom Verein geprüft.",
                "team_application_success"
            )

            return redirect(url_for("teamform"))

    return render_template("teamform.html", error=error)


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

@app.route("/admin/team-applications")
@admin_required
def admin_team_applications():
    statement = db.select(TeamApplication).order_by(
        TeamApplication.created_at.desc(),
        TeamApplication.id.desc()
    )

    applications = db.session.execute(statement).scalars().all()

    return render_template(
        "admin_team_applications.html",
        applications=applications
    )


@app.route(
    "/admin/team-applications/<int:application_id>/approve",
    methods=["POST"]
)
@admin_required
def admin_team_application_approve(application_id):
    check_csrf()

    application = db.get_or_404(TeamApplication, application_id)

    level = request.form.get("level", "").strip()

    if not level or len(level) > 100:
        flash(
            "Bitte ein Spielniveau mit 1 bis 100 Zeichen eingeben.",
            "team_application_admin"
        )
        return redirect(url_for("admin_team_applications"))


    statement = (
        db.update(TeamApplication)
        .where(
            TeamApplication.id == application_id,
            TeamApplication.status == "pending"
        )
        .values(status="approved")
    )

    result = db.session.execute(statement)

    if result.rowcount != 1:
        db.session.rollback()

        flash(
            "Dieser Antrag wurde bereits bearbeitet.",
            "team_application_admin"
        )
        return redirect(url_for("admin_team_applications"))

    new_team = Team(
        name=application.name,
        game=application.game,
        level=level,
        member_count=application.player_count,
        description="",
        image=None
    )

    db.session.add(new_team)
    db.session.commit()

    flash(
        "Der Antrag wurde angenommen. Das Team ist jetzt veröffentlicht.",
        "team_application_admin"
    )

    return redirect(url_for("admin_team_applications"))


@app.route(
    "/admin/team-applications/<int:application_id>/reject",
    methods=["POST"]
)
@admin_required
def admin_team_application_reject(application_id):
    check_csrf()

    db.get_or_404(TeamApplication, application_id)

    statement = (
        db.update(TeamApplication)
        .where(
            TeamApplication.id == application_id,
            TeamApplication.status == "pending"
        )
        .values(status="rejected")
    )

    result = db.session.execute(statement)

    if result.rowcount != 1:
        db.session.rollback()

        flash(
            "Dieser Antrag wurde bereits bearbeitet.",
            "team_application_admin"
        )
    else:
        db.session.commit()

        flash(
            "Der Antrag wurde abgelehnt.",
            "team_application_admin"
        )

    return redirect(url_for("admin_team_applications"))

if __name__ == "__main__":
    app.run(debug=True)