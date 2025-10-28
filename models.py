from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    spotify_user_id = db.Column(db.String(128), unique=True, nullable=False)
    display_name = db.Column(db.String(128))
    access_token = db.Column(db.String(2048))
    refresh_token = db.Column(db.String(512))
    token_expires_at = db.Column(db.DateTime)

class Playlist(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(256))
    spotify_playlist_id = db.Column(db.String(128))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    seed_params = db.Column(db.Text)  # JSON string of mood/genres/sliders
