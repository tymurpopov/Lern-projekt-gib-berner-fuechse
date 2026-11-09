import hmac
import secrets
from datetime import datetime, timezone

from flask import (
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from sqlalchemy.exc import SQLAlchemyError

from models import db, Order, OrderItem, Product


def order_cart(order):
    return {
        str(item.product_id): item.quantity
        for item in order.items
    }


def register_checkout(app, read_cart, check_csrf):

    @app.cli.command("init-shop-orders")
    def init_shop_orders():
        db.metadata.create_all(
            db.engine,
            tables=[
                Order.__table__,
                OrderItem.__table__,
            ]
        )

        print(
            "Bestelltabellen bereit. "
            "Bestehende Produktdaten unverändert."
        )

    @app.route("/checkout", methods=["GET", "POST"])
    def checkout():

        # POST: пользователь подтвердил учебную оплату.
        if request.method == "POST":
            check_csrf()

            token = request.form.get("order_token", "")
            expected = session.get("checkout_token", "")

            if not expected or not hmac.compare_digest(
                token.encode("utf-8"),
                expected.encode("utf-8")
            ):
                abort(400)

            order = db.session.execute(
                db.select(Order).where(
                    Order.token == token
                )
            ).scalar_one_or_none()

            if order is None:
                abort(404)

            # Этот заказ уже оплачен:
            # показываем результат без нового списания.
            if order.status == "test_paid":
                return redirect(
                    url_for("checkout_result", token=token)
                )

            # После просмотра заказа корзину могли изменить.
            if order_cart(order) != read_cart():
                db.session.rollback()
                session.pop("checkout_token", None)

                flash(
                    "Der Warenkorb wurde geändert. "
                    "Bitte erneut prüfen.",
                    "cart_message"
                )

                return redirect(url_for("cart_view"))

            try:
                # Только один запрос может подтвердить заказ.
                claimed = db.session.execute(
                    db.update(Order)
                    .where(
                        Order.id == order.id,
                        Order.status == "pending"
                    )
                    .values(
                        status="test_paid",
                        paid_at=datetime.now(timezone.utc)
                    )
                    .execution_options(
                        synchronize_session=False
                    )
                )

                if claimed.rowcount != 1:
                    db.session.rollback()

                    return redirect(
                        url_for(
                            "checkout_result",
                            token=token
                        )
                    )

                # Списываем каждую позицию заказа.
                for item in sorted(
                    order.items,
                    key=lambda row: row.product_id
                ):
                    changed = db.session.execute(
                        db.update(Product)
                        .where(
                            Product.id == item.product_id,
                            Product.active.is_(True),
                            Product.quantity >= item.quantity,
                            Product.price_cents
                            == item.unit_price_cents
                        )
                        .values(
                            quantity=(
                                Product.quantity
                                - item.quantity
                            )
                        )
                        .execution_options(
                            synchronize_session=False
                        )
                    )

                    if changed.rowcount != 1:
                        raise ValueError(
                            "Preis oder Lagerbestand hat sich "
                            "geändert. Bitte den Warenkorb prüfen. "
                            "Es wurde nichts gekauft."
                        )

                # Заказ и все списания сохраняются вместе.
                db.session.commit()

            except ValueError as exc:
                # Отменяем ВСЕ списания этого заказа.
                db.session.rollback()
                session.pop("checkout_token", None)

                flash(str(exc), "cart_message")

                return redirect(url_for("cart_view"))

            except SQLAlchemyError:
                db.session.rollback()

                app.logger.exception(
                    "Testzahlung konnte nicht gespeichert werden."
                )

                flash(
                    "Testzahlung fehlgeschlagen. "
                    "Bitte erneut versuchen.",
                    "cart_message"
                )

                return redirect(url_for("cart_view"))

            # Очищаем корзину только после успешного сохранения.
            session["cart"] = {}

            return redirect(
                url_for("checkout_result", token=token)
            )

        # GET: показываем заказ перед оплатой.
        cart = read_cart()

        if not cart:
            flash(
                "Dein Warenkorb ist leer.",
                "cart_message"
            )

            return redirect(url_for("cart_view"))

        products = db.session.execute(
            db.select(Product)
            .where(
                Product.id.in_([
                    int(key)
                    for key in cart
                ]),
                Product.active.is_(True)
            )
            .order_by(Product.id)
        ).scalars().all()

        if len(products) != len(cart) or any(
            cart[str(product.id)] > product.quantity
            for product in products
        ):
            flash(
                "Nicht genügend verfügbare Produkte. "
                "Bitte den Warenkorb prüfen.",
                "cart_message"
            )

            return redirect(url_for("cart_view"))

        # Цены берём из БД, а не из данных браузера.
        order = Order(
            token=secrets.token_hex(32),
            total_cents=sum(
                product.price_cents * cart[str(product.id)]
                for product in products
            )
        )

        order.items = [
            OrderItem(
                product_id=product.id,
                product_name=product.name,
                quantity=cart[str(product.id)],
                unit_price_cents=product.price_cents
            )
            for product in products
        ]

        try:
            db.session.add(order)
            db.session.commit()

        except SQLAlchemyError:
            db.session.rollback()

            app.logger.exception(
                "Bestellvorschau konnte nicht gespeichert werden."
            )

            flash(
                "Bestellvorschau nicht verfügbar. "
                "Bitte erneut versuchen.",
                "cart_message"
            )

            return redirect(url_for("cart_view"))

        session["checkout_token"] = order.token

        return render_template(
            "checkout.html",
            order=order
        )

    @app.route("/checkout/result/<token>")
    def checkout_result(token):
        expected = session.get("checkout_token", "")

        if not expected or not hmac.compare_digest(
            token.encode("utf-8"),
            expected.encode("utf-8")
        ):
            abort(404)

        order = db.session.execute(
            db.select(Order).where(
                Order.token == token,
                Order.status == "test_paid"
            )
        ).scalar_one_or_none()

        if order is None:
            abort(404)

        response = app.make_response(
            render_template(
                "checkout_result.html",
                order=order
            )
        )

        response.headers["Cache-Control"] = "private, no-store"

        return response