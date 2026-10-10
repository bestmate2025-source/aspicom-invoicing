"""Flask app factory for the Aspicom invoicing API.

Run locally with:
    python app.py

Reads DB connection details from a .env file (see .env.example) — never
hardcode credentials here.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from flask import Flask, g, request, send_from_directory
from flask_cors import CORS

from extensions import db
from routes.auth import auth_bp
from routes.clients import clients_bp
from routes.companies import companies_bp
from routes.company import company_bp
from routes.inventory import inventory_bp
from routes.invoices import invoices_bp, payments_bp
from routes.pdf import pdf_bp
from routes.statements import statements_bp
from routes.users import users_bp
from routes.vat_report import vat_report_bp
from utils.auth import auth_enabled, get_current_user
from utils.responses import api_response

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

    # Fail at start-up (clear message in the Railway logs) rather than with a
    # confusing 500 on the first request.
    if auth_enabled() and not os.environ.get("JWT_SECRET", "").strip():
        raise RuntimeError("AUTH_ENABLED is true but JWT_SECRET is not set.")

    # Local dev only — CORS wide open so the standalone HTML frontend
    # (served separately, e.g. via `python -m http.server` or opened as a
    # file) can call this API. Tighten to specific origins before any real
    # deployment.

    CORS(app, resources={r"/api/*": {"origins": "*"}},
     methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
     allow_headers=["Content-Type", "Authorization"])
    
    app.register_blueprint(auth_bp, url_prefix="/api/auth")  # login / me / logout — see routes/auth.py
    app.register_blueprint(users_bp, url_prefix="/api/users")  # user management (admin only) — see routes/users.py
    app.register_blueprint(clients_bp, url_prefix="/api/clients")
    app.register_blueprint(invoices_bp, url_prefix="/api/invoices")
    app.register_blueprint(payments_bp, url_prefix="/api")  # DELETE /api/payments/<id> — see routes/invoices.py
    app.register_blueprint(inventory_bp, url_prefix="/api/inventory")
    app.register_blueprint(company_bp, url_prefix="/api/company")
    app.register_blueprint(companies_bp, url_prefix="/api/companies")  # list/create/edit companies + per-company logo/stamp
    app.register_blueprint(pdf_bp, url_prefix="/api")  # /api/parse-pdf, /api/import-pdf
    app.register_blueprint(statements_bp, url_prefix="/api")  # GET /api/clients/<id>/statement — see routes/statements.py
    app.register_blueprint(vat_report_bp, url_prefix="/api")  # GET /api/vat-report — see routes/vat_report.py

    @app.before_request
    def _enforce_auth():
        """When AUTH_ENABLED=true, every /api/ call needs a valid login.

        Read-only users ('user' role) may only GET; POST / PUT / DELETE / PATCH
        need the 'admin' role. Does nothing at all when AUTH_ENABLED is not "true".
        """
        if not auth_enabled():
            return None

        # Browsers send an OPTIONS "preflight" (with no token) before any call that
        # carries an Authorization header. Blocking it would break every request.
        if request.method == "OPTIONS":
            return None

        path = request.path
        if path.startswith("/api/auth/") or path == "/api/health":
            return None  # public
        if not path.startswith("/api/"):
            return None  # /uploads/... and other non-API paths

        user = get_current_user()
        if user is None:
            return api_response(error="unauthorized", message="Authentication required."), 401

        if request.method in ("POST", "PUT", "DELETE", "PATCH") and user.role != "admin":
            return api_response(error="forbidden", message="Admin access required."), 403

        g.current_user = user
        return None

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
