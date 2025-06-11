# -*- coding: utf-8 -*-

from odoo import api, fields, models
from odoo.osv import expression


class AccountPaymentMethod(models.Model):
    _inherit = "account.payment.method"

    @api.model
    def _get_payment_method_information(self):
        """
        Contains details about how to initialize a payment method with the code x.
        The contained info are:
            mode: Either unique if we only want one of them at a single time (payment providers for example)
                   or multi if we want the method on each journal fitting the domain.
            domain: The domain defining the eligible journals.
            currency_id: The id of the currency necessary on the journal (or company) for it to be eligible.
            country_id: The id of the country needed on the company for it to be eligible.
            hidden: If set to true, the method will not be automatically added to the journal,
                    and will not be selectable by the user.
        """
        res = super()._get_payment_method_information()
        res.update({
            'qr_br_pix': {'mode': 'unique', 'domain': [('type', 'in', ('bank',))]},
            'boleto_br': {'mode': 'unique', 'domain': [('type', 'in', ('bank', ))]},
            'out_credit_card': {'mode': 'multi', 'domain': [('type', 'in', ('bank',))]},
        })
        return res

