import logging

from odoo import fields, models
from odoo.tools.translate import _
import logging

_logger = logging.getLogger(__name__)


class ResCompany(models.Model):
    _inherit = "res.company"

    bank_account_id = fields.Many2one('res.partner.bank', string="Bank Slip Account")
    next_bank_number = fields.Integer(string="Next Bank Slip Number")
    bank_contract = fields.Char(string='Bank Contract (size=40)')    
    bank_transmision_code = fields.Char(string='Código de Transmissão (size=15)')
    interest_account_id = fields.Many2one('account.account', string="Interest Account")