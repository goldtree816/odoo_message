{
    'name': 'WhatsApp Dashboard',
    'version': '19.0.1.0.0',
    'category': 'Discuss',
    'summary': 'WhatsApp Web-style dashboard with Twilio integration for Odoo 19',
    'author': 'Custom Addons',
    'depends': ['web', 'mail', 'base'],
    'data': [
        'security/ir.model.access.csv',
        'data/demo_data.xml',
        'views/whatsapp_dashboard_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'whatsapp_dashboard/static/src/css/layout.css',
            'whatsapp_dashboard/static/src/css/components.css',
            'whatsapp_dashboard/static/src/css/modals.css',
            'whatsapp_dashboard/static/src/xml/whatsapp_dashboard.xml',
            'whatsapp_dashboard/static/src/js/whatsapp_dashboard.js',
        ],
    },
    'installable': True,
    'application': True,
    'auto_install': False,
    'license': 'LGPL-3',
    'sequence': 200,
}