from odoo import models, fields
from datetime import timezone, timedelta
import re
import logging
import requests

_logger = logging.getLogger(__name__)

class WhatsAppMessage(models.Model):
    _name = 'whatsapp.message'
    _description = 'WhatsApp Message'
    _order = 'timestamp asc, id asc'

    # ── Fields ─────────────────────────────────────────────────────────────
    thread_id = fields.Many2one(
        'whatsapp.thread',
        string='Thread',
        required=True,
        ondelete='cascade',
        index=True,
    )
    body = fields.Text('Message Body')
    direction = fields.Selection([
        ('incoming', 'Incoming'),
        ('outgoing', 'Outgoing'),
    ], required=True, string='Direction')
    message_type = fields.Selection([
        ('external', 'External'),
        ('internal', 'Internal Note'),
    ], default='external', string='Message Type')
    status = fields.Selection([
        ('sent', 'Sent'),
        ('delivered', 'Delivered'),
        ('read', 'Read'),
    ], default='sent', string='Status')
    timestamp = fields.Datetime('Sent At', default=fields.Datetime.now, index=True)
    twilio_sid = fields.Char('Twilio Message SID', index=True)
    attachment_id = fields.Many2one('ir.attachment', string='Attachment')

    # ── Spam Detection Fields ───────────────────────────────────────────────
    is_spam = fields.Boolean('Spam', default=False, help='Marked as spam by automatic detection')
    spam_score = fields.Float('Spam Score', default=0.0, help='Score 0-1, higher = more likely spam')
    spam_reasons = fields.Text('Spam Reasons', help='Why this message was flagged')

    # ── Helpers ─────────────────────────────────────────────────────────────
    def _format_time(self):
        if not self.timestamp:
            return ''
        dt = self.timestamp.replace(tzinfo=timezone.utc)
        return dt.strftime('%I:%M %p').lstrip('0')

    # ── OpenAI Moderation ──────────────────────────────────────────────────
    def _check_openai_moderation(self, text):
        """Call OpenAI Moderation API and return a spam score (0-1)."""
        # Read API key from Odoo system parameters
        api_key = self.env['ir.config_parameter'].sudo().get_param('openai.api_key')
        if not api_key:
            return 0.0

        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        }
        data = {'input': text}
        try:
            resp = requests.post(
                'https://api.openai.com/v1/moderations',
                headers=headers,
                json=data,
                timeout=5,
            )
            if resp.status_code == 200:
                result = resp.json()
                scores = result['results'][0]['category_scores']
                # Weighted sum for a more accurate spam score
                weighted = (
                    scores.get('harassment', 0) * 1.0 +
                    scores.get('hate', 0) * 1.0 +
                    scores.get('self-harm', 0) * 1.2 +
                    scores.get('sexual', 0) * 0.8 +
                    scores.get('violence', 0) * 1.0
                )
                total = min(weighted / 2.0, 1.0)
                return total
            else:
                _logger.warning("OpenAI moderation error: %s", resp.text)
                return 0.0
        except Exception as e:
            _logger.error("OpenAI moderation exception: %s", e)
            return 0.0

    # ── Main Spam Detection ────────────────────────────────────────────────
    def _detect_spam(self):
        """
        Analyse message content and return a spam score (0-1) and list of reasons.
        Combines local rules + OpenAI Moderation.
        """
        self.ensure_one()
        score = 0.0
        reasons = []
        body = self.body or ''

        # 1. Count URLs
        urls = re.findall(r'https?://[^\s]+', body)
        if len(urls) > 2:
            score += 0.3
            reasons.append(f'Excessive URLs ({len(urls)})')

        # 2. Known spam keywords
        spam_keywords = ['viagra', 'lottery', 'bitcoin', 'claim', 'prize', 'free money',
                         'click here', 'urgent', 'limited time', 'exclusive offer',
                         'crypto', 'earn', 'income', 'winner', 'cash', 'bonus']
        found = [kw for kw in spam_keywords if kw in body.lower()]
        if found:
            score += 0.4
            reasons.append(f'Spam keywords: {", ".join(found)}')

        # 3. All caps percentage
        caps = sum(1 for c in body if c.isupper())
        if len(body) > 10 and (caps / len(body)) > 0.7:
            score += 0.2
            reasons.append('Excessive capitalisation')

        # 4. Excessive punctuation
        punct = sum(1 for c in body if c in '!?')
        if len(body) > 10 and (punct / len(body)) > 0.3:
            score += 0.1
            reasons.append('Too many punctuation marks')

        # 5. Frequency from same sender (last 5 minutes)
        recent = self.search([
            ('thread_id.phone', '=', self.thread_id.phone),
            ('timestamp', '>', fields.Datetime.now() - timedelta(minutes=5)),
            ('id', '!=', self.id)
        ], limit=10)
        if len(recent) >= 5:
            score += 0.2
            reasons.append('High frequency of messages from this sender')

        # 6. OpenAI Moderation (external API)
        openai_score = self._check_openai_moderation(body)
        if openai_score > 0.4:
            score += openai_score * 0.3
            reasons.append(f'Flagged by OpenAI Moderation (score: {openai_score:.2f})')

        # Cap at 1.0 and determine spam
        score = min(score, 1.0)
        is_spam = score >= 0.3

        return {
            'is_spam': is_spam,
            'spam_score': round(score, 2),
            'spam_reasons': ', '.join(reasons) if reasons else 'No spam indicators'
        }

    # ── JSON for Frontend ──────────────────────────────────────────────────
    def get_message_data(self):
        """Dict suitable for JSON serialisation to the OWL frontend."""
        self.ensure_one()
        data = {
            'id': self.id,
            'body': self.body,
            'time': self._format_time(),
            'direction': self.direction,
            'type': self.message_type,
            'status': self.status,
            'is_spam': self.is_spam,
            'spam_score': self.spam_score,
        }
        if self.attachment_id:
            data['attachment'] = {
                'id': self.attachment_id.id,
                'name': self.attachment_id.name,
                'url': f"/web/content/{self.attachment_id.id}?download=true"
            }
        return data
