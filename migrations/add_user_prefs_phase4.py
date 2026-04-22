"""
PHASE 4 — User Preferences Migration Script

This idempotent migration script adds:
1. User.user_prefs JSON column for storing user preferences
   (used for checklist dismissal, theme preferences, etc.)

Run with: python migrations/add_user_prefs_phase4.py

Safety:
- Checks column existence before creating
- No destructive operations
- Can be run multiple times safely
- SQLite supports JSON via TEXT column
"""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app import app, db
from sqlalchemy import inspect, text


def check_column_exists(table_name: str, column_name: str) -> bool:
    """Check if column exists in table."""
    inspector = inspect(db.engine)
    columns = [col['name'] for col in inspector.get_columns(table_name)]
    return column_name in columns


def run_migration():
    """Execute migration safely."""
    with app.app_context():
        print("=" * 70)
        print("PHASE 4 — User Preferences Migration")
        print("=" * 70)

        # Add user_prefs JSON column to user table
        if not check_column_exists('user', 'user_prefs'):
            print("\n[1/1] Adding user_prefs column to user table...")

            # SQLite stores JSON as TEXT
            db.session.execute(text("""
                ALTER TABLE user
                ADD COLUMN user_prefs TEXT DEFAULT '{}'
            """))
            db.session.commit()
            print("✅ Added user.user_prefs (JSON/TEXT column)")
        else:
            print("\n[1/1] Column user.user_prefs already exists — skipping")

        print("\n" + "=" * 70)
        print("✅ PHASE 4 Migration Complete!")
        print("=" * 70)
        print("\nNext steps:")
        print("1. Profile completeness meter will appear in navbar")
        print("2. Onboarding checklist available for new students")
        print("3. Users can dismiss checklist via user_prefs")
        print()


if __name__ == "__main__":
    try:
        run_migration()
    except Exception as e:
        print(f"\n❌ Migration failed: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
