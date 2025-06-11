# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models, tools
from odoo.tools import formatLang

class PurchaseBillUnion(models.Model):
    _inherit = 'purchase.bill.union'

    def _compute_display_name(self):
        if not self._context.get('show_value_amount', False):
            super()._compute_display_name()
            return
        for doc in self:
            name = doc.name or ''
            if doc.reference:
                name += ' - ' + doc.reference
            amount = doc.amount
            name += ': ' + formatLang(self.env, amount, monetary=True, currency_obj=doc.currency_id)
            doc.display_name = name
