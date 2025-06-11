{  # pylint: disable=C8101,C8103
    'name': 'Odoo Next - Enable tax calculations',
    'description': 'Enable Tax Calculations',
    'version': '17.0.1.0.1',
    'category': 'Localization',
    'author': 'Trustcode',
    'license': 'Other proprietary',
    'website': 'http://www.crosspy.com',
    'contributors': [
        'Danimar Ribeiro <danimaribeiro@gmail.com>',
        'Felipe Paloschi <paloschi.eca@gmail.com>',
        'Osvaldo Cruz <ocruz@crosspy.com'
    ],
    'depends': [
        'account',
        'l10n_br_base',
    ],
    'data': [
        'data/cron.xml',
        'data/product.xml',
        'data/account_payment_method_data.xml',
        'security/ir.model.access.csv',
        'views/fiscal_position.xml',
        'views/account_tax.xml',
        'views/product.xml',
        'views/base_account.xml',
        'views/account_move.xml',
        'views/account_move_line.xml',
        'views/res_company.xml',
        'wizard/account_move_reversal_view.xml',
        'wizard/payment_move_line.xml',
    ],
    'post_init_hook': 'post_init',
}
