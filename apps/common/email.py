import logging

from django.conf import settings
from django.core.mail import EmailMessage

logger = logging.getLogger(__name__)


def _from_email():
    return getattr(settings, "DEFAULT_FROM_EMAIL", None) or getattr(settings, "EMAIL_HOST_USER", None)


def send_attendance_email(*, subject, message, recipient_email, cc=None, reply_to=None):
    if not recipient_email:
        logger.warning("Skipping email '%s': no recipient.", subject)
        return False

    from_email = _from_email()
    if not from_email:
        logger.warning("Skipping email '%s': DEFAULT_FROM_EMAIL not configured.", subject)
        return False

    cc_list = [address.strip() for address in (cc or []) if address and str(address).strip()]
    reply_to_list = [address.strip() for address in (reply_to or []) if address and str(address).strip()]

    try:
        email = EmailMessage(
            subject=subject,
            body=message,
            from_email=from_email,
            to=[recipient_email],
            cc=cc_list,
            reply_to=reply_to_list,
        )
        email.send(fail_silently=False)
        return True
    except Exception:
        logger.exception(
            "Failed to send email '%s' to %s (cc=%s)",
            subject,
            recipient_email,
            ", ".join(cc_list) or "(none)",
        )
        return False
