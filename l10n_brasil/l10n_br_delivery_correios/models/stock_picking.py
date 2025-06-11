# -*- coding: utf-8 -*-

import json
import math
import time
from ast import literal_eval
from datetime import date, timedelta
from collections import defaultdict

from .correios_request import CorreiosProvider
from odoo import SUPERUSER_ID, _, api, Command, fields, models
from odoo.addons.stock.models.stock_move import PROCUREMENT_PRIORITIES
from odoo.addons.web.controllers.utils import clean_action
from odoo.exceptions import UserError, ValidationError
from odoo.osv import expression
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT, format_datetime, format_date, groupby
from odoo.tools.float_utils import float_compare, float_is_zero, float_round

import json

class Picking(models.Model):
    _inherit = "stock.picking"

    correios_orders = fields.Char(
        string="Correios Order(s)",
        copy=False, readonly=True,
        help="Store Correios order(s) in a (+) separated string, used in cancelling the order."
    )

    @api.depends('carrier_id')
    def _check_is_correios(self):
        for pick in self:
            pick.is_correios = pick.carrier_id.delivery_type == 'correios'

    def action_pre_postagem_control(self, txt=0):
        if any(carrier.delivery_type  != 'correios' for carrier in self.mapped('carrier_id')):
            raise ValidationError(_('All the carriers must be Correios'))
        
        return self._action_pre_postagem_control(txt)

    def _action_pre_postagem_control(self, txt=0):
        post_values = self._prepare_postagem_values(txt)
        return post_values

    def _prepare_postagem_values(self, txt=0):
        picks = self.filtered(lambda x: x.carrier_id.delivery_type == 'correios')
        res = picks[0].carrier_id.postagem_control(picks)
        if txt:
            res = json.dumps(res)
        return res
    
    def action_correios_get_label(self):
        picks = self.filtered(lambda x: x.carrier_id.delivery_type == 'correios')
        res = picks[0].carrier_id.correios_get_return_label(picks)
        return res
        
    def do_print_simple_eletronic_document(self):
        # self.write({'printed': True})
        sale_line_ids = self.move_ids.mapped('sale_line_id')
        invoices = sale_line_ids.mapped('invoice_lines').mapped('move_id')
        # invoices = self.move_ids.mapped('sale_line_id.invoice_lines').mapped('move_id')

        edoc = self.env['eletronic.document'].search(
            [('move_id', 'in', invoices.ids)], limit=1)
        if not edoc:
            raise UserError(_('Não Existe Nota Fiscal para esta entrega...'))
        return self.env.ref('l10n_br_delivery_correios.action_report_simple_edoc').report_action(edoc)
    