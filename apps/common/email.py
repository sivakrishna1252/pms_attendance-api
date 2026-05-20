import logging

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


def send_attendance_email(*, subject, message, recipient_email):
    if not recipient_email:
        logger.warning("Skipping email '%s': no recipient.", subject)
        return False

    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None) or getattr(settings, "EMAIL_HOST_USER", None)
    if not from_email:
        logger.warning("Skipping email '%s': DEFAULT_FROM_EMAIL not configured.", subject)
        return False

    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=from_email,
            recipient_list=[recipient_email],
            fail_silently=False,
        )
        return True
    except Exception:
        logger.exception("Failed to send email '%s' to %s", subject, recipient_email)
        return False
