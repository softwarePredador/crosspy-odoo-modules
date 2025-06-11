{  
    "name": "Odoo Next - Enable tax calculations on Sale",
    "description": "Enable Tax Calculations",
    "version": "17.0.1.0.4",
    "category": "Localization",
    "author": "Code 137",
    'license': 'Other OSI approved licence',
    "website": "http://www.code137.com.br",
    "contributors": [
        "Fábio Luna <fabiocluna@hotmail.com>",
        "Felipe Paloschi <paloschi.eca@gmail.com>"
    ],
    "depends": [
        "sale",
        "delivery",
        "l10n_br_eletronic_document",
    ],
    'data': [
        'views/delivery_view.xml',
        'views/account_move_views.xml',
        'views/sale_order.xml',
        'views/product_pricelist.xml',
        'views/stock_picking_views.xml',
        'report/sale_report_templates.xml',
    ],
    "auto_install": True,
}
