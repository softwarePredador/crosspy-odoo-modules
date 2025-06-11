# -*- coding: utf-8 -*-

from odoo import models, fields, api
from odoo.exceptions import UserError

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    is_client_sender = fields.Boolean(string="Client Sender")
    carrier_partner_id = fields.Many2one('res.partner', related='carrier_id.partner_id', readonly=True)

    @api.onchange('partner_id', 'partner_shipping_id')
    def _onchange_partner_shipping(self):
        if self.partner_id == self.partner_shipping_id:
            self.is_client_sender = False

    def action_open_delivery_wizard(self):
        res = super().action_open_delivery_wizard()
        res['context']['default_modalidade_frete'] = self.modalidade_frete
        return res