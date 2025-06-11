
{  
    "name": "Odoo Brasil - Account Enterprise",
    "description": "Enable advanced tax management",
    "version": "17.0.1.0.0",
    "category": "Localization",
    "author": "Trustcode",
    "license": "Other OSI approved licence",
    "website": "http://www.trustcode.com,br",
    "contributors": [
        "Danimar Ribeiro <danimaribeiro@gmail.com>",
    ],
    "depends": [
        "l10n_br_account",
        "l10n_br_base",
        "l10n_br_eletronic_document",
        "l10n_br_sale",
        "l10n_br_purchase",
    ],
    "data": [
        "data/account_accountant.xml",
        'security/res_groups_data.xml',
        "views/account_fiscal_position.xml",
        "views/account_move.xml",
        "views/sale_order.xml",
        "views/purchase.xml",
    ],
}
