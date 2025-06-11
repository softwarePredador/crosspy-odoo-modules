# -*- coding: utf-8 -*-
import base64
import logging

_logger = logging.getLogger(__name__)

from odoo import api, fields, models, _

class EletronicDocument(models.Model):
    _inherit = 'eletronic.document'

    def _find_attachment_ids_email(self):
        attms = super()._find_attachment_ids_email()
        if not self.move_id.has_bank_slip:
            return attms
        new_slip = self.move_id.bank_slip_ids.filtered(lambda x: not x.pdf)
        new_slip.action_generate_boleto()
        for slip in self.move_id.bank_slip_ids:
            pdf_data = slip.pdf
            _logger.warning('CrossPy %s' % pdf_data[:20])
            attms.append(self.env['ir.attachment'].create(
                {
                'name': slip.pdf_name,
                'type': 'binary',
                'datas': slip.pdf,
                'res_model': slip._name,
                'res_id': slip.id,
                'mimetype': 'application/pdf',
                }).id)

        return attms
