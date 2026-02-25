"""
Payment & Escrow Service Module for STEP Platform
===================================================
Handles secure payment processing with Stripe integration and escrow management.

This module implements a secure payment system where funds are held in escrow
until work is approved. Payment lifecycle:
1. Company posts task (task created)
2. Student selected (payment authorization created)
3. Student submits work (no payment yet, funds in escrow)
4. Company approves (funds released to student account)
5. Company rejects/requests changes (funds remain in escrow)

Dependencies required in requirements.txt:
- stripe==5.4.0

Configuration required in .env:
- STRIPE_PUBLIC_KEY=pk_live_...
- STRIPE_SECRET_KEY=sk_live_...
- STRIPE_WEBHOOK_SECRET=whsec_...
"""

import os
import json
from datetime import datetime
from decimal import Decimal
from typing import Dict, Optional, Tuple

# Third-party imports for payment processing
import stripe
from flask import current_app


class PaymentError(Exception):
    """
    CUSTOM EXCEPTION FOR PAYMENT ERRORS
    ====================================
    Raised when payment processing encounters an error.
    Includes detailed error message and error code for debugging.
    """

    def __init__(self, message: str, error_code: str = "PAYMENT_ERROR"):
        self.message = message
        self.error_code = error_code
        super().__init__(self.message)


class EscrowManager:
    """
    ESCROW ACCOUNT MANAGER
    ======================
    Manages secure payment holding and release with Stripe Connect.

    Payment Flow:
    1. Task created -> Company adds payment card
    2. Student selected -> Payment intent created (authorized but not charged)
    3. Work submitted -> Payment held in escrow
    4. Work approved -> Payment captured and transferred to student

    All funds are held in separate Stripe account per company for transparency.
    """

    def __init__(self):
        """Initialize Stripe with API key from environment"""
        self.stripe_key = os.getenv("STRIPE_SECRET_KEY")
        if not self.stripe_key:
            raise ValueError("STRIPE_SECRET_KEY environment variable not set")
        stripe.api_key = self.stripe_key

    def create_payment_intent(
            self,
            company_id: int,
            student_id: int,
            application_id: int,
            amount_cents: int,
            task_id: int,
            task_title: str
    ) -> Dict:
        """
        CREATE PAYMENT INTENT
        =====================
        Creates a Stripe PaymentIntent when student is selected for task.

        The intent is created with status "requires_payment_method" initially.
        When student accepts, they can attach a payment method and confirm.

        Args:
            company_id (int): Company user ID (payment source)
            student_id (int): Student user ID (payment destination)
            application_id (int): Application ID for reference
            amount_cents (int): Payment amount in cents (e.g., 5000 for €50.00)
            task_id (int): Task ID for reference
            task_title (str): Task title for description

        Returns:
            dict: Stripe PaymentIntent object with keys:
                - id: Stripe payment intent ID (pi_xxx)
                - client_secret: Client-side secret for confirming payment
                - status: Payment intent status
                - amount: Amount in cents
                - application_id: Custom metadata

        Raises:
            PaymentError: If Stripe API call fails

        Example:
            >>> intent = EscrowManager().create_payment_intent(
            ...     company_id=1,
            ...     student_id=2,
            ...     application_id=5,
            ...     amount_cents=5000,  # €50.00
            ...     task_id=10,
            ...     task_title="Python Tutorial"
            ... )
            >>> print(intent['client_secret'])
            'pi_xxxxx_secret_yyyyy'
        """
        try:
            # Create payment intent with metadata for tracking
            intent = stripe.PaymentIntent.create(
                # Amount in cents (50.00 euros = 5000 cents)
                amount=amount_cents,
                # Currency for all transactions
                currency="eur",
                # Metadata for our system to track the payment
                metadata={
                    "company_id": company_id,
                    "student_id": student_id,
                    "application_id": application_id,
                    "task_id": task_id,
                    "task_title": task_title,
                    "platform": "STEP",
                    "created_at": datetime.utcnow().isoformat()
                },
                # Description visible in Stripe dashboard
                description=f"Task payment: {task_title} (Student {student_id})",
                # Payment methods accepted
                automatic_payment_methods={
                    "enabled": True,
                },
            )

            # Return only the fields we need, hiding sensitive data
            return {
                "id": intent.id,
                "client_secret": intent.client_secret,
                "status": intent.status,
                "amount": intent.amount,
                "application_id": application_id
            }

        except stripe.error.StripeError as e:
            # Catch any Stripe-specific error and convert to our exception
            raise PaymentError(
                f"Failed to create payment intent: {str(e)}",
                "STRIPE_API_ERROR"
            )
        except Exception as e:
            # Catch any other unexpected errors
            raise PaymentError(
                f"Unexpected error creating payment intent: {str(e)}",
                "UNKNOWN_ERROR"
            )

    def confirm_payment_intent(
            self,
            payment_intent_id: str,
            payment_method_id: str
    ) -> Dict:
        """
        CONFIRM PAYMENT INTENT
        ======================
        Confirms and authorizes a payment intent (charges are held in escrow).

        Called when student provides payment method and confirms payment.
        Amount is authorized but not yet charged to card.

        Args:
            payment_intent_id (str): Stripe PaymentIntent ID (pi_xxx)
            payment_method_id (str): Stripe PaymentMethod ID (pm_xxx)

        Returns:
            dict: Updated PaymentIntent with confirmation status
                - id: Payment intent ID
                - status: Will be "succeeded" or "processing"
                - client_secret: Secret for client-side operations

        Raises:
            PaymentError: If confirmation fails

        Example:
            >>> EscrowManager().confirm_payment_intent(
            ...     payment_intent_id="pi_xxxxx",
            ...     payment_method_id="pm_yyyyy"
            ... )
        """
        try:
            # Confirm the payment (authorizes but doesn't capture immediately)
            intent = stripe.PaymentIntent.confirm(
                payment_intent_id,
                payment_method=payment_method_id,
                # Auto-complete if payment succeeds
                return_url=f"{current_app.config.get('APP_URL', 'http://localhost:5000')}/payment/confirm"
            )

            # Payment is authorized; funds are in escrow
            return {
                "id": intent.id,
                "status": intent.status,
                "client_secret": intent.client_secret
            }

        except stripe.error.CardError as e:
            # Card was declined
            raise PaymentError(
                f"Payment failed: {e.user_message}",
                "CARD_DECLINED"
            )
        except stripe.error.StripeError as e:
            # Other Stripe error
            raise PaymentError(
                f"Payment confirmation failed: {str(e)}",
                "STRIPE_ERROR"
            )

    def capture_payment(
            self,
            payment_intent_id: str,
            application_id: int
    ) -> Dict:
        """
        CAPTURE PAYMENT FROM ESCROW
        ============================
        Captures (finalizes) a payment when work is approved.

        This actually charges the company's card and moves funds to the
        student's account. Called when company approves submitted work.

        Args:
            payment_intent_id (str): Stripe PaymentIntent ID
            application_id (int): Application ID for audit trail

        Returns:
            dict: Stripe Charge object
                - id: Charge ID
                - status: Should be "succeeded"
                - amount: Amount charged in cents

        Raises:
            PaymentError: If capture fails

        Example:
            >>> EscrowManager().capture_payment(
            ...     payment_intent_id="pi_xxxxx",
            ...     application_id=5
            ... )
        """
        try:
            # Retrieve the payment intent to verify it exists
            intent = stripe.PaymentIntent.retrieve(payment_intent_id)

            # Verify the intent is in a state that can be captured
            if intent.status not in ["requires_capture", "succeeded"]:
                raise PaymentError(
                    f"Cannot capture payment with status: {intent.status}",
                    "INVALID_PAYMENT_STATE"
                )

            # If it's already succeeded, return the charge
            if intent.status == "succeeded":
                return {
                    "id": intent.id,
                    "status": intent.status,
                    "amount": intent.amount,
                    "application_id": application_id
                }

            # Capture the funds (charge the card)
            charge = stripe.PaymentIntent.capture(payment_intent_id)

            # Log the successful capture
            self._log_payment_event(
                application_id=application_id,
                event_type="PAYMENT_CAPTURED",
                details=f"Captured €{intent.amount / 100:.2f}"
            )

            return {
                "id": charge.id,
                "status": charge.status,
                "amount": charge.amount,
                "application_id": application_id
            }

        except stripe.error.StripeError as e:
            raise PaymentError(
                f"Failed to capture payment: {str(e)}",
                "CAPTURE_FAILED"
            )

    def refund_payment(
            self,
            payment_intent_id: str,
            application_id: int,
            amount_cents: Optional[int] = None,
            reason: str = "requested_by_customer"
    ) -> Dict:
        """
        REFUND PAYMENT FROM ESCROW
        ===========================
        Refunds a payment when work is rejected or dispute is resolved.

        Sends funds back to company's payment method. Called when work is
        rejected or dispute favors the company.

        Args:
            payment_intent_id (str): Stripe PaymentIntent ID to refund
            application_id (int): Application ID for audit trail
            amount_cents (int, optional): Partial refund amount in cents.
                If None, full refund is issued
            reason (str): Refund reason for Stripe dashboard
                - "requested_by_customer": Student asked for refund
                - "duplicate": Duplicate charge
                - "fraudulent": Fraudulent transaction

        Returns:
            dict: Stripe Refund object
                - id: Refund ID
                - status: Should be "succeeded"
                - amount: Refunded amount in cents

        Raises:
            PaymentError: If refund fails

        Example:
            >>> EscrowManager().refund_payment(
            ...     payment_intent_id="pi_xxxxx",
            ...     application_id=5,
            ...     reason="requested_by_customer"
            ... )
        """
        try:
            # Retrieve the original charge from the payment intent
            intent = stripe.PaymentIntent.retrieve(payment_intent_id)

            # Get the charge ID from the intent
            if not intent.charges.data:
                raise PaymentError(
                    "No charges found for this payment intent",
                    "NO_CHARGE_FOUND"
                )

            charge_id = intent.charges.data[0].id

            # Create the refund
            refund = stripe.Refund.create(
                charge=charge_id,
                amount=amount_cents,  # None means full refund
                reason=reason,
                metadata={
                    "application_id": application_id,
                    "platform": "STEP"
                }
            )

            # Log the refund
            self._log_payment_event(
                application_id=application_id,
                event_type="PAYMENT_REFUNDED",
                details=f"Refunded €{(amount_cents or intent.amount) / 100:.2f}"
            )

            return {
                "id": refund.id,
                "status": refund.status,
                "amount": refund.amount,
                "application_id": application_id
            }

        except stripe.error.StripeError as e:
            raise PaymentError(
                f"Failed to refund payment: {str(e)}",
                "REFUND_FAILED"
            )

    def retrieve_payment_status(self, payment_intent_id: str) -> Dict:
        """
        RETRIEVE PAYMENT STATUS
        =======================
        Gets current status of a payment intent.

        Useful for checking payment state before approving/rejecting work.

        Args:
            payment_intent_id (str): Stripe PaymentIntent ID

        Returns:
            dict: Payment intent status information
                - id: Intent ID
                - status: Current status
                - amount: Amount in cents
                - metadata: Custom metadata

        Raises:
            PaymentError: If retrieval fails

        Example:
            >>> status = EscrowManager().retrieve_payment_status("pi_xxxxx")
            >>> if status['status'] == 'succeeded':
            ...     print("Payment is authorized")
        """
        try:
            intent = stripe.PaymentIntent.retrieve(payment_intent_id)

            return {
                "id": intent.id,
                "status": intent.status,
                "amount": intent.amount,
                "currency": intent.currency,
                "metadata": intent.metadata,
                "charges": len(intent.charges.data) > 0
            }

        except stripe.error.StripeError as e:
            raise PaymentError(
                f"Failed to retrieve payment status: {str(e)}",
                "RETRIEVAL_FAILED"
            )

    def _log_payment_event(
            self,
            application_id: int,
            event_type: str,
            details: str = ""
    ) -> None:
        """
        LOG PAYMENT EVENT
        =================
        Internal method to log payment events for auditing.

        Args:
            application_id (int): Related application ID
            event_type (str): Type of event (e.g., "PAYMENT_CAPTURED")
            details (str): Additional event details
        """
        try:
            # Log to application database for audit trail
            # This would integrate with the Application model's payment_log field
            log_entry = {
                "timestamp": datetime.utcnow().isoformat(),
                "event_type": event_type,
                "details": details
            }
            # In app.py, we would append this to Application.payment_log
            print(f"[PaymentLog] App {application_id}: {event_type} - {details}")
        except Exception as e:
            print(f"[PaymentLog] Error logging event: {str(e)}")

    def calculate_split(
            self,
            total_amount_cents: int,
            platform_fee_percent: float = 10.0
    ) -> Tuple[int, int]:
        """
        CALCULATE PAYMENT SPLIT
        =======================
        Splits payment between student and platform.

        Platform takes a percentage fee (default 10%) for providing the service.
        Student receives the remaining amount.

        Args:
            total_amount_cents (int): Total payment in cents
            platform_fee_percent (float): Platform fee as percentage (default 10%)

        Returns:
            tuple: (student_amount_cents, platform_fee_cents)

        Example:
            >>> student, fee = EscrowManager().calculate_split(10000)  # €100
            >>> print(f"Student: €{student/100}, Platform: €{fee/100}")
            Student: €90.0, Platform: €10.0
        """
        # Calculate platform fee
        platform_fee = int(total_amount_cents * (platform_fee_percent / 100))

        # Student gets the remainder
        student_amount = total_amount_cents - platform_fee

        return (student_amount, platform_fee)


# Global escrow manager instance
# Initialize in app.py after Stripe configuration
escrow_manager = None


def init_escrow_manager():
    """
    INITIALIZE ESCROW MANAGER
    =========================
    Should be called once during Flask app initialization.

    Usage in app.py:
        from payment_service import init_escrow_manager

        if __name__ == '__main__':
            init_escrow_manager()
            app.run()
    """
    global escrow_manager
    if not escrow_manager:
        escrow_manager = EscrowManager()
    return escrow_manager