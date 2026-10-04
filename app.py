import hmac
import os
import secrets
import threading
import time
from datetime import date, datetime, timedelta
from urllib.parse import quote_plus, urlencode

import requests
from flask import (Flask, abort, flash, redirect, render_template, request,
                   send_from_directory, session, url_for)
from flask_sqlalchemy import SQLAlchemy
from PIL import Image, ImageOps
from sqlalchemy import func, inspect, text

app = Flask(__name__)
app.secret_key = os.environ["SECRET_KEY"]
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ["DATABASE_URL"]
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.permanent_session_lifetime = timedelta(days=90)
db = SQLAlchemy(app)

APP_PASSWORD = os.environ["APP_PASSWORD"]
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
STRAVA_ID = os.environ.get("STRAVA_CLIENT_ID", "")
STRAVA_SECRET = os.environ.get("STRAVA_CLIENT_SECRET", "")
STRAVA_API = "https://www.strava.com/api/v3"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
SYNC_INTERVAL = 3600  # Sekunden zwischen automatischen Abgleichen
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "/data/uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.config["MAX_CONTENT_LENGTH"] = 30 * 1024 * 1024  # Handyfotos


# Teile des Antriebs; alle liegen in der Tabelle "chain"
KINDS = {"chain": "Kette", "cassette": "Kassette", "chainring": "Kettenblatt"}
KINDS_PLURAL = {"chain": "Ketten", "cassette": "Kassetten", "chainring": "Kettenblätter"}
app.jinja_env.globals.update(KINDS=KINDS, KINDS_PLURAL=KINDS_PLURAL)


# ---------------------------------------------------------------- Modelle

class Bike(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    strava_id = db.Column(db.String(32), unique=True)  # None = manuell angelegt
    name = db.Column(db.String(120), nullable=False)
    photo = db.Column(db.String(80))
    retired = db.Column(db.Boolean, default=False)
    # Antrieb
    dt_front = db.Column(db.String(4))       # "1x", "2x", "3x"
    dt_speeds = db.Column(db.String(4))      # Ritzel hinten, z. B. "12"
    dt_rings = db.Column(db.String(80))      # Kettenblatt/-blaetter, z. B. "50/34"
    dt_cassette = db.Column(db.String(120))  # z. B. "11-34 Ultegra"
    dt_group = db.Column(db.String(120))     # Schaltgruppe
    dt_note = db.Column(db.String(300))

    def mounted(self, kind):
        """Aktuell montiertes Teil dieser Art (oder None)."""
        for m in Mount.query.filter_by(bike_id=self.id, end_day=None):
            if m.chain.k == kind:
                return m.chain
        return None

    def drivetrain(self):
        """Kurzfassung fuer die Uebersicht, z. B. '2x12 · 50/34 · 11-34'."""
        if self.dt_front and self.dt_speeds:
            head = f"{self.dt_front}{self.dt_speeds}"
        elif self.dt_speeds:
            head = f"{self.dt_speeds}-fach"
        else:
            head = self.dt_front
        ring, cas = self.mounted("chainring"), self.mounted("cassette")
        return " · ".join(p for p in (head, ring and ring.name, cas and cas.name) if p)


class Chain(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    model = db.Column(db.String(120), default="")
    initial_km = db.Column(db.Float, default=0)     # km vor Beginn der Aufzeichnung
    wear_limit = db.Column(db.Float, default=0.75)  # Wechselgrenze in %
    retired = db.Column(db.Boolean, default=False)
    photo = db.Column(db.String(80))
    kind = db.Column(db.String(12), default="chain")  # Schluessel aus KINDS
    swap_km = db.Column(db.Float, default=1000)       # Kette: Wechsel nach so vielen km seit Montage
    shop_url = db.Column(db.String(500))              # Link zum Artikel im Shop
    bought = db.Column(db.Date)                       # Kaufdatum
    speeds = db.Column(db.String(4))                  # passt zu so vielen Gaengen hinten, z. B. "12"
    note = db.Column(db.String(300))

    def is_new(self):
        """Noch nie montiert und ohne Vorkilometer."""
        return not self.mounts and not self.initial_km

    def fits(self, bike):
        """True/False, wenn beide Seiten die Gangzahl kennen, sonst None (unbekannt)."""
        if not (self.speeds and bike.dt_speeds):
            return None
        return self.speeds == bike.dt_speeds

    def shop_search(self):
        """Suchlink fuer Teile ohne hinterlegten Artikel."""
        return "https://www.bike24.de/suche?searchTerm=" + quote_plus(self.model or self.name)
    mounts = db.relationship("Mount", backref="chain", order_by="(Mount.start_day, Mount.id)")
    checks = db.relationship("WearCheck", backref="chain", order_by="WearCheck.day")

    def km(self):
        return (self.initial_km or 0) + sum(m.km() for m in self.mounts)

    def last_wear(self):
        return self.checks[-1] if self.checks else None

    @property
    def k(self):
        return self.kind if self.kind in KINDS else "chain"

    def since_mount(self):
        """km seit der aktuellen Montage; None, wenn nicht montiert."""
        m = self.current_mount()
        return m.km() if m else None

    def status(self):
        """Nur Ketten haben eine Ampel; andere Teile zeigen nur ihre Kilometer."""
        if self.k != "chain":
            return ""
        w = self.last_wear()
        if w and w.percent >= self.wear_limit:
            return "verschlissen"
        done, limit = self.since_mount(), self.swap_km or 0
        if limit and done is not None:
            if done >= limit:
                return "wechseln"
            if done >= 0.8 * limit:
                return "bald"
        if w and w.percent >= self.wear_limit - 0.25:
            return "bald"
        return "ok"

    def home_bike(self):
        """Rad, zu dem die Kette gehoert: das, an dem sie zuletzt montiert war."""
        return self.mounts[-1].bike if self.mounts else None

    def current_mount(self):
        return next((m for m in self.mounts if m.end_day is None), None)


class Mount(db.Model):
    """Zeitraum, in dem eine Kette an einem Rad montiert war."""
    id = db.Column(db.Integer, primary_key=True)
    chain_id = db.Column(db.Integer, db.ForeignKey("chain.id"), nullable=False)
    bike_id = db.Column(db.Integer, db.ForeignKey("bike.id"), nullable=False)
    start_day = db.Column(db.Date, nullable=False)
    end_day = db.Column(db.Date)  # None = aktuell montiert
    notified = db.Column(db.Integer, default=0)  # 0 nichts, 1 Vorwarnung, 2 Wechsel gemeldet
    bike = db.relationship("Bike")

    def km(self):
        q = db.session.query(func.coalesce(func.sum(Ride.km), 0)).filter(
            Ride.bike_id == self.bike_id, Ride.day >= self.start_day)
        if self.end_day:
            q = q.filter(Ride.day < self.end_day)
        return float(q.scalar())


class Ride(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    bike_id = db.Column(db.Integer, db.ForeignKey("bike.id"), nullable=False)
    day = db.Column(db.Date, nullable=False)
    km = db.Column(db.Float, nullable=False)
    note = db.Column(db.String(200), default="")
    strava_id = db.Column(db.BigInteger, unique=True)  # None = manuell
    bike = db.relationship("Bike")


class WearCheck(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    chain_id = db.Column(db.Integer, db.ForeignKey("chain.id"), nullable=False)
    day = db.Column(db.Date, nullable=False)
    percent = db.Column(db.Float, nullable=False)
    km_at = db.Column(db.Float, default=0)


class Setting(db.Model):
    key = db.Column(db.String(40), primary_key=True)
    value = db.Column(db.String(500), default="")


class Token(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    access = db.Column(db.String(200))
    refresh = db.Column(db.String(200))
    expires_at = db.Column(db.Integer, default=0)
    last_sync = db.Column(db.Integer, default=0)


with app.app_context():
    db.create_all()
    # create_all legt nur neue Tabellen an; neue Spalten hier nachziehen
    for table, col, ddl in (("bike", "photo", "VARCHAR(80)"), ("chain", "photo", "VARCHAR(80)"),
                            ("bike", "retired", "BOOLEAN DEFAULT FALSE"),
                            ("bike", "dt_front", "VARCHAR(4)"), ("bike", "dt_speeds", "VARCHAR(4)"),
                            ("bike", "dt_rings", "VARCHAR(80)"), ("bike", "dt_cassette", "VARCHAR(120)"),
                            ("bike", "dt_group", "VARCHAR(120)"), ("bike", "dt_note", "VARCHAR(300)"),
                            ("chain", "kind", "VARCHAR(12) DEFAULT 'chain'"),
                            ("chain", "swap_km", "FLOAT DEFAULT 1000"),
                            ("chain", "shop_url", "VARCHAR(500)"),
                            ("mount", "notified", "INTEGER DEFAULT 0"),
                            ("chain", "bought", "DATE"), ("chain", "speeds", "VARCHAR(4)"),
                            ("chain", "note", "VARCHAR(300)")):
        if col not in [c["name"] for c in inspect(db.engine).get_columns(table)]:
            db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
            # sofort abschliessen: eine offene Aenderung sperrt die Tabelle und
            # wuerde die naechste Spaltenpruefung endlos warten lassen
            db.session.commit()
    db.session.remove()
    db.engine.dispose()  # keine offene Verbindung in spaeter gestartete Prozesse mitnehmen


# ---------------------------------------------------------------- Helfer

def form_day(field="day"):
    try:
        return date.fromisoformat(request.form.get(field, ""))
    except ValueError:
        return date.today()


def form_date_opt(field):
    try:
        return date.fromisoformat(request.form.get(field, ""))
    except ValueError:
        return None


def form_speeds():
    v = request.form.get("speeds", "").strip()
    return v if v.isdigit() and len(v) <= 2 else ""


def form_url(field="shop_url"):
    """Nur echte Web-Adressen annehmen."""
    url = request.form.get(field, "").strip()[:500]
    return url if url.startswith(("https://", "http://")) else ""


def form_float(field, default=0.0):
    try:
        return float(request.form.get(field, "").replace(",", "."))
    except ValueError:
        return default


def bike_km(bike_id):
    return float(db.session.query(func.coalesce(func.sum(Ride.km), 0))
                 .filter(Ride.bike_id == bike_id).scalar())


def remove_photo(obj):
    if obj.photo:
        try:
            os.remove(os.path.join(UPLOAD_DIR, obj.photo))
        except OSError:
            pass
        obj.photo = None


def save_photo(obj):
    f = request.files.get("photo")
    if not f or not f.filename:
        return
    try:
        img = ImageOps.exif_transpose(Image.open(f.stream)).convert("RGB")
        img.thumbnail((1600, 1600))  # Handyfotos verkleinern
    except Exception:
        flash("Das Bild konnte nicht gelesen werden")
        return
    name = f"{secrets.token_hex(12)}.jpg"
    img.save(os.path.join(UPLOAD_DIR, name), "JPEG", quality=85)
    remove_photo(obj)
    obj.photo = name


def get_setting(key):
    row = db.session.get(Setting, key)
    return row.value if row else ""


def set_setting(key, value):
    row = db.session.get(Setting, key) or Setting(key=key)
    row.value = value
    db.session.add(row)
    db.session.commit()


WARN_KM = 100  # Vorwarnung so viele km vor dem Wechsel


def notify(title, message, click=""):
    """Nachricht ueber ntfy aufs Handy schicken. Gibt False zurueck, wenn nichts eingerichtet ist."""
    url = get_setting("ntfy_url").rstrip("/")
    if not url:
        return False
    server, topic = url.rsplit("/", 1)
    r = requests.post(server, timeout=15, json={
        "topic": topic, "title": title, "message": message,
        "click": click or BASE_URL, "tags": ["bike"]})
    r.raise_for_status()
    return True


def check_chains():
    """Fuer jede montierte Kette hoechstens einmal vorwarnen und einmal den Wechsel melden."""
    if not get_setting("ntfy_url"):
        return
    for c in Chain.query.all():
        m = c.current_mount()
        if c.retired or c.k != "chain" or not m or not c.swap_km:
            continue
        done, old = m.km(), m.notified or 0
        level = 2 if done >= c.swap_km else 1 if done >= c.swap_km - WARN_KM else 0
        if level < old:  # Intervall erhoeht oder Fahrten geloescht: wieder scharf stellen
            m.notified = level
            db.session.commit()
        if level <= old:
            continue
        # Stufe zuerst in der Datenbank setzen, damit nie doppelt gemeldet wird
        claimed = Mount.query.filter(Mount.id == m.id, func.coalesce(Mount.notified, 0) < level).update(
            {"notified": level}, synchronize_session=False)
        db.session.commit()
        if not claimed:
            continue
        where = f"{c.name} am {m.bike.name}"
        try:
            if level == 2:
                notify("Kette wechseln", f"{where}: {done:.0f} von {c.swap_km:.0f} km erreicht.",
                       f"{BASE_URL}/chain/{c.id}")
            else:
                notify("Kette bald wechseln", f"{where}: {done:.0f} von {c.swap_km:.0f} km, "
                       f"noch {c.swap_km - done:.0f} km.", f"{BASE_URL}/chain/{c.id}")
        except Exception:
            Mount.query.filter_by(id=m.id).update({"notified": old}, synchronize_session=False)
            db.session.commit()  # beim naechsten Lauf erneut versuchen
            raise


@app.context_processor
def inject():
    return {"today": date.today().isoformat(),
            "strava_connected": db.session.get(Token, 1) is not None,
            "strava_configured": bool(STRAVA_ID)}


# ---------------------------------------------------------------- Login

@app.before_request
def require_login():
    if request.endpoint in ("login", "static"):
        return
    if not session.get("ok"):
        return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if hmac.compare_digest(request.form.get("password", "").encode(), APP_PASSWORD.encode()):
            session.permanent = True
            session["ok"] = True
            return redirect(url_for("index"))
        time.sleep(1)
        flash("Falsches Passwort")
    return render_template("login.html")


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------- Strava

def strava_access_token():
    t = db.session.get(Token, 1)
    if not t:
        return None
    if t.expires_at < time.time() + 60:
        r = requests.post(STRAVA_TOKEN_URL, timeout=20, data={
            "client_id": STRAVA_ID, "client_secret": STRAVA_SECRET,
            "grant_type": "refresh_token", "refresh_token": t.refresh})
        r.raise_for_status()
        d = r.json()
        t.access, t.refresh, t.expires_at = d["access_token"], d["refresh_token"], d["expires_at"]
        db.session.commit()
    return t.access


def strava_sync(full=False):
    """Raeder und Fahrten von Strava holen. Gibt die Zahl neuer Fahrten zurueck."""
    token = strava_access_token()
    if not token:
        return 0
    headers = {"Authorization": f"Bearer {token}"}

    r = requests.get(f"{STRAVA_API}/athlete", headers=headers, timeout=20)
    r.raise_for_status()
    for b in r.json().get("bikes", []):
        bike = Bike.query.filter_by(strava_id=b["id"]).first()
        if not bike:
            bike = Bike(strava_id=b["id"], name=b["name"])
            db.session.add(bike)
        bike.name = b["name"]
    db.session.commit()
    bikes = {b.strava_id: b.id for b in Bike.query.filter(Bike.strava_id.isnot(None))}

    params = {"per_page": 200}
    last = db.session.query(func.max(Ride.day)).filter(Ride.strava_id.isnot(None)).scalar()
    if last and not full:
        # zwei Wochen Ueberlappung, damit nachtraegliche Aenderungen ankommen
        start = datetime.combine(last - timedelta(days=14), datetime.min.time())
        params["after"] = int(start.timestamp())

    new, page = 0, 1
    while True:
        r = requests.get(f"{STRAVA_API}/athlete/activities", headers=headers,
                         params={**params, "page": page}, timeout=30)
        r.raise_for_status()
        acts = r.json()
        if not acts:
            break
        for a in acts:
            bike_id = bikes.get(a.get("gear_id"))
            if not bike_id:
                continue  # Lauf, Aktivitaet ohne Rad usw.
            ride = Ride.query.filter_by(strava_id=a["id"]).first()
            if not ride:
                ride = Ride(strava_id=a["id"])
                db.session.add(ride)
                new += 1
            ride.bike_id = bike_id
            ride.day = date.fromisoformat(a["start_date_local"][:10])
            ride.km = a["distance"] / 1000
            ride.note = (a.get("name") or "")[:200]
        page += 1

    db.session.get(Token, 1).last_sync = int(time.time())
    db.session.commit()
    return new


def auto_sync():
    t = db.session.get(Token, 1)
    if t and time.time() - (t.last_sync or 0) > SYNC_INTERVAL:
        try:
            strava_sync()
        except Exception as e:  # Strava nicht erreichbar -> App bleibt benutzbar
            db.session.rollback()
            flash(f"Strava-Abgleich fehlgeschlagen: {e}")


@app.get("/strava/login")
def strava_login():
    session["oauth_state"] = secrets.token_urlsafe(16)
    return redirect("https://www.strava.com/oauth/authorize?" + urlencode({
        "client_id": STRAVA_ID, "response_type": "code",
        "redirect_uri": f"{BASE_URL}/strava/callback",
        "approval_prompt": "auto",
        "scope": "read,profile:read_all,activity:read_all",
        "state": session["oauth_state"]}))


@app.get("/strava/callback")
def strava_callback():
    if request.args.get("state") != session.pop("oauth_state", None):
        abort(400)
    if "code" not in request.args:
        flash("Strava-Anmeldung abgebrochen")
        return redirect(url_for("settings"))
    r = requests.post(STRAVA_TOKEN_URL, timeout=20, data={
        "client_id": STRAVA_ID, "client_secret": STRAVA_SECRET,
        "code": request.args["code"], "grant_type": "authorization_code"})
    r.raise_for_status()
    d = r.json()
    t = db.session.get(Token, 1) or Token(id=1)
    t.access, t.refresh, t.expires_at = d["access_token"], d["refresh_token"], d["expires_at"]
    db.session.add(t)
    db.session.commit()
    n = strava_sync(full=True)
    flash(f"Strava verbunden, {n} Fahrten importiert")
    return redirect(url_for("settings"))


@app.post("/strava/sync")
def strava_sync_now():
    try:
        n = strava_sync(full=request.form.get("full") == "1")
        flash(f"{n} neue Fahrten importiert")
    except Exception as e:
        db.session.rollback()
        flash(f"Strava-Abgleich fehlgeschlagen: {e}")
    return redirect(url_for("settings"))


# ---------------------------------------------------------------- Seiten

@app.get("/")
def index():
    auto_sync()
    all_chains = Chain.query.order_by(Chain.name).all()
    chains = [c for c in all_chains if not c.retired]
    bikes, retired_bikes = [], []
    for b in Bike.query.order_by(Bike.name):
        if b.retired:
            retired_bikes.append(b)
            continue
        own = [c for c in chains if c.home_bike() and c.home_bike().id == b.id]
        own.sort(key=lambda c: (list(KINDS).index(c.k), c.current_mount() is None))
        bikes.append({"bike": b, "km": bike_km(b.id), "chains": own})
    # Ketten, die noch nie montiert waren oder deren Rad im Ruhestand ist
    loose = [c for c in chains if not c.home_bike() or c.home_bike().retired]
    manual = Ride.query.filter_by(strava_id=None).order_by(Ride.day.desc(), Ride.id.desc()).limit(10).all()
    return render_template("index.html", bikes=bikes, loose=loose, manual=manual,
                           all_bikes=[b["bike"] for b in bikes], retired_bikes=retired_bikes,
                           ntfy_url=get_setting("ntfy_url"),
                           retired_chains=[c for c in all_chains if c.retired])


@app.get("/bike/<int:bike_id>")
def bike_detail(bike_id):
    bike = db.get_or_404(Bike, bike_id)
    chains = [c for c in Chain.query.order_by(Chain.name)
              if c.home_bike() and c.home_bike().id == bike.id]
    chains.sort(key=lambda c: (bool(c.retired), c.current_mount() is None))
    chains = {k: [c for c in chains if c.k == k] for k in KINDS}
    # Zusammenfuehren: nur fuer manuelle Raeder, Ziel ist ein von Strava angelegtes Rad
    strava_bikes = [] if bike.strava_id else Bike.query.filter(Bike.strava_id.isnot(None)).order_by(Bike.name).all()
    manual = (Ride.query.filter_by(bike_id=bike.id, strava_id=None)
              .order_by(Ride.day.desc(), Ride.id.desc()).limit(100).all())
    return render_template("bike.html", bike=bike, km=bike_km(bike.id), chains=chains,
                           strava_bikes=strava_bikes, manual=manual)


@app.get("/photo/<name>")
def photo(name):
    return send_from_directory(UPLOAD_DIR, name, max_age=86400)


@app.post("/<kind>/<int:obj_id>/photo")
def photo_set(kind, obj_id):
    model = {"bike": Bike, "chain": Chain}.get(kind) or abort(404)
    obj = db.get_or_404(model, obj_id)
    if request.form.get("delete") == "1":
        remove_photo(obj)
    else:
        save_photo(obj)
    db.session.commit()
    return redirect(url_for(f"{kind}_detail", **{f"{kind}_id": obj_id}))


@app.get("/chain/<int:chain_id>")
def chain_detail(chain_id):
    chain = db.get_or_404(Chain, chain_id)
    bikes = [b for b in Bike.query.order_by(Bike.name) if not b.retired]
    return render_template("chain.html", chain=chain, bikes=bikes)


@app.post("/bike")
def bike_add():
    name = request.form.get("name", "").strip()
    if name:
        db.session.add(Bike(name=name))
        db.session.commit()
    return redirect(url_for("index"))


@app.post("/chain")
def chain_add():
    name = request.form.get("name", "").strip()
    if not name:
        return redirect(url_for("index"))
    kind = request.form.get("kind", "chain")
    chain = Chain(name=name, model=request.form.get("model", "").strip(),
                  kind=kind if kind in KINDS else "chain",
                  bought=form_date_opt("bought"), speeds=form_speeds(), shop_url=form_url(),
                  note=request.form.get("note", "").strip()[:300],
                  initial_km=form_float("initial_km"),
                  swap_km=form_float("swap_km", 1000),
                  wear_limit=form_float("wear_limit", 0.75) or 0.75)
    db.session.add(chain)
    db.session.commit()
    # direkt von der Rad-Seite angelegt: gleich an diesem Rad montieren
    bike = db.session.get(Bike, int(request.form.get("bike_id") or 0))
    if bike:
        mount_part(chain, bike, form_day())
        db.session.commit()
        return redirect(url_for("bike_detail", bike_id=bike.id))
    if request.form.get("back") == "stock":
        flash(f"{KINDS[chain.k]} {chain.name} ins Lager gelegt")
        return redirect(url_for("stock"))
    return redirect(url_for("chain_detail", chain_id=chain.id))


def mount_part(chain, bike, day):
    """Teil montieren; loest am Rad nur das Teil derselben Art ab."""
    for m in Mount.query.filter(Mount.end_day.is_(None),
                                (Mount.chain_id == chain.id) | (Mount.bike_id == bike.id)):
        if m.chain_id == chain.id or m.chain.k == chain.k:
            m.end_day = max(day, m.start_day)
    db.session.add(Mount(chain_id=chain.id, bike_id=bike.id, start_day=day))
    chain.retired = False


@app.post("/chain/<int:chain_id>/mount")
def chain_mount(chain_id):
    """Kette an ein Rad montieren; beendet offene Zeitraeume von Kette und Rad."""
    chain = db.get_or_404(Chain, chain_id)
    bike = db.get_or_404(Bike, int(request.form["bike_id"]))
    mount_part(chain, bike, form_day())
    db.session.commit()
    if request.form.get("back") == "bike":
        return redirect(url_for("bike_detail", bike_id=bike.id))
    return redirect(url_for("chain_detail", chain_id=chain.id))


@app.post("/chain/<int:chain_id>/unmount")
def chain_unmount(chain_id):
    chain = db.get_or_404(Chain, chain_id)
    day = form_day()
    for m in chain.mounts:
        if m.end_day is None:
            m.end_day = max(day, m.start_day)
    if request.form.get("retire") == "1":
        chain.retired = True
    db.session.commit()
    return redirect(url_for("chain_detail", chain_id=chain.id))


@app.post("/chain/<int:chain_id>/edit")
def chain_edit(chain_id):
    chain = db.get_or_404(Chain, chain_id)
    name = request.form.get("name", "").strip()[:120]
    if name:
        chain.name = name
    chain.model = request.form.get("model", "").strip()[:120]
    chain.initial_km = form_float("initial_km", chain.initial_km or 0)
    chain.shop_url = form_url()
    chain.bought, chain.speeds = form_date_opt("bought"), form_speeds()
    chain.note = request.form.get("note", "").strip()[:300]
    if chain.k == "chain":
        chain.swap_km = form_float("swap_km", chain.swap_km or 0)
        chain.wear_limit = form_float("wear_limit", chain.wear_limit) or 0.75
    db.session.commit()
    flash("Gespeichert")
    return redirect(url_for("chain_detail", chain_id=chain.id))


@app.get("/settings")
def settings():
    t = db.session.get(Token, 1)
    last = datetime.fromtimestamp(t.last_sync).strftime("%d.%m.%Y %H:%M") if t and t.last_sync else ""
    return render_template("settings.html", ntfy_url=get_setting("ntfy_url"), last_sync=last)


@app.post("/settings/ntfy")
def settings_ntfy():
    url = form_url("ntfy_url")
    if url.count("/") < 3 or not url.rstrip("/").rsplit("/", 1)[-1]:
        url = ""  # es fehlt der Kanalname hinter der Server-Adresse
    set_setting("ntfy_url", url)
    if not url:
        flash("Benachrichtigungen ausgeschaltet")
    else:
        try:
            notify("Ketten-App", "Die Benachrichtigungen funktionieren.")
            flash("Gespeichert, Testnachricht gesendet")
            check_chains()
        except Exception as e:
            db.session.rollback()
            flash(f"Gespeichert, aber die Testnachricht ging nicht raus: {e}")
    return redirect(url_for("settings"))


@app.get("/lager")
def stock():
    """Lager: alle Teile, die gerade an keinem Rad montiert sind."""
    bikes = [b for b in Bike.query.order_by(Bike.name) if not b.retired]
    sel = db.session.get(Bike, request.args.get("bike", type=int) or 0)
    parts = [c for c in Chain.query.order_by(Chain.name) if not c.retired and not c.current_mount()]
    if sel:
        parts = [c for c in parts if c.fits(sel) is not False]
    # neue Teile zuerst, darunter das zuletzt gekaufte oben
    parts.sort(key=lambda c: (not c.is_new(), -(c.bought.toordinal() if c.bought else 0)))
    groups = {k: [c for c in parts if c.k == k] for k in KINDS}
    fit = {c.id: [b for b in bikes if c.fits(b)] for c in parts}
    return render_template("stock.html", groups=groups, bikes=bikes, sel=sel, fit=fit, total=len(parts))


@app.get("/shop")
def shop():
    """Ersatzteile: alle aktiven Teile mit Shop-Link, faellige zuerst."""
    order = {"verschlissen": 0, "wechseln": 1, "bald": 2}
    parts = [c for c in Chain.query.order_by(Chain.name) if not c.retired]
    parts.sort(key=lambda c: (order.get(c.status(), 3), list(KINDS).index(c.k), c.name.lower()))
    return render_template("shop.html", parts=parts)


@app.post("/chain/<int:chain_id>/shop")
def chain_shop(chain_id):
    chain = db.get_or_404(Chain, chain_id)
    chain.shop_url = form_url()
    db.session.commit()
    return redirect(url_for("shop"))


@app.post("/chain/<int:chain_id>/retire")
def chain_retire(chain_id):
    """Kette in den Ruhestand schicken oder zurueckholen."""
    chain = db.get_or_404(Chain, chain_id)
    chain.retired = not chain.retired
    if chain.retired:
        for m in chain.mounts:
            if m.end_day is None:
                m.end_day = max(date.today(), m.start_day)
    db.session.commit()
    return redirect(url_for("chain_detail", chain_id=chain.id))


@app.post("/chain/<int:chain_id>/delete")
def chain_delete(chain_id):
    chain = db.get_or_404(Chain, chain_id)
    for obj in list(chain.mounts) + list(chain.checks):
        db.session.delete(obj)
    remove_photo(chain)
    db.session.delete(chain)
    db.session.commit()
    flash("Teil gelöscht")
    return redirect(url_for("index"))


@app.post("/bike/<int:bike_id>/drivetrain")
def bike_drivetrain(bike_id):
    bike = db.get_or_404(Bike, bike_id)
    f = lambda k, n: request.form.get(k, "").strip()[:n]
    front, speeds = f("dt_front", 4), f("dt_speeds", 4)
    bike.dt_front = front if front in ("1x", "2x", "3x") else ""
    bike.dt_speeds = speeds if speeds.isdigit() else ""
    bike.dt_rings, bike.dt_cassette = f("dt_rings", 80), f("dt_cassette", 120)
    bike.dt_group, bike.dt_note = f("dt_group", 120), f("dt_note", 300)
    if not bike.strava_id and f("name", 120):  # Strava-Namen kaemen beim Abgleich zurueck
        bike.name = f("name", 120)
    db.session.commit()
    flash("Antrieb gespeichert")
    return redirect(url_for("bike_detail", bike_id=bike.id))


@app.post("/bike/<int:bike_id>/merge")
def bike_merge(bike_id):
    """Manuelles Rad mit seinem Strava-Rad zusammenfuehren.

    Das manuelle Rad bleibt bestehen (mit Ketten, Teilen, Foto, Antrieb) und
    uebernimmt die Strava-Kennung und alle Strava-Fahrten.
    """
    bike = db.get_or_404(Bike, bike_id)
    src = db.get_or_404(Bike, int(request.form["strava_bike_id"]))
    if bike.strava_id or not src.strava_id or src.id == bike.id:
        abort(400)
    if request.form.get("drop_manual") == "1":
        Ride.query.filter_by(bike_id=bike.id, strava_id=None).delete(synchronize_session=False)
    Ride.query.filter_by(bike_id=src.id).update({"bike_id": bike.id}, synchronize_session=False)
    Mount.query.filter_by(bike_id=src.id).update({"bike_id": bike.id}, synchronize_session=False)
    sid, name = src.strava_id, src.name
    remove_photo(src)
    db.session.delete(src)
    db.session.flush()  # Strava-Kennung ist eindeutig: erst freigeben, dann vergeben
    bike.strava_id, bike.name = sid, name
    db.session.commit()
    flash(f"Mit Strava-Rad {name} zusammengeführt")
    return redirect(url_for("bike_detail", bike_id=bike.id))


@app.post("/bike/<int:bike_id>/retire")
def bike_retire(bike_id):
    """Rad in den Ruhestand schicken oder zurueckholen."""
    bike = db.get_or_404(Bike, bike_id)
    bike.retired = not bike.retired
    if bike.retired:
        for m in Mount.query.filter_by(bike_id=bike.id, end_day=None):
            m.end_day = max(date.today(), m.start_day)
    db.session.commit()
    return redirect(url_for("bike_detail", bike_id=bike.id))


@app.post("/bike/<int:bike_id>/delete")
def bike_delete(bike_id):
    bike = db.get_or_404(Bike, bike_id)
    if bike.strava_id:  # kaeme beim naechsten Abgleich zurueck
        flash("Strava-Räder lassen sich nur in den Ruhestand schicken")
        return redirect(url_for("bike_detail", bike_id=bike.id))
    name = bike.name
    Ride.query.filter_by(bike_id=bike.id).delete(synchronize_session=False)
    Mount.query.filter_by(bike_id=bike.id).delete(synchronize_session=False)
    remove_photo(bike)
    db.session.delete(bike)
    db.session.commit()
    flash(f"Rad {name} gelöscht")
    return redirect(url_for("index"))


@app.post("/chain/<int:chain_id>/wear")
def chain_wear(chain_id):
    chain = db.get_or_404(Chain, chain_id)
    db.session.add(WearCheck(chain_id=chain.id, day=form_day(),
                             percent=form_float("percent"), km_at=chain.km()))
    db.session.commit()
    return redirect(url_for("chain_detail", chain_id=chain.id))


@app.post("/ride")
def ride_add():
    bike = db.get_or_404(Bike, int(request.form["bike_id"]))
    km = form_float("km")
    if km > 0:
        db.session.add(Ride(bike_id=bike.id, day=form_day(), km=km,
                            note=request.form.get("note", "").strip()[:200]))
        db.session.commit()
        flash(f"{km:g} km für {bike.name} eingetragen")
    return redirect(url_for("index"))


@app.post("/ride/<int:ride_id>/delete")
def ride_delete(ride_id):
    ride = db.get_or_404(Ride, ride_id)
    bike_id = ride.bike_id
    if ride.strava_id is None:  # Strava-Fahrten kaemen beim Abgleich zurueck
        db.session.delete(ride)
        db.session.commit()
    if request.form.get("back") == "bike":
        return redirect(url_for("bike_detail", bike_id=bike_id))
    return redirect(url_for("index"))


# ---------------------------------------------------------------- Hintergrund

def background():
    """Stuendlich: Strava abgleichen und Ketten pruefen, auch wenn niemand die App oeffnet."""
    time.sleep(60)
    while True:
        with app.app_context():
            try:
                t = db.session.get(Token, 1)
                if t and time.time() - (t.last_sync or 0) > SYNC_INTERVAL - 300:
                    strava_sync()
                check_chains()
            except Exception as e:
                db.session.rollback()
                app.logger.warning("Hintergrundlauf fehlgeschlagen: %s", e)
        time.sleep(3600)


if os.environ.get("BACKGROUND", "1") == "1":
    threading.Thread(target=background, daemon=True).start()
