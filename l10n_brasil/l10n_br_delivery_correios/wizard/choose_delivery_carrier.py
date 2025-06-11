# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ChooseDeliveryCarrier(models.TransientModel):
    _inherit = 'choose.delivery.carrier'

    modalidade_frete = fields.Selection(
        [('0', '0 - Contratação do Frete por conta do Remetente (CIF)'),
         ('1', '1 - Contratação do Frete por conta do Destinatário (FOB)'),
         ('2', '2 - Contratação do Frete por conta de Terceiros'),
         ('3', '3 - Transporte Próprio por conta do Remetente'),
         ('4', '4 - Transporte Próprio por conta do Destinatário'),
         ('9', '9 - Sem Ocorrência de Transporte')], 
        required=True,
        company_dependent=True, 
        string="Fleet mode", 
        help="Default fleet mode method used in sales orders.")


    def button_confirm(self):
        res = super().button_confirm()

        if self.order_id.is_all_service or not self.order_id.delivery_set:
            self.order_id.modalidade_frete = '9'
        else:
            self.order_id.modalidade_frete = self.modalidade_frete
        if self.order_id.modalidade_frete == '9' and self.carrier_id:
            raise UserError(_('Fleet mode must be set'))
        return res

    @api.onchange('carrier_id')
    def _onchange_carrier_id(self):
        if self.carrier_id and self.carrier_id.modalidade_frete:
            self.modalidade_frete = self.carrier_id.modalidade_frete
            self.update_price()