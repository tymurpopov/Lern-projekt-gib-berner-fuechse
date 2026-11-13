# Berner Füchse

A fictional esports club website developed as an IT school project at **gibb Berufsfachschule Bern** during the 2026/2027 school year.

The project demonstrates a Flask-based website with team and member information, events, membership applications, and a small shop with an administration area.

> **Note:** Berner Füchse is a fictional club created for educational purposes. This is not the official website of a real esports organisation. The shop is a demonstration feature and does not process real payments.

## Features

### Public website
- Homepage with information about the club
- Team overview and individual team pages
- Public member profiles
- Events overview
- Membership information and club rules
- Application forms for new members and teams

### Shop
- Product catalogue
- Product search and filtering
- Sorting by product name, price, and date
- Product variants and size selection
- Shopping cart
- Stock and availability management
- Demonstration order workflow (no real payment processing)

### Administration
- Protected admin area
- Admin login with hashed passwords
- Manage teams, members, events, and products
- Review membership and team applications
- Manage product variants and stock
- View orders and update their processing status

## Technologies

- **Python**
- **Flask**
- **Flask-SQLAlchemy**
- **SQLite**
- **SQLAlchemy**
- **Pillow**
- **HTML, CSS, JavaScript**
- **Jinja2 templates**

## Project structure

```text
Lern-projekt-gib-berner-fuechse/
├── app.py
├── models.py
├── shop_cart.py
├── admin_orders.py
├── checkout.py
├── requirements.txt
├── README.md
├── static/
│   ├── css/
│   ├── js/
│   └── img/
├── templates/
└── instance/             # Created locally; contains the SQLite database
```

Some folders and files may contain additional subfolders.

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/tymurpopov/Lern-projekt-gib-berner-fuechse.git
cd Lern-projekt-gib-berner-fuechse
```

### 2. Create and activate a virtual environment

**Windows (PowerShell):**

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**Linux / macOS:**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install the dependencies

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Configuration

The application requires a `SECRET_KEY` environment variable. The key is used to protect Flask sessions.

Generate a key and set it for the current terminal session.

**Windows (PowerShell):**

```powershell
$env:SECRET_KEY = py -c "import secrets; print(secrets.token_hex(32))"
```

**Linux / macOS:**

```bash
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
```

Keep this key private. Do not publish it or commit it to the repository. Set a persistent secret through your operating system or hosting provider when deploying the application.

## Database setup

The project uses SQLite. The database file is stored at:

```text
instance/berner_fuechse.db
```

Create the `instance` directory if it does not already exist, then initialise the database.

**Windows (PowerShell):**

```powershell
New-Item -ItemType Directory -Force instance
flask --app app init-db
```

**Linux / macOS:**

```bash
mkdir -p instance
flask --app app init-db
```

The `init-db` command creates the database tables from the application's SQLAlchemy models. It does **not** add example teams, members, events, or products. These can be added through the administration area.

## Create an administrator

After initialising the database, create an admin account:

```bash
flask --app app create-admin
```

The command asks for a username and password. The password must contain at least 12 characters and is stored as a password hash.

## Run the application

Start the local development server:

```bash
flask --app app run
```

Open the address shown in the terminal (usually `http://127.0.0.1:5000`) in your browser.

The Flask development server is intended for local development and testing, not production deployment.

## Important notes

- The SQLite database and uploaded user files are local application data. They are not included in a fresh Git clone.
- A new installation starts with an empty database. Create an administrator and add the required content yourself.
- The shop is part of a school demonstration project. Do not use it for real customer orders or payments.
- Do not use real personal information when testing application forms.
- Before making changes to an existing database, create a backup. Database migration commands are intended for specific existing database versions and should not be run blindly on a fresh installation.

## Author

**Tymur Popov**

GitHub: [@tymurpopov](https://github.com/tymurpopov)
