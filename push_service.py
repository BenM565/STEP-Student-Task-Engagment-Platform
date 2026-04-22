"""
PHASE 5 — Web Push Notification Service

Helper module for sending web push notifications to users.

Usage:
    from push_service import send_push_to_user

    send_push_to_user(
        user_id=42,
        title="New Task Available",
        body="Check out the latest task matching your skills!",
        url="/browse-tasks"
    )

Environment Variables Required:
    VAPID_PRIVATE_KEY - Private key for VAPID authentication
    VAPID_PUBLIC_KEY - Public key for VAPID authentication
    VAPID_CLAIM_EMAIL - Email for VAPID claims (e.g., mailto:admin@example.com)
"""

import os
import json
from datetime import datetime
from typing import Optional


def send_push_to_user(user_id: int, title: str, body: str, url: Optional[str] = None):
    """
    Send web push notification to all of a user's subscribed devices.

    Args:
        user_id: ID of the user to notify
        title: Notification title
        body: Notification body text
        url: Optional URL to open when notification is clicked

    Returns:
        int: Number of successful notifications sent
    """
    try:
        from pywebpush import webpush, WebPushException
        from app import db, PushSubscription
    except ImportError:
        print("[Push] pywebpush not installed. Install with: pip install pywebpush")
        return 0

    # Get VAPID credentials from environment
    vapid_private_key = os.getenv('VAPID_PRIVATE_KEY')
    vapid_public_key = os.getenv('VAPID_PUBLIC_KEY')
    vapid_claims = {"sub": os.getenv('VAPID_CLAIM_EMAIL', 'mailto:admin@example.com')}

    if not vapid_private_key or not vapid_public_key:
        print("[Push] VAPID keys not configured. Set VAPID_PRIVATE_KEY and VAPID_PUBLIC_KEY environment variables.")
        return 0

    # Get all subscriptions for this user
    subscriptions = PushSubscription.query.filter_by(user_id=user_id).all()

    if not subscriptions:
        print(f"[Push] No subscriptions found for user {user_id}")
        return 0

    # Prepare notification payload
    payload = {
        "title": title,
        "body": body,
        "icon": "/static/icons/icon-192x192.png",
        "badge": "/static/icons/icon-72x72.png",
    }

    if url:
        payload["url"] = url

    sent_count = 0
    failed_endpoints = []

    # Send to each subscription
    for subscription in subscriptions:
        try:
            subscription_info = {
                "endpoint": subscription.endpoint,
                "keys": {
                    "p256dh": subscription.p256dh,
                    "auth": subscription.auth
                }
            }

            webpush(
                subscription_info=subscription_info,
                data=json.dumps(payload),
                vapid_private_key=vapid_private_key,
                vapid_claims=vapid_claims
            )

            # Update last_used_at
            subscription.last_used_at = datetime.utcnow()
            db.session.commit()

            sent_count += 1
            print(f"[Push] Sent to user {user_id} (endpoint: {subscription.endpoint[:50]}...)")

        except WebPushException as e:
            print(f"[Push] WebPushException for user {user_id}: {str(e)}")

            # If subscription is invalid/expired (410 Gone), mark for deletion
            if e.response and e.response.status_code == 410:
                failed_endpoints.append(subscription.id)

        except Exception as e:
            print(f"[Push] Unexpected error sending to user {user_id}: {str(e)}")

    # Clean up failed subscriptions
    if failed_endpoints:
        for sub_id in failed_endpoints:
            subscription = PushSubscription.query.get(sub_id)
            if subscription:
                db.session.delete(subscription)
        db.session.commit()
        print(f"[Push] Removed {len(failed_endpoints)} expired subscription(s)")

    return sent_count


def send_push_to_multiple_users(user_ids: list, title: str, body: str, url: Optional[str] = None):
    """
    Send web push notification to multiple users.

    Args:
        user_ids: List of user IDs to notify
        title: Notification title
        body: Notification body text
        url: Optional URL to open when notification is clicked

    Returns:
        int: Total number of successful notifications sent
    """
    total_sent = 0

    for user_id in user_ids:
        sent = send_push_to_user(user_id, title, body, url)
        total_sent += sent

    return total_sent
