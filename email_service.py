"""
Email Service Module for STEP Platform
======================================
Handles all email notifications for task updates, applications, and submissions.

This module provides an abstraction layer for sending emails to users. It currently
uses Flask-Mail (to be configured) and can be extended to use third-party services
like SendGrid, AWS SES, or Mailgun.

Dependencies required in requirements.txt:
- Flask-Mail==0.9.1

Configuration required in .env:
- MAIL_SERVER=smtp.gmail.com
- MAIL_PORT=587
- MAIL_USE_TLS=true
- MAIL_USERNAME=your_email@gmail.com
- MAIL_PASSWORD=your_app_password
- MAIL_DEFAULT_SENDER=noreply@step-platform.com
"""

import os
from datetime import datetime
from typing import List, Optional
from functools import wraps
from threading import Thread

# Flask imports for mail functionality
from flask import current_app
from flask_mail import Mail, Message

# Initialize Flask-Mail (configured in app.py)
mail = Mail()


def async_send_email(app, msg):
    """
    ASYNCHRONOUS EMAIL SENDER
    ========================
    Sends email in a background thread to prevent blocking the main request.

    Args:
        app: Flask application context
        msg: Flask-Mail Message object to send

    Returns:
        None (runs in background thread)

    Usage:
        This is called internally by send_email() and should not be used directly.
    """
    with app.app_context():
        mail.send(msg)


def send_email(
        recipient_email: str,
        subject: str,
        html_body: str,
        text_body: Optional[str] = None,
        sender: Optional[str] = None
) -> bool:
    """
    SEND EMAIL WRAPPER
    ==================
    Thread-safe email sender with error handling and logging.

    Args:
        recipient_email (str): Email address of recipient
        subject (str): Email subject line
        html_body (str): HTML content of email body
        text_body (str, optional): Plain text fallback for email body
        sender (str, optional): Override default sender email

    Returns:
        bool: True if email queued successfully, False on error

    Raises:
        Catches all exceptions and returns False without raising

    Example:
        >>> send_email(
        ...     recipient_email="student@example.com",
        ...     subject="Your application was accepted!",
        ...     html_body="<p>Congratulations on being selected!</p>"
        ... )
        True
    """
    try:
        # Use default sender from config if not specified
        if sender is None:
            sender = current_app.config.get(
                "MAIL_DEFAULT_SENDER",
                "noreply@step-platform.com"
            )

        # Create Message object with both HTML and text versions for compatibility
        msg = Message(
            subject=subject,
            recipients=[recipient_email],
            html=html_body,
            body=text_body or "Please view this email in HTML format.",
            sender=sender
        )

        # Send asynchronously in background thread to avoid blocking
        Thread(
            target=async_send_email,
            args=(current_app._get_current_object(), msg)
        ).start()

        return True

    except Exception as e:
        # Log error but don't raise to prevent breaking application flow
        print(f"[EmailService] Error sending email to {recipient_email}: {str(e)}")
        return False


# ============================================================================
# EMAIL TEMPLATES - Notification Event Handlers
# ============================================================================

def send_task_posted_notification(task, company):
    """
    TASK POSTED NOTIFICATION
    ========================
    Sent to students when a new task matching their skills is posted.

    Args:
        task: Task object containing task details
        company: User object (company) who posted the task

    Returns:
        bool: Success status of email send

    Use Case:
        Called after company creates new task. Emails are sent to students
        whose skills match task tags.
    """
    subject = f"New Task Alert: {task.title}"

    html_body = f"""
    <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; border: 1px solid #ddd; padding: 20px;">

                <h2 style="color: #0066cc;">New Task Posted!</h2>

                <p>Hi there,</p>

                <p>A new task matching your skills has been posted:</p>

                <div style="background-color: #f5f5f5; padding: 15px; border-radius: 5px; margin: 20px 0;">
                    <h3 style="margin-top: 0; color: #333;">{task.title}</h3>
                    <p><strong>Posted by:</strong> {company.name}</p>
                    <p><strong>Description:</strong></p>
                    <p>{task.description[:200]}...</p>
                    <p><strong>Payment Type:</strong> {task.payment_type.capitalize()}</p>
                    {f'<p><strong>Fixed Price:</strong> €{task.fixed_price}</p>' if task.fixed_price else ''}
                    {f'<p><strong>Hourly Rate:</strong> €{task.hourly_rate}/hr</p>' if task.hourly_rate else ''}
                </div>

                <p>
                    <a href="{current_app.config.get('APP_URL', 'http://localhost:5000')}/task/{task.id}" 
                       style="background-color: #0066cc; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; display: inline-block;">
                        View Task
                    </a>
                </p>

                <p>Best regards,<br>STEP Platform Team</p>

            </div>
        </body>
    </html>
    """

    text_body = f"""
    New Task Posted: {task.title}

    Posted by: {company.name}

    Description: {task.description[:200]}...

    Payment Type: {task.payment_type.capitalize()}

    View the full task at: {current_app.config.get('APP_URL', 'http://localhost:5000')}/task/{task.id}
    """

    return send_email(
        recipient_email=None,  # This should be filtered by skill matching in calling function
        subject=subject,
        html_body=html_body,
        text_body=text_body
    )


def send_application_received_notification(application, task, company):
    """
    APPLICATION RECEIVED NOTIFICATION
    ==================================
    Sent to company when student applies for their task.

    Args:
        application: Application object linking student to task
        task: Task object that was applied for
        company: User object (company) receiving the notification

    Returns:
        bool: Success status of email send

    Use Case:
        Called when student submits application. Notifies company immediately.
    """
    subject = f"New Application for: {task.title}"

    html_body = f"""
    <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; border: 1px solid #ddd; padding: 20px;">

                <h2 style="color: #0066cc;">New Application Received!</h2>

                <p>Hi {company.name},</p>

                <p>A student has applied for your task:</p>

                <div style="background-color: #f5f5f5; padding: 15px; border-radius: 5px; margin: 20px 0;">
                    <h3 style="margin-top: 0; color: #333;">{task.title}</h3>
                    <p><strong>Applicant:</strong> {application.student.name}</p>
                    <p><strong>Student Email:</strong> {application.student.email}</p>
                    <p><strong>Student Skills:</strong> {application.student.skills or 'Not specified'}</p>
                    <p><strong>Application Date:</strong> {application.created_at.strftime('%Y-%m-%d %H:%M')}</p>
                </div>

                <p>
                    <a href="{current_app.config.get('APP_URL', 'http://localhost:5000')}/company/applicants" 
                       style="background-color: #0066cc; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; display: inline-block;">
                        Review Application
                    </a>
                </p>

                <p>Best regards,<br>STEP Platform Team</p>

            </div>
        </body>
    </html>
    """

    text_body = f"""
    New Application Received!

    Task: {task.title}
    Applicant: {application.student.name}
    Student Email: {application.student.email}

    Review the application at: {current_app.config.get('APP_URL', 'http://localhost:5000')}/company/applicants
    """

    return send_email(
        recipient_email=company.email,
        subject=subject,
        html_body=html_body,
        text_body=text_body
    )


def send_application_accepted_notification(application, task, student):
    """
    APPLICATION ACCEPTED NOTIFICATION
    ==================================
    Sent to student when company accepts their application.

    Args:
        application: Application object that was accepted
        task: Task object for the application
        student: User object (student) receiving acceptance

    Returns:
        bool: Success status of email send

    Use Case:
        Called when company marks application as 'selected'. Student gets
        details about next steps and payment information.
    """
    subject = f"Congratulations! Your Application was Accepted - {task.title}"

    html_body = f"""
    <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; border: 1px solid #ddd; padding: 20px;">

                <h2 style="color: #28a745;">Congratulations! 🎉</h2>

                <p>Hi {student.name},</p>

                <p>We're excited to tell you that your application has been accepted!</p>

                <div style="background-color: #f5f5f5; padding: 15px; border-radius: 5px; margin: 20px 0;">
                    <h3 style="margin-top: 0; color: #333;">{task.title}</h3>
                    <p><strong>Company:</strong> {task.company.name}</p>
                    <p><strong>Payment Type:</strong> {task.payment_type.capitalize()}</p>
                    {f'<p><strong>Payment Amount:</strong> €{task.fixed_price}</p>' if task.fixed_price else ''}
                    {f'<p><strong>Hourly Rate:</strong> €{task.hourly_rate}/hr</p>' if task.hourly_rate else ''}
                    {f'<p><strong>Estimated Duration:</strong> {task.estimated_hours} hours</p>' if task.estimated_hours else ''}
                </div>

                <h3>Next Steps:</h3>
                <ol>
                    <li>Review the task details and acceptance email from the company</li>
                    <li>Begin working on the task according to the company's requirements</li>
                    <li>Submit your completed work through the platform</li>
                    <li>Payment will be processed once the company approves your submission</li>
                </ol>

                <p><strong>Note:</strong> Payment funds are held securely in escrow until the company approves your work.</p>

                <p>
                    <a href="{current_app.config.get('APP_URL', 'http://localhost:5000')}/student/tasks" 
                       style="background-color: #0066cc; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; display: inline-block;">
                        View Your Tasks
                    </a>
                </p>

                <p>Best regards,<br>STEP Platform Team</p>

            </div>
        </body>
    </html>
    """

    text_body = f"""
    Congratulations! Your Application was Accepted!

    Task: {task.title}
    Company: {task.company.name}
    Payment Type: {task.payment_type.capitalize()}
    Payment Amount: €{task.fixed_price if task.fixed_price else 'TBD'}

    View your task at: {current_app.config.get('APP_URL', 'http://localhost:5000')}/student/tasks
    """

    return send_email(
        recipient_email=student.email,
        subject=subject,
        html_body=html_body,
        text_body=text_body
    )


def send_application_rejected_notification(application, task, student, reason: str = ""):
    """
    APPLICATION REJECTED NOTIFICATION
    ==================================
    Sent to student when company rejects their application.

    Args:
        application: Application object that was rejected
        task: Task object for the application
        student: User object (student) receiving rejection
        reason (str, optional): Company's reason for rejection

    Returns:
        bool: Success status of email send

    Use Case:
        Called when company marks application as rejected. Encourages student
        to apply to other tasks or improve skills.
    """
    subject = f"Application Status Update - {task.title}"

    html_body = f"""
    <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; border: 1px solid #ddd; padding: 20px;">

                <h2 style="color: #ff6b6b;">Application Update</h2>

                <p>Hi {student.name},</p>

                <p>Thank you for applying to the following task:</p>

                <div style="background-color: #f5f5f5; padding: 15px; border-radius: 5px; margin: 20px 0;">
                    <h3 style="margin-top: 0; color: #333;">{task.title}</h3>
                    <p><strong>Company:</strong> {task.company.name}</p>
                    <p>Unfortunately, your application was not selected at this time.</p>
                    {f'<p><strong>Company Feedback:</strong> {reason}</p>' if reason else ''}
                </div>

                <p>Don't be discouraged! Keep developing your skills and apply to other tasks on our platform. 
                Each application is a learning opportunity.</p>

                <p>
                    <a href="{current_app.config.get('APP_URL', 'http://localhost:5000')}/browse-tasks" 
                       style="background-color: #0066cc; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; display: inline-block;">
                        Browse More Tasks
                    </a>
                </p>

                <p>Best regards,<br>STEP Platform Team</p>

            </div>
        </body>
    </html>
    """

    text_body = f"""
    Application Status Update

    Task: {task.title}
    Company: {task.company.name}
    Status: Not Selected

    {f'Feedback: {reason}' if reason else ''}

    Browse more tasks at: {current_app.config.get('APP_URL', 'http://localhost:5000')}/browse-tasks
    """

    return send_email(
        recipient_email=student.email,
        subject=subject,
        html_body=html_body,
        text_body=text_body
    )


def send_work_submitted_notification(application, task, company):
    """
    WORK SUBMITTED NOTIFICATION
    ============================
    Sent to company when student submits completed work.

    Args:
        application: Application object with submitted work
        task: Task object
        company: User object (company) to notify

    Returns:
        bool: Success status of email send

    Use Case:
        Called when student marks work as complete and submits for review.
        Company needs to review and approve/request changes.
    """
    subject = f"Work Submitted for Review - {task.title}"

    html_body = f"""
    <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; border: 1px solid #ddd; padding: 20px;">

                <h2 style="color: #0066cc;">Work Submitted for Review</h2>

                <p>Hi {company.name},</p>

                <p>The student working on your task has submitted their work for review:</p>

                <div style="background-color: #f5f5f5; padding: 15px; border-radius: 5px; margin: 20px 0;">
                    <h3 style="margin-top: 0; color: #333;">{task.title}</h3>
                    <p><strong>Student:</strong> {application.student.name}</p>
                    <p><strong>Submitted:</strong> {application.submitted_at.strftime('%Y-%m-%d %H:%M') if application.submitted_at else 'N/A'}</p>
                </div>

                <p>Please review the submitted work and either approve it or request changes from the student.</p>

                <p>
                    <a href="{current_app.config.get('APP_URL', 'http://localhost:5000')}/company/applicants" 
                       style="background-color: #0066cc; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; display: inline-block;">
                        Review Submission
                    </a>
                </p>

                <p><strong>Important:</strong> Payment is held in escrow until you approve the work.</p>

                <p>Best regards,<br>STEP Platform Team</p>

            </div>
        </body>
    </html>
    """

    text_body = f"""
    Work Submitted for Review

    Task: {task.title}
    Student: {application.student.name}
    Submitted: {application.submitted_at.strftime('%Y-%m-%d %H:%M') if application.submitted_at else 'N/A'}

    Review the submission at: {current_app.config.get('APP_URL', 'http://localhost:5000')}/company/applicants
    """

    return send_email(
        recipient_email=company.email,
        subject=subject,
        html_body=html_body,
        text_body=text_body
    )


def send_work_approved_notification(application, task, student):
    """
    WORK APPROVED NOTIFICATION
    ===========================
    Sent to student when company approves their submitted work.

    Args:
        application: Application object with approved work
        task: Task object
        student: User object (student) receiving approval

    Returns:
        bool: Success status of email send

    Use Case:
        Called when company marks work as approved. Student's payment
        is released from escrow. Includes payment details.
    """
    subject = f"Your Work Has Been Approved! - {task.title}"

    # Calculate payment amounts
    platform_fee = task.fixed_price * 0.10 if task.fixed_price else 0
    student_payment = (task.fixed_price or 0) - platform_fee

    html_body = f"""
    <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; border: 1px solid #ddd; padding: 20px;">

                <h2 style="color: #28a745;">Excellent Work! 👏</h2>

                <p>Hi {student.name},</p>

                <p>Great news! Your submitted work has been approved by {task.company.name}.</p>

                <div style="background-color: #f5f5f5; padding: 15px; border-radius: 5px; margin: 20px 0;">
                    <h3 style="margin-top: 0; color: #333;">{task.title}</h3>
                    <p><strong>Completed:</strong> {application.completed_at.strftime('%Y-%m-%d %H:%M') if application.completed_at else 'N/A'}</p>
                    <p><strong>Status:</strong> <span style="color: #28a745; font-weight: bold;">APPROVED</span></p>
                </div>

                <h3>Payment Details:</h3>
                <table style="width: 100%; border-collapse: collapse;">
                    <tr style="border-bottom: 1px solid #ddd;">
                        <td style="padding: 10px; text-align: left;"><strong>Total Amount:</strong></td>
                        <td style="padding: 10px; text-align: right;">€{task.fixed_price:.2f}</td>
                    </tr>
                    <tr style="border-bottom: 1px solid #ddd;">
                        <td style="padding: 10px; text-align: left;"><strong>Platform Fee (10%):</strong></td>
                        <td style="padding: 10px; text-align: right;">-€{platform_fee:.2f}</td>
                    </tr>
                    <tr style="background-color: #f0f0f0;">
                        <td style="padding: 10px; text-align: left;"><strong>Amount to Your Account:</strong></td>
                        <td style="padding: 10px; text-align: right; font-weight: bold; color: #28a745;">€{student_payment:.2f}</td>
                    </tr>
                </table>

                <p style="margin-top: 20px;">Your payment has been released from escrow and processed. 
                You should see the funds in your account within 1-2 business days.</p>

                <p>
                    <a href="{current_app.config.get('APP_URL', 'http://localhost:5000')}/student/dashboard" 
                       style="background-color: #0066cc; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; display: inline-block;">
                        View Dashboard
                    </a>
                </p>

                <p>Congratulations on another successful project!<br>STEP Platform Team</p>

            </div>
        </body>
    </html>
    """

    text_body = f"""
    Your Work Has Been Approved!

    Task: {task.title}
    Company: {task.company.name}
    Status: APPROVED

    Payment Details:
    Total Amount: €{task.fixed_price:.2f}
    Platform Fee: €{platform_fee:.2f}
    Your Amount: €{student_payment:.2f}

    View dashboard at: {current_app.config.get('APP_URL', 'http://localhost:5000')}/student/dashboard
    """

    return send_email(
        recipient_email=student.email,
        subject=subject,
        html_body=html_body,
        text_body=text_body
    )


def send_change_requested_notification(application, task, student, feedback: str = ""):
    """
    CHANGE REQUESTED NOTIFICATION
    =============================
    Sent to student when company requests changes to submitted work.

    Args:
        application: Application object with requested changes
        task: Task object
        student: User object (student) receiving request
        feedback (str, optional): Company's feedback on required changes

    Returns:
        bool: Success status of email send

    Use Case:
        Called when company marks work as needing changes. Student needs
        to revise and resubmit. Payment remains in escrow.
    """
    subject = f"Changes Requested - {task.title}"

    html_body = f"""
    <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; border: 1px solid #ddd; padding: 20px;">

                <h2 style="color: #ff9800;">Changes Requested</h2>

                <p>Hi {student.name},</p>

                <p>{task.company.name} has reviewed your work and would like you to make some changes:</p>

                <div style="background-color: #fff3cd; padding: 15px; border-radius: 5px; margin: 20px 0; border-left: 4px solid #ff9800;">
                    <h3 style="margin-top: 0; color: #333;">{task.title}</h3>
                    {f'<p><strong>Feedback:</strong></p><p>{feedback}</p>' if feedback else '<p>Please review the detailed feedback on the platform.</p>'}
                </div>

                <p>Please make the requested changes and resubmit your work. Your payment will be processed once 
                the company approves the revised work.</p>

                <p>
                    <a href="{current_app.config.get('APP_URL', 'http://localhost:5000')}/student/tasks" 
                       style="background-color: #0066cc; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; display: inline-block;">
                        Resubmit Work
                    </a>
                </p>

                <p>Best regards,<br>STEP Platform Team</p>

            </div>
        </body>
    </html>
    """

    text_body = f"""
    Changes Requested

    Task: {task.title}
    Company: {task.company.name}

    {f'Feedback:\n{feedback}' if feedback else 'Please review the detailed feedback on the platform.'}

    Resubmit your work at: {current_app.config.get('APP_URL', 'http://localhost:5000')}/student/tasks
    """

    return send_email(
        recipient_email=student.email,
        subject=subject,
        html_body=html_body,
        text_body=text_body
    )


def send_dispute_notification(dispute, admin_email: str):
    """
    DISPUTE FILED NOTIFICATION
    ===========================
    Sent to admin when a dispute is raised by user.

    Args:
        dispute: Dispute object that was created
        admin_email (str): Email address of admin to notify

    Returns:
        bool: Success status of email send

    Use Case:
        Called when student or company files a dispute. Admin needs to
        review and potentially intervene.
    """
    subject = f"New Dispute Reported - {dispute.raised_by_user.name}"

    html_body = f"""
    <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; border: 1px solid #ddd; padding: 20px;">

                <h2 style="color: #d32f2f;">New Dispute Filed</h2>

                <p>A new dispute has been filed and requires admin review:</p>

                <div style="background-color: #f5f5f5; padding: 15px; border-radius: 5px; margin: 20px 0;">
                    <p><strong>Reported by:</strong> {dispute.raised_by_user.name} ({dispute.raised_by_user.email})</p>
                    {f'<p><strong>Against:</strong> {dispute.against_user.name}</p>' if dispute.against_user else ''}
                    {f'<p><strong>Related Task:</strong> {dispute.task.title if dispute.task else "N/A"}</p>' if dispute.task else ''}
                    <p><strong>Issue:</strong></p>
                    <p>{dispute.message}</p>
                    <p><strong>Severity:</strong> {dispute.severity}/5</p>
                </div>

                <p>
                    <a href="{current_app.config.get('APP_URL', 'http://localhost:5000')}/admin/disputes" 
                       style="background-color: #d32f2f; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; display: inline-block;">
                        Review Dispute
                    </a>
                </p>

                <p>STEP Platform Admin System</p>

            </div>
        </body>
    </html>
    """

    return send_email(
        recipient_email=admin_email,
        subject=subject,
        html_body=html_body
    )