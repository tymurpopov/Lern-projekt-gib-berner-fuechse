from flask import Flask, render_template

app = Flask(__name__)

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/teams")
def teams():
    return render_template("teams.html")

@app.route("/events")
def events():
    return render_template("events.html")

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
    return render_template("event.html")

if __name__ == "__main__":
    app.run(debug=True)