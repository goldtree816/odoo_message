from odoo import models, fields, api

class WhatsAppSubaccount(models.Model):
    _name = 'whatsapp.subaccount'
    _description = 'WhatsApp Subaccount'
    _order = 'create_date desc'

    name = fields.Char('Friendly Name', required=True)
    unique_name = fields.Char('Unique Name', required=True)
    email = fields.Char('Email')

    status = fields.Selection([
        ('active', 'Active'),
        ('suspended', 'Suspended'),
    ], default='active', string='Status')

    subaccount_type = fields.Selection([
        ('standard', 'Standard'),
        ('restricted', 'Restricted'),
    ], default='standard', string='Subaccount Type')

    voice_enabled = fields.Boolean('Voice', default=True)
    sms_enabled = fields.Boolean('SMS', default=True)
    mms_enabled = fields.Boolean('MMS', default=True)
    whatsapp_enabled = fields.Boolean('WhatsApp', default=True)

    phone_numbers_count = fields.Integer('Phone Numbers', default=0)
    sms_sent_this_month = fields.Integer('SMS Sent This Month', default=0)
    voice_minutes_this_month = fields.Float('Voice Minutes This Month', default=0.0)
    usage_cost_this_month = fields.Float('Usage Cost This Month', default=0.0)

    # ── Real Twilio credentials (set by controller after API call) ──
    sid = fields.Char('Twilio Account SID', readonly=True)
    auth_token = fields.Char('Twilio Auth Token', readonly=True)

    create_date = fields.Datetime('Created On', readonly=True)
    created_by = fields.Many2one('res.users', string='Created By',
                                 default=lambda self: self.env.user)

    _sql_constraints = [
        ('unique_name_uniq', 'unique(unique_name)', 'Unique Name must be unique!'),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        """
        Only auto-generate SID/auth_token if not provided
        (the controller will provide the REAL ones from Twilio).
        """
        for vals in vals_list:
            if not vals.get('sid'):
                vals['sid'] = self._generate_sid()
            if not vals.get('auth_token'):
                vals['auth_token'] = self._generate_auth_token()
        return super(WhatsAppSubaccount, self).create(vals_list)

    def _generate_sid(self):
        """Fallback — only used if Twilio call fails."""
        import uuid
        return 'AC' + str(uuid.uuid4()).replace('-', '')[:32]

    def _generate_auth_token(self):
        """Fallback — only used if Twilio call fails."""
        import secrets
        return secrets.token_hex(32)

    def get_subaccount_data(self):
        self.ensure_one()
        return {
            'id': self.id,
            'name': self.name,
            'unique_name': self.unique_name,
            'email': self.email or '',
            'status': self.status,
            'subaccount_type': self.subaccount_type,
            'sid': self.sid or '',
            'phone_numbers': self.phone_numbers_count,
            'sms_sent': self.sms_sent_this_month,
            'voice_minutes': self.voice_minutes_this_month,
            'usage_cost': self.usage_cost_this_month,
            'created_on': self.create_date.strftime('%b %d, %Y') if self.create_date else '',
            'created_time': self.create_date.strftime('%I:%M %p') if self.create_date else '',
            'capabilities': self._get_capabilities(),
            'initials': self._get_initials(),
            'color': self._get_avatar_color(),
        }

    def _get_capabilities(self):
        caps = []
        if self.voice_enabled: caps.append('Voice')
        if self.sms_enabled: caps.append('SMS')
        if self.mms_enabled: caps.append('MMS')
        if self.whatsapp_enabled: caps.append('WhatsApp')
        return ', '.join(caps)

    def _get_initials(self):
        name = self.name or 'SA'
        parts = name.split()
        if len(parts) >= 2:
            return (parts[0][0] + parts[1][0]).upper()
        elif parts:
            return (parts[0][:2]).upper()
        return 'SA'

    def _get_avatar_color(self):
        colors = ['#f97316', '#3b82f6', '#14b8a6', '#8b5cf6',
                  '#ef4444', '#22c55e', '#eab308', '#ec4899']
        if self.id:
            return colors[(self.id - 1) % len(colors)]
        return colors[0]