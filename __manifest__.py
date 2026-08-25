{
    'name': 'POS Preparation Direct Print',
    'version': '18.0.1.1.0',
    'category': 'Point of Sale',
    'summary': 'Direct ESC/POS printing for kitchen/bar preparation tickets',
    'description': """
        Print preparation tickets directly to network ESC/POS printers.
        No agent required - prints directly from Odoo server to printers on the same network.
    """,
    'author': 'Yousif Shakir',
    'website': 'https://donialink.com',
    'depends': ['point_of_sale', 'pos_restaurant'],
    'data': [
        'security/ir.model.access.csv',
        'data/sequences.xml',
        'views/pos_preparation_printer_views.xml',
        'views/pos_category_views.xml',
        'views/pos_config_views.xml',
        'views/pos_order_views.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'pos_preparation_direct/static/src/js/preparation_printing.js',
        ],
    },
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
