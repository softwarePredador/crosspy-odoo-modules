# -*- conding: utf-8 -*-

from odoo import fields, models, _

import logging
_logger = logging.getLogger(__name__)

class BankSlip(models.Model):
    _inherit = 'res.bank'

    logo = fields.Image('Logo', help='Bank logo.', copy=False, attachment=True, max_width=1024, max_height=512)
    has_slip = fields.Boolean('Bank has slip', default=False)