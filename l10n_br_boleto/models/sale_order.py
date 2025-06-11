# -*- coding: utf-8 -*-
from odoo import _, fields, models

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    has_bank_slip = fields.Boolean(copy=False, default=False, string='Bank Slip Payment Methode')

    def _prepare_invoice(self):
        """
        Add the has_bank_slip boolean to the dict values
        """
        values = super()._prepare_invoice()
        values.update({
            'has_bank_slip': self.has_bank_slip,
        })
        return values

