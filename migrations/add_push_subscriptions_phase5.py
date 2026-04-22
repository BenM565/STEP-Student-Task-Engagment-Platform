"""
PHASE 5 — Web Push Subscriptions Migration Script

This idempotent migration script adds:
1. PushSubscription table for storing browser push notification subscriptions

Run with: python migrations/add_push_subscriptions_phase5.py

Safety:
- Checks table existence before creating
- No destructive operations
- Can be run multiple times safely

Next steps after migration:
1. Generate VAPID keys using: python -c "from pywebpush import webpush; print(webpush.gen_vapid())"
2. Set environment variables: VAPID_PRIVATE_KEY and VAPID_PUBLIC_KEY
3. Set VAPID_CLAIM_EMAIL (e.g., mailto:admin@example.com)
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


def run_migration():
    """Execute migration safely."""
    with app.app_context():
        print("=" * 70)
        print("PHASE 5 — Web Push Subscriptions Migration")
        print("=" * 70)

        # Create PushSubscription table
        if not check_table_exists('push_subscription'):
            print("\n[1/1] Creating push_subscription table...")
            db.session.execute(text("""
                CREATE TABLE push_subscription (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    endpoint TEXT NOT NULL UNIQUE,
                    p256dh VARCHAR(255) NOT NULL,
                    auth VARCHAR(255) NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_used_at DATETIME,
                    FOREIGN KEY (user_id) REFERENCES user (id)
                )
            """))
            db.session.commit()
            print("✅ Created push_subscription table")
        else:
            print("\n[1/1] Table push_subscription already exists — skipping")

        print("\n" + "=" * 70)
        print("✅ PHASE 5 Migration Complete!")
        print("=" * 70)
        print("\n⚠️  IMPORTANT NEXT STEPS:")
        print("\n1. Generate VAPID keys:")
        print("   python -c \"from pywebpush import webpush; vapid = webpush.gen_vapid(); print('PRIVATE:', vapid['private']); print('PUBLIC:', vapid['public'])\"")
        print("\n2. Add to environment variables (or .env file):")
        print("   VAPID_PRIVATE_KEY=<your_private_key>")
        print("   VAPID_PUBLIC_KEY=<your_public_key>")
        print("   VAPID_CLAIM_EMAIL=mailto:admin@step.example.com")
        print("\n3. Install pywebpush if not installed:")
        print("   pip install pywebpush")
        print()


if __name__ == "__main__":
    try:
        run_migration()
    except Exception as e:
        print(f"\n❌ Migration failed: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
