"""Shared Flask-SQLAlchemy instance.

Kept in its own module (rather than defined in app.py or models.py) so both
can import it without a circular import: app.py calls db.init_app(app),
models.py defines db.Model subclasses, and neither needs to import the other.
"""

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()