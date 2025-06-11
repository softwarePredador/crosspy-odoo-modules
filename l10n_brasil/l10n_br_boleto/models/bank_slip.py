# -*- coding: utf-8 -*-

import pytz
import base64
import logging
import re
import io
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
from io import StringIO
from unicodedata import normalize

# from erpbrasil.edoc.nfe import NFe as edoc_nfe

import requests
import json


from .boleto import boleto
from lxml import etree
from werkzeug.urls import url_join

from odoo import api, fields, models, _
from odoo.addons.base.models.ir_mail_server import MailDeliveryException
from odoo.exceptions import AccessError, MissingError, ValidationError, UserError
from odoo.osv.expression import AND, OR


_logger = logging.getLogger(__name__)

class BankSlip(models.Model):
    _name = 'bank.slip'
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Bank Slip"
    _check_company_auto = True

    # _inherit = ['bank.slip']

    name = fields.Char(string="Nome", readonly=True)
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        required=True,
        ondelete="restrict",
        default=lambda self: self.env.company,
    )
    active = fields.Boolean(default=True, tracking=True)
    invoice = fields.Many2one("account.move", string="Fatura", readonly=True, ondelete='cascade')
    nfe_number = fields.Integer(string="Número NFe", related="invoice.nfe_number")
    invoice_line_id = fields.Many2one(
        string="Linha da Fatura",
        comodel_name="account.move.line",
        readonly=True,
        index=True,
    )
    invoice_name = fields.Char(string="NF/Parcela", readonly=True)
    customer = fields.Many2one("res.partner", string="Cliente", readonly=True)
    id_integracao = fields.Char(string="Id Integração", readonly=True)

    state = fields.Selection(
        [
            ("SALVO", "SALVO"),
            ("EMITIDO", "EMITIDO"),
            ("FALHA", "FALHA"),
            ("REGISTRADO", "REGISTRADO"),
            ("CONFIRMED", "CONFIRMED"),
            ("LIQUIDADO", "LIQUIDADO"),
            ("REJEITADO", "REJEITADO"),
            ("BAIXADO", "BAIXADO"),
            ("APAGADO", "APAGADO"),
            ("MANUAL", "MANUAL"),
        ],
        string="State",
        readonly=True,
        tracking=True,
    )

    status = fields.Selection(
        [
            ("SALVO", "SALVO"),
            ("EMITIDO", "EMITIDO"),
            ("FALHA", "FALHA"),
            ("CONFIRMED", "CONFIRMED"),
            ("REGISTRADO", "REGISTRADO"),
            ("LIQUIDADO", "LIQUIDADO"),
            ("REJEITADO", "REJEITADO"),
            ("BAIXADO", "BAIXADO"),
            ("APAGADO", "APAGADO"),
            ("MANUAL", "MANUAL"),
            ("ERROR", "ERROR"),
        ],
        string="Status",
        readonly=True,
        tracking=True,
    )

    pdf = fields.Binary(string="Pdf", readonly=True)
    pdf_name = fields.Char(string="Nome do PDF", readonly=True)
    due_date = fields.Date(string="Vencimento", readonly=True, tracking=True)
    value = fields.Float(string="Valor", readonly=True, tracking=True)
    reconciled = fields.Boolean(related='invoice_line_id.reconciled')
    shipping_file_id = fields.Many2one(
        comodel_name="ir.attachment",
        string="Arquivo de Remessa",
        copy=False,
        readonly=True,
    )
    shipping_file = fields.Binary(
        string="Arquivo de Remessa", readonly=True, related="shipping_file_id.datas")
    shipping_file_name = fields.Char(
        string="Nome do Arquivo de Remessa", readonly=True, related="shipping_file_id.name")

    @api.ondelete(at_uninstall=False)
    def _prevent_delete_emited(self):
        if any(record.status == 'EMITIDO' for record in self):
            raise UserError("You can't delete this slip in issued state.")
        
    date_updated = fields.Char(
        string="Data de Atualização", readonly=True, tracking=True)
    
    sent_mail_id = fields.Many2one('mail.mail', string='Sent Mail')

    plugboleto_conciliado = fields.Boolean(
        string="Não Conciliado",
        help="Indica que o boleto não foi conciliado com a API do PlugBoleto",
        default=True,
        tracking=True
    )

    nosso_numero = fields.Integer(
        string="Nosso Número de controle", readonly=True, tracking=True)

    titulo_nosso_numero = fields.Char(
        string="Nosso Número", readonly=True, tracking=True)

    bank_account_id = fields.Many2one('res.partner.bank', 
                                      string='Bank Account')
    
    _sql_constraints = [
        ('titulo_nosso_numero_unique', 'unique (titulo_nosso_numero, company_id)', 'O Nosso Número deve ser único por empresa!'),
    ]

    def action_view_invoice(self, invoices=False):
        """This function returns an action that display  vinculated invoice and show it in form view"""
        action = self.env.ref("account.action_move_out_invoice_type").read()[0]
        action["views"] = [(self.env.ref("account.view_move_form").id, "form")]
        action["res_id"] = self.invoice.id
        return action

    def update_pdf(self):
        data = self.consult_bank_slip_ids(
            self.id_integracao, self.company_id)
        if self.invoice_name:
            pdf_name = "Boleto_" + self.invoice_name.replace("/", "-") + ".pdf"
        elif self.invoice.document_number:
            pdf_name = "Boleto_" + self.invoice.document_number + ".pdf"
        else:
            pdf_name = "Boleto_" + self.id_integracao + ".pdf"

        if data["_dados"][0]["UrlBoleto"]:
            self.pdf = self.invoice.convertToBase64(
                data["_dados"][0]["UrlBoleto"])
            self.pdf_name = pdf_name
        else:
            self.pdf = False
            self.pdf_name = False

    def download_pdf(self):
        self.update_pdf()
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/bank.slip/%s/pdf/%s?download=true" % (self.id, self.pdf_name),
            "target": "self",
        }

    def discard_bank_slip(self):
        selected_ids = self.env.context.get('active_ids', [])
        if not selected_ids:
            raise UserError(_("Nenhum boleto selecionado para descarte."))

        for bank_slip in self.browse(selected_ids):
            if bank_slip.status in ["EMITIDO", "FALHA", "REJEITADO"]:
                bank_slip.update_status()
                api_url = self.get_url_tecnospeed(
                    company_id=bank_slip.invoice.company_id)
                discard_request = requests.request(
                    "POST",
                    api_url + "/api/v1/boletos/descarta/lote",
                    headers=self.headers(bank_slip.company_id),
                    data=json.dumps([self.id_integracao]),
                )
                data = json.loads(discard_request.text)
                data_success = data["_dados"]["_sucesso"]
                data_error = data["_dados"]["_falha"]
                self.invoice.cancel_bank_slip(self.id_integracao)
                if data_success:
                    status = "APAGADO"
                    self.env.user.notify_success(
                        _("Boleto %s descartado com sucesso.") % self.invoice_name)
                else:
                    errorMsg = data_error[0]["idintegracao"] + \
                        " - " + data_error[0]["_erro"]
                    self.env.user.notify_warning(
                        _("Erro ao descartar boleto %s.") % errorMsg)
                    status = "APAGADO"
                bank_slip.write({"status": status})
            else:
                raise UserError(
                    _("Não é possível descartar um boleto que não esteja em EMITIDO, FALHA ou REJEITADO"))
        return

    def update_status(self, from_bank_return=False):
        selected_ids = self.env.context.get('active_ids', [])
        if not selected_ids and not from_bank_return:
            raise UserError(_("Nenhum boleto selecionado para Atualização."))
        else:
            _logger.info("Atualizando status dos boletos")
            _logger.info(selected_ids)

            for bank_slip in self.browse(selected_ids) or self:
                if bank_slip.status == "MANUAL":
                    _logger.info("Boleto %s está em status MANUAL" %
                                 bank_slip.invoice_name)
                    return
                _logger.info("Atualizando status do boleto %s" % bank_slip.id_integracao)

                data = self.consult_bank_slip_ids(
                    bank_slip.id_integracao, bank_slip.company_id)
                updated = datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")
                status = "APAGADO"
                active = False
                nosso_numero = False

                if data["_dados"] and data["_status"] == "sucesso":
                    if data["_dados"][0]["situacao"]:
                        status = data["_dados"][0]["situacao"]
                        updated = data["_dados"][0]["atualizado"]
                        nosso_numero = data["_dados"][0]["TituloNossoNumero"]
                        active = True
                    
                    if bank_slip.date_updated == updated and bank_slip.status == status:
                        _logger.info("Bank Slip %s already updated" %
                                    bank_slip.id_integracao)
                    else:
                        if not bank_slip.invoice_name and bank_slip.invoice.document_number:
                            bank_slip.invoice_name = bank_slip.invoice.document_number
                        if data["_dados"][0]["TituloOcorrencias"]:
                            bank_slip_occurrences = data["_dados"][0]["TituloOcorrencias"]
                            self.bank_slip_events(bank_slip_occurrences, bank_slip)
                        if data["_dados"][0]["TituloMovimentos"]:
                            bank_slip_moviments = data["_dados"][0]["TituloMovimentos"]
                            bank_slip_moviments_occurrences = data["_dados"][0]["TituloMovimentos"][0]["ocorrencias"]
                            self.bank_slip_moviments(
                                bank_slip_moviments, bank_slip_moviments_occurrences, bank_slip)
                        if status == "LIQUIDADO":
                            self.bank_slip_payment(data, bank_slip)
                    _logger.info("Bank Slip %s updated" % bank_slip.id_integracao)
                elif data["_mensagem"] == "Nenhum registro encontrado":
                    self.env.user.notify_warning(
                        _("Boleto %s não encontrado.") % bank_slip.id_integracao)
                else:
                    raise ValidationError(_("Erro ao consultar boleto %s: %s") % (
                            bank_slip.id_integracao, data["_dados"][0]["_erro"]))
                
                bank_slip.write({"status": status, "date_updated": updated, "active": active, "titulo_nosso_numero": nosso_numero})

        return

    def consult_bank_slip_ids(self, id_integracao, company_id):
        api_url = self.get_url_tecnospeed(company_id=company_id)
        data = requests.request(
            "GET",
            api_url + "/api/v1/boletos?IdIntegracao=" + id_integracao,
            headers=self.headers(company_id),
        )
        return json.loads(data.text)

    def get_url_tecnospeed(self, company_id):
        tecnospeed = self.env["payment.acquirer"].search(
            [("provider", "=", "tecnospeed_boletos")]
        )
        return tecnospeed.tecnospeed_get_form_action_url(company_id)

    def bank_slip_events(self, bank_slip_occurrences, bank_slip):
        if bank_slip.event_ids:
            for event in bank_slip.event_ids:
                event.unlink()
        bank_slip.event_ids = [(0, 0, {
            "bank_slip_id": bank_slip.id,
            "code": event["codigo"],
            "message": event["mensagem"],
            "created": event["criado"],
            "updated": event["atualizado"],
        }) for event in bank_slip_occurrences]

        return

    def bank_slip_moviments(self, bank_slip_moviments, bank_slip_moviments_occurrences, bank_slip):
        moviments = bank_slip.moviment_ids
        moviments_occurrences = moviments.occurrence_ids

        if not bank_slip_moviments:
            return

        if moviments_occurrences:
            for occurrence in moviments_occurrences:
                occurrence.unlink()

        if moviments:
            for moviment in moviments:
                moviment.unlink()

        for moviment in bank_slip_moviments:
            moviment_id = self.env["bank.slip.moviment"].create({
                "name": bank_slip.id_integracao + " - " + moviment["mensagem"],
                "bank_slip_id": bank_slip.id,
                "code": moviment["codigo"],
                "message": moviment["mensagem"],
                "date": moviment["data"],
                "tax": moviment["taxa"]
            })
            if bank_slip_moviments_occurrences:
                for occurrence in bank_slip_moviments_occurrences:
                    self.env["bank.slip.moviment.occurrence"].create({
                        "name": moviment_id.name + " - " + occurrence["mensagem"],
                        "bank_slip_id": bank_slip.id,
                        "moviment_id": moviment_id.id,
                        "code": occurrence["codigo"],
                        "message": occurrence["mensagem"],
                    })

        return

    def bank_slip_payment(self, data, bank_slip):
        if not data:
            return

        if bank_slip.payment_ids:
            for payment in bank_slip.payment_ids:
                payment.unlink()

        if data["_dados"][0]["PagamentoRealizado"]:
            bank_slip.payment_ids = [(0, 0, {
                "name": "Pagamento: " + data["_dados"][0]["TituloNumeroDocumento"],
                "bank_slip_id": bank_slip.id,
                "payment_date": data["_dados"][0]["PagamentoData"],
                "payment_date_credit": data["_dados"][0]["PagamentoDataCredito"],
                "payment_made": data["_dados"][0]["PagamentoRealizado"],
                "payment_value_credit": data["_dados"][0]["PagamentoValorCredito"],
                "payment_value_paid": data["_dados"][0]["PagamentoValorPago"],
                "payment_value_tax_collection": data["_dados"][0]["PagamentoValorTaxaCobranca"],
                "payment_value_additions": data["_dados"][0]["PagamentoValorAcrescimos"],
                "payment_value_discount": data["_dados"][0]["PagamentoValorDesconto"],
                "payment_value_abatement": data["_dados"][0]["PagamentoValorAbatimento"],
                "payment_date_bank_fee": data["_dados"][0]["PagamentoDataTaxaBancaria"],
                "payment_value_other_expenses": data["_dados"][0]["PagamentoValorOutrasDespesas"],
                "payment_value_other_credits": data["_dados"][0]["PagamentoValorOutrosCreditos"],
            })]

        return

    def download_bank_slip_shipping(self, selected_ids=None, company_id=None):
        ## TODO
        file_content = self.generate_txt_file240(self.ids)
        file_name = 'COB_033_130020357_' + fields.date.today().strftime('%d%m%y') + '_00001.rem'
        # bank_slips = data_success[0]["titulos"]
        # _logger.warning('CrossPy - file_content: %s' % (file_content))
        # _logger.warning('CrossPy - file_content: %s' % (file_content))

        attachment = self.env['ir.attachment'].create({
            'name': file_name,
            'datas': base64.encodebytes(file_content.encode('utf-8')),
            'res_model': 'bank.slip',
            'type': 'binary'
        })
        # _logger.warning('CrossPy - file_content: %s, attachment_id: %s' % (file_content, attachment.id))
        # _logger.warning('CrossPy - file_content: %s, attachment_id: %s' % (file_content, attachment.id))

        self.write({"shipping_file_id": attachment.id})
        # for bank_slip in bank_slips:
        #     bank_slip_idintegracao = bank_slip["idintegracao"]
        #     bank_slip_id = self.env["bank.slip"].search(
        #         [("id_integracao", "=", bank_slip_idintegracao)])
        #     bank_slip_id.write({"shipping_file_id": attachment.id})
        #     _logger.info(
        #         "Bank slip %s has been linked to shipping file" % bank_slip_id.id)
            
        bank_notify = "O arquivo de remessa foi gerado com sucesso e anexado aos boletos selecionados: "
        bank_notify += file_name
        return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Arquivo de remessa gerado'),
                    'message': '%s' % bank_notify,
                    'sticky': True,
                }
            }



        # self.env.user.notify_success(
        #     title="Arquivo de remessa gerado",
        #     message="%s" % bank_notify,
        #     sticky=True
        # )
        # _logger.warning('CrossPy - file_content: %s, attachment_id: %s' % (file_content, attachment.id))
        # _logger.warning('CrossPy - file_content: %s, attachment_id: %s' % (file_content, attachment.id))

    def generate_txt_file240(self, ids):
        data = ''
        # _logger.warning('CrossPy - data: %s' % data)
        query = 'SELECT max(nosso_numero) FROM bank_slip limit 1'
        self._cr.execute(query)
        last_bnk_nosso_numero = self._cr.fetchall()[0][0]
        # import pdb; pdb.set_trace()


        # last_bnk_slip_id = self.env['bank.slip'].search([('titulo_nosso_numero', '!=', False)], order='titulo_nosso_numero DESC', limit=1)7

        next_nosso_numero = 0
        if self.env.company.next_bank_number:
            next_nosso_numero = self.env.company.next_bank_number

        if not next_nosso_numero and last_bnk_nosso_numero:
            next_nosso_numero = int(int(last_bnk_nosso_numero)/10) + 1
        elif not next_nosso_numero and not last_bnk_nosso_numero:
            next_nosso_numero = 1

        data += self._get_header_arquivo_remessa()
        # _logger.warning('CrossPy - data header arquivo: %s' % data)

        data += self._get_header_lote_remessa()
        # _logger.warning('CrossPy - data header lote: %s' % data)
        # _logger.warning('CrossPy - detalhe self: %s' % self)
        seq = 0
        for slip in self:
            seq += 1
            data += self._get_detalhe_lote_remessa_P(slip, seq, next_nosso_numero)
            # _logger.warning('CrossPy - detalhe lote P: %s' % data)
            seq += 1
            data += self._get_detalhe_lote_remessa_Q(slip, seq)
            # _logger.warning('CrossPy - detalhe lote Q: %s' % data)
            seq += 1
            data += self._get_detalhe_lote_remessa_R(slip, seq)
            # _logger.warning('CrossPy - detalhe lote R: %s' % data)
    
        data += self._get_trailer_lote_remessa(len(self))
        data += self._get_trailer_arquivo_remessa(len(self))
        return data

    def _get_header_arquivo_remessa(self): # 
        company_id = self.company_id or self.env.company
        line = company_id.bank_account_id.bank_id.code_bc #'033' # Código do Banco na compensação
        line += ''.ljust(4, '0') # Número do Lote Remesa
        line += '0' # Tipo de registro
        line += ''.ljust(8) # Reservado (uso Banco) 
        line += '2' if company_id.partner_id.is_company else '1' # Tipo de inscrição da Empresa
        cnpj = re.sub('[^0-9]', '', company_id.partner_id.vat)
        # cnpj = '19860927000120'
        line += (cnpj or '').rjust(15, '0')[:15] # Número de Inscripção da empresa 
        # line += '4542'.ljust(4) # Agência do Destinatária
        # line += '0'.ljust(1) # Dígito da Ag do Destinatária 
        # line += '013002035'.ljust(9) # Número da conta corrente 130020357
        # line += '7'.ljust(1) # Digito Verificador da conta
        line += (company_id.bank_contract or '').ljust(40) # Código de Transmissão
        # 20 + 9 + 
        # [] + [] + '0126'
        # 3522291
        # 2222470453000113522291
        # '76449000002585000000'
        # line += ''.ljust(25, ' ') # Reservado (uso Banco) 
        line += company_id.partner_id.legal_name.ljust(30)[:30] # Nome do Beneficiario
        line += company_id.bank_account_id.bank_id.name.ljust(30) # Nome do Banco
        line += ''.ljust(10, ' ') # Reservado (uso Banco) 
        line += '1'.ljust(1) # Código Remessa 1=Remessa
        line += fields.date.today().strftime('%d%m%Y').ljust(8) # Data de geração do arquivo 
        line += ''.ljust(6, ' ') # Reservado (uso Banco) 
        line += '000001'.ljust(6) # Nº seqüencial do arquivo 
        line += '040'.ljust(3) # Nº da versão do layout do arquivo 
        line += ''.ljust(74) # Uso Exclusivo FEBRABAN / CNAB
        line += '\r\n'
        return line

    def _get_header_lote_remessa(self): # 
        company_id = self.company_id or self.env.company
        line = company_id.bank_account_id.bank_id.code_bc # Código do Banco na compensação
        line += '0001'.ljust(4, '0') # Número do Lote Remesa
        line += '1' # Tipo de registro
        line += 'R' # Cód. Segmento do registro detalhe
        line += '01' # Tipo de servicio
        line += ''.ljust(2, ' ') # Reservado (uso Banco)
        line += '030'.ljust(3) # Número da Versão do Layout do Lote
        line += ''.ljust(1) # Reservado (uso Banco) 
        line += '2' if company_id.partner_id.is_company else '1' # Tipo de inscrição da Empresa
        cnpj = re.sub('[^0-9]', '', company_id.partner_id.vat)
        #cnpj = '19860927000120'
        line += (cnpj or '').rjust(15, '0')[:15] # Número de Inscripção da empresa 
        line += ''.ljust(20) # Reservado (uso Banco)
        line += (company_id.bank_transmision_code or '').ljust(15) # Código de Transmissão
        line += ''.ljust(5) # Reservado (uso Banco) 
        line += company_id.partner_id.legal_name.ljust(30)[:30] # Nome do Beneficiario
        line += 'Cobrar juros de 0,33%% ao dia'.ljust(40) # Messagem 1
        line += 'Cobrar multa de 2,00%% ao mês'.ljust(40) # Messagem 2
        line += '00000001'.ljust(8) # Número remessa
        line += fields.date.today().strftime('%d%m%Y').ljust(8) # Data de geração da remessa
        line += ''.ljust(41) # Uso Exclusivo FEBRABAN / CNAB
        line += '\r\n'
        return line

    def _get_detalhe_lote_remessa_P(self, slip, seq, nro): # Finalizado
        #v040
        nosso_numero = self._gerar_nosso_numero(nro + int(((seq+2)/3)))
        slip.titulo_nosso_numero = nosso_numero
        slip.nosso_numero = int(nosso_numero)
        # _logger.warning('CrossPy - print nosso_numero: %s' % nosso_numero)
        if len(self.company_id.partner_id.bank_ids) > 0 and self.company_id.partner_id.bank_ids[0]:
            slip.bank_account_id = self.company_id.partner_id.bank_ids[0]
        company_id = self.company_id or self.env.company
        line = company_id.bank_account_id.bank_id.code_bc # Código do Banco na compensação
        line += '0001'.ljust(4, '0') # Número do Lote Remesa
        line += '3' # Tipo de registro
        line += '{:05.0f}'.format(seq) # Número sequencial de registro do lote
        line += 'P' # Cód. Segmento do registro detalhe
        line += ''.ljust(1, ' ') # Reservado (uso Banco) 
        line += '01'.ljust(2) # Código de movimento remessa
        
        line += (company_id.bank_account_id.bra_number or '').ljust(4) # Agência do Destinatária
        line += (company_id.bank_account_id.bra_number_dig or '').ljust(1) # Dígito da Ag do Destinatária 
        line += (company_id.bank_account_id.acc_number or '').ljust(9, '0') # Número da conta corrente 130020357
        line += (company_id.bank_account_id.acc_number_dig or '').ljust(1) # Digito Verificador da conta
        line += (company_id.bank_account_id.acc_number or '').ljust(9) # Conta cobrança Destinatária FIDC 
        line += (company_id.bank_account_id.acc_number_dig or '').ljust(1) # Dígito da conta cobrança Destinatária FIDC 
        line += ''.ljust(2) # Reservado (uso Banco)
        line += nosso_numero.ljust(13, '0') # Identificação do boleto no Banco
        line += '5'.ljust(1) # Tipo de cobrança
        line += '1'.ljust(1) # Forma de cadastramento
        line += '1'.ljust(1) # Tipo de documento
        line += ''.ljust(1) # Reservado (uso Banco) 
        line += ''.ljust(1) # Reservado (uso Banco) 
        line += slip.invoice_name.ljust(15)[:15] # Nº do documento
        line += slip.due_date.strftime('%d%m%Y').ljust(8) # Data de vencimento do boleto
        line += '{:016.2f}'.format(slip.value).replace('.','') # Valor nominal do boleto
        line += ''.ljust(4, '0') # Agência encarregada da cobrança FIDC
        line += ''.ljust(1, '0') # Dígito da Agência do Beneficiário FIDC
        line += ''.ljust(1) # Reservado (uso Banco) 
        line += '02'.ljust(2) # Espécie do boleto
        line += 'N'.ljust(1) # Identif. de boleto Aceito/Não Aceito
        line += (slip.date_updated or fields.date.today()).strftime('%d%m%Y').ljust(8) # Data da emissão do boleto
        line += '1'.ljust(1) # Código de juros de mora
        line += (slip.date_updated or fields.date.today()).strftime('%d%m%Y').ljust(8) # Data de juros de mora
        line += '{:016.2f}'.format(0.33).replace('.','') # Valor da mora/dia ou Taxa mensal

        line += ''.ljust(1, '0') # Código de desconto 1
        line += ''.ljust(8, '0') # Data de desconto 1
        line += '{:016.2f}'.format(0.0).replace('.','') # Valor o porcentual do desconto concedido
        line += '{:016.5f}'.format(0.0).replace('.','') # Percentual do IOF a ser recolhido
        line += '{:016.2f}'.format(0.0).replace('.','') # Valor do abatimento
        line += ''.ljust(25) # Identificação do boleto na empresa numero de boleto XXXXXX
        line += ''.ljust(1, '0') # código para protesto
        line += ''.ljust(2, '0') # Número de días para protesto
        line += '2'.ljust(1) # Código para Baixa/Devolução
        line += ''.ljust(1, '0') # Reservado (uso Banco) zero fixo
        line += '00'.ljust(2) # Número de días para Baixa/Devolução
        line += '00'.ljust(2) # Código da moeda
        line += ''.ljust(11) # Uso Exclusivo FEBRABAN / CNAB
        line += '\r\n'
        return line

    def _get_detalhe_lote_remessa_Q(self, slip, seq): # 
        company_id = slip.company_id or self.env.company
        line = company_id.bank_account_id.bank_id.code_bc # Código do Banco na compensação
        line += '0001' # Lote Remesa
        line += '3' # Tipo de registro
        line += '{:05.0f}'.format(seq) # Número sequencial de registro do lote
        line += 'Q' # Tipo de operação R
        line += ''.ljust(1, ' ') # Reservado (uso Banco) 
        line += '01'.ljust(2) # Código de movimiento remesa
        line += '2' if slip.customer.is_company else '1' # Tipo de inscrição do Pagador
        cnpj = re.sub('[^0-9]', '', slip.customer.vat)
        line += (cnpj or '').rjust(15, '0')[:15] # Número de inscrição do Pagador 
        line += (slip.customer.legal_name or '').ljust(40)[:40] # Nome do Pagador 
        line += ((slip.customer.street_name or '') + (slip.customer.street2 or '') + (' ' + str(slip.customer.street_number) or '')).ljust(40)[:40] # Endereço do Pagador
        line += (slip.customer.district or '').ljust(15)[:15] # Bairro do Pagador
        #cep = slip.customer.zip.split('-')
        cep = re.sub('[^0-9]', '', slip.customer.zip or '')
        line += cep.ljust(8)[:8] # Cep do Pagador
        line += (slip.customer.city_id.name or '').ljust(15)[:15] # Cidade do Pagador
        line += (slip.customer.state_id.code or '').ljust(2) # Unidade de Federação do Pagador
        line += ''.ljust(1,  '0') # Tipo de inscrição Beneficiário Final 
        line += ''.ljust(15, '0') # Nº de inscrição Beneficiário Final
        line += ''.ljust(40, ' ') # Nome do Beneficiário Final
        line += ''.ljust(3, '0') # Reservado (uso Banco) 
        line += ''.ljust(3, '0') # Reservado (uso Banco) 
        line += ''.ljust(3, '0') # Reservado (uso Banco) 
        line += ''.ljust(3, '0') # Reservado (uso Banco) 
        line += ''.ljust(19, ' ') # Reservado (uso Banco) 
        line += '\r\n'
        return line

    def _get_detalhe_lote_remessa_R(self, slip, seq): # Finalizado
        company_id = slip.company_id
        line = company_id.bank_account_id.bank_id.code_bc # Código do Banco na compensação
        line += '0001' # Lote Remesa
        line += '3' # Tipo de registro
        line += '{:05.0f}'.format(seq) # Número sequencial de registro do lote
        line += 'R' # Código segmento do registro detalhe
        line += ''.ljust(1, ' ') # Reservado (uso Banco) 
        line += '01'.ljust(2) # Código de movimeinto
        line += '0'.ljust(1) # Código do desconto 2
        line += ''.ljust(8, '0') # Data do desconto 2
        line += '{:016.2f}'.format(0.0).replace('.','') # Valor/Percentual a ser concedido
        line += ' '.ljust(24) # Código do desconto 3
        #line += '0'.ljust(1) # Código do desconto 3
        #line += ''.ljust(8, '0') # Data do desconto 3
        #line += '{:016.2f}'.format(0.0).replace('.','') # Valor/Percentual a ser concedido
        line += '2'.ljust(1) # Código da multa
        line += slip.due_date.strftime('%d%m%Y').ljust(8) # Data da multa
        line += '{:016.2f}'.format(2.0).replace('.','') # Valor/Percentual a ser aplicado
        line += ''.ljust(10, ' ') # Reservado (uso Banco) 
        line += ''.ljust(40, ' ') # Messagem 3
        line += ''.ljust(40, ' ') # Messagem 4
        line += ''.ljust(61, ' ') # Uso Exclusivo FEBRABAN / CNAB
        line += '\r\n'
        return line

    def _get_trailer_lote_remessa(self, cant): # 
        company_id = self.company_id
        line = company_id.bank_account_id.bank_id.code_bc # Código do Banco na compensação
        line += '0001'.ljust(4, '0') # Número do Lote Remesa
        line += '5' # Tipo de registro
        line += ''.ljust(9, ' ') # Reservado (uso Banco)
        line += '{:06.0f}'.format(cant) # Quantidade de registros do lote
        line += ''.ljust(217, ' ') # Reservado (uso Banco)
        line += '\r\n'
        return line

    def _get_trailer_arquivo_remessa(self, cant): # 
        cant += 2
        company_id = self.company_id
        line = company_id.bank_account_id.bank_id.code_bc # Código do Banco na compensação
        line += ''.ljust(4, '9') # Número do Lote Remesa
        line += '9' # Tipo de registro
        line += ''.ljust(9, ' ') # Reservado (uso Banco)
        line += '{:06.0f}'.format(1.0) # Quantidade de lotes do arquivo - Registros tipo=1
        line += '{:06.0f}'.format(cant) # Quantidade de registros do arquivo - Registros tipo=0+1+2+3+5+9
        line += ''.ljust(211, ' ') # Reservado (uso Banco)
        line += '\r\n'
        return line

    def _get_header_linexxxx240(self):
        line = '0' #Código de registro
        line += '1' # Código de remessa
        line += 'REMESSA' # Literal de transmissão
        line += '01' #Código do tipo serviço
        line += 'COBRANÇA'.ljust(15) #Literal de serviço
        line += ''.ljust(20) # Código de Transmissão
        line += self.env.company.partner_id.legal_name.ljust(30)[:30] # Nome do Beneficiário
        line += company_id.bank_account_id.bank_id.code_bc.ljust(3) # Código do Banco
        line += 'SANTANDER'.ljust(15) # Nome do Banco
        line += fields.date.today().strftime('%d%m%y').ljust(6) # Data da Geração do Arquivo
        line += ''.ljust(16, '0') # Reservado (uso Banco)
        line += ''.ljust(47) # Mensagem 1
        line += ''.ljust(47) # Mensagem 2
        line += ''.ljust(47) # Mensagem 3
        line += ''.ljust(47) # Mensagem 4
        line += ''.ljust(47) # Mensagem 5
        line += ''.ljust(34) # Reservado (uso Banco)
        line += ''.ljust(6) # Reservado (uso Banco)
        line += '001'.ljust(3) # Nº sequencial do arquivo
        line += '000001'.ljust(6) # Nº sequencial do registro no arquivo
        line += '\r\n'
        return line

    def generate_txt_file400(self, ids):
        data = ''
        data += self._get_header_line()
        for slip in self:
            data += self._get_slip_line(slip)
        return data

    def _get_header_line400(self):
        line = '0' #Código de registro
        line += '1' # Código de remessa
        line += 'REMESSA' # Literal de transmissão
        line += '01' #Código do tipo serviço
        line += 'COBRANÇA'.ljust(15) #Literal de serviço
        line += ''.ljust(20) # Código de Transmissão
        line += self.env.company.partner_id.legal_name.ljust(30)[:30] # Nome do Beneficiário
        line += company_id.bank_account_id.bank_id.code_bc.ljust(3) # Código do Banco
        line += 'SANTANDER'.ljust(15) # Nome do Banco
        line += fields.date.today().strftime('%d%m%y').ljust(6) # Data da Geração do Arquivo
        line += ''.ljust(16, '0') # Reservado (uso Banco)
        line += ''.ljust(47) # Mensagem 1
        line += ''.ljust(47) # Mensagem 2
        line += ''.ljust(47) # Mensagem 3
        line += ''.ljust(47) # Mensagem 4
        line += ''.ljust(47) # Mensagem 5
        line += ''.ljust(34) # Reservado (uso Banco)
        line += ''.ljust(6) # Reservado (uso Banco)
        line += '001'.ljust(3) # Nº sequencial do arquivo
        line += '000001'.ljust(6) # Nº sequencial do registro no arquivo
        line += '\r\n'
        return line

    def _get_slip_line400(self, slip):
        line = '1'
        if not self.env.company.partner_id.is_company:
            line += '01'
        else:
            line += '02'
        line += '19860927000200'
        line += '4542'
        line += '13002035'
        line += '13002035'
        line += slip.invoice_name.ljust(25)[:25]
        line += ''.ljust(8, '0')
        line += ''.ljust(6) # data do desconto 2
        line += ''.ljust(1) # Branco
        line += ''.ljust(1) # Código de multa
        line += '{:2.2f}'.format(0.0) # Percentual de multa
        line += ''.ljust(2, '0') # Código de moeda
        line += '{:8.5f}'.format(0.0) # Valor do Boleto em outra unidade
        line += ''.ljust(4) # Brancos
        line += ''.ljust(6) # Data da multa
        line += '1'.ljust(1) # Código de carteira
        line += '01'.ljust(2) # Código da ocorrência
        line += (slip.titulo_nosso_numero or '').ljust(10) # seu número
        line += slip.due_date.strftime('%d%m%y').ljust(6) # Data de vencimiento de boleto
        line += '{:8.2f}'.format(slip.value) # Valor nominal do boleto
        line += company_id.bank_account_id.bank_id.code_bc.ljust(3) # Numero do banco cobrador VER
        line += ''.ljust(5, '0') # Código de agencia co bradora VER
        line += '01'.ljust(2) # Especie do boleto
        line += ''.ljust(1) # Identificação boleto aceite / não aceite
        line += fields.date.today().strftime('%d%m%y').ljust(6) # Data de emissão do boleto
        line += '00'.ljust(2) # Primeira instrução
        line += '00'.ljust(2) # Segunda instrução
        line += '{:8.2f}'.format(0.0) # Valor de Mora dia ***
        line += ''.ljust(6, '0') # Data Limite para concessão do desconto 
        line += '{:8.2f}'.format(0.0) # Valor do desconto a ser concedido
        line += '{:2.5f}'.format(0.0) # Percentual do IOF a ser recolhido
        line += '{:8.2f}'.format(0.0) # Valor do abatimento ou Valor do segundo desconto
        line += '02' if slip.customer.is_company else '01' # Tipo de inscrição do Pagador
        cnpj = re.sub('[^0-9]', '', slip.customer.vat)
        line += (cnpj or '').ljust(14)[:14] # Número de inscrição do Pagador 
        line += (slip.customer.legal_name or '').ljust(40)[:40] # Nome do Pagador 
        line += ((slip.customer.street_name or '') + (slip.customer.street2 or '') + (str(slip.customer.street_number) or '')).ljust(40)[:40] # Endereço do Pagador
        line += (slip.customer.district or '').ljust(12)[:12] # Bairro do Pagador
        cep = slip.customer.zip.split('-')
        line += cep[0].ljust(5) # Cep do Pagador
        if len(cep) > 1:
            line += cep[1].ljust(3) # Sufixo do Cep do Pagador
        else:
            line += ''.ljust(3, '0') # Sufixo do Cep do Pagador
        line += (slip.customer.city_id.name or '').ljust(15)[:15] # Cidade do Pagado
        line += (slip.customer.state_id.code or '').ljust(2) # Unidade de Federação do Pagador
        line += ''.ljust(30) # Brancos
        line += ''.ljust(1) # Brancos
        line += ''.ljust(1) # Identificador do complemento
        line += ''.ljust(2) # Complemento
        line += ''.ljust(6) # Brancos
        line += ''.ljust(2) # Número de dias corridos para Protesto
        line += ''.ljust(1) # Brancos
        line += ''.ljust(6) # Número sequencial do registro no arquivo 
        line += '\r\n'
        return line

    def _gerar_nosso_numero(self, numero_int):
        """ Rotina para geração do digito verificador no Nosso Numero do Banco
        :param string numero: numero a ser calculado
        :return numero novo
        """
        numero = str(numero_int)
        # Limpando o numero
        if not numero.isdigit():
            numero = re.sub('[^0-9]', '', numero)

        # verificando o tamano do  numero
        if len(numero) > 12:
            return False

        # Pega apenas os 12 primeiros dígitos do CNPJ e gera os digitos
        numero = numero.rjust(12, '0')
        numero = list(map(int, numero))
        novo = numero[:12]
        _logger.warning('CrossPy - novo: %s' % novo)

        prod = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
        while len(novo) < 13:
            r = sum([x * y for (x, y) in zip(novo, prod)]) % 11
            if r > 1:
                f = 11 - r
            else:
                f = 0
            novo.append(f)
        novo_str = [str(l) for l in novo]
        return ''.join(novo_str)

    def download_bank_slip_shipping2(self, selected_ids, company_id):
        if not company_id.plugboleto_cnpj_cedente:
            raise ValidationError(
                _("CNPJ Cedente não informado na empresa %s.") % company_id.name)
        data = self.generate_json_file(selected_ids)
        api_url = self.get_url_tecnospeed(company_id)
        data = requests.request(
            "POST",
            api_url + "/api/v1/remessas/lote",
            headers=self.headers(company_id),
            data=json.dumps(data),
        )
        data = data.json()
        if data["_status"] == "erro":
            raise ValidationError(
                "Mensagem: " + data["_mensagem"] + "\nErro: " + data["_dados"][0]["_erro"])

        data_success = False
        data_error = False
        if data["_dados"]["_sucesso"]:
            data_success = data["_dados"]["_sucesso"]
        if data["_dados"]["_falha"]:
            data_error = data["_dados"]["_falha"]

        if data_success:
            if data_success[0]["situacao"] == "GERADA":
                file_name = data_success[0]["arquivo"]
                file_content = data_success[0]["remessa"]
                bank_slips = data_success[0]["titulos"]
                attachment = self.env['ir.attachment'].create({
                    'name': file_name,
                    'datas': base64.encodebytes(file_content),
                    'res_model': 'bank.slip',
                    'type': 'binary'
                })
                for bank_slip in bank_slips:
                    bank_slip_idintegracao = bank_slip["idintegracao"]
                    bank_slip_id = self.env["bank.slip"].search(
                        [("id_integracao", "=", bank_slip_idintegracao)])
                    bank_slip_id.write({"shipping_file_id": attachment.id})
                    _logger.info(
                        "Bank slip %s has been linked to shipping file" % bank_slip_id.id)
                bank_notify = "O arquivo de remessa foi gerado com sucesso e anexado aos boletos selecionados: "
                bank_notify += file_name
                return {
                        'type': 'ir.actions.client',
                        'tag': 'display_notification',
                        'params': {
                            'title': _('Arquivo de remessa gerado'),
                            'message': '%s' % bank_notify,
                            'sticky': False,
                        }
                    }

        else:
            if data_error:
                erroMsg = "Erro ao gerar arquivo de remessa: \n"
                for result in data_error:
                    erroMsg += result["idintegracao"] + \
                        " - " + result["_erro"] + "\n"
                raise UserError(erroMsg)

    def generate_json_file(self, selected_ids):
        bank_slip = self.env["bank.slip"]
        selected_records = bank_slip.browse(selected_ids)
        if not selected_records:
            raise UserError(
                _("Nenhum boleto selecionado para Gerar a Remessa."))
        data = []
        for result in selected_records:
            data.append(result.id_integracao)

        _logger.info("Generating json file with %s bank slips" % len(data))
        return data
    
    def action_update_bank_slip(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Alterar boleto',
            'res_model': 'update.bank.slip.wizard',
            'view_mode': 'form',
            'target': 'new',
        }

    def headers(self, company_id):
        if not company_id:
            raise ValidationError(
                _("Não foi possível encontrar a empresa para gerar o boleto.")
            )
        if not company_id.plugboleto_cnpj_cedente:
            raise ValidationError(
                _("CNPJ Cedente não informado na empresa %s.") % company_id.name)
        tecnospeed = self.env["payment.acquirer"].search(
            [("provider", "=", "tecnospeed_boletos")]
        )
        headers = {
            "Content-Type": "application/json",
            "cnpj-sh": tecnospeed.tecnospeed_cnpj_sh,
            "token-sh": tecnospeed.tecnospeed_token_sh,
            "cnpj-cedente": company_id.plugboleto_cnpj_cedente
        }
        return headers
    
    def update_bank_slip_display_name(self):
        selected_ids = self.env.context.get("active_ids", [])
        if not selected_ids:
            raise UserError(
                _("Nenhum boleto selecionado para atualizar o nome.")
            )
        for bank_slip in self.env["bank.slip"].browse(selected_ids):
            if bank_slip.nfe_number:
                bank_slip.name = 'NF:{} ({})'.format(bank_slip.nfe_number, bank_slip.customer.name)
                parcela = bank_slip.invoice_line_id.name.replace('FAT', 'INV')
                parcela = parcela.replace(bank_slip.invoice.name, '').strip()
                bank_slip.invoice_name = 'NF:{} ({})'.format(bank_slip.nfe_number, parcela)
            elif bank_slip.invoice_name:
                bank_slip.name = bank_slip.invoice_name + " (" + bank_slip.customer.name + ")"
            elif bank_slip.invoice.nfe_number:
                bank_slip.name = bank_slip.invoice.nfe_number + " (" + bank_slip.customer.name + ")"
                bank_slip.invoice_name = bank_slip.invoice.nfe_number
            else:
                return

    def action_generate_boleto(self):
        for slip in self:
            slip.bbrasil_send_slip()
            slip.make_pdf()
    
    def make_pdf(self):
        # if not self.filtered(filter_processador_edoc_nfe):
        #     return super().make_pdf()
        self.ensure_one()

        file_pdf = self.pdf
        self.pdf = False
        if file_pdf:
            file_pdf = None

        # Teste Usando impressao via ReportLab Pytrustnfe
        evento_xml = []
        # cce_list = self.env['l10n_br_fiscal.event'].search([
        #     ('type', '=', '14'),
        #     ('document_id', '=', self.id),
        # ])

        # if cce_list:
        #     for cce in cce_list:
        #         cce_xml = base64.b64decode(cce.file_request_id.datas)
        #         evento_xml.append(etree.fromstring(cce_xml))

        logo = base64.b64decode(self.company_id.logo)
        bank_logo = False
        tmpBankLogo = io.BytesIO()
        # bank_account = False
        bank_account = self.company_id.bank_account_id
        if (len(self.company_id.partner_id.bank_ids) > 0 
            and self.company_id.partner_id.bank_ids[0] 
            and self.company_id.bank_account_id
            and self.company_id.bank_account_id.bank_id
            and self.company_id.bank_account_id.bank_id.logo
        ):
            bank_logo = base64.b64decode(bank_account.bank_id.logo)
            tmpBankLogo.write(bank_logo)
            tmpBankLogo.seek(0)

        tmpLogo = io.BytesIO()
        tmpLogo.write(logo)
        tmpLogo.seek(0)

        timezone = pytz.timezone(self.env.context.get('tz') or 'UTC')
        # xml_element = etree.fromstring(xml_string)

        # cancel_list = self.env['l10n_br_fiscal.event'].search([
        #     ('type', '=', '2'),
        #     ('document_id', '=', self.id),
        # ])
        # if cancel_list:
        #     cancel_xml = base64.b64decode(cancel_list.file_request_id.datas).decode()
        #     evento_xml.append(etree.fromstring(cancel_xml))

        xml_element = '1234567890'
        company_partner = self.company_id.partner_id
        invoice_partner = self.invoice.partner_id
        barcode = self._get_barcode()
        barcode_DV = barcode[4:5]
        # _logger.warning('CrossPy - barcode: \n%s' % barcode)
        data = {
            'nChave': barcode,
            'bank_code': '| %s - 7 |' % bank_account.bank_id.code_bc,
            'bankLogo': tmpBankLogo,
            'nro_banco': self._gerar_number(barcode, bank_account_id=bank_account),
            'date_due': self.due_date.strftime('%d/%m/%Y'),
            'date_doc': self.invoice.invoice_date.strftime('%d/%m/%Y'),
            'bank_agency': bank_account.bra_number,
            'number': self.invoice_name,
            'titulo_nosso_numero': (self.titulo_nosso_numero).rjust(13, '0'),
            'value': '{:,.2f}'.format(float(self.value)),
            'payment_issue': 'Pagável em qualquer banco até o vencimento',
            'benefCode': '7000677',
            'benefName1': company_partner.legal_name or company_partner.name + '- CNPJ' + self.company_id.partner_id.vat,
            # 'benefName1': company_partner.legal_name + '- CNPJ' + '19.860.927/0001-20',
            'benefName2': '%s, %s, %s, %s, %s, %s' % (company_partner.street_name, 
                                              company_partner.street_number, 
                                              company_partner.district, 
                                              company_partner.city_id.name,
                                              company_partner.zip,
                                              company_partner.state_id.name),
            'pagadorName1': invoice_partner.legal_name or invoice_partner.name + '- CNPJ ' + invoice_partner.vat,
            'pagadorName2': '%s, %s, %s, %s, %s, %s' % (invoice_partner.street_name, 
                                              invoice_partner.street_number, 
                                              invoice_partner.district, 
                                              invoice_partner.city_id.name,
                                              invoice_partner.zip,
                                              invoice_partner.state_id.name),
            'company_partner_name': self.company_id.partner_id.legal_name,
            'company_partner_vat': self.company_id.partner_id.vat,
            'especie': 'DM',
            'approved': 'N',
            'carteira': 'COB. SIMPLES RCR',
            'especie_moeda': 'R$'
            }

        oBoleto = boleto(list_xml=[xml_element], logo=tmpLogo, data=data,
            evento_xml=evento_xml, timezone=timezone)
        tmpBoleto = io.BytesIO()
        oBoleto.writeto_pdf(tmpBoleto)
        boleto_file = tmpBoleto.getvalue()
        tmpBoleto.close()

        # base64.b64encode(bytes(tmpDanfe)),
        pattern = r"parcela\s+n[ºo°](\d+)"
        match = re.search(pattern, self.invoice_name, re.IGNORECASE)
        file_name = ''
        if match:
            file_name = match.group(1)
        file_name = 'NF{}_{}'.format(self.nfe_number or self.invoice_name or '', file_name)
        self.pdf_name = file_name + ".pdf"
        self.pdf = base64.b64encode(boleto_file)

    def action_update_bank_slip(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Alterar boleto',
            'res_model': 'update.bank.slip.wizard',
            'view_mode': 'form',
            'target': 'new',
        }

    def _get_first_group_DV(self, number):
        number_base = list(map(int, number))
        prod = [2, 1, 2, 1, 2, 1, 2, 1, 2]
        r = [x * y for (x, y) in zip(number_base, prod)]
        r = sum([sum(x) for x in [list(map(int,str(y))) for y in r]]) % 10
        f = 10 - r
        if f == 10:
            f = 0
        return str(f)

    def _get_second_group_DV(self, number):
        number_base = list(map(int, number))
        prod = [1, 2, 1, 2, 1, 2, 1, 2, 1, 2]
        r = [x * y for (x, y) in zip(number_base, prod)]
        r = sum([sum(x) for x in [list(map(int,str(y))) for y in r]]) % 10
        f = 10 - r
        if f == 10:
            f = 0
        return str(f)

    def _get_third_group_DV(self, number):
        number_base = list(map(int, number))
        prod = [1, 2, 1, 2, 1, 2, 1, 2, 1, 2]
        r = [x * y for (x, y) in zip(number_base, prod)]
        r = sum([sum(x) for x in [list(map(int,str(y))) for y in r]]) % 10
        f = 10 - r
        if f == 10:
            f = 0
        return str(f)

    def _gerar_number(self, barcode='', bank_account_id=None):
        if not bank_account_id:
            bank_account_id = self.bank_account_id
        bank_code = (bank_account_id.bank_id.code_bc).ljust(3)
        partner_code = self.env.company.bank_contract.strip()
        nn = (self.titulo_nosso_numero).rjust(13, '0')
        # First Group
        number1 = bank_code     # 01-03 3 9 (03) Banco = 033
        number1 += '9'          # 04-04 1 9 (01) Código da moeda = 9 (real)
                                # Código da moeda = 8 (outras moedas)
        number1 += barcode[20:25]  # 05-05 1 9 (01) Fixo “9”
        # number1 += partner_code[:4]    # 06-09 4 9 (04) Código do Beneficiário padrão Santander
        number1 += self._get_first_group_DV(number1)  # 10-10 1 9 (01) Código verificador do primeiro grupo

        # Second Group
        number2 = barcode[24:34]  # 11-13 3 9 (03) Restante do código do beneficiário
                                    # padrão Santander
        # number2 += nn[:7]             # 14-20 7 9 (07) 7 primeiros campos do N/N
        number2 += self._get_second_group_DV(number2) # 21-21 1 9 (01) Dígito verificador do segundo grupo

        # Third Group
        number3 = barcode[34:44]    # 22-27 6 9 (06) Restante do Nosso Número com DV
        # number3 += '0'          # 28-28 1 9 (01) Fixo “0”
        # number3 += '01'         # 29-31 3 9 (03) Tipo de Modalidade Carteira
                                #                101-Cobrança Rápida COM Registro
                                #                104-Cobrança Eletrônica COM Registro
        number3 += self._get_third_group_DV(number3)       # 32-32 1 9 (01) Dígito verificador do terceiro grupo        
                                
        # Fourth Group
        number4 = barcode[4:5] # 33-33 1 9 (01) Dígito Verificador do Código de Barras

        # Fifth Group
        number5 = str((self.due_date - date(2000, 7, 3)).days + 2000)[-4:]
        number5 += '{:011.2f}'.format(self.value).replace('.','') # Valor nominal do boleto
        return number1 + number2 + number3 + number4 + number5

    def _get_barcode(self):
        bank_code = (self.env.company.bank_account_id and self.env.company.bank_account_id.bank_id.code_bc or '033').ljust(3)
        partner_code = self.env.company.bank_contract.strip()
        # nn = self.titulo_nosso_numero
        if self.titulo_nosso_numero:
            nn = (self.titulo_nosso_numero).rjust(13, '0')
        elif self.env.company.next_bank_number:
            nn = ('{}'.format(self.env.company.next_bank_number)).rjust(13, '0')
            self.env.company.next_bank_number += 1
            self.titulo_nosso_numero = nn
        else:
            raise UserError(_('Slip without Bank Number'))

        # First Group
        number1 = bank_code     # 01-03 3 9 (03) Banco = 033
        number1 += '9'          # 04-04 1 9 (01) Código da moeda = 9 (real)
                                # Código da moeda = 8 (outras moedas)
        number1 += '0'  # 05-05 1 9 (01) DV do código de barra
        number1 += str((self.due_date - date(2000, 7, 3)).days + 2000)[-4:]    # 06-09 4 9 (04) Fator de vencimento
        number1 += '{:011.2f}'.format(self.value).replace('.','')      # 10-19 10 9 (08)V99 Valor nominal do boleto
        number1 += '000000'  # 20-20 1 9 (01) Fixo “9”
        number1 += partner_code[:7]     # 21-27 7 9 (07) Código do beneficiário o convenio padrão Santander
        number1 += nn[-10:]      # 28-40 13 9 (13) Nosso Número com DV
        # number1 += '0'      # 41-41 1 9 (01) Fixo “0”
        number1 += '17'    # 42-44 3 9 (03) Tipo de Modalidade Carteira
                            #                  101-Cobrança Rápida COM Registro
                            #                  104-Cobrança Eletrônica COM Registro
        
        return self._update_barcode_DV(number1)

    def _update_barcode_DV(self, numero):
        """ Rotina para geração do digito verificador no Nosso Numero do Banco
        :param string numero: numero a ser calculado
        :return numero novo
        """
        # Limpando o numero
        if not numero.isdigit():
            numero = re.sub('[^0-9]', '', numero)

        # verificando o tamano do  numero
        if len(numero) > 44:
            return False

        # Pega apenas os 12 primeiros dígitos do CNPJ e gera os digitos
        if not len(numero) == 43:
            numero = numero.rjust(44, '0')
            numero = list(map(int, numero))
            novo = numero[:44]
            novo.pop(4)
        else:
            novo = list(map(int, numero))

        prod = [4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]

        while len(novo) < 44:
            r = (sum([x * y for (x, y) in zip(novo, prod)]) * 10) % 11
            if r in (0, 1, 10):
                r = 1
            novo.insert(4, r)
        novo_str = [str(l) for l in novo]
        return ''.join(novo_str)

    def action_send_email(self):
        invoice_ids = self.mapped('invoice').ids
        edocs = self.env['eletronic.document'].search(
                [('move_id', 'in', invoice_ids)])
        edocs.send_email_nfe()
        # return True
    
    def action_send_about_maturity(self):
        template = False
        try:
            template = self.env.ref('l10n_br_boleto.mail_template_slip_about_maturity')
        except ValueError:
            pass
        assert template._name == 'mail.template'

        for slip in self.filtered(lambda x: x.due_date == (datetime.now() +  relativedelta(days=1)).date()):
            attachment = self.env['ir.attachment'].create({
                'datas': slip.pdf,
                'name': slip.pdf_name,
                'mimetype': 'application/pdf',
                'type': 'binary',
            })

            invoice_emails = slip._get_invoice_emails()

            template_values = {
                'email_cc': False,
                'scheduled_date': False,
                'attachment_ids': attachment.ids,
            }
            if len(invoice_emails):
                template_values.update({'email_to': ', '.join(invoice_emails)})
            email_values = template._generate_template(slip.ids, tuple(template_values.keys()))
            email_values[slip.id].update(template_values)
            template.send_mail(slip.id, email_values=email_values[slip.id], force_send=True)

    def action_send_overdue(self):
        template = False
        try:
            template = self.env.ref('l10n_br_boleto.mail_template_slip_overdue')
        except ValueError:
            pass
        assert template._name == 'mail.template'

        for slip in self.filtered(lambda x: x.due_date < datetime.now().date() and not x.reconciled):
            attachment = self.env['ir.attachment'].create({
                'datas': slip.pdf,
                'name': slip.pdf_name,
                'mimetype': 'application/pdf',
                'type': 'binary',
            })

            invoice_emails = slip._get_invoice_emails()

            template_values = {
                'scheduled_date': False,
                'attachment_ids': attachment.ids,
            }
            if len(invoice_emails):
                template_values.update({'email_to': ', '.join(invoice_emails)})
            email_values = template._generate_template(slip.ids, tuple(template_values.keys()))
            email_values[slip.id].update(template_values)
            template.send_mail(slip.id, email_values=email_values[slip.id], force_send=True)

    def _get_invoice_emails(self, customer=None):
        if not customer:
            customer = self.customer
        return ['%s <%s>' % (invoice_contact.name, invoice_contact.email) for invoice_contact in customer.child_ids if invoice_contact.type == 'invoice' and invoice_contact.email]

 
    def action_send(self):
        # to_slowdown = self._check_daily_logs()
        to_slowdown = self.env['bank.slip']
        for slip in self:
            for customer in slip.customer:
                slip.with_context(
                    slip_slowdown=slip in to_slowdown,
                    lang=customer.lang
                )._action_send_to_customer(customer, tips_count=1)

    def _action_send_to_customer(self, customer, tips_count=1, consum_tips=True):
        template = False
        try:
            template = self.env.ref('acccount.email_template_edi_invoice')
        except ValueError:
            pass
        assert template._name == 'mail.template'

        attachments = self.env['ir.attachment']
        for slip in self.invoice.bank_slip_ids:
            attachment = self.env['ir.attachment'].create({
                'datas': slip.pdf,
                'name': slip.pdf_name,
                'mimetype': 'application/pdf',
                'type': 'binary',
            })
            attachments |= attachment

        invoice_emails = ['%s <%s>' % (invoice_contact.name, invoice_contact.email) for invoice_contact in customer.child_ids if invoice_contact.type == 'invoice' and invoice_contact.email]

        template_values = {
            'email_to': ', '.join(invoice_emails) if invoice_emails else False, # '${object.customer.email|safe}',
            'email_cc': False,
            'auto_delete': True,
            'partner_to': customer.id if not invoice_emails else False, # False,
            'scheduled_date': False,
            'attachment_ids': attachments.ids,
        }
        template.write(template_values)

        no_email_slips = self.env['bank.slip']

        for slip in self:
            if not invoice_emails and not slip.customer.email:
                no_email_slips |= slip
                _logger.warning("Email NOT sent for customer <%s> to <%s>", customer.name, slip.name)
                # raise UserError(_("Cannot send email: customer %s has no email address.", customer.name))
            # TDE FIXME: make this template technical (qweb)
            with self.env.cr.savepoint():
                force_send = not(self.env.context.get('import_file', False))
                slip.sent_mail_id = template.send_mail(self.invoice.id, force_send=force_send, raise_exception=True)
            _logger.info("Email sent for customer <%s> to <%s>", customer.name, customer.email)

    def _action_send_to_customer_invoice(self, customer, tips_count=1, consum_tips=True):
        
        template = False
        try:
            template = self.env.ref('acccount.email_template_edi_invoice')
            # template = self.env['mail.template'].browse(36)
        except ValueError:
            pass
        assert template._name == 'mail.template'

        attachments = self.env['ir.attachment']
        for slip in self.invoice.bank_slip_ids:
            attachment = self.env['ir.attachment'].create({
                'datas': slip.pdf,
                'name': slip.pdf_name,
                'mimetype': 'application/pdf',
                'type': 'binary',
            })
            attachments |= attachment

        invoice_emails = ['%s <%s>' % (invoice_contact.name, invoice_contact.email) for invoice_contact in customer.child_ids if invoice_contact.type == 'invoice' and invoice_contact.email]

        template_values = {
            'email_to': ', '.join(invoice_emails) if invoice_emails else False, # '${object.customer.email|safe}',
            'email_cc': False,
            'auto_delete': True,
            'partner_to': customer.id if not invoice_emails else False, # False,
            'scheduled_date': False,
            'attachment_ids': attachments.ids,
        }

        # template.generate_email(self.invoice.id)
        template.write(template_values)

        no_email_slips = self.env['bank.slip']

        for slip in self:
            if not invoice_emails and not slip.customer.email:
                no_email_slips |= slip
                _logger.warning("Email NOT sent for customer <%s> to <%s>", customer.name, slip.name)
                # raise UserError(_("Cannot send email: customer %s has no email address.", customer.name))
            # TDE FIXME: make this template technical (qweb)
            with self.env.cr.savepoint():
                force_send = not(self.env.context.get('import_file', False))
                slip.sent_mail_id = template.send_mail(self.invoice.id, force_send=force_send, raise_exception=True)
            _logger.info("Email sent for customer <%s> to <%s>", customer.name, customer.email)

    def action_view_invoice(self, invoices=False):
        """This function returns an action that display  vinculated invoice and show it in form view"""
        action = self.env.ref("account.action_move_out_invoice_type").read()[0]
        action["views"] = [(self.env.ref("account.view_move_form").id, "form")]
        action["res_id"] = self.invoice.id
        return action

    @api.model
    def _cron_send_slip_email(self):
        if fields.Date.today().weekday() in (5, 6):
            return None
        date = fields.Date.today() + relativedelta(days=1)
        domain = [('due_date', '=', date)]
        while date.weekday() in (5, 6):
            date += relativedelta(days=1)
            domain = OR([domain, [('due_date', '=', date)]])
        domain = AND([domain, [('status', '=', 'REGISTRADO')]])
        
        bankslips = self.search(domain)
        for bankslip in bankslips:
            try:
                bankslip.action_send()
            except MailDeliveryException as e:
                _logger.warning('MailDeliveryException while sending slip %d. Slip is now scheduled for next cron update.', bankslip.name)

    # Check if the mails are already generated

    def _check_daily_logs(self):
        one_day_ahead = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + relativedelta(days=1)
        to_slowdown = self.env['bank.slip']
        for slip in self.filtered(lambda slip: slip.due_date == 'daily'):
            users_logs = self.env['res.users.log'].sudo()
            # users_logs = self.env['res.users.log'].sudo().search_count([
            #     ('create_uid', 'in', digest.user_ids.ids),
            #     ('create_date', '>=', three_days_ago)
            # ])
            if not users_logs:
                to_slowdown += slip
        return to_slowdown

    def compute_preferences(self, company, partner_id):
        """ Give an optional text for preferences, like a shortcut for configuration.

        :return string: html to put in template
        """
        preferences = []
        if self._context.get('digest_slowdown'):
            preferences.append(_("We have noticed you did not connect these last few days so we've automatically switched your preference to weekly Digests."))
        elif self.periodicity == 'daily' and partner_id.has_group('base.group_erp_manager'):
            preferences.append('<p>%s<br /><a href="/digest/%s/set_periodicity?periodicity=weekly" target="_blank" style="color:#875A7B; font-weight: bold;">%s</a></p>' % (
                _('Prefer a broader overview ?'),
                self.id,
                _('Switch to weekly Digests')
            ))
        if partner_id.has_group('base.group_erp_manager'):
            preferences.append('<p>%s<br /><a href="/web#view_type=form&amp;model=%s&amp;id=%s" target="_blank" style="color:#875A7B; font-weight: bold;">%s</a></p>' % (
                _('Want to customize this email?'),
                self._name,
                self.id,
                _('Choose the metrics you care about')
            ))

        return preferences
