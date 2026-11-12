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
    image = db.Column(db.String(255), nullable=True)


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

class Product(db.Model):
    __tablename__ = "products"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    name = db.Column(
        db.String(150),
        nullable=False
    )

    description = db.Column(
        db.Text,
        nullable=False,
        default=""
    )

    price_cents = db.Column(
        db.Integer,
        nullable=False
    )

    quantity = db.Column(
        "stock",
        db.Integer,
        nullable=False,
        default=0
    )

    image = db.Column(
        db.String(255),
        nullable=True
    )

    sizes = db.Column(
        db.String(200),
        nullable=False,
        default=""
    )

    variants = db.relationship(
        "ProductVariant",
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="ProductVariant.id",
    )

    active = db.Column(
        db.Boolean,
        nullable=False,
        default=True
    )

    created_at = db.Column(
        db.DateTime,
        nullable=True,
        default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        db.CheckConstraint(
            "price_cents >= 0",
            name="check_product_price_nonnegative"
        ),
        db.CheckConstraint(
            "stock >= 0",
            name="check_product_quantity_nonnegative"
        ),
    )

class Order(db.Model):
    __tablename__ = "test_orders"

    id = db.Column(db.Integer, primary_key=True)

    token = db.Column(
        db.String(64),
        unique=True,
        nullable=False
    )

    status = db.Column(
        db.String(20),
        nullable=False,
        default="pending"
    )

    total_cents = db.Column(
        db.Integer,
        nullable=False
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )

    paid_at = db.Column(
        db.DateTime,
        nullable=True
    )

    items = db.relationship(
        "OrderItem",
        cascade="all, delete-orphan",
        order_by="OrderItem.id",
        lazy="selectin"
    )

    __table_args__ = (
        db.CheckConstraint(
            "total_cents >= 0",
            name="check_order_total"
        ),
        db.CheckConstraint(
            "status IN ('pending', 'test_paid')",
            name="check_order_status"
        ),
    )


class OrderItem(db.Model):
    __tablename__ = "test_order_items"

    id = db.Column(db.Integer, primary_key=True)

    order_id = db.Column(
        db.Integer,
        db.ForeignKey("test_orders.id"),
        nullable=False
    )

    product_id = db.Column(
        db.Integer,
        nullable=False
    )

    product_name = db.Column(
        db.String(150),
        nullable=False
    )

    quantity = db.Column(
        db.Integer,
        nullable=False
    )

    unit_price_cents = db.Column(
        db.Integer,
        nullable=False
    )

    __table_args__ = (
        db.CheckConstraint(
            "quantity > 0",
            name="check_order_item_quantity"
        ),
        db.CheckConstraint(
            "unit_price_cents >= 0",
            name="check_order_item_price"
        ),
        db.UniqueConstraint(
            "order_id",
            "product_id",
            name="unique_order_product"
        ),
    )

class DemoOrder(db.Model):
    __tablename__ = "demo_orders"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    token = db.Column(
        db.String(32),
        unique=True,
        nullable=False
    )

    total_cents = db.Column(
        db.Integer,
        nullable=False,
        default=0
    )

    items_json = db.Column(
        db.Text,
        nullable=False,
        default="[]"
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        db.CheckConstraint(
            "total_cents >= 0",
            name="check_demo_order_total_nonnegative"
        ),
    )