"""WSGI entry point for Kala Mod.

Run with:
    gunicorn server_ready:app --bind 0.0.0.0:$PORT

The main Flask application lives in app.py. Configuration such as
KALAMOD_DATA_DIR and SECRET_KEY is read by app.py itself.
"""

from app import app


if __name__ == "__main__":
    # Optional local test only; production should use Gunicorn.
    import os

    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
