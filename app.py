"""Flask app factory for the Aspicom invoicing API.

Run locally with:
    python app.py

Reads DB connection details from a .env file (see .env.example) — never
hardcode credentials here.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from flask import Flask, send_from_directory
from flask_cors import CORS

from extensions import db
from routes.clients import clients_bp
from routes.company import company_bp
from routes.inventory import inventory_bp
from routes.invoices import invoices_bp, payments_bp
from routes.pdf import pdf_bp

load_dotenv()


def create_app() -> Flask:
    """Build and configure the Flask app.

    Returns:
        A configured Flask app, not yet running. Call .run() on it, or hand
        it to a WSGI server (gunicorn, etc.) in production.
    """
    app = Flask(__name__)

    db_user = os.environ["DB_USER"]
    db_password = os.environ["DB_PASSWORD"]
    db_host = os.environ.get("DB_HOST", "localhost")
    db_port = os.environ.get("DB_PORT", "3306")
    db_name = os.environ["DB_NAME"]

    app.config["SQLALCHEMY_DATABASE_URI"] = (
        f"mysql+pymysql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Where uploaded logo/stamp images land — read from env so moving to a
    # cloud bucket later is a config change, not a code change (per the
    # Q3 answer from the schema-approval stage).
    app.config["UPLOAD_FOLDER"] = os.environ.get("UPLOAD_FOLDER", "uploads")
    app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB cap on any single upload

    db.init_app(app)

    # Local dev only — CORS wide open so the standalone HTML frontend
    # (served separately, e.g. via `python -m http.server` or opened as a
    # file) can call this API. Tighten to specific origins before any real
    # deployment.

    CORS(app, resources={r"/api/*": {"origins": "*"}},
     methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
     allow_headers=["Content-Type", "Authorization"])
    
    app.register_blueprint(clients_bp, url_prefix="/api/clients")
    app.register_blueprint(invoices_bp, url_prefix="/api/invoices")
    app.register_blueprint(payments_bp, url_prefix="/api")  # DELETE /api/payments/<id> — see routes/invoices.py
    app.register_blueprint(inventory_bp, url_prefix="/api/inventory")
    app.register_blueprint(company_bp, url_prefix="/api/company")
    app.register_blueprint(pdf_bp, url_prefix="/api")  # /api/parse-pdf, /api/import-pdf

    @app.route("/api/health")
    def health():
        return {"status": "ok"}, 200

    @app.route("/uploads/<path:filename>")
    def uploaded_file(filename):
        """Serve files saved by POST /api/company/logo and /stamp.

        company.logo_path / stamp_path hold values like 'uploads/stamp.png';
        the frontend loads them as <img src="{API_URL}/uploads/stamp.png">. Reads the
        folder from config (not a hardcoded "uploads") so it always matches
        where the upload endpoints actually write. send_from_directory
        rejects path traversal ('../') with a 404.
        """
        return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, port=5000)
