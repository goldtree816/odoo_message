from odoo import models, fields

class PurchasedNumber(models.Model):
    _name = 'whatsapp.purchased_number'
    _description = 'Purchased WhatsApp Numbers'

    number = fields.Char('Phone Number', required=True)
    sid = fields.Char('Twilio SID')
    friendly_name = fields.Char('Friendly Name')
    status = fields.Selection([
        ('active', 'Active'),
        ('pending', 'Pending'),
        ('failed', 'Failed'),
    ], default='pending')
    purchase_date = fields.Datetime(default=fields.Datetime.now)