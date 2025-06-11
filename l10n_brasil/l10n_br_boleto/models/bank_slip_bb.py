# -*- coding: utf-8 -*-

import base64
import io
import time
import logging
import phonenumbers
import psycopg2
from datetime import datetime

_logger = logging.getLogger(__name__)

try:
    from erpbrasil.base import misc
    from erpbrasil.base.fiscal import cnpj_cpf
except ImportError:
    _logger.error("Biblioteca erpbrasil.base não instalada")

from odoo.http import request, content_disposition

from .slip_banco_brasil import BBrasilProvider
from markupsafe import Markup
from odoo.tools.zeep.helpers import serialize_object

from odoo import api, models, fields, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_repr
from odoo.tools.safe_eval import const_eval



class BankSlip(models.Model):
    _inherit = 'bank.slip'

    def log_xml(self, xml_string, func):
        self.ensure_one()

        if self.debug_logging:
            self.env.flush_all()
            db_name = self._cr.dbname

            # Use a new cursor to avoid rollback that could be caused by an upper method
            try:
                db_registry = registry(db_name)
                with db_registry.cursor() as cr:
                    env = api.Environment(cr, SUPERUSER_ID, {})
                    IrLogging = env['ir.logging']
                    IrLogging.sudo().create({'name': 'delivery.carrier',
                              'type': 'server',
                              'dbname': db_name,
                              'level': 'DEBUG',
                              'message': xml_string,
                              'path': self.delivery_type,
                              'func': func,
                              'line': 1})
            except psycopg2.Error:
                pass

    def bbrasil_send_slip(self):
        """
        Send slip to Banco do Brasil
        """

        sr = BBrasilProvider(self, self.log_xml)

        slips = []
        for slip in self:
            slip_data = sr._send_slip(slip)
            slips.append(slip_data)
        return slips

    @api.model
    def bbrasil_sync_slips(self, slip_type='A', start_date=None , end_date=None):
        """
        Send slip to Banco do Brasil
        

            {
            "numeroBoletoBB": "00035222910000000503",
            "dataRegistro": "02.04.2025",
            "dataVencimento": "16.04.2025",
            "valorOriginal": 465,
            "carteiraConvenio": 17,
            "variacaoCarteiraConvenio": 19,
            "codigoEstadoTituloCobranca": 2,
            "estadoTituloCobranca": "Mvto. Cartorio",
            "contrato": 0,
            "dataMovimento": "",
            "dataCredito": "",
            "valorAtual": 0,
            "valorPago": 0
            }
        """

        sr = BBrasilProvider(self, self.log_xml)
        baixados = slip_type == 'B'
        params = sr._get_query_params(self, baixados)
        params.update({
            'dataInicioRegistro': start_date.strftime("%d.%m.%Y"),
            'dataFimRegistro': end_date.strftime("%d.%m.%Y"),
        })

        is_continue = True
        while is_continue:
            slip_sync = sr._get_slips(self, params)
            json_slips = slip_sync.get('boletos', False)
            self._update_slips_status(json_slips)
            is_continue = False if slip_sync.get('indicadorContinuidade', 'N').upper() != 'S' else True
            if is_continue:
                params.update({
                    'indice': slip_sync.get('proximoIndice', 0)
                })

    def get_bbrasil_slip(self):
        sr = BBrasilProvider(self, self.log_xml)
        for slip in self:
            bbrasil_slip = sr._get_slip_info(slip)
            error_msg = []
            # Check if value and partner
            # 'codigoTipoInscricaoSacado': 2
            # 'numeroInscricaoSacadoCobranca': 47607487000139
            # 'valorOriginalTituloCobranca': 1430.27
            # 'dataVencimentoTituloCobranca': '06.06.2025'
            partner = self.env['res.partner']
            if bbrasil_slip['codigoTipoInscricaoSacado'] == 1:
                partner_vat = str(bbrasil_slip['numeroInscricaoSacadoCobranca']).zfill(11)
            if bbrasil_slip['codigoTipoInscricaoSacado'] == 2:
                partner_vat = str(bbrasil_slip['numeroInscricaoSacadoCobranca']).zfill(14)
            partner_vat = cnpj_cpf.formata(partner_vat)
            bb_partner = partner.search([('vat', '=', partner_vat), ('parent_id', '=', False)])
            value = float(bbrasil_slip['valorOriginalTituloCobranca'])
            due_date = datetime.strptime(bbrasil_slip['dataVencimentoTituloCobranca'], '%d.%m.%Y').date()
            correction = True
            if not bb_partner or (bb_partner.id != slip.customer.id and bb_partner.id != slip.customer.commercial_partner_id.id):
                error_msg.append('Partner Error')
                correction = False
            if due_date != slip.due_date:
                error_msg.append('Due date Error')
            if value != slip.value:
                error_msg.append('Value Error')
                correction = False
            if len(error_msg) > 0:
                self.message_post(
                    body="This slip has error when compare with bank information: {}".format(', '.join(error_msg)),
                    subject="Error Notification",
                    message_type="comment"
                )
                if correction:
                    slip.due_date = due_date
                else:
                    slip.status = 'FALHA'
                    return
            else:
                bbrasil_slip.update({'numeroBoletoBB': slip.titulo_nosso_numero})
                slip._update_slips_status([bbrasil_slip])

            # if bbrasil_slip.get('codigoEstadoTituloCobranca', False) == 7:
                
            if bbrasil_slip.get('codigoEstadoTituloCobranca', False) == 6 and slip.invoice_line_id and not slip.invoice_line_id.reconciled:
                company = self.env.company
                if not bbrasil_slip.get('valorJuroMoraTitulo', 0.0) or  not bbrasil_slip.get('valorJuroMoraTitulo', 0.0):
                    if not company.interest_account_id:
                        raise ValidationError(_('No Interest account configured for the company.'))
                slip.status = 'LIQUIDADO'
                
                #Create & Reconcile invoice payment
                if slip.invoice_line_id.reconciled:
                    return
                value = bbrasil_slip.get('valorPagoSacado', 0)
                difference = - (value - slip.invoice_line_id.amount_residual)
                payment_vals = {
                                'journal_id': company.bank_account_id.journal_id.id,
                                'payment_date': datetime.strptime(bbrasil_slip['dataRecebimentoTitulo'], '%d.%m.%Y').date(),
                                'communication': '{} Retorno BB API'.format(slip.invoice_line_id.name),
                                'amount': value,
                                }

                if not slip.invoice_line_id.currency_id.is_zero(difference):
                    interest_account_id = company.interest_account_id
                    payment_vals.update({
                                        'payment_difference': difference,
                                        'payment_difference_handling': 'reconcile',
                                        'writeoff_account_id': interest_account_id.id,
                                        'writeoff_label': 'Juros de boleto {}'.format(slip.display_name)
                    })
                payment = self.env['account.payment.register'].with_context(active_model='account.move.line', active_ids=slip.invoice_line_id.ids).new(payment_vals)
                payment._create_payments()

                # slip.process = 'processed'
            elif bbrasil_slip.get('codigoEstadoTituloCobranca', False) == 6:
                slip.status = 'LIQUIDADO'


    def _update_slips_status(self, json_slips):
        estados = {
            1: 'EMITIDO',
            2: 'MANUAL',
            3: 'MANUAL',
            4: 'MANUAL',
            5: 'MANUAL',
            6: 'LIQUIDADO',
            7: 'BAIXADO',
            8: 'MANUAL',
            9: 'MANUAL',
            12: 'MANUAL',
                  }
        """
            01 - NORMAL 
            02 - MOVIMENTO CARTORIO 
            03 - EM CARTORIO 
            04 - TITULO COM OCORRENCIA DE CARTORIO 
            05 - PROTESTADO ELETRONICO 
            06 - LIQUIDADO 
            07 - BAIXADO 
            08 - TITULO COM PENDENCIA DE CARTORIO 
            09 - TITULO PROTESTADO MANUAL 
            10 - TITULO BAIXADO/PAGO EM CARTORIO 
            11 - TITULO LIQUIDADO/PROTESTADO 
            12 - TITULO LIQUID/PGCRTO 
            13 - TITULO PROTESTADO AGUARDANDO BAIXA 
            14 - TITULO EM LIQUIDACAO 
            15 - TITULO AGENDADO 
            16 - TITULO CREDITADO 
            17 - PAGO EM CHEQUE - AGUARD.LIQUIDACAO 
            18 - PAGO PARCIALMENTE CREDITADO 
            80 - EM PROCESSAMENTO (ESTADO TRANSITÓRIO)
        """
        for j_slip in json_slips:
            tituloCliente = j_slip.get('numeroBoletoBB', False)
            if not tituloCliente:
                continue
            estado = j_slip.get('codigoEstadoTituloCobranca', False)
            nosso_numero = tituloCliente[10:] if len(tituloCliente)>10 else tituloCliente
            slip = self.env['bank.slip'].search([('titulo_nosso_numero', '=', nosso_numero)])
            if slip and estado and estado in estados:
                slip.status = estados[estado]
            elif slip:
                _logger.warning('Estado de boleto no encontrado: %s' % (estado))
            else:
                _logger.warning('Boleto não encontrado %s, valor: %s, vencimento: %s' % (nosso_numero, j_slip.get('dataVencimento', False), j_slip.get('valorOriginal', False)))
        




