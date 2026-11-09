from flask import Flask, render_template
from models import (
    db,
    Event,
    Team,
    Admin,
    TeamApplication,
    MemberApplication,
    Member,
    Product,
)
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
    send_from_directory,
)
import os
import secrets
import hmac
from datetime import timedelta, date, time, timezone
from functools import wraps
import re
from urllib.parse import urlsplit
from sqlalchemy.exc import IntegrityError
import sqlite3
from pathlib import Path
from contextlib import closing
from sqlalchemy import inspect, text
import warnings
from io import BytesIO
from PIL import Image, ImageOps, UnidentifiedImageError
import shutil
import sqlite3
from pathlib import Path
from contextlib import closing
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY")

if not app.config["SECRET_KEY"]:
    raise RuntimeError(
        "SECRET_KEY fehlt. Setze die Umgebungsvariable vor dem Start."
    )

app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=2)
app.config["MAX_CONTENT_LENGTH"] = 6 * 1024 * 1024

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///berner_fuechse.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

def public_member_photo_folder():
    return Path(app.static_folder) / "img" / "mitglieder"

def team_photo_folder():
    return Path(app.instance_path) / "pending_uploads" / "teams"


def save_team_photo(upload):
    if upload is None or not upload.filename:
        return None

    max_size = 5 * 1024 * 1024
    content = upload.stream.read(max_size + 1)

    if not content:
        raise ValueError("Die ausgewählte Bilddatei ist leer.")

    if len(content) > max_size:
        raise ValueError("Das Teambild darf höchstens 5 MB groß sein.")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter(
                "error",
                Image.DecompressionBombWarning
            )

            with Image.open(BytesIO(content)) as original:
                if original.format not in {"JPEG", "PNG", "WEBP"}:
                    raise ValueError(
                        "Bitte ein JPG-, PNG- oder WEBP-Bild auswählen."
                    )

                if original.width * original.height > 12_000_000:
                    raise ValueError(
                        "Das Bild darf höchstens 12 Megapixel haben."
                    )

                original.load()

                photo = ImageOps.exif_transpose(original).convert("RGBA")

    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning
    ):
        raise ValueError(
            "Die Datei konnte nicht als gültiges Bild gelesen werden."
        )

    photo.thumbnail((1200, 1200))
    photo.info.clear()

    folder = team_photo_folder()
    folder.mkdir(parents=True, exist_ok=True)

    filename = f"{secrets.token_hex(16)}.png"
    photo.save(folder / filename, format="PNG")

    return filename

def publish_team_photo(application):
    if application.status != "approved" or not application.team_id:
        raise ValueError("Der Antrag ist keinem angenommenen Team zugeordnet.")

    if not application.photo_filename:
        return False

    team = db.session.get(Team, application.team_id)

    if team is None:
        raise ValueError("Das zugehörige Team existiert nicht mehr.")

    filename = application.photo_filename

    if not re.fullmatch(r"[a-f0-9]{32}\.png", filename):
        raise ValueError("Der gespeicherte Bilddateiname ist ungültig.")

    public_folder = Path(app.static_folder) / "img" / "teams"
    source = team_photo_folder() / filename
    destination = public_folder / filename

    public_folder.mkdir(parents=True, exist_ok=True)

    created_copy = False

    try:
        if not destination.is_file():
            if not source.is_file():
                raise FileNotFoundError(
                    "Das eingereichte Teambild wurde nicht gefunden."
                )

            created_copy = True
            shutil.copyfile(source, destination)

        team.image = f"img/teams/{filename}"
        db.session.commit()

    except Exception:
        db.session.rollback()

        if created_copy:
            destination.unlink(missing_ok=True)

        raise

    return True

def public_member_photo(application):
    if (
        application.status != "approved"
        or not application.publish_profile
        or not application.photo_filename
    ):
        return None

    filename = application.photo_filename

    if not re.fullmatch(r"[a-f0-9]{32}\.png", filename):
        return None

    photo_path = public_member_photo_folder() / filename

    if not photo_path.is_file():
        return None

    return filename


def publish_member_photo(application):
    if (
        application.status != "approved"
        or not application.publish_profile
        or not application.photo_filename
    ):
        return False

    filename = application.photo_filename

    if not re.fullmatch(r"[a-f0-9]{32}\.png", filename):
        raise ValueError("Der gespeicherte Fotodateiname ist ungültig.")

    folder = public_member_photo_folder()
    destination = folder / filename

    if destination.is_file():
        return True

    source = member_photo_folder() / filename

    if not source.is_file():
        raise FileNotFoundError("Das eingereichte Foto wurde nicht gefunden.")

    folder.mkdir(parents=True, exist_ok=True)

    try:
        shutil.copyfile(source, destination)
    except OSError:
        destination.unlink(missing_ok=True)
        raise

    return True

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

@app.route(
    "/admin/team-applications/<int:application_id>/photo"
)

@admin_required
def admin_team_application_photo(application_id):
    application = db.get_or_404(
        TeamApplication,
        application_id
    )

    if not application.photo_filename:
        abort(404)

    response = send_from_directory(
        str(team_photo_folder()),
        application.photo_filename
    )

    response.headers["Cache-Control"] = "private, no-store"

    return response


@app.route(
    "/admin/team-applications/<int:application_id>/publish-photo",
    methods=["POST"]
)
@admin_required
def admin_team_photo_publish(application_id):
    check_csrf()

    application = db.get_or_404(
        TeamApplication,
        application_id
    )

    if (
        application.status != "approved"
        or not application.team_id
        or not application.photo_filename
    ):
        abort(409)

    try:
        publish_team_photo(application)
    except (OSError, ValueError, SQLAlchemyError):
        app.logger.exception(
            "Teamfotoveröffentlichung fehlgeschlagen: Antrag %s",
            application.id
        )

        flash(
            "Das Teambild konnte nicht veröffentlicht werden. "
            "Bitte den Server-Log prüfen.",
            "team_application_admin"
        )
    else:
        flash(
            "Das Teambild wurde veröffentlicht.",
            "team_application_admin"
        )

    return redirect(url_for("admin_team_applications"))

@app.cli.command("add-team-photo")
def add_team_photo():
    inspector = inspect(db.engine)

    if not inspector.has_table("team_applications"):
        raise click.ClickException(
            "Die Tabelle team_applications fehlt. "
            "Führe zuerst init-db aus."
        )

    columns = {
        column["name"]
        for column in inspector.get_columns("team_applications")
    }

    changes = [
        (
            "photo_filename",
            "ALTER TABLE team_applications "
            "ADD COLUMN photo_filename VARCHAR(255)"
        ),
        (
            "team_id",
            "ALTER TABLE team_applications "
            "ADD COLUMN team_id INTEGER REFERENCES teams(id)"
        ),
    ]

    missing_changes = [
        sql
        for column_name, sql in changes
        if column_name not in columns
    ]

    if not missing_changes:
        click.echo("Die Team-Fotofelder sind bereits vorhanden.")
        return

    database_path = Path(db.engine.url.database)

    backup_folder = Path(app.instance_path) / "backups"
    backup_folder.mkdir(parents=True, exist_ok=True)

    backup_path = backup_folder / (
        f"before_team_photo_{secrets.token_hex(8)}.db"
    )

    with closing(sqlite3.connect(str(database_path))) as source:
        with closing(sqlite3.connect(str(backup_path))) as destination:
            source.backup(destination)

    with db.engine.begin() as connection:
        for sql in missing_changes:
            connection.execute(text(sql))

    click.echo(f"Backup erstellt: {backup_path}")
    click.echo("Team-Fotofelder erfolgreich hinzugefügt.")

@app.context_processor
def member_photo_helpers():
    return {
        "public_member_photo": public_member_photo
    }

def event_photo_folder():
    return Path(app.static_folder) / "img" / "events"


def save_event_photo(upload):
    if upload is None or not upload.filename:
        return None

    max_size = 5 * 1024 * 1024
    content = upload.stream.read(max_size + 1)

    if not content:
        raise ValueError("Die ausgewählte Bilddatei ist leer.")

    if len(content) > max_size:
        raise ValueError("Das Eventbild darf höchstens 5 MB groß sein.")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter(
                "error",
                Image.DecompressionBombWarning
            )

            with Image.open(BytesIO(content)) as original:
                if original.format not in {"JPEG", "PNG", "WEBP"}:
                    raise ValueError(
                        "Bitte ein JPG-, PNG- oder WEBP-Bild auswählen."
                    )

                if original.width * original.height > 12_000_000:
                    raise ValueError(
                        "Das Bild darf höchstens 12 Megapixel haben."
                    )

                original.load()

                photo = ImageOps.exif_transpose(original).convert("RGB")

    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning
    ):
        raise ValueError(
            "Die Datei konnte nicht als gültiges Bild gelesen werden."
        )

    photo.thumbnail((1200, 1200))
    photo.info.clear()

    folder = event_photo_folder()
    folder.mkdir(parents=True, exist_ok=True)

    filename = f"{secrets.token_hex(16)}.jpg"
    photo.save(folder / filename, format="JPEG", quality=88)

    return f"img/events/{filename}"


@app.cli.command("add-event-image")
def add_event_image():
    inspector = inspect(db.engine)

    if not inspector.has_table("events"):
        raise click.ClickException(
            "Die Tabelle events fehlt. Führe zuerst init-db aus."
        )

    columns = {
        column["name"]
        for column in inspector.get_columns("events")
    }

    if "image" in columns:
        click.echo("Das Eventbild-Feld ist bereits vorhanden.")
        return

    database_path = Path(db.engine.url.database)

    backup_folder = Path(app.instance_path) / "backups"
    backup_folder.mkdir(parents=True, exist_ok=True)

    backup_path = backup_folder / (
        f"before_event_image_{secrets.token_hex(8)}.db"
    )

    with closing(sqlite3.connect(str(database_path))) as source:
        with closing(sqlite3.connect(str(backup_path))) as destination:
            source.backup(destination)

    with db.engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE events ADD COLUMN image VARCHAR(255)")
        )

    click.echo(f"Backup erstellt: {backup_path}")
    click.echo("Eventbild-Feld erfolgreich hinzugefügt.")

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

def member_photo_folder():
    return Path(app.instance_path) / "pending_uploads" / "mitglieder"


def save_member_photo(upload):
    if upload is None or not upload.filename:
        return None

    max_size = 5 * 1024 * 1024
    content = upload.stream.read(max_size + 1)

    if not content:
        raise ValueError("Die ausgewählte Fotodatei ist leer.")

    if len(content) > max_size:
        raise ValueError("Das Foto darf höchstens 5 MB groß sein.")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter(
                "error",
                Image.DecompressionBombWarning
            )

            with Image.open(BytesIO(content)) as original:
                if original.format not in {"JPEG", "PNG", "WEBP"}:
                    raise ValueError(
                        "Bitte ein JPG-, PNG- oder WEBP-Bild auswählen."
                    )

                if original.width * original.height > 12_000_000:
                    raise ValueError(
                        "Das Foto darf höchstens 12 Megapixel haben."
                    )

                original.load()

                photo = ImageOps.exif_transpose(original).convert("RGB")

    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning
    ):
        raise ValueError(
            "Die Datei konnte nicht als gültiges Bild gelesen werden."
        )

    photo.thumbnail((1200, 1200))
    photo.info.clear()

    folder = member_photo_folder()
    folder.mkdir(parents=True, exist_ok=True)

    filename = f"{secrets.token_hex(16)}.png"
    photo.save(folder / filename, format="PNG")

    return filename

def read_selected_teams():
    raw_ids = request.form.getlist("team_ids")

    try:
        team_ids = {int(value) for value in raw_ids}
    except ValueError:
        raise ValueError("Die Teamauswahl ist ungültig.")

    if not team_ids:
        return []

    selected_teams = db.session.execute(
        db.select(Team).where(Team.id.in_(team_ids))
    ).scalars().all()

    if len(selected_teams) != len(team_ids):
        raise ValueError(
            "Mindestens ein ausgewähltes Team existiert nicht mehr."
        )

    return selected_teams

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
            r"img/(?:teams/)?[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp)",
            image,
            flags=re.IGNORECASE
        )

        if len(image) > 255 or not valid_image:
            raise ValueError(
                "Bildpfad ungültig. Beispiel: img/teams/team_alpha.jpg"
            )

    return {
        "name": name,
        "game": game,
        "level": level,
        "member_count": member_count,
        "description": description,
        "image": image or None,
    }



def csrf_token():
    token = session.get("csrf_token")

    if token is None:
        token = secrets.token_hex(32)
        session["csrf_token"] = token

    return token

@app.cli.command("add-member-photo")
def add_member_photo():
    inspector = inspect(db.engine)

    if not inspector.has_table("member_applications"):
        raise click.ClickException(
            "Die Tabelle member_applications fehlt. "
            "Führe zuerst init-db aus."
        )

    columns = {
        column["name"]
        for column in inspector.get_columns("member_applications")
    }

    if "photo_filename" in columns:
        click.echo("Die Fotospalte ist bereits vorhanden.")
        return

    database_path = Path(db.engine.url.database)

    backup_folder = Path(app.instance_path) / "backups"
    backup_folder.mkdir(parents=True, exist_ok=True)

    backup_path = backup_folder / (
        f"before_member_photo_{secrets.token_hex(8)}.db"
    )

    with closing(sqlite3.connect(str(database_path))) as source:
        with closing(sqlite3.connect(str(backup_path))) as destination:
            source.backup(destination)

    with db.engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE member_applications "
                "ADD COLUMN photo_filename VARCHAR(255)"
            )
        )

    click.echo(f"Backup erstellt: {backup_path}")
    click.echo("Fotospalte erfolgreich hinzugefügt.")

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

@app.route(
    "/admin/members/<int:member_id>/teams",
    methods=["POST"]
)
@admin_required
def admin_member_teams_save(member_id):
    check_csrf()

    member = db.get_or_404(Member, member_id)

    if member.application.status != "approved":
        abort(409)

    try:
        selected_teams = read_selected_teams()
    except ValueError as exc:
        flash(str(exc), "member_application_admin")
    else:
        member.teams = selected_teams
        db.session.commit()

        flash(
            "Die Teamzuordnung wurde gespeichert.",
            "member_application_admin"
        )

    return redirect(url_for("admin_member_applications"))

def delete_member_photo_files(filename):
    if not filename or not re.fullmatch(r"[a-f0-9]{32}\.png", filename):
        return

    (public_member_photo_folder() / filename).unlink(missing_ok=True)
    (member_photo_folder() / filename).unlink(missing_ok=True)


def read_member_edit_form():
    values = {
        "nickname": request.form.get("nickname", "").strip(),
        "game": request.form.get("game", "").strip(),
        "role": request.form.get("role", "").strip(),
        "external_profile": request.form.get("external_profile", "").strip(),
    }

    limits = {
        "nickname": 80,
        "game": 100,
        "role": 100,
        "external_profile": 500,
    }

    if not values["nickname"] or not values["game"]:
        raise ValueError("Nickname und Spiel sind Pflichtfelder.")

    for key, limit in limits.items():
        if len(values[key]) > limit:
            raise ValueError("Ein Feld ist zu lang.")

    if values["external_profile"]:
        parts = urlsplit(values["external_profile"])

        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError("Der externe Profil-Link ist ungültig.")

    values["publish_profile"] = request.form.get("publish_profile") == "1"

    return values


@app.route("/admin/members/<int:member_id>/edit", methods=["POST"])
@admin_required
def admin_member_edit_save(member_id):
    check_csrf()

    member = db.get_or_404(Member, member_id)
    application = member.application

    try:
        values = read_member_edit_form()
    except ValueError as exc:
        flash(str(exc), "member_application_admin")

        return redirect(url_for("admin_member_edit", member_id=member_id))

    application.nickname = values["nickname"]
    application.game = values["game"]
    application.role = values["role"]
    application.external_profile = values["external_profile"]
    application.publish_profile = values["publish_profile"]
    member.nickname_key = values["nickname"].casefold()

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()

        flash(
            "Dieser Nickname ist bereits vergeben.",
            "member_application_admin"
        )

        return redirect(url_for("admin_member_edit", member_id=member_id))

    if application.publish_profile:
        try:
            publish_member_photo(application)
        except (OSError, ValueError):
            app.logger.exception(
                "Fotoveröffentlichung fehlgeschlagen: Mitglied %s",
                member_id
            )

            flash(
                "Das Mitglied wurde gespeichert, aber das Foto "
                "konnte nicht veröffentlicht werden.",
                "member_application_admin"
            )

            return redirect(url_for("admin_member_applications"))
    else:
        filename = application.photo_filename

        if filename and re.fullmatch(r"[a-f0-9]{32}\.png", filename):
            (public_member_photo_folder() / filename).unlink(missing_ok=True)

    flash(
        "Das Mitglied wurde gespeichert.",
        "member_application_admin"
    )

    return redirect(url_for("admin_member_applications"))


@app.route("/admin/members/<int:member_id>/delete", methods=["POST"])
@admin_required
def admin_member_delete(member_id):
    check_csrf()

    member = db.get_or_404(Member, member_id)
    application = member.application
    filename = application.photo_filename

    db.session.delete(member)
    db.session.delete(application)
    db.session.commit()

    delete_member_photo_files(filename)

    flash(
        "Das Mitglied wurde gelöscht.",
        "member_application_admin"
    )

    return redirect(url_for("admin_member_applications"))


def delete_member_photo_files(filename):
    if not filename or not re.fullmatch(r"[a-f0-9]{32}\.png", filename):
        return

    (public_member_photo_folder() / filename).unlink(missing_ok=True)
    (member_photo_folder() / filename).unlink(missing_ok=True)


def unpublish_member_photo(filename):
    if filename and re.fullmatch(r"[a-f0-9]{32}\.png", filename):
        (public_member_photo_folder() / filename).unlink(missing_ok=True)


def member_form_from_request():
    return {
        "nickname": request.form.get("nickname", "").strip(),
        "game": request.form.get("game", "").strip(),
        "role": request.form.get("role", "").strip(),
        "external_profile": request.form.get("external_profile", "").strip(),
        "publish_profile": request.form.get("publish_profile") == "1",
        "team_ids": request.form.getlist("team_ids"),
    }


def member_form_for(member):
    application = member.application

    return {
        "nickname": application.nickname,
        "game": application.game,
        "role": application.role,
        "external_profile": application.external_profile,
        "publish_profile": application.publish_profile,
        "team_ids": [str(team.id) for team in member.teams],
    }


def validate_member_form(form):
    limits = {
        "nickname": 80,
        "game": 100,
        "role": 100,
        "external_profile": 500,
    }

    if not form["nickname"] or not form["game"]:
        raise ValueError("Nickname und Spiel sind Pflichtfelder.")

    for key, limit in limits.items():
        if len(form[key]) > limit:
            raise ValueError("Ein Feld ist zu lang.")

    if form["external_profile"]:
        parts = urlsplit(form["external_profile"])

        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError("Der externe Profil-Link ist ungültig.")


@app.route("/admin/members")
@admin_required
def admin_members():
    statement = db.select(Member).order_by(Member.nickname_key)
    all_members = db.session.execute(statement).scalars().all()

    return render_template(
        "admin_member_list.html",
        members=all_members
    )


@app.route("/admin/members/<int:member_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_member_edit(member_id):
    selected_member = db.get_or_404(Member, member_id)
    application = selected_member.application
    error = None

    if request.method == "POST":
        check_csrf()
        form = member_form_from_request()

        try:
            validate_member_form(form)
            selected_teams = read_selected_teams()
        except ValueError as exc:
            error = str(exc)
        else:
            application.nickname = form["nickname"]
            application.game = form["game"]
            application.role = form["role"]
            application.external_profile = form["external_profile"]
            application.publish_profile = form["publish_profile"]
            selected_member.nickname_key = form["nickname"].casefold()
            selected_member.teams = selected_teams

            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                error = "Dieser Nickname ist bereits vergeben."
            else:
                photo_failed = False

                if application.publish_profile:
                    try:
                        publish_member_photo(application)
                    except (OSError, ValueError):
                        app.logger.exception(
                            "Fotoveröffentlichung fehlgeschlagen: Mitglied %s",
                            selected_member.id
                        )
                        photo_failed = True
                else:
                    unpublish_member_photo(application.photo_filename)

                if photo_failed:
                    flash(
                        "Gespeichert, aber das Foto konnte nicht "
                        "veröffentlicht werden.",
                        "member_admin"
                    )
                else:
                    flash("Das Mitglied wurde gespeichert.", "member_admin")

                return redirect(url_for("admin_members"))
    else:
        form = member_form_for(selected_member)

    teams = db.session.execute(
        db.select(Team).order_by(Team.name)
    ).scalars().all()

    return render_template(
        "admin_member_form.html",
        member=selected_member,
        form=form,
        teams=teams,
        error=error
    )


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
    statement = db.select(MemberApplication).where(
        MemberApplication.status == "pending"
    ).order_by(
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

    teams = db.session.execute(
        db.select(Team).order_by(Team.name)
    ).scalars().all()

    return render_template(
        "admin_members.html",
        applications=applications,
        members_by_application=members_by_application,
        teams=teams
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

    try:
        selected_teams = read_selected_teams()
    except ValueError as exc:
        flash(str(exc), "member_application_admin")

        return redirect(url_for("admin_member_applications"))

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
        nickname_key=application.nickname.strip().casefold(),
        teams=selected_teams
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
        try:
            publish_member_photo(application)
        except (OSError, ValueError):
            app.logger.exception(
                "Fotoveröffentlichung fehlgeschlagen: Antrag %s",
                application.id
            )

            flash(
                "Der Antrag wurde angenommen, aber das Foto "
                "konnte noch nicht veröffentlicht werden.",
                "member_application_admin"
            )
        else:
            flash(
                "Der Mitgliedsantrag wurde angenommen.",
                "member_application_admin"
            )

    return redirect(url_for("admin_member_applications"))

@app.route(
    "/admin/member-applications/<int:application_id>/publish-photo",
    methods=["POST"]
)
@admin_required
def admin_member_photo_publish(application_id):
    check_csrf()

    application = db.get_or_404(
        MemberApplication,
        application_id
    )

    if (
        application.status != "approved"
        or not application.publish_profile
        or not application.photo_filename
    ):
        abort(409)

    try:
        publish_member_photo(application)
    except (OSError, ValueError):
        app.logger.exception(
            "Fotoveröffentlichung fehlgeschlagen: Antrag %s",
            application.id
        )

        flash(
            "Das Foto konnte nicht veröffentlicht werden. "
            "Bitte den Server-Log prüfen.",
            "member_application_admin"
        )
    else:
        flash(
            "Das Foto wurde veröffentlicht.",
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
        profile=application,
        member=member
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
            photo_path = save_event_photo(request.files.get("photo"))
        except ValueError as exc:
            error = str(exc)
        else:
            new_event = Event(**data, image=photo_path)

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
            photo_path = save_event_photo(request.files.get("photo"))
        except ValueError as exc:
            error = str(exc)
        else:
            selected_event.title = data["title"]
            selected_event.date = data["date"]
            selected_event.time = data["time"]
            selected_event.location = data["location"]
            selected_event.game = data["game"]
            selected_event.description = data["description"]

            if photo_path:
                selected_event.image = photo_path

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

def shop_photo_folder():
    return Path(app.static_folder) / "img" / "shop"


def save_shop_photo(upload):
    if upload is None or not upload.filename:
        return None

    content = upload.stream.read(5 * 1024 * 1024 + 1)

    if not content or len(content) > 5 * 1024 * 1024:
        raise ValueError(
            "Bitte ein nicht leeres Bild mit höchstens 5 MB auswählen."
        )

    try:
        with warnings.catch_warnings():
            warnings.simplefilter(
                "error",
                Image.DecompressionBombWarning
            )

            with Image.open(BytesIO(content)) as original:
                if original.format not in {"JPEG", "PNG", "WEBP"}:
                    raise ValueError(
                        "Bitte ein JPG-, PNG- oder WEBP-Bild auswählen."
                    )

                if original.width * original.height > 12_000_000:
                    raise ValueError(
                        "Das Bild darf höchstens 12 Megapixel haben."
                    )

                original.load()
                photo = ImageOps.exif_transpose(original).convert("RGBA")

    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning
    ):
        raise ValueError(
            "Die Datei konnte nicht als gültiges Bild gelesen werden."
        )

    photo.thumbnail((1200, 1200))
    photo.info.clear()

    folder = shop_photo_folder()
    folder.mkdir(parents=True, exist_ok=True)

    filename = f"{secrets.token_hex(16)}.png"
    photo.save(folder / filename, format="PNG")

    return f"img/shop/{filename}"


def read_product_form():
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    price = request.form.get("price", "").strip().replace(",", ".")
    quantity = request.form.get("quantity", "").strip()

    if not name or len(name) > 150:
        raise ValueError(
            "Der Produktname muss 1 bis 150 Zeichen lang sein."
        )

    if len(description) > 10000:
        raise ValueError("Die Beschreibung ist zu lang.")

    if not re.fullmatch(r"[0-9]{1,5}(?:\.[0-9]{1,2})?", price):
        raise ValueError(
            "Bitte einen Preis von 0 bis 99999.99 CHF eingeben."
        )

    if (
        not re.fullmatch(r"[0-9]{1,5}", quantity)
        or int(quantity) > 10000
    ):
        raise ValueError(
            "Der Lagerbestand muss zwischen 0 und 10000 liegen."
        )

    francs, _, cents = price.partition(".")
    price_cents = int(francs) * 100 + int(cents.ljust(2, "0"))

    return {
        "name": name,
        "description": description,
        "price_cents": price_cents,
        "quantity": int(quantity),
    }


@app.route("/admin/shop")
@admin_required
def admin_shop():
    products = db.session.execute(
        db.select(Product).order_by(Product.name)
    ).scalars().all()

    return render_template(
        "admin_shop.html",
        products=products
    )


@app.route("/admin/shop/new", methods=["GET", "POST"])
@admin_required
def admin_product_create():
    error = None
    photo_path = None

    if request.method == "POST":
        check_csrf()

        try:
            values = read_product_form()
            photo_path = save_shop_photo(request.files.get("photo"))

            db.session.add(Product(**values, image=photo_path))
            db.session.commit()

        except ValueError as exc:
            db.session.rollback()
            error = str(exc)

        except (OSError, SQLAlchemyError):
            db.session.rollback()

            app.logger.exception(
                "Produkt konnte nicht gespeichert werden."
            )

            if photo_path:
                try:
                    (Path(app.static_folder) / photo_path).unlink(
                        missing_ok=True
                    )
                except OSError:
                    app.logger.exception(
                        "Neues Produktbild konnte nicht entfernt werden."
                    )

            error = (
                "Das Produkt konnte nicht gespeichert werden. "
                "Bitte erneut versuchen."
            )

        else:
            flash(
                "Das Produkt wurde gespeichert.",
                "shop_admin"
            )

            return redirect(url_for("admin_shop"))

    return render_template(
        "admin_product_form.html",
        product=None,
        error=error
    )

def remove_shop_photo(image_path):
    if not image_path or not re.fullmatch(
        r"img/shop/[a-f0-9]{32}\.png",
        image_path
    ):
        return

    try:
        referenced = db.session.execute(
            db.select(Product.id)
            .where(Product.image == image_path)
            .limit(1)
        ).first()

        if referenced is None:
            (Path(app.static_folder) / image_path).unlink(
                missing_ok=True
            )

    except (OSError, SQLAlchemyError):
        app.logger.exception(
            "Produktbild konnte nicht entfernt werden."
        )


@app.route(
    "/admin/shop/<int:product_id>/edit",
    methods=["GET", "POST"]
)
@admin_required
def admin_product_edit(product_id):
    product = db.get_or_404(Product, product_id)
    error = None
    photo_path = None
    old_image = product.image

    if request.method == "POST":
        check_csrf()

        try:
            values = read_product_form()
            photo_path = save_shop_photo(request.files.get("photo"))

            for field, value in values.items():
                setattr(product, field, value)

            if photo_path:
                product.image = photo_path

            db.session.commit()

        except ValueError as exc:
            db.session.rollback()
            error = str(exc)

        except (OSError, SQLAlchemyError):
            db.session.rollback()

            app.logger.exception(
                "Produkt konnte nicht aktualisiert werden."
            )

            remove_shop_photo(photo_path)

            error = (
                "Das Produkt konnte nicht gespeichert werden. "
                "Bitte erneut versuchen."
            )

        else:
            if photo_path:
                remove_shop_photo(old_image)

            flash(
                "Das Produkt wurde aktualisiert.",
                "shop_admin"
            )

            return redirect(url_for("admin_shop"))

    return render_template(
        "admin_product_form.html",
        product=product,
        error=error
    )


@app.route(
    "/admin/shop/<int:product_id>/delete",
    methods=["POST"]
)
@admin_required
def admin_product_delete(product_id):
    check_csrf()

    product = db.get_or_404(Product, product_id)
    image_path = product.image

    try:
        db.session.delete(product)
        db.session.commit()

    except SQLAlchemyError:
        db.session.rollback()

        app.logger.exception(
            "Produkt konnte nicht gelöscht werden."
        )

        flash(
            "Das Produkt konnte nicht gelöscht werden.",
            "shop_admin"
        )

    else:
        remove_shop_photo(image_path)

        flash(
            "Das Produkt wurde gelöscht.",
            "shop_admin"
        )

    return redirect(url_for("admin_shop"))

@app.cli.command("init-db")
def init_db():
    db.create_all()
    print("Datenbank bereit: instance/berner_fuechse.db")

@app.cli.command("add-member-photo")
def add_member_photo():
    inspector = inspect(db.engine)

    if not inspector.has_table("member_applications"):
        raise click.ClickException(
            "Die Tabelle member_applications fehlt. "
            "Führe zuerst init-db aus."
        )

    columns = {
        column["name"]
        for column in inspector.get_columns("member_applications")
    }

    if "photo_filename" in columns:
        click.echo("Die Fotospalte ist bereits vorhanden.")
        return

    database_path = Path(db.engine.url.database)

    backup_folder = Path(app.instance_path) / "backups"
    backup_folder.mkdir(parents=True, exist_ok=True)

    backup_path = backup_folder / (
        f"before_member_photo_{secrets.token_hex(8)}.db"
    )

    with closing(sqlite3.connect(str(database_path))) as source:
        with closing(sqlite3.connect(str(backup_path))) as destination:
            source.backup(destination)

    with db.engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE member_applications "
                "ADD COLUMN photo_filename VARCHAR(255)"
            )
        )

    click.echo(f"Backup erstellt: {backup_path}")
    click.echo("Fotospalte erfolgreich hinzugefügt.")

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

@app.route("/teams/<int:team_id>")
def team_profile(team_id):
    team = db.get_or_404(Team, team_id)

    statement = (
        db.select(Member)
        .join(MemberApplication)
        .where(
            Member.teams.any(Team.id == team.id),
            MemberApplication.status == "approved",
            MemberApplication.publish_profile.is_(True)
        )
        .order_by(Member.nickname_key)
    )

    public_members = db.session.execute(statement).scalars().all()

    return render_template(
        "team.html",
        team=team,
        members=public_members
    )

@app.route("/events")
def events():
    statement = db.select(Event).order_by(Event.date, Event.time)
    all_events = db.session.execute(statement).scalars().all()

    return render_template("events.html", events=all_events)

@app.route("/membership")
def membership():
    statement = (
        db.select(Member)
        .join(MemberApplication)
        .where(
            MemberApplication.status == "approved",
            MemberApplication.publish_profile.is_(True)
        )
        .order_by(Member.nickname_key)
    )

    members = db.session.execute(statement).scalars().all()

    return render_template(
        "membership.html",
        members=members
    )

@app.route("/shop")
def shop():
    sorting = {
        "name_asc": (
            db.func.lower(Product.name).asc(),
            Product.id.asc()
        ),
        "name_desc": (
            db.func.lower(Product.name).desc(),
            Product.id.asc()
        ),
        "price_asc": (
            Product.price_cents.asc(),
            Product.id.asc()
        ),
        "price_desc": (
            Product.price_cents.desc(),
            Product.id.asc()
        ),
        "newest": (
            Product.created_at.is_(None).asc(),
            Product.created_at.desc(),
            Product.id.desc()
        ),
        "oldest": (
            Product.created_at.is_(None).asc(),
            Product.created_at.asc(),
            Product.id.asc()
        ),
    }

    sort = request.args.get("sort", "name_asc")

    if sort not in sorting:
        sort = "name_asc"

    products = db.session.execute(
        db.select(Product)
        .where(Product.active.is_(True))
        .order_by(*sorting[sort])
    ).scalars().all()

    return render_template(
        "shop.html",
        products=products,
        sort=sort
    )

def read_cart():
    raw = session.get("cart", {})

    if not isinstance(raw, dict):
        return {}

    cart = {}

    for key, quantity in list(raw.items())[:50]:
        if (
            isinstance(key, str)
            and re.fullmatch(r"[1-9][0-9]{0,11}", key)
            and type(quantity) is int
            and 1 <= quantity <= 10000
        ):
            cart[key] = quantity

    return cart


def read_cart_quantity(allow_zero=False):
    value = request.form.get("quantity", "").strip()
    minimum = 0 if allow_zero else 1

    if not re.fullmatch(r"[0-9]{1,5}", value):
        raise ValueError(
            "Bitte eine gültige ganze Anzahl eingeben."
        )

    quantity = int(value)

    if not minimum <= quantity <= 10000:
        raise ValueError(
            f"Bitte eine Anzahl von {minimum} bis 10000 eingeben."
        )

    return quantity


@app.context_processor
def cart_helpers():
    return {
        "cart_count": sum(read_cart().values())
    }


@app.route("/shop/<int:product_id>")
def product_detail(product_id):
    product = db.get_or_404(Product, product_id)

    if not product.active:
        abort(404)

    return render_template(
        "product.html",
        product=product
    )


@app.route(
    "/cart/add/<int:product_id>",
    methods=["POST"]
)
def cart_add(product_id):
    check_csrf()

    product = db.get_or_404(Product, product_id)

    if not product.active:
        abort(404)

    cart = read_cart()
    key = str(product.id)

    try:
        quantity = read_cart_quantity()

        if key not in cart and len(cart) >= 50:
            raise ValueError(
                "Die maximale Anzahl verschiedener Produkte ist erreicht."
            )

        new_quantity = cart.get(key, 0) + quantity

        if (
            new_quantity > product.quantity
            or new_quantity > 10000
        ):
            raise ValueError(
                "Diese Anzahl ist nicht verfügbar."
            )

    except ValueError as exc:
        flash(str(exc), "cart_message")

        return redirect(
            url_for("product_detail", product_id=product.id)
        )

    cart[key] = new_quantity
    session["cart"] = cart

    flash(
        "Das Produkt wurde zum Warenkorb hinzugefügt.",
        "cart_message"
    )

    return redirect(url_for("cart_view"))


@app.route("/cart")
def cart_view():
    cart = read_cart()
    products = []

    if cart:
        products = db.session.execute(
            db.select(Product)
            .where(
                Product.id.in_([int(key) for key in cart]),
                Product.active.is_(True)
            )
            .order_by(Product.name)
        ).scalars().all()

    items = []
    kept = {}
    total_cents = 0

    for product in products:
        quantity = cart[str(product.id)]
        subtotal = product.price_cents * quantity

        kept[str(product.id)] = quantity

        items.append({
            "product": product,
            "quantity": quantity,
            "subtotal_cents": subtotal,
            "available": quantity <= product.quantity,
        })

        total_cents += subtotal

    if kept != cart:
        session["cart"] = kept

        flash(
            "Nicht mehr verfügbare Produkte wurden "
            "aus dem Warenkorb entfernt.",
            "cart_message"
        )

    return render_template(
        "cart.html",
        items=items,
        total_cents=total_cents
    )


@app.route(
    "/cart/update/<int:product_id>",
    methods=["POST"]
)
def cart_update(product_id):
    check_csrf()

    cart = read_cart()
    key = str(product_id)

    if key not in cart:
        return redirect(url_for("cart_view"))

    try:
        quantity = read_cart_quantity(allow_zero=True)

        if quantity == 0:
            cart.pop(key, None)

        else:
            product = db.get_or_404(Product, product_id)

            if not product.active:
                abort(404)

            if quantity > product.quantity:
                raise ValueError(
                    "Für diese Anzahl reicht der Lagerbestand nicht aus."
                )

            cart[key] = quantity

    except ValueError as exc:
        flash(str(exc), "cart_message")

    else:
        session["cart"] = cart

        flash(
            "Der Warenkorb wurde aktualisiert.",
            "cart_message"
        )

    return redirect(url_for("cart_view"))


@app.route(
    "/cart/remove/<int:product_id>",
    methods=["POST"]
)
def cart_remove(product_id):
    check_csrf()

    cart = read_cart()
    cart.pop(str(product_id), None)

    session["cart"] = cart

    flash(
        "Das Produkt wurde aus dem Warenkorb entfernt.",
        "cart_message"
    )

    return redirect(url_for("cart_view"))

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
            try:
                photo_filename = save_member_photo(
                    request.files.get("photo")
                )
            except ValueError as exc:
                return render_template(
                    "join.html",
                    error=str(exc)
                )

            application = MemberApplication(
                **data,
                birth_date=birth_date,
                photo_filename=photo_filename,
                publish_profile=(
                    request.form.get("publish_profile") == "yes"
                )
            )

            db.session.add(application)

            try:
                db.session.commit()
            except Exception:
                db.session.rollback()

                if photo_filename:
                    photo_path = member_photo_folder() / photo_filename
                    photo_path.unlink(missing_ok=True)

                raise

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
            try:
                photo_filename = save_team_photo(
                    request.files.get("photo")
                )
            except ValueError as exc:
                return render_template(
                    "teamform.html",
                    error=str(exc)
                )

            application = TeamApplication(
                name=name,
                game=game,
                player_count=player_count,
                captain_name=captain_name,
                email=email,
                notes=notes,
                photo_filename=photo_filename
            )

            db.session.add(application)

            try:
                db.session.commit()
            except Exception:
                db.session.rollback()

                if photo_filename:
                    photo_path = team_photo_folder() / photo_filename
                    photo_path.unlink(missing_ok=True)

                raise

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

@app.route(
    "/admin/member-applications/<int:application_id>/photo"
)
@admin_required
def admin_member_application_photo(application_id):
    application = db.get_or_404(
        MemberApplication,
        application_id
    )

    if not application.photo_filename:
        abort(404)

    response = send_from_directory(
        str(member_photo_folder()),
        application.photo_filename
    )

    response.headers["Cache-Control"] = "private, no-store"

    return response

@app.route("/admin/logout", methods=["POST"])
@admin_required
def admin_logout():
    check_csrf()
    session.clear()

    return redirect(url_for("admin_login"))

@app.route("/admin/team-applications")
@admin_required
def admin_team_applications():
    statement = db.select(TeamApplication).where(
        TeamApplication.status == "pending"
    ).order_by(
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
    db.session.flush()

    application.team_id = new_team.id

    db.session.commit()

    try:
        publish_team_photo(application)
    except (OSError, ValueError, SQLAlchemyError):
        app.logger.exception(
            "Teamfotoveröffentlichung fehlgeschlagen: Antrag %s",
            application.id
        )

        flash(
            "Das Team wurde angenommen, aber das Bild konnte "
            "noch nicht veröffentlicht werden.",
            "team_application_admin"
        )
    else:
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

@app.cli.command("add-product-quantity")
def add_product_quantity():
    from datetime import datetime
    from contextlib import closing
    import sqlite3

    if db.engine.url.get_backend_name() != "sqlite":
        raise click.ClickException(
            "Diese Migration unterstützt nur SQLite."
        )

    inspector = inspect(db.engine)

    if "products" not in inspector.get_table_names():
        raise click.ClickException(
            "Die Tabelle products fehlt. Zuerst init-db ausführen."
        )

    columns = {
        column["name"]
        for column in inspector.get_columns("products")
    }

    if "quantity" in columns:
        click.echo(
            "Die Spalte products.quantity ist bereits vorhanden."
        )
        return

    database = db.engine.url.database

    if not database or database == ":memory:":
        raise click.ClickException(
            "Keine SQLite-Dateidatenbank gefunden."
        )

    database_path = Path(database).resolve()

    if not database_path.is_file():
        raise click.ClickException(
            "Die SQLite-Datei wurde nicht gefunden."
        )

    backup_folder = Path(app.instance_path) / "backups"
    backup_folder.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_folder / (
        f"before_product_quantity_{timestamp}_"
        f"{secrets.token_hex(4)}.db"
    )

    db.session.remove()

    try:
        with closing(sqlite3.connect(str(database_path))) as source:
            with closing(sqlite3.connect(str(backup_path))) as backup:
                source.backup(backup)

        click.echo(f"Backup erstellt: {backup_path}")

        with db.engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE products "
                    "ADD COLUMN quantity INTEGER NOT NULL "
                    "DEFAULT 0 CHECK (quantity >= 0)"
                )
            )

    except (OSError, sqlite3.Error, SQLAlchemyError) as exc:
        raise click.ClickException(
            f"Migration fehlgeschlagen: {exc}"
        ) from exc

    click.echo(
        "Die Spalte products.quantity wurde hinzugefügt."
    )

@app.cli.command("add-product-created-at")
def add_product_created_at():
    import sqlite3
    import secrets
    from pathlib import Path
    from contextlib import closing
    from sqlalchemy import inspect, text

    if db.engine.url.get_backend_name() != "sqlite":
        raise click.ClickException(
            "Diese Migration unterstützt nur SQLite."
        )

    inspector = inspect(db.engine)

    if not inspector.has_table("products"):
        raise click.ClickException(
            "Die Tabelle products fehlt. Zuerst init-db ausführen."
        )

    columns = {
        column["name"]
        for column in inspector.get_columns("products")
    }

    if "created_at" in columns:
        click.echo("Die Spalte created_at ist bereits vorhanden.")
        return

    database = db.engine.url.database

    if not database or database == ":memory:":
        raise click.ClickException(
            "Keine SQLite-Dateidatenbank gefunden."
        )

    database_path = Path(database).resolve()

    if not database_path.is_file():
        raise click.ClickException(
            "Die SQLite-Datei wurde nicht gefunden."
        )

    backup_folder = Path(app.instance_path) / "backups"
    backup_folder.mkdir(parents=True, exist_ok=True)

    backup_path = backup_folder / (
        f"before_product_created_at_{secrets.token_hex(8)}.db"
    )

    db.session.remove()

    with closing(sqlite3.connect(str(database_path))) as source:
        with closing(sqlite3.connect(str(backup_path))) as backup:
            source.backup(backup)

    click.echo(f"Backup erstellt: {backup_path}")

    with db.engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE products "
                "ADD COLUMN created_at DATETIME"
            )
        )

    click.echo("Die Spalte products.created_at wurde hinzugefügt.")

if __name__ == "__main__":
    app.run(debug=True)