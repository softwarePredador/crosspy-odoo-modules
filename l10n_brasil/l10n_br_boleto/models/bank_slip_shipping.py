import base64
import datetime
import json
import logging
import re

import requests
from odoo import api, fields, models, _

from odoo.exceptions import UserError, ValidationError
from .boleto import format_cnpj_cpf

_logger = logging.getLogger(__name__)

# try:
#     from erpbrasil.base import misc
#     from erpbrasil.base.fiscal import cnpj_cpf, ie
# except ImportError:
#     _logger.error("Biblioteca erpbrasil.base não instalada")

reg_struct = [
    ('0', ' ', 'codigo_banco', 0, 3, 'A'),
    ('0', ' ', 'lot_return_number', 3, 7, 'N'),
    ('0', ' ', 'tipo_registro', 7, 8, 'A'),
    ('0', ' ', 'tipo_inscripcao', 16, 17, 'N'),
    ('0', ' ', 'cnpj_cpf', 17, 32, 'A'),
    ('0', ' ', 'agencia', 32, 36, 'N'),
    ('0', ' ', 'dv_agencia', 36, 37, 'N'),
    ('0', ' ', 'bank_account', 37, 46, 'N'),
    ('0', ' ', 'dv_account', 46, 47, 'N'),
    ('0', ' ', 'code_benef', 52, 61, 'N'),
    ('0', ' ', 'partner_name', 72, 102, 'A'),
    ('0', ' ', 'bank_name', 102, 132, 'A'),
    ('0', ' ', 'remesa_retorno', 142, 143, 'N'),
    ('0', ' ', 'file_date', 143, 151, 'D'),
    ('0', ' ', 'file_sequence', 157, 163, 'N'),
    ('0', ' ', 'file_version', 163, 166, 'N'), # Current must be 040
    ('1', 'T', 'codigo_banco', 0, 3, 'A'),
    ('1', 'T', 'lot_return_number', 3, 7, 'N'),
    ('1', 'T', 'tipo_registro', 7, 8, 'N'),
    ('1', 'T', 'tipo_servicio', 8, 9, 'N'),
    ('1', 'T', 'file_version', 13, 16, 'N'), # Current must be 040 / # Version confirm
    ('1', 'T', 'tipo_inscripcao', 17, 18, 'N'),
    ('1', 'T', 'cnpj_cpf', 18, 33, 'A'),
    ('1', 'T', 'benef_bank_agency', 53, 57, 'N'),
    ('1', 'T', 'dv_benef_bank_agency', 57, 58, 'N'),
    ('1', 'T', 'benef_bank_account', 58, 67, 'N'),
    ('1', 'T', 'dv_benef_bank_account', 67, 68, 'N'),
    ('1', 'T', 'benef_name', 73, 103, 'N'),
    ('1', 'T', 'return_number', 183, 191, 'N'),
    ('1', 'T', 'write_date', 191, 199, 'D'),

    ('3', 'T', 'codigo_banco', 0, 3, 'A'),
    ('3', 'T', 'lot_return_number', 3, 7, 'N'),
    ('3', 'T', 'tipo_registro', 7, 8, 'A'),
    ('3', 'T', 'seq_lot_number', 8, 13, 'N'),
    ('3', 'T', 'code_line_registro', 13, 14, 'N'),
    ('3', 'T', 'move_code', 15, 17, 'A'),
    ('3', 'T', 'benef_bank_agency', 17, 21, 'N'),
    ('3', 'T', 'dv_benef_bank_agency', 21, 22, 'N'),
    ('3', 'T', 'benef_bank_account', 22, 31, 'N'),
    ('3', 'T', 'dv_benef_bank_account', 31, 31, 'N'),
    ('3', 'T', 'nosso_numero', 40, 53, 'N'),
    ('3', 'T', 'carteira_code', 53, 54, 'N'),
    ('3', 'T', 'invoice_line_number', 54, 69, 'A'),
    ('3', 'T', 'date_maturity', 69, 77, 'D'),
    ('3', 'T', 'value', 77, 92, 'N', 2),           # 2 decimais sem separador
    ('3', 'T', 'nro_bank_cobro', 92, 95, 'N'),
    ('3', 'T', 'cobro_bank_agency', 95, 99, 'N'),
    ('3', 'T', 'cobro_dv_bank_agency', 99, 100, 'N'),
    ('3', 'T', 'cobro_number', 100, 125, 'N'),
    ('3', 'T', 'currency', 125, 127, 'N'),
    ('3', 'T', 'cobro_tipo_inscripcao', 127, 128, 'N'),
    ('3', 'T', 'cobro_cnpj_cpf', 128, 143, 'A'),
    ('3', 'T', 'cobro_name', 143, 183, 'N'),
    ('3', 'T', 'cobro_bank_account', 183, 193, 'N'),
    ('3', 'T', 'tarifa_value', 193, 208, 'N'),
    ('3', 'T', 'id_1', 208, 210, 'N'),
    ('3', 'T', 'id_2', 210, 212, 'N'),
    ('3', 'T', 'id_3', 212, 214, 'N'),
    ('3', 'T', 'id_4', 214, 216, 'N'),
    ('3', 'T', 'id_5', 216, 218, 'N'),

    ('3', 'U', 'codigo_banco', 0, 3, 'A'),
    ('3', 'U', 'lot_return_number', 3, 7, 'N'),
    ('3', 'U', 'tipo_registro', 7, 8, 'A'),
    ('3', 'U', 'seq_lot_number', 8, 13, 'N'),
    ('3', 'U', 'code_line_registro', 13, 14, 'N'),
    ('3', 'U', 'move_code', 15, 17, 'A'),
    ('3', 'U', 'juros_multa', 17, 32, 'N', 2),       # 2 casas decimais
    ('3', 'U', 'discount_value', 32, 47, 'N', 2),    # 2 casas decimais
    ('3', 'U', 'abatimento', 47, 62, 'N', 2),
    ('3', 'U', 'IOF_recolhido', 62, 77, 'N', 2),
    ('3', 'U', 'valor_pago', 77, 92, 'N', 2),
    ('3', 'U', 'valor_credito', 92, 107, 'N', 2),
    ('3', 'U', 'valor_despesas', 107, 122, 'N', 2),
    ('3', 'U', 'valor_outros_creditos', 122, 137, 'N', 2),
    ('3', 'U', 'date', 137, 145, 'D'),
    ('3', 'U', 'credit_date', 145, 153, 'D'),
    ('3', 'U', 'pagador_code', 153, 157, 'N'),
    ('3', 'U', 'pagador_date', 157, 165, 'D'),
    ('3', 'U', 'pagador_value', 165, 180, 'N'),
    ('3', 'U', 'pagador_complemento', 180, 210, 'N'),
    ('3', 'U', 'clearing_code_bank', 210, 213, 'N'),
                ]


class BankSlipShipping(models.Model):
    _name = 'bank.slip.shipping'
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Bank Slip Shipping"
    _check_company_auto = True

    name = fields.Char(string="Nome")
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        required=True,
        ondelete="restrict",
        default=lambda self: self.env.company,
    )

    company_partner_id = fields.Many2one(string='Company Partner', related='company_id.partner_id')

    bank_account_id = fields.Many2one('res.partner.bank',
        string="Bank Account",
        ondelete='restrict', copy=False,
        check_company=True,
        domain="[('partner_id','=', company_partner_id), '|', ('company_id', '=', False), ('company_id', '=', company_id)]")

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("sent", "Sent"),
            ("cancel", "Cancel"),
            ("processed", "Processed"),
        ],
        string="State",
        tracking=True,
    )

    file_date = fields.Date(string="File Date on:", tracking=True)
    create_file_date = fields.Date(string="Create File Date on:", tracking=True)
    process_date = fields.Date(string="Processed on:", tracking=True)

    slip_move_ids = fields.One2many('bank.slip.move', 'shipping_id', string='Shipping Slips', tracking=True)
    slip_ids = fields.Many2many('bank.slip', 'bank_slip_move', 'shipping_id', 'slip_id', string='Bank Slips')

    shipping_file_id = fields.Many2one(
        comodel_name="ir.attachment",
        string="Arquivo de Retorno",
        copy=False,
        readonly=True,
        tracking=True,
    )
    shipping_file = fields.Binary(
        string="Arquivo de Retorno", tracking=True, readonly=True)
 
    shipping_file_name = fields.Char(
        string="Nome do Arquivo de Retorno", tracking=True, readonly=True)
    
    shipping_type = fields.Selection([('remessa', 'Retorno'),
                                      ('retorno', 'Retorno')],
                                       string='Shipping Type',
                                       )
    _order = 'file_date desc, name desc'
    # _sql_constraints = [
    #     ('shipping_name_unique', 'unique (name, company_id)', 'O Arquivo de retorno deve ser único por empresa!'),
    # ]

    @api.onchange('shipping_file')
    def _onchange_shipping_file(self):
        if self.shipping_file:
            file_decoded = base64.b64decode(self.shipping_file)
            self._get_cnab_240_return(file_decoded)

    def _get_cnab_240_return(self, file_decoded):
        processado = {}
        lines = file_decoded.splitlines()
        header = {}
        slip_lines = {}
        footer = {}
        while lines:
            line = lines.pop(0).decode('utf-8')
            commons = self._get_common(line)
            reg_type = commons['tipo_registro']
            if reg_type == '0':    
                header.update(self._get_headers(line))
            elif reg_type == '1':
                header.update(self._get_headers(line))
            elif reg_type == '3':
                reg_line = self._get_lines(line)
                if reg_line['code_line_registro'] == 'T':
                    reg = int((reg_line['seq_lot_number'] + 1) / 2)
                elif reg_line['code_line_registro'] == 'U':
                    reg = int(reg_line['seq_lot_number'] / 2)
                slip_lines.setdefault(reg, {})
                slip_lines[reg].update(reg_line)

        shipping_type = 'retorno'
        # create_file_date = header['file_date']
        moves = []
        for reg, slip in slip_lines.items():
            return_code_id = self.env['bank.slip.return.code'].search([('name', '=', slip['move_code'])])
            partner_id = self.env['res.partner']
            if 'cobro_cnpj_cpf' in slip:
                partner_id = partner_id.search(['|', ('cnpj_cpf', '=', slip['cobro_cnpj_cpf']), ('cnpj_cpf', '=', slip['cobro_cnpj_cpf'][1:19])])

            moves.append((0, 0, {
                'move_code_id': return_code_id.id,
                'cobro_partner_id': partner_id[0].id if partner_id else None,
                'cobro_cnpj_cpf': slip['cobro_cnpj_cpf'] if 'cobro_cnpj_cpf' in slip else None,
                'cobro_partner_name': slip['cobro_name'] if 'cobro_name' in slip else None,
                'cobro_invoice_number': slip['invoice_line_number'] if 'invoice_line_number' in slip else None,
                'value': slip['valor_pago'] if 'valor_pago' in slip else None,
                'name': (reg * 2) - 1,
                'slip_id': slip['slip_id'] if 'slip_id' in slip else None,
                'message': return_code_id.note,
                'date': slip['date'],
                'date_maturity': slip['date_maturity']
            }))
        slip_move_ids = moves

        bank_id = self.env['res.bank'].search([('code_bc', '=', header['codigo_banco'])])
        if bank_id:
            bank_account = self.env['res.partner.bank'].search([('bank_id','=', bank_id.id),
                                                                ('bra_number','=', '{:04}'.format(header['agencia'])),
                                                                ('bra_number_dig','=', '{:01}'.format(header['dv_agencia'])),
                                                                ('acc_number','=', '{:09}'.format(header['bank_account'])),
                                                                ('acc_number_dig','=', '{:01}'.format(header['dv_account']))
                                                                ])
            if not bank_account:
                acc_number = '{:04}'.format(header['agencia']) + '{}'.format(int(header['bank_account'])) + '{:01}'.format(header['dv_account'])
                bank_account = self.env['res.partner.bank'].search([('bank_id','=', bank_id.id),
                                                                    ('bra_number','=', '{:04}'.format(header['agencia'])),
                                                                    ('bra_number_dig','=', '{:01}'.format(header['dv_agencia'])),
                                                                    ('acc_number','=', acc_number),
                                                                    ('acc_number_dig','=', '{:01}'.format(header['dv_account']))
                                                                    ])

        self.update({
            'name': header['lot_return_number'],
            'file_date': header['file_date'],
            'company_id': self.company_id.id,
            'state': 'draft',
            'slip_move_ids': moves,
            'bank_account_id': bank_account.id,
            # 'shipping_file_id': attachment.id,
        })

    def _get_common(self, line):
        common = {}
        for common_field in reg_struct[:3]:
            value = line[common_field[3]:common_field[4]]
            value = int(value) if common_field[5] == 'N' else value
            common.update({common_field[2]: value})
        if 'lote_servicio' in common and common['lote_servicio'] == '0000':
            common.pop('lote_servicio')
        return common

    def _get_headers(self, line):
        reg_type = line[reg_struct[2][3]:reg_struct[2][4]]
        header_struct = [field for field in reg_struct if field[0] == reg_type]
        header = {}
        for header_field in header_struct:
            value = line[header_field[3]:header_field[4]]
            try:
                if header_field[5] == 'N':
                    value = int(value)
                elif header_field[5] == 'D':
                    if not int(value):
                        value = None
                    else: 
                        value = datetime.datetime.strptime(value, '%d%m%Y')
            except:
                _logger.warning(_('Wrong variable type'))
            if header_field[2].find('cnpj_cpf') > 0:
                value = format_cnpj_cpf(value)
            header.update({header_field[2]: value})
                        
        return header

    def _get_lines(self, line):
        reg_type = line[reg_struct[2][3]:reg_struct[2][4]]
        code_registro = line[13:14]

        line_struct = [field for field in reg_struct if field[0] == reg_type and field[1] == code_registro]
        slip_line = {}
        for line_field in line_struct:
            value = line[line_field[3]:line_field[4]]
            try:
                if line_field[5] == 'N':
                    value = int(value)
                    if len(line_field) > 6 and line_field[6]:
                        dec = line_field[6] or 1
                        value = value / (10**dec)
                elif line_field[5] == 'D':
                    value = datetime.datetime.strptime(value, '%d%m%Y')
                elif line_field[5] == 'A':
                    value = value.strip()
            except:
                _logger.warning(_('Wrong variable type'))

            if line_field[2].find('cnpj_cpf') > 0:
                value = format_cnpj_cpf(value)

            slip_line.update({line_field[2]: value})
        if 'invoice_line_number' in slip_line:
            invoice_line_id = self.env['bank.slip'].search([('invoice_name', '=', slip_line['invoice_line_number'])])
            if invoice_line_id:
                slip_line['slip_id'] = invoice_line_id.id
        return slip_line

    def action_process_shipment(self):
        if not self.bank_account_id.journal_id:
            raise UserError(_('Bank account without Journal, not payment method'))
        for ship in self:
            # ship.slip_move_ids.filtered(lambda x: not x.slip_id and not x.process == 'manual')
            # ship.bank_account_id = 
            for slip_move in ship.slip_move_ids.filtered(lambda x: x.invoice_line_id):
                slip_move.slip_move_process()
        return