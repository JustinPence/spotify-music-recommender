# Spotify Mood Recommender

Creates a Spotify playlist (up to 30 tracks) from a selected mood and genre, then saves it to your Spotify account.

## Stack
- Python, Flask, SQLAlchemy
- HTML/Jinja templates
- Spotify Web API (OAuth Authorization Code)
- SQLite (optional, created at runtime)

## Requirements
- Python 3.10+
- Spotify Developer account and app

## Setup
```bash
git clone https://github.com/justinpence/spotify-music-recommender.git
cd spotify-music-recommender
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
