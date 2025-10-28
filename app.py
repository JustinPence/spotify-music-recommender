import os, base64, json, random
from datetime import datetime, timedelta
from urllib.parse import urlencode

import requests
from flask import Flask, redirect, request, session, render_template, url_for, flash
from dotenv import load_dotenv
from models import db, User, Playlist

# ---------- Load environment ----------
load_dotenv()
CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")
SECRET_KEY = os.getenv("SECRET_KEY", "dev")

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"
SCOPES = "playlist-modify-private playlist-modify-public user-read-email user-read-private"

# ---------- Flask setup ----------
app = Flask(__name__)
app.secret_key = SECRET_KEY

basedir = os.path.abspath(os.path.dirname(__file__))
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{os.path.join(basedir, 'app.db')}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)
with app.app_context():
    db.create_all()

# ---------- Helper functions ----------
def _basic_auth_header():
    creds = f"{CLIENT_ID}:{CLIENT_SECRET}".encode()
    return {"Authorization": "Basic " + base64.b64encode(creds).decode()}

def _save_tokens(user, tok_json):
    user.access_token = tok_json["access_token"]
    if tok_json.get("refresh_token"):
        user.refresh_token = tok_json["refresh_token"]
    exp = tok_json.get("expires_in", 3600)
    user.token_expires_at = datetime.utcnow() + timedelta(seconds=exp - 60)
    db.session.commit()

def _ensure_token(user):
    if user.token_expires_at and datetime.utcnow() < user.token_expires_at:
        return user.access_token
    data = {"grant_type": "refresh_token", "refresh_token": user.refresh_token}
    tok = requests.post(TOKEN_URL, data=data, headers=_basic_auth_header())
    tok.raise_for_status()
    _save_tokens(user, tok.json())
    return user.access_token

def _sp_get(token, endpoint, params=None):
    url = API_BASE + endpoint
    r = requests.get(url, headers={"Authorization": "Bearer " + token}, params=params or {})
    if r.status_code >= 400:
        raise RuntimeError(f"GET {r.url} -> {r.status_code}: {r.text}")
    return r.json()

def _sp_post(token, endpoint, payload):
    url = API_BASE + endpoint
    r = requests.post(
        url,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        data=json.dumps(payload),
    )
    if r.status_code >= 400:
        raise RuntimeError(f"POST {r.url} -> {r.status_code}: {r.text}")
    return r.json()

def _pick_seed_artists_from_genres(token, genres, max_artists=2):
    """For each genre, search a popular artist and return up to max_artists IDs."""
    artists = []
    for g in genres:
        try:
            res = _sp_get(token, "/search", params={
                "q": f'genre:\"{g}\"', "type": "artist", "limit": 1, "market": "US"
            })
            items = res.get("artists", {}).get("items", [])
            if items:
                artists.append(items[0]["id"])
            if len(artists) >= max_artists:
                break
        except Exception:
            continue
    return list(dict.fromkeys(artists))

# ---------- Routes ----------
@app.route("/")
def home():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return render_template("home.html")

@app.route("/login")
def login():
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "show_dialog": "true",  # forces account chooser each login
    }
    return redirect(f"{AUTH_URL}?{urlencode(params)}")

@app.route("/callback")
def callback():
    if "error" in request.args:
        return f"Auth error: {request.args['error']}", 400
    code = request.args.get("code")
    if not code:
        return "No code returned", 400

    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI}
    tok = requests.post(TOKEN_URL, data=data, headers=_basic_auth_header())

    if tok.status_code >= 400:
        return f"<h3>Token exchange failed</h3><pre>{tok.text}</pre>", 400

    access = tok.json()["access_token"]
    me = requests.get(API_BASE + "/me", headers={"Authorization": f"Bearer {access}"}).json()
    spotify_id = me["id"]
    display = me.get("display_name") or spotify_id

    user = User.query.filter_by(spotify_user_id=spotify_id).first()
    if not user:
        user = User(spotify_user_id=spotify_id, display_name=display)
        db.session.add(user)
        db.session.commit()

    _save_tokens(user, tok.json())
    session["user_id"] = user.id
    flash(f"Welcome, {display}!", "info")
    return redirect(url_for("dashboard"))

@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "info")
    return redirect(url_for("home"))

@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect(url_for("home"))
    user = User.query.get(session["user_id"])
    token = _ensure_token(user)

    try:
        _ = _sp_get(token, "/me")
    except Exception as e:
        return f"<h3>Spotify /me failed</h3><pre>{e}</pre>", 500

    genres = [
        "pop","rock","hip-hop","edm","r-n-b","country","latin","indie","jazz","house",
        "dance","electronic","soul","funk","punk","metal","k-pop","afrobeats","reggae","blues",
        "folk","classical","ambient","techno","trance","dubstep","drum-and-bass","grunge","emo"
    ]
    history = Playlist.query.filter_by(user_id=user.id).order_by(Playlist.created_at.desc()).all()
    return render_template("dashboard.html", display_name=user.display_name,
                           genres=sorted(genres), history=history)

@app.route("/recommend", methods=["POST"])
def recommend():
    """Richer recs: mix genre + artist seeds, use ranges, add tiny randomness."""
    if "user_id" not in session:
        return redirect(url_for("home"))
    user = User.query.get(session["user_id"])
    token = _ensure_token(user)

    mood = request.form.get("mood") or ""
    selected_genres = request.form.getlist("genres")
    limit = min(max(int(request.form.get("limit", 15)), 1), 30)
    energy10 = request.form.get("energy10")
    positivity10 = request.form.get("positivity10")
    dance10 = request.form.get("danceability10")

    mood_centers = {
        "happy": dict(energy=0.78, valence=0.85, dance=0.72, acoustic=0.25),
        "chill": dict(energy=0.35, valence=0.55, dance=0.45, acoustic=0.55),
        "focus": dict(energy=0.30, valence=0.40, dance=0.35, acoustic=0.70),
        "party": dict(energy=0.88, valence=0.75, dance=0.88, acoustic=0.10),
        "":      dict(energy=0.55, valence=0.55, dance=0.55, acoustic=0.35),
    }
    c = mood_centers.get(mood, mood_centers[""])
    if energy10: c["energy"] = float(energy10)/10.0
    if positivity10: c["valence"] = float(positivity10)/10.0
    if dance10: c["dance"] = float(dance10)/10.0

    def jiggle(x, amp=0.08): return max(0.0, min(1.0, x + random.uniform(-amp, amp)))
    e, v, d, a = map(jiggle, [c["energy"], c["valence"], c["dance"], c["acoustic"]])

    span = 0.20
    params = {
        "limit": limit, "market": "US",
        "min_energy": max(0.0, e-span), "max_energy": min(1.0, e+span),
        "min_valence": max(0.0, v-span), "max_valence": min(1.0, v+span),
        "min_danceability": max(0.0, d-span), "max_danceability": min(1.0, d+span),
        "min_acousticness": max(0.0, a-span), "max_acousticness": min(1.0, a+span),
        "min_popularity": 40, "max_popularity": 95
    }

    seed_genres = (selected_genres[:3] if selected_genres else ["pop"])
    seed_artists = _pick_seed_artists_from_genres(token, seed_genres, 2)
    params["seed_genres"] = ",".join(seed_genres)
    if seed_artists:
        params["seed_artists"] = ",".join(seed_artists)

    def format_tracks(items):
        return [{
            "id": t["id"], "uri": t["uri"], "name": t["name"],
            "artists": ", ".join(a["name"] for a in t["artists"]),
            "album_img": (t["album"]["images"][1]["url"] if t["album"]["images"] else ""),
            "preview_url": t.get("preview_url"),
            "external_url": t["external_urls"]["spotify"],
        } for t in items]

    try:
        recs = _sp_get(token, "/recommendations", params=params).get("tracks", [])
        tracks = format_tracks(recs)
    except Exception:
        g = seed_genres[0]
        q = f'genre:\"{g}\"'
        search_params = {"q": q, "type": "track", "limit": limit,
                         "market": "US", "offset": random.choice([0,20,40,60])}
        res = _sp_get(token, "/search", params=search_params)
        tracks = format_tracks(res.get("tracks", {}).get("items", []))

    session["last_seed_params"] = dict(
        mood=mood, genres=selected_genres or ["pop"], limit=limit,
        ranges=dict(
            energy=(params["min_energy"], params["max_energy"]),
            valence=(params["min_valence"], params["max_valence"]),
            danceability=(params["min_danceability"], params["max_danceability"]),
            acousticness=(params["min_acousticness"], params["max_acousticness"]),
            popularity=(params["min_popularity"], params["max_popularity"]),
        ),
        seeds=dict(genres=seed_genres, artists=seed_artists)
    )
    return render_template("recommend.html", tracks=tracks)

@app.route("/playlist/create", methods=["POST"])
def create_playlist():
    if "user_id" not in session:
        return redirect(url_for("home"))
    user = User.query.get(session["user_id"])
    token = _ensure_token(user)

    chosen = request.form.getlist("track_uri")
    playlist_name = request.form.get("playlist_name") or "Mood/Genre Mix"
    is_public = bool(request.form.get("public"))

    me = _sp_get(token, "/me")
    uid = me["id"]
    pl = _sp_post(token, f"/users/{uid}/playlists", {
        "name": playlist_name,
        "public": is_public,
        "description": "Created with Flask Spotify Recommender"
    })
    if chosen:
        _sp_post(token, f"/playlists/{pl['id']}/tracks", {"uris": chosen})

    rec = Playlist(
        user_id=user.id, name=playlist_name,
        spotify_playlist_id=pl["id"],
        seed_params=json.dumps(session.get("last_seed_params", {}))
    )
    db.session.add(rec)
    db.session.commit()

    return render_template("playlist_result.html",
                           playlist_name=playlist_name,
                           playlist_url=pl["external_urls"]["spotify"])

if __name__ == "__main__":
    app.run(debug=True)
