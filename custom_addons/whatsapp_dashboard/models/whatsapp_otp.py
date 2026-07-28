import random
import string
from datetime import timedelta

from odoo import models, fields, api


class WhatsAppSubaccountOTP(models.Model):
    """One-Time-Password records used to verify a user's email before a
    Twilio subaccount is created.

    Flow:
      1. Frontend calls generate_and_send(email) -> a 6-digit code is
         created and emailed out. Any previous unverified codes for the
         same email/purpose are discarded first.
      2. Frontend calls verify(email, code) when the user submits the
         "Create Subaccount" form. If valid, the code is consumed
         (one-time use) and the subaccount can then be created.
    """
    _name = 'whatsapp.subaccount.otp'
    _description = 'WhatsApp Subaccount Creation OTP'
    _order = 'create_date desc'

    email = fields.Char('Email', required=True, index=True)
    code = fields.Char('OTP Code', required=True)
    purpose = fields.Char('Purpose', default='subaccount_create')
    expires_at = fields.Datetime('Expires At', required=True)
    verified = fields.Boolean('Verified', default=False)
    attempts = fields.Integer('Attempts', default=0)

    MAX_ATTEMPTS = 5
    OTP_VALID_MINUTES = 5
    OTP_LENGTH = 6

    @api.model
    def _generate_code(self):
        return ''.join(random.choices(string.digits, k=self.OTP_LENGTH))

    @api.model
    def generate_and_send(self, email, purpose='subaccount_create'):
        """Create a new OTP, invalidate old unverified ones for this email,
        email it out, and return the record."""
        email = (email or '').strip().lower()
        if not email:
            raise ValueError('Email is required to send an OTP.')

        # Invalidate any previous, still-unverified OTPs for this email/purpose
        stale = self.sudo().search([
            ('email', '=', email),
            ('purpose', '=', purpose),
            ('verified', '=', False),
        ])
        stale.unlink()

        code = self._generate_code()
        expires_at = fields.Datetime.now() + timedelta(minutes=self.OTP_VALID_MINUTES)
        otp = self.sudo().create({
            'email': email,
            'code': code,
            'purpose': purpose,
            'expires_at': expires_at,
        })
        otp._send_email(code)
        return otp

    def _send_email(self, code):
        self.ensure_one()
        body = (
            "<div style='font-family:Arial,sans-serif'>"
            "<p>Your verification code is:</p>"
            f"<h2 style='letter-spacing:4px'>{code}</h2>"
            f"<p>This code will expire in {self.OTP_VALID_MINUTES} minutes. "
            "If you did not request this, you can safely ignore this email.</p>"
            "</div>"
        )
        mail = self.env['mail.mail'].sudo().create({
            'subject': 'Your WhatsApp Dashboard Verification Code',
            'body_html': body,
            'email_to': self.email,
            'auto_delete': True,
        })
        mail.send()

    @api.model
    def verify(self, email, code, purpose='subaccount_create'):
        """Validate the code for this email/purpose, consuming it on success.
        Raises ValueError with a user-facing message on any failure."""
        email = (email or '').strip().lower()
        code = (code or '').strip()

        if not code:
            raise ValueError('Please enter the OTP code sent to your email.')

        otp = self.sudo().search([
            ('email', '=', email),
            ('purpose', '=', purpose),
            ('verified', '=', False),
        ], order='create_date desc', limit=1)

        if not otp:
            raise ValueError('No OTP found for this email. Please request a new code.')

        if otp.attempts >= self.MAX_ATTEMPTS:
            otp.unlink()
            raise ValueError('Too many incorrect attempts. Please request a new code.')

        if fields.Datetime.now() > otp.expires_at:
            otp.unlink()
            raise ValueError('OTP has expired. Please request a new code.')

        if otp.code != code:
            otp.attempts += 1
            raise ValueError('Incorrect OTP code.')

        # Valid — mark verified then remove (one-time use only)
        otp.write({'verified': True})
        otp.unlink()
        return True
