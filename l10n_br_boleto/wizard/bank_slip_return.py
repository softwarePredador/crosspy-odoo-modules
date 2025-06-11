# -*- coding: utf-8 -*-

import logging
import time
import requests
import json
import base64
import datetime

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

reg_struct = [
    ('0', ' ', 'codigo_banco', 0, 3, 'A'),
    ('0', ' ', 'lot_return_number', 3, 7, 'N'),
    ('0', ' ', 'tipo_registro', 7, 8, 'A'),
    ('0', ' ', 'tipo_inscripcao', 16, 17, 'N'),
    ('0', ' ', 'cnpj_cpf', 17, 32, 'N'),
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
    ('1', 'T', 'cnpj_cpf', 18, 33, 'N'),
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
    ('3', 'T', 'maturity_date', 69, 77, 'D'),
    ('3', 'T', 'value', 77, 92, 'N'),           # 2 decimais sem separador
    ('3', 'T', 'nro_bank_cobro', 92, 95, 'N'),
    ('3', 'T', 'cobro_bank_agency', 95, 99, 'N'),
    ('3', 'T', 'cobro_dv_bank_agency', 99, 100, 'N'),
    ('3', 'T', 'cobro_number', 100, 125, 'N'),
    ('3', 'T', 'currency', 125, 127, 'N'),
    ('3', 'T', 'cobro_tipo_inscripcao', 127, 128, 'N'),
    ('3', 'T', 'cobro_cnpj_cpf', 128, 143, 'N'),
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
    ('3', 'U', 'juros_multa', 17, 32, 'N'),       # 2 casas decimais
    ('3', 'U', 'discount_value', 32, 47, 'N'),    # 2 casas decimais
    ('3', 'U', 'abatimento', 47, 62, 'N'),
    ('3', 'U', 'IOF_recolhido', 62, 77, 'N'),
    ('3', 'U', 'valor_pago', 77, 92, 'N'),
    ('3', 'U', 'valor_credito', 92, 107, 'N'),
    ('3', 'U', 'valor_despesas', 107, 122, 'N'),
    ('3', 'U', 'valor_outros_creditos', 122, 137, 'N'),
    ('3', 'U', 'date', 137, 145, 'D'),
    ('3', 'U', 'credit_date', 145, 153, 'D'),
    ('3', 'U', 'pagador_code', 153, 157, 'N'),
    ('3', 'U', 'pagador_date', 157, 165, 'D'),
    ('3', 'U', 'pagador_value', 165, 180, 'N'),
    ('3', 'U', 'pagador_complemento', 180, 210, 'N'),
    ('3', 'U', 'clearing_code_bank', 210, 213, 'N'),
                ]

class BankSlipReturn(models.TransientModel):
    _name = 'bank.slip.return.wizard'
    _description = 'Return Bank Slip'

    bank_return_file = fields.Binary(
        'Arquivo de Retorno do Banco', required=True)
    bank_return_file_name = fields.Char('Nome do Arquivo')
    company_id = fields.Many2one(
        'res.company', string='Empresa', required=True, default=lambda self: self.env.company)
    plugboleto_retorno_status = fields.Char(string="Status")
    plugboleto_retorno_protocolo = fields.Char(string="Protocolo")
    plugboleto_retorno_situacao = fields.Char(string="Situação")
    plugboleto_retorno_processados = fields.Integer(
        string="Boletos Processados")
    bank_slip_ids = fields.Many2many(
        'bank.slip', string='Boletos', readonly=True)
    
    def headers(self):
        tecnospeed = self.env["payment.acquirer"].search(
            [("provider", "=", "tecnospeed_boletos")]
        )
        headers = {
            "Content-Type": "application/json",
            "cnpj-sh": tecnospeed.tecnospeed_cnpj_sh,
            "token-sh": tecnospeed.tecnospeed_token_sh,
            "cnpj-cedente": self.company_id.plugboleto_cnpj_cedente,
        }
        return headers

    def bank_return(self):
        file_decode = base64.b64decode(self.bank_return_file)
        shipping_id = self._get_cnab_240_return(file_decode)

        return {
            "type": "ir.actions.act_window",
            "res_model": "bank.slip.shipping",
            "res_id": shipping_id.id,
            "view_mode": "form",
            "target": "current",
        }

        return
        if False:
            return_text = self._get_tecnospeed_request_return(file_decode)
        bank_slip = json.loads(return_text)

        time.sleep(10)

        _logger.warning(bank_slip)
        self.plugboleto_retorno_status = bank_slip["_status"]
        self.plugboleto_retorno_situacao = bank_slip["_dados"]["situacao"]
        self.plugboleto_retorno_protocolo = bank_slip["_dados"]["protocolo"]

        save_return = self.env["bank.slip.return.log"].create({
            "company_id": self.company_id.id,
            "name": self.plugboleto_retorno_protocolo,
            "return_file": file_decode,
            "return_file_name": self.bank_return_file_name,
            "plugboleto_retorno_status": self.plugboleto_retorno_status,
            "plugboleto_retorno_protocolo": self.plugboleto_retorno_protocolo,
            "plugboleto_retorno_situacao": self.plugboleto_retorno_situacao
        })

        _logger.warning(save_return)

        return {
            "type": "ir.actions.act_window",
            "res_model": "bank.slip.return.log",
            "res_id": save_return.id,
            "view_mode": "form",
            "target": "current",
        }
    
    def _get_cnab_240_return(self, file_decode):
        processado = {}
        lines = file_decode.splitlines()
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
        create_file_date = header['file_date']
        moves = []
        for reg, slip in slip_lines.items():
            return_code_id = self.env['bank.slip.return.code'].search([('name', '=', slip['move_code'])])
            moves.append((0, 0, {
                'move_code_id': return_code_id.id,
                'slip_id': slip['slip_id'] if 'slip_id' in slip else None,
                'message': return_code_id.note,
                'value': slip['valor_pago'], 
                'cobro_invoice_number': slip['invoice_line_number'],
                'cobro_tipo_inscripcao': slip['cobro_tipo_inscripcao'],
                'cobro_cnpj_cpf': slip['cobro_cnpj_cpf'],
                'cobro_name': slip['cobro_name'],
                'date': slip['date']
            }))
        slip_move_ids = moves

        attachment = self.env['ir.attachment'].create({
                'name': self.bank_return_file_name, #file_name,
                'datas': base64.encodebytes(file_decode),
                'res_model': 'bank.slip',
                'type': 'binary'
            })

        shipping_id = self.env['bank.slip.shipping'].create({
            'name': header['lot_return_number'],
            'company_id': self.company_id.id,
            'state': 'draft',
            'create_file_date': create_file_date,
            'slip_move_ids': moves,
            'shipping_file_id': attachment.id,
        })

        return shipping_id
                    
        # for line_b in lines:
        #     line = line_b.decode('utf-8')
        #     segment_code = line[13:14]
        #     if segment_code != 'T':
        #         continue
        #     move_code = line[15:17]
        #     protocolo = line[100:124]
        #     seu_numero = line[54:69]
        #     status = ''
        #     situacao = 'PROCESSADO'
        #     processado.setdefault(move_code, [])
        #     processado[move_code].append(seu_numero.strip())

        # status_codes = {'02': 'EMITIDO',
        #                 '03': 'REJEITADO',
        #                 '06': 'LIQUIDADO',
        #                 '09': 'BAIXADO',
        #                 '14': 'EMITIDO',
        #                 }

        # processados = self.env['bank.slip']
        # for status, status_str in status_codes.items():
        #     if status in processado and '02' in  processado:
        #         processados = processados.search([('invoice_name', 'in', processado['02'])])
        #         _logger.warning('CrossPy procesados: %s' % processados)                                                                                                                                                                                                                                                                           
        #         processados.write({'status': status_str})

        # save_return = self.env["bank.slip.return.log"].create({
        #     "company_id": self.company_id.id,
        #     "name": self.bank_return_file_name,
        #     "return_file": '', #decode,
        #     "return_file_name": '', #self.bank_return_file_name,
        #     "plugboleto_retorno_status": 'sucesso',
        #     "plugboleto_retorno_protocolo": self.bank_return_file_name,
        #     "plugboleto_retorno_situacao": 'PROCESSADO'
        # })

    def _get_tecnospeed_request_return(self, file_decode=''):
        tecnospeed = self.env["payment.acquirer"].search(
            [("provider", "=", "tecnospeed_boletos")]
        )
        api_url = tecnospeed.tecnospeed_get_form_action_url(self.company_id)

        headers = self.headers()
        headers["Content-Length"] = str(len(file_decode))

        request_return = requests.request(
            "POST",
            api_url + "/api/v1/retornos",
            headers=headers,
            data=json.dumps({"arquivo": file_decode})
        )

        if request_return.status_code != 200:
            raise ValidationError(
                _("Ocorreu um erro ao processar, contacte o adminitrador"))
        
        return request_return.text
    
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
                elif line_field[5] == 'D':
                    value = datetime.datetime.strptime(value, '%d%m%Y')
                elif line_field[5] == 'A':
                    value = value.strip()
            except:
                _logger.warning(_('Wrong variable type'))
            slip_line.update({line_field[2]: value})
        if 'invoice_line_number' in slip_line:
            invoice_line_id = self.env['bank.slip'].search([('invoice_name', '=', slip_line['invoice_line_number'])])
            if invoice_line_id:
                slip_line['slip_id'] = invoice_line_id.id
        return slip_line


class BankSlipReturnCode(models.Model):
    _name = "bank.slip.return.code"
    _description = "Bank Slip Return Code Base"

    name = fields.Char(string='Code', required=True, size=2)
    note = fields.Text(string='Description')

    def name_get(self):
        res = []
        for code in self:
            name = code.name
            if code.note :
                name = '%s - %s' % (name, code.note)
            res += [(code.id, name)]
        return res





