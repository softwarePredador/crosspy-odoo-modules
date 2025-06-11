# -*- conding: utf-8 -*-
from odoo import _, api, fields, models
import base64
import logging
_logger = logging.getLogger(__name__)

class Document(models.Model):
    _inherit = 'l10n_br_fiscal.document'

    def _get_mail_attachment(self):
        attachment_ids = super()._get_mail_attachment()
        # moves = self.env['account.move'].browse(self.move_ids.ids)
        bank_slips = self.sudo().env['bank.slip'].search([('invoice', 'in', self.move_ids.ids)])
        # _logger.warning('CrossPy - Creating mail with slips')
        for slip in bank_slips:
            # .sudo().filtered(lambda x: x.pdf)
            pdf_data = slip.pdf
            _logger.warning('CrossPy - Creating mail with slips, slip: %s' % pdf_data)
            attachment = self.env['ir.attachment'].create({
                'datas': pdf_data,
                'name': slip.pdf_name,
                'mimetype': 'application/pdf',
                'type': 'binary',
            })
            # _logger.warning('CrossPy - Adding slip attachment')

            attachment_ids.append(attachment.id)
        return attachment_ids
    # 
