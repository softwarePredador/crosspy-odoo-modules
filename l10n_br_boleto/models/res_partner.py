# -*- conding: utf-8 -*-

from odoo import api, fields, models, _

import logging
_logger = logging.getLogger(__name__)

class Partner(models.Model):
    _inherit = 'res.partner'

    vat = fields.Char(inverse='_inverse_vat')

    @api.onchange('vat')
    def _inverse_vat(self):
        self.cnpj_cpf = self.vat
