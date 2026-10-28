# TODO

Non-blocking cosmetic warnings surfaced by the test suite (25 passed, 34
warnings). Neither affects correctness today — both are deprecation notices
for APIs that still work, just not indefinitely. Fix when convenient.

- [ ] **`datetime.utcnow()` is deprecated.** Used as the default for every
      `created_at`/`updated_at`/`imported_at`/`changed_at` column in
      `models.py`. Switch to a timezone-aware equivalent, e.g.
      `lambda: datetime.now(datetime.UTC)`, when convenient. Note this is a
      real (if minor) behavior change too, not just a syntax swap — naive
      vs. aware datetimes compare and serialize slightly differently, so
      test the diff before merging, don't just find-and-replace.

- [ ] **`Query.get()` is legacy SQLAlchemy 1.x API.** Used throughout
      `routes/*.py` (e.g. `Client.query.get(id)`). Switch to the 2.x-style
      `db.session.get(Model, id)` when convenient. Purely mechanical —
      same return semantics (the object or `None`), just a different call
      shape.

Both are non-blocking. Fix later.
