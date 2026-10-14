import os
import sys
from pathlib import Path


PROJECT_DIR = str(Path(__file__).resolve().parent)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

secret_key = os.environ.get("FLASK_SECRET_KEY")
if not secret_key:
    raise RuntimeError("Atur FLASK_SECRET_KEY di konfigurasi WSGI sebelum menjalankan aplikasi.")

from app import create_app


application = create_app(
    {
        "SECRET_KEY": secret_key,
        "SESSION_COOKIE_SECURE": True,
    }
)
