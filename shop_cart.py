import json
import re
import secrets
import hmac
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

import click
from flask import (
    abort, flash, redirect, render_template,
    request, session, url_for,
)
from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError

from models import db, Product


class ProductVariant(db.Model):
    __tablename__ = "product_variants"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(
        db.Integer,
        db.ForeignKey("products.id"),
        nullable=False,
        index=True,
    )
    size = db.Column(db.String(32), nullable=False, default="")
    stock = db.Column(db.Integer, nullable=False, default=0)

    product = db.relationship(
        "Product",
        back_populates="variants",
    )

    __table_args__ = (
        db.UniqueConstraint(
            "product_id", "size",
            name="uq_product_variant_size",
        ),
        db.CheckConstraint(
            "stock >= 0",
            name="ck_product_variant_stock",
        ),
        db.CheckConstraint(
            "length(size) <= 32",
            name="ck_product_variant_size_length",
        ),
    )


class VariantOrder(db.Model):
    __tablename__ = "variant_orders"

    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(32), unique=True, nullable=False)
    status = db.Column(
        db.String(20), nullable=False, default="pending"
    )
    total_cents = db.Column(
        db.Integer, nullable=False, default=0
    )
    items_json = db.Column(
        db.Text, nullable=False, default="[]"
    )
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    paid_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.CheckConstraint("total_cents >= 0"),
        db.CheckConstraint(
            "status IN ('pending', 'test_paid')"
        ),
    )


def inventory_ready(product):
    variants = product.variants

    return bool(variants) and (
        all(v.size for v in variants)
        or (
            len(variants) == 1
            and variants[0].size == ""
        )
    ) and sum(v.stock for v in variants) == product.quantity


def product_variant_form(product=None):
    if request.method == "POST":
        return dict(
            variant_mode=request.form.get("variant_mode", ""),
            variant_text=request.form.get("variant_text", ""),
        )

    if product and any(v.size for v in product.variants):
        return dict(
            variant_mode="sizes",
            variant_text="\n".join(
                f"{v.size}={v.stock}"
                for v in product.variants
                if v.size
            ),
        )

    if (
        product
        and not product.variants
        and product.sizes.strip()
    ):
        return dict(variant_mode="legacy", variant_text="")

    return dict(variant_mode="none", variant_text="")


def read_variant_form(product=None):
    mode = request.form.get("variant_mode", "")

    if (
        mode == "legacy"
        and product
        and not product.variants
        and product.sizes.strip()
    ):
        return product.quantity, product.sizes, None

    if mode == "none":
        stock = request.form.get("quantity", "").strip()

        if (
            not re.fullmatch(r"[0-9]{1,5}", stock)
            or int(stock) > 10000
        ):
            raise ValueError(
                "Der Bestand muss zwischen 0 und 10000 liegen."
            )

        return int(stock), "", [("", int(stock))]

    if mode != "sizes":
        raise ValueError(
            "Bitte einen gültigen Grössenmodus auswählen."
        )

    raw = request.form.get("variant_text", "")

    if len(raw) > 4000:
        raise ValueError("Die Grössenliste ist zu lang.")

    rows, seen = [], set()

    for line in raw.splitlines():
        if not line.strip():
            continue

        size, separator, stock = line.strip().rpartition("=")
        size, stock = size.strip().upper(), stock.strip()

        if (
            not separator
            or not size
            or len(size) > 32
            or any(c in size for c in ",=\r\n")
        ):
            raise ValueError(
                "Eine Grösse pro Zeile eingeben, zum Beispiel S=8."
            )

        if size in seen:
            raise ValueError(
                f"Die Grösse {size} wurde mehrfach eingegeben."
            )

        if (
            not re.fullmatch(r"[0-9]{1,5}", stock)
            or int(stock) > 10000
        ):
            raise ValueError(
                "Jeder Bestand muss zwischen 0 und 10000 liegen."
            )

        seen.add(size)
        rows.append((size, int(stock)))

    sizes = ", ".join(size for size, stock in rows)
    total = sum(stock for size, stock in rows)

    if (
        not 1 <= len(rows) <= 30
        or len(sizes) > 200
        or total > 10000
    ):
        raise ValueError(
            "Bitte 1–30 Grössen mit zusammen höchstens "
            "10000 Stück eingeben."
        )

    return total, sizes, rows


def save_product_variants(product, rows):
    if rows is None:
        return

    wanted = dict(rows)
    existing = {v.size: v for v in product.variants}

    for variant in list(product.variants):
        if variant.size not in wanted:
            product.variants.remove(variant)

    for size, stock in rows:
        if size in existing:
            existing[size].stock = stock
        else:
            product.variants.append(
                ProductVariant(size=size, stock=stock)
            )


def read_cart():
    raw = session.get("cart", {})

    if not isinstance(raw, dict):
        raw = {}

    cart, removed = {}, False
    legacy = session.get("cart_version") != 2

    for key, quantity in list(raw.items())[:50]:
        if (
            not isinstance(key, str)
            or type(quantity) is not int
            or not 1 <= quantity <= 10000
        ):
            removed = True
            continue

        if (
            not legacy
            and re.fullmatch(r"v:[1-9][0-9]{0,11}", key)
        ):
            cart[key] = quantity

        elif (
            legacy
            and re.fullmatch(r"[1-9][0-9]{0,11}", key)
        ):
            product = db.session.get(Product, int(key))

            if (
                product
                and product.active
                and inventory_ready(product)
                and len(product.variants) == 1
                and product.variants[0].size == ""
            ):
                cart[f"v:{product.variants[0].id}"] = quantity
            else:
                removed = True

        else:
            removed = True

    if raw != cart or legacy:
        session["cart"], session["cart_version"] = cart, 2

    if removed or len(raw) > 50:
        flash(
            "Alte oder ungültige Positionen wurden entfernt. "
            "Bitte die gewünschte Grösse erneut auswählen.",
            "cart_message",
        )

    return cart


def read_quantity(allow_zero=False):
    raw = request.form.get("quantity", "").strip()
    minimum = 0 if allow_zero else 1

    if (
        not re.fullmatch(r"[0-9]{1,5}", raw)
        or not minimum <= int(raw) <= 10000
    ):
        raise ValueError(
            f"Bitte eine ganze Anzahl von {minimum} "
            "bis 10000 eingeben."
        )

    return int(raw)


def store_cart(cart):
    session["cart"], session["cart_version"] = cart, 2
    session.pop("variant_checkout_token", None)


def register_shop_cart(app, check_csrf):
    @app.cli.command("init-variant-shop")
    def init_variant_shop():
        path = Path(db.engine.url.database).resolve()
        folder = (
            Path(app.root_path).parent
            / "Berner-Fuechse-Backups"
        )
        folder.mkdir(parents=True, exist_ok=True)

        backup = folder / (
            f"before_variants_"
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

        for model in (ProductVariant, VariantOrder):
            if inspector.has_table(model.__tablename__):
                columns = {
                    c["name"]
                    for c in inspector.get_columns(
                        model.__tablename__
                    )
                }

                if not set(
                    model.__table__.columns.keys()
                ).issubset(columns):
                    raise click.ClickException(
                        f"Unerwartete Struktur: "
                        f"{model.__tablename__}. "
                        "Keine Tabellen gelöscht."
                    )

        db.metadata.create_all(
            db.engine,
            tables=[
                ProductVariant.__table__,
                VariantOrder.__table__,
            ],
        )

        count = 0

        try:
            products = db.session.execute(
                db.select(Product)
            ).scalars()

            for product in products:
                if (
                    not product.variants
                    and not product.sizes.strip()
                ):
                    product.variants.append(
                        ProductVariant(
                            size="",
                            stock=product.quantity,
                        )
                    )
                    count += 1

                elif not inventory_ready(product):
                    click.echo(
                        f"Produkt {product.id}: "
                        "Bestände im Admin prüfen und speichern."
                    )

            db.session.commit()

        except SQLAlchemyError as exc:
            db.session.rollback()
            raise click.ClickException(
                "Bestände nicht übertragen. "
                "Bitte die Fehlermeldung prüfen."
            ) from exc

        click.echo(
            f"Bereit. {count} Produkte ohne Grössen übertragen. "
            "Alte Bestellungen unverändert."
        )

    @app.context_processor
    def cart_helpers():
        return dict(
            cart_count=sum(read_cart().values()),
            inventory_ready=inventory_ready,
        )

    @app.route("/shop/<int:product_id>")
    def product_detail(product_id):
        product = db.get_or_404(Product, product_id)

        if not product.active:
            abort(404)

        return render_template(
            "product.html",
            product=product,
        )

    @app.route(
        "/cart/add/<int:product_id>",
        methods=["POST"],
    )
    def cart_add(product_id):
        check_csrf()
        product = db.get_or_404(Product, product_id)

        if not product.active:
            abort(404)

        cart = read_cart()

        try:
            if not inventory_ready(product):
                raise ValueError(
                    "Die Bestände dieses Produkts müssen "
                    "zuerst im Admin gespeichert werden."
                )

            raw = request.form.get("variant_id", "").strip()

            if (
                not raw
                and len(product.variants) == 1
                and product.variants[0].size == ""
            ):
                variant = product.variants[0]

            else:
                if not re.fullmatch(
                    r"[1-9][0-9]{0,11}", raw
                ):
                    raise ValueError(
                        "Bitte eine verfügbare Grösse auswählen."
                    )

                variant = db.session.get(
                    ProductVariant, int(raw)
                )

                if (
                    not variant
                    or variant.product_id != product.id
                ):
                    raise ValueError(
                        "Diese Grösse gehört nicht "
                        "zu diesem Produkt."
                    )

            key = f"v:{variant.id}"
            quantity = cart.get(key, 0) + read_quantity()

            if key not in cart and len(cart) >= 50:
                raise ValueError(
                    "Maximal 50 verschiedene Positionen "
                    "sind erlaubt."
                )

            if (
                quantity > variant.stock
                or quantity > 10000
            ):
                raise ValueError(
                    "Für diese Grösse ist diese Anzahl "
                    "nicht verfügbar."
                )

            cart[key] = quantity
            store_cart(cart)

        except ValueError as exc:
            flash(str(exc), "cart_message")
            return redirect(
                url_for(
                    "product_detail",
                    product_id=product.id,
                )
            )

        flash("Zum Warenkorb hinzugefügt.", "cart_message")
        return redirect(url_for("cart_view"))

    @app.route("/cart")
    def cart_view():
        cart = read_cart()
        ids = [int(key[2:]) for key in cart]

        variants = (
            db.session.execute(
                db.select(ProductVariant)
                .join(Product)
                .where(
                    ProductVariant.id.in_(ids),
                    Product.active.is_(True),
                )
                .order_by(Product.name, ProductVariant.id)
            ).scalars().all()
            if ids else []
        )

        items, records, kept = [], [], {}

        for variant in variants:
            product = variant.product
            key = f"v:{variant.id}"

            if not inventory_ready(product):
                continue

            quantity = cart[key]
            kept[key] = quantity

            items.append(dict(
                product=product,
                variant=variant,
                quantity=quantity,
                subtotal_cents=product.price_cents * quantity,
                available=quantity <= variant.stock,
            ))

            records.append(dict(
                key=key,
                variant_id=variant.id,
                product_id=product.id,
                name=product.name,
                size=variant.size,
                quantity=quantity,
                price_cents=product.price_cents,
            ))

        if kept != cart:
            store_cart(kept)
            flash(
                "Gelöschte oder noch nicht freigegebene "
                "Positionen wurden entfernt.",
                "cart_message",
            )

        total = sum(
            r["quantity"] * r["price_cents"]
            for r in records
        )
        token = None

        if records:
            serialized = json.dumps(
                records,
                ensure_ascii=False,
                sort_keys=True,
            )

            order = db.session.execute(
                db.select(VariantOrder).where(
                    VariantOrder.token
                    == session.get(
                        "variant_checkout_token", ""
                    )
                )
            ).scalar_one_or_none()

            if (
                not order
                or order.status != "pending"
                or order.items_json != serialized
            ):
                order = VariantOrder(
                    token=secrets.token_hex(16),
                    total_cents=total,
                    items_json=serialized,
                )
                db.session.add(order)

                try:
                    db.session.commit()
                except SQLAlchemyError:
                    db.session.rollback()
                    app.logger.exception(
                        "Bestellvorschau konnte nicht "
                        "gespeichert werden."
                    )
                    abort(
                        503,
                        description=(
                            "Bestellvorschau nicht verfügbar. "
                            "Bitte erneut versuchen."
                        ),
                    )

                session["variant_checkout_token"] = order.token

            token = order.token

        return render_template(
            "cart.html",
            items=items,
            total_cents=total,
            checkout_token=token,
        )

    @app.route(
        "/cart/variant/<int:variant_id>/update",
        methods=["POST"],
    )
    def cart_update(variant_id):
        check_csrf()
        cart, key = read_cart(), f"v:{variant_id}"

        if key not in cart:
            return redirect(url_for("cart_view"))

        try:
            quantity = read_quantity(True)

            if quantity == 0:
                cart.pop(key)
            else:
                variant = db.get_or_404(
                    ProductVariant, variant_id
                )

                if (
                    not variant.product
                    or not variant.product.active
                ):
                    abort(404)

                if (
                    not inventory_ready(variant.product)
                    or quantity > variant.stock
                ):
                    raise ValueError(
                        "Für diese Grösse reicht "
                        "der Lagerbestand nicht aus."
                    )

                cart[key] = quantity

            store_cart(cart)

        except ValueError as exc:
            flash(str(exc), "cart_message")

        return redirect(url_for("cart_view"))

    @app.route(
        "/cart/variant/<int:variant_id>/remove",
        methods=["POST"],
    )
    def cart_remove(variant_id):
        check_csrf()
        cart = read_cart()
        cart.pop(f"v:{variant_id}", None)
        store_cart(cart)
        return redirect(url_for("cart_view"))

    def finish_order(order):
        receipts = session.get("variant_receipts", [])

        if order.token not in receipts:
            cart = read_cart()

            for record in json.loads(order.items_json):
                if (
                    cart.get(record["key"])
                    == record["quantity"]
                ):
                    cart.pop(record["key"], None)

            store_cart(cart)
            session["variant_receipts"] = (
                receipts + [order.token]
            )[-10:]

        return redirect(
            url_for(
                "variant_order_result",
                token=order.token,
            )
        )

    @app.route("/cart/checkout", methods=["POST"])
    def cart_checkout():
        check_csrf()

        token = request.form.get("checkout_token", "")
        expected = session.get("variant_checkout_token", "")

        if not re.fullmatch(r"[a-f0-9]{32}", token):
            abort(400)

        is_current = (
            isinstance(expected, str)
            and hmac.compare_digest(token, expected)
        )

        if (
            not is_current
            and token not in session.get(
                "variant_receipts", []
            )
        ):
            flash(
                "Die Bestellseite ist abgelaufen. "
                "Bitte den Warenkorb neu laden.",
                "cart_message",
            )
            return redirect(url_for("cart_view"))

        order = db.session.execute(
            db.select(VariantOrder).where(
                VariantOrder.token == token
            )
        ).scalar_one_or_none()

        if not order:
            abort(404)

        if order.status == "test_paid":
            return finish_order(order)

        try:
            if (
                not is_current
                or request.form.get("confirm_demo") != "yes"
            ):
                raise ValueError(
                    "Bitte bestätigen, dass dies "
                    "nur ein Testkauf ist."
                )

            records = json.loads(order.items_json)
            cart = read_cart()

            if {
                r["key"]: r["quantity"]
                for r in records
            } != cart:
                raise ValueError(
                    "Der Warenkorb hat sich geändert. "
                    "Bitte neu laden."
                )

            selected = set(
                request.form.getlist("selected_products")
            )

            if (
                not selected
                or not selected.issubset(cart)
            ):
                raise ValueError(
                    "Bitte mindestens eine gültige "
                    "Position auswählen."
                )

            records = [
                r for r in records
                if r["key"] in selected
            ]

            # Один захват заказа и все списания:
            # общая транзакция, без промежуточного commit.
            claimed = db.session.execute(
                db.update(VariantOrder)
                .where(
                    VariantOrder.id == order.id,
                    VariantOrder.status == "pending",
                )
                .values(status="test_paid")
                .execution_options(
                    synchronize_session=False
                )
            )

            if claimed.rowcount != 1:
                db.session.rollback()
                db.session.expire_all()

                if order.status == "test_paid":
                    return finish_order(order)

                raise ValueError(
                    "Bestellung bitte erneut laden."
                )

            for r in records:
                product = db.session.get(
                    Product, r["product_id"]
                )

                if (
                    not product
                    or not inventory_ready(product)
                ):
                    raise ValueError(
                        "Ein Produkt ist nicht mehr verfügbar."
                    )

                variant_update = db.session.execute(
                    db.update(ProductVariant)
                    .where(
                        ProductVariant.id == r["variant_id"],
                        ProductVariant.product_id
                        == r["product_id"],
                        ProductVariant.size == r["size"],
                        ProductVariant.stock
                        >= r["quantity"],
                    )
                    .values(
                        stock=(
                            ProductVariant.stock
                            - r["quantity"]
                        )
                    )
                    .execution_options(
                        synchronize_session=False
                    )
                )

                product_update = db.session.execute(
                    db.update(Product)
                    .where(
                        Product.id == r["product_id"],
                        Product.active.is_(True),
                        Product.name == r["name"],
                        Product.price_cents
                        == r["price_cents"],
                        Product.quantity
                        >= r["quantity"],
                    )
                    .values(
                        quantity=(
                            Product.quantity
                            - r["quantity"]
                        )
                    )
                    .execution_options(
                        synchronize_session=False
                    )
                )

                if (
                    variant_update.rowcount != 1
                    or product_update.rowcount != 1
                ):
                    raise ValueError(
                        "Bestand, Grösse oder Preis hat "
                        "sich geändert. Bitte den "
                        "Warenkorb neu laden."
                    )

            order.items_json = json.dumps(
                records,
                ensure_ascii=False,
                sort_keys=True,
            )
            order.total_cents = sum(
                r["quantity"] * r["price_cents"]
                for r in records
            )
            order.paid_at = datetime.now(timezone.utc)

            db.session.commit()
            db.session.expire_all()

        except ValueError as exc:
            db.session.rollback()
            session.pop("variant_checkout_token", None)
            flash(str(exc), "cart_message")
            return redirect(url_for("cart_view"))

        except SQLAlchemyError:
            db.session.rollback()
            app.logger.exception(
                "Testbestellung fehlgeschlagen."
            )
            flash(
                "Die Bestellung konnte nicht gespeichert "
                "werden. Es wurde nichts abgebucht.",
                "cart_message",
            )
            return redirect(url_for("cart_view"))

        return finish_order(order)

    @app.route("/cart/order/<token>")
    def variant_order_result(token):
        if token not in session.get("variant_receipts", []):
            abort(404)

        order = db.session.execute(
            db.select(VariantOrder).where(
                VariantOrder.token == token,
                VariantOrder.status == "test_paid",
            )
        ).scalar_one_or_none()

        if not order:
            abort(404)

        return (
            render_template(
                "variant_order_result.html",
                order=order,
                records=json.loads(order.items_json),
            ),
            200,
            {"Cache-Control": "no-store"},
        )

    @app.route("/checkout", methods=["GET", "POST"])
    def legacy_checkout():
        if request.method == "POST":
            check_csrf()
            abort(
                410,
                description=(
                    "Bitte über den neuen Warenkorb bestellen."
                ),
            )

        return redirect(url_for("cart_view"))