from odoo import models, fields
from datetime import timezone


class WhatsAppMessage(models.Model):
    _name = 'whatsapp.message'
    _description = 'WhatsApp Message'
    _order = 'timestamp asc, id asc'

    # ── Fields ─────────────────────────────────────────────────────────────
    thread_id    = fields.Many2one(
        'whatsapp.thread',
        string='Thread',
        required=True,
        ondelete='cascade',
        index=True,
    )
    body         = fields.Text('Message Body', required=True)
    direction    = fields.Selection([
        ('incoming', 'Incoming'),
        ('outgoing', 'Outgoing'),
    ], required=True, string='Direction')
    message_type = fields.Selection([
        ('external', 'External'),
        ('internal', 'Internal Note'),
    ], default='external', string='Message Type')
    status       = fields.Selection([
        ('sent',      'Sent'),
        ('delivered', 'Delivered'),
        ('read',      'Read'),
    ], default='sent', string='Status')
    timestamp    = fields.Datetime('Sent At', default=fields.Datetime.now, index=True)
    twilio_sid   = fields.Char('Twilio Message SID', index=True)   # for dedup
    attachment_id = fields.Many2one('ir.attachment', string='Attachment')   # <-- NEW

    # ── Helpers ─────────────────────────────────────────────────────────────
    def _format_time(self):
        if not self.timestamp:
            return ''
        dt = self.timestamp.replace(tzinfo=timezone.utc)
        return dt.strftime('%I:%M %p').lstrip('0')

    def get_message_data(self):
        """Dict suitable for JSON serialisation to the OWL frontend."""
        self.ensure_one()
        data = {
            'id':        self.id,
            'body':      self.body,
            'time':      self._format_time(),
            'direction': self.direction,
            'type':      self.message_type,
            'status':    self.status,
        }
        if self.attachment_id:
            data['attachment'] = {
                'id':   self.attachment_id.id,
                'name': self.attachment_id.name,
                'url':  f"/web/content/{self.attachment_id.id}?download=true"
            }
        return data