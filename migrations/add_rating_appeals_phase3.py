"""
PHASE 3 — Rating Appeals Migration Script

This idempotent migration script adds:
1. RatingAppeal table for student appeal workflow
2. Application.rating_locked column (prevents rating changes after appeal)
3. Application.rating_overridden column (admin manually adjusted rating)

Run with: python migrations/add_rating_appeals_phase3.py

Safety:
- Checks table/column existence before creating
- No destructive operations
- Can be run multiple times safely
"""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app import app, db
from sqlalchemy import inspect, text


def check_table_exists(table_name: str) -> bool:
    """Check if table exists in database."""
    inspector = inspect(db.engine)
    return table_name in inspector.get_table_names()


def check_column_exists(table_name: str, column_name: str) -> bool:
    """Check if column exists in table."""
    inspector = inspect(db.engine)
    columns = [col['name'] for col in inspector.get_columns(table_name)]
    return column_name in columns


def run_migration():
    """Execute migration safely."""
    with app.app_context():
        print("=" * 70)
        print("PHASE 3 — Rating Appeals Migration")
        print("=" * 70)

        # Step 1: Create RatingAppeal table
        if not check_table_exists('rating_appeal'):
            print("\n[1/3] Creating rating_appeal table...")
            db.session.execute(text("""
                CREATE TABLE rating_appeal (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            print("✅ Created rating_appeal table")
        else:
            print("\n[1/3] Table rating_appeal already exists — skipping")

        # Step 2: Add rating_locked column to application
        if not check_column_exists('application', 'rating_locked'):
            print("\n[2/3] Adding rating_locked column to application...")
            db.session.execute(text("""
                ALTER TABLE application
                ADD COLUMN rating_locked BOOLEAN DEFAULT 0
            """))
            db.session.commit()
            print("✅ Added application.rating_locked")
        else:
            print("\n[2/3] Column application.rating_locked already exists — skipping")

        # Step 3: Add rating_overridden column to application
        if not check_column_exists('application', 'rating_overridden'):
            print("\n[3/3] Adding rating_overridden column to application...")
            db.session.execute(text("""
                ALTER TABLE application
                ADD COLUMN rating_overridden BOOLEAN DEFAULT 0
            """))
            db.session.commit()
            print("✅ Added application.rating_overridden")
        else:
            print("\n[3/3] Column application.rating_overridden already exists — skipping")

        print("\n" + "=" * 70)
        print("✅ PHASE 3 Migration Complete!")
        print("=" * 70)
        print("\nNext steps:")
        print("1. Students can now appeal ratings via /ratings/<app_id>/appeal")
        print("2. Admins can review appeals at /admin/appeals")
        print("3. Check student_performance_breakdown.html for score explanation")
        print()


if __name__ == "__main__":
    try:
        run_migration()
    except Exception as e:
        print(f"\n❌ Migration failed: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
