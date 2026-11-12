import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

import click
from flask import (
    abort, flash, redirect, render_template,
    request, url_for,
)
from sqlalchemy import (
    and_, case, func, inspect, literal, union_all,
)
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import SQLAlchemyError

from models import db, Order, DemoOrder
from shop_cart import VariantOrder


SOURCES = {
    "variants": ("Bestellung mit Grössen", VariantOrder),
    "test": ("Frühere Testbestellung", Order),
    "demo": ("Früherer Demo-Kauf", DemoOrder),
}

STATES = {
    "new": "Neu",
    "processing": "In Bearbeitung",
    "done": "Erledigt",
    "archived": "Archiviert",
}


class OrderHandling(db.Model):
    __tablename__ = "order_handling"

    id = db.Column(db.Integer, primary_key=True)
    source = db.Column(db.String(16), nullable=False)
    order_id = db.Column(db.Integer, nullable=False)
    state = db.Column(
        db.String(20), nullable=False, default="new"
    )
    updated_at = db.Column(db.DateTime, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("source", "order_id"),
        db.CheckConstraint(
            "state IN ('new', 'processing', 'done', 'archived')"
        ),
        db.CheckConstraint(
            "source IN ('variants', 'test', 'demo')"
        ),
    )


def json_items(raw):
    try:
        rows = json.loads(raw)
    except (TypeError, ValueError):
        return [], True

    if not isinstance(rows, list):
        return [], True

    items, error = [], len(rows) > 100

    for row in rows[:100]:
        if not isinstance(row, dict):
            error = True
            continue

        quantity = row.get("quantity")
        price = row.get("price_cents")
        name = row.get("name")
        size = row.get("size")

        if (
            type(quantity) is not int
            or quantity <= 0
            or type(price) is not int
            or price < 0
            or not isinstance(name, str)
            or (
                size is not None
                and not isinstance(size, str)
            )
        ):
            error = True
            continue

        items.append(dict(
            name=name,
            size=size,
            quantity=quantity,
            price_cents=price,
        ))

    return items, error


def get_order(source, order_id):
    if source not in SOURCES:
        abort(404)

    model = SOURCES[source][1]

    if not inspect(db.engine).has_table(model.__tablename__):
        abort(404)

    return db.get_or_404(model, order_id)


def is_complete(source, order):
    return source == "demo" or order.status == "test_paid"


def register_admin_orders(app, admin_required, check_csrf):
    @app.cli.command("init-order-history")
    def init_order_history():
        path = Path(db.engine.url.database).resolve()
        folder = (
            Path(app.root_path).parent
            / "Berner-Fuechse-Backups"
        )
        folder.mkdir(parents=True, exist_ok=True)

        backup = folder / (
            f"before_order_history_"
            f"{datetime.now():%Y%m%d_%H%M%S_%f}.db"
        )

        src = sqlite3.connect(
            path.as_uri() + "?mode=ro",
            uri=True,
        )
        dst = sqlite3.connect(backup)

        try:
            src.backup(dst)
        finally:
            src.close()
            dst.close()

        click.echo(f"Backup: {backup}")

        inspector = inspect(db.engine)

        if inspector.has_table("order_handling"):
            columns = {
                c["name"]
                for c in inspector.get_columns("order_handling")
            }

            if not set(
                OrderHandling.__table__.columns.keys()
            ).issubset(columns):
                raise click.ClickException(
                    "Unerwartete Struktur von order_handling. "
                    "Keine Daten gelöscht."
                )

        db.metadata.create_all(
            db.engine,
            tables=[OrderHandling.__table__],
        )

        click.echo(
            "Bestellhistorie bereit. Bestehende "
            "Bestellungen und Bestände unverändert."
        )

    @app.route("/admin/orders")
    @admin_required
    def admin_orders():
        source = request.args.get("source", "")
        state = request.args.get("state", "")
        scope = request.args.get("scope", "paid")

        if source not in SOURCES:
            source = ""

        if state not in {*STATES, "draft"}:
            state = ""

        if scope not in {"paid", "all"}:
            scope = "paid"

        try:
            page = max(
                1,
                min(
                    int(request.args.get("page", "1")),
                    100000,
                ),
            )
        except ValueError:
            page = 1

        tables = set(inspect(db.engine).get_table_names())
        queries = []

        for key, (label, model) in SOURCES.items():
            if (
                model.__tablename__ not in tables
                or (source and key != source)
            ):
                continue

            complete = (
                literal(True)
                if key == "demo"
                else model.status == "test_paid"
            )

            queries.append(db.select(
                literal(key).label("source"),
                model.id.label("id"),
                model.created_at.label("created_at"),
                model.total_cents.label("total_cents"),
                complete.label("complete"),
            ))

        rows, count, pages = [], 0, 1

        if queries:
            combined = union_all(*queries).subquery()

            handling = case(
                (
                    combined.c.complete.is_(True),
                    func.coalesce(OrderHandling.state, "new"),
                ),
                else_="draft",
            )

            query = (
                db.select(
                    combined,
                    handling.label("handling"),
                )
                .outerjoin(
                    OrderHandling,
                    and_(
                        OrderHandling.source == combined.c.source,
                        OrderHandling.order_id == combined.c.id,
                    ),
                )
            )

            if scope == "paid":
                query = query.where(
                    combined.c.complete.is_(True)
                )

            if state:
                query = query.where(handling == state)

            count = db.session.execute(
                db.select(func.count())
                .select_from(query.subquery())
            ).scalar_one()

            pages = max(1, (count + 29) // 30)
            page = min(page, pages)

            query = query.order_by(
                combined.c.created_at.desc(),
                combined.c.source,
                combined.c.id.desc(),
            )

            rows = db.session.execute(
                query.limit(30).offset((page - 1) * 30)
            ).mappings().all()

        return (
            render_template(
                "admin_orders.html",
                orders=rows,
                count=count,
                page=page,
                pages=pages,
                source=source,
                state=state,
                scope=scope,
                sources=SOURCES,
                states=STATES,
            ),
            200,
            {"Cache-Control": "no-store"},
        )

    @app.route("/admin/orders/<source>/<int:order_id>")
    @admin_required
    def admin_order_detail(source, order_id):
        order = get_order(source, order_id)
        complete = is_complete(source, order)

        if source == "test":
            items = [
                dict(
                    name=item.product_name,
                    size=None,
                    quantity=item.quantity,
                    price_cents=item.unit_price_cents,
                )
                for item in order.items
            ]
            error = False

        else:
            items, error = json_items(order.items_json)

        handling = db.session.execute(
            db.select(OrderHandling).where(
                OrderHandling.source == source,
                OrderHandling.order_id == order.id,
            )
        ).scalar_one_or_none()

        state = handling.state if handling else "new"

        return (
            render_template(
                "admin_order_detail.html",
                order=order,
                items=items,
                snapshot_error=error,
                source=source,
                source_label=SOURCES[source][0],
                complete=complete,
                state=state,
                states=STATES,
                handling=handling,
            ),
            200,
            {"Cache-Control": "no-store"},
        )

    @app.route(
        "/admin/orders/<source>/<int:order_id>/status",
        methods=["POST"],
    )
    @admin_required
    def admin_order_status(source, order_id):
        check_csrf()

        order = get_order(source, order_id)
        state = request.form.get("state", "")

        if (
            state not in STATES
            or not is_complete(source, order)
        ):
            abort(400)

        now = datetime.now(timezone.utc)

        statement = (
            insert(OrderHandling)
            .values(
                source=source,
                order_id=order.id,
                state=state,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=["source", "order_id"],
                set_={
                    "state": state,
                    "updated_at": now,
                },
            )
        )

        try:
            db.session.execute(statement)
            db.session.commit()

        except SQLAlchemyError:
            db.session.rollback()
            app.logger.exception(
                "Bearbeitungsstatus konnte nicht "
                "gespeichert werden."
            )
            flash(
                "Der Status konnte nicht gespeichert werden. "
                "Bitte erneut versuchen.",
                "order_admin",
            )

        else:
            flash(
                "Der Bearbeitungsstatus wurde gespeichert.",
                "order_admin",
            )

        return redirect(
            url_for(
                "admin_order_detail",
                source=source,
                order_id=order.id,
            )
        )