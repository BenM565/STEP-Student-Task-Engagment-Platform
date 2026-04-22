"""
Quick migration runner for Phase 3, 4, and 5 columns
Run this with your virtual environment activated
"""

from app import app, db

def run_all_migrations():
    """Add all missing columns from Phase 3, 4, and 5"""

    with app.app_context():
        print("=" * 70)
        print("Running All Phase Migrations")
        print("=" * 70)

        from sqlalchemy import inspect, text

        inspector = inspect(db.engine)

        # Get existing columns in user table
        user_columns = [col['name'] for col in inspector.get_columns('user')]

        # Phase 4: Add user_prefs if missing
        if 'user_prefs' not in user_columns:
            print("\n[Phase 4] Adding user_prefs column to user table...")
            try:
                # MySQL doesn't allow TEXT with defaults, so add without default
                db.session.execute(text("""
                    ALTER TABLE user
                    ADD COLUMN user_prefs TEXT
                """))
                # Then update existing rows to have the default value
                db.session.execute(text("""
                    UPDATE user SET user_prefs = '{}' WHERE user_prefs IS NULL
                """))
                db.session.commit()
                print("Added user.user_prefs")
            except Exception as e:
                print(f"Error adding user_prefs: {e}")
                db.session.rollback()
        else:
            print("\n[Phase 4] user_prefs already exists")

        # Get existing columns in application table
        app_columns = [col['name'] for col in inspector.get_columns('application')]

        # Phase 3: Add rating fields if missing
        if 'rating_locked' not in app_columns:
            print("\n[Phase 3] Adding rating_locked to application table...")
            try:
                db.session.execute(text("""
                    ALTER TABLE application
                    ADD COLUMN rating_locked BOOLEAN DEFAULT 0
                """))
                db.session.commit()
                print("[OK] Added application.rating_locked")
            except Exception as e:
                print(f"[ERROR] Error adding rating_locked: {e}")
                db.session.rollback()
        else:
            print("\n[Phase 3] rating_locked already exists")

        if 'rating_overridden' not in app_columns:
            print("\n[Phase 3] Adding rating_overridden to application table...")
            try:
                db.session.execute(text("""
                    ALTER TABLE application
                    ADD COLUMN rating_overridden BOOLEAN DEFAULT 0
                """))
                db.session.commit()
                print("[OK] Added application.rating_overridden")
            except Exception as e:
                print(f"[ERROR] Error adding rating_overridden: {e}")
                db.session.rollback()
        else:
            print("\n[Phase 3] rating_overridden already exists")

        # Check for rating_appeal table
        tables = inspector.get_table_names()

        if 'rating_appeal' not in tables:
            print("\n[Phase 3] Creating rating_appeal table...")
            try:
                db.session.execute(text("""
                    CREATE TABLE rating_appeal (
                        id INTEGER PRIMARY KEY AUTO_INCREMENT,
                        application_id INTEGER NOT NULL,
                        student_id INTEGER NOT NULL,
                        reason TEXT NOT NULL,
                        status VARCHAR(20) DEFAULT 'pending',
                        admin_note TEXT,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        resolved_at DATETIME,
                        resolved_by_id INTEGER,
                        FOREIGN KEY (application_id) REFERENCES application (id),
                        FOREIGN KEY (student_id) REFERENCES user (id),
                        FOREIGN KEY (resolved_by_id) REFERENCES user (id)
                    )
                """))
                db.session.commit()
                print("[OK] Created rating_appeal table")
            except Exception as e:
                print(f"[ERROR] Error creating rating_appeal table: {e}")
                db.session.rollback()
        else:
            print("\n[Phase 3] rating_appeal table already exists")

        # Check for push_subscription table
        if 'push_subscription' not in tables:
            print("\n[Phase 5] Creating push_subscription table...")
            try:
                db.session.execute(text("""
                    CREATE TABLE push_subscription (
                        id INTEGER PRIMARY KEY AUTO_INCREMENT,
                        user_id INTEGER NOT NULL,
                        endpoint VARCHAR(500) NOT NULL UNIQUE,
                        p256dh VARCHAR(255) NOT NULL,
                        auth VARCHAR(255) NOT NULL,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        last_used_at DATETIME,
                        FOREIGN KEY (user_id) REFERENCES user (id)
                    )
                """))
                db.session.commit()
                print("[OK] Created push_subscription table")
            except Exception as e:
                print(f"[ERROR] Error creating push_subscription table: {e}")
                db.session.rollback()
        else:
            print("\n[Phase 5] push_subscription table already exists")

        print("\n" + "=" * 70)
        print("[SUCCESS] All Migrations Complete!")
        print("=" * 70)
        print("\nYou can now restart your Flask app.")


if __name__ == "__main__":
    run_all_migrations()
