from collections import Counter
from datetime import datetime
import json
import re
import logging

import requests

from odoo import api, models, _, fields
from odoo.exceptions import ValidationError, UserError


_logger = logging.getLogger(__name__)

class Partner(models.Model):
    _name = "res.partner"
    _inherit = [_name, "l10n_br_base.party.mixin"]

    cnpjws_atualizadoem = fields.Datetime(
        string="Public data in",
        help="Date of the last update of information in the public database",
        readonly=True,
        copy=False
    )
    cnpjws_nome_fantasia = fields.Char(
        string="Fantasy name",
        readonly=True,
        copy=False
    )
    cnpjws_situacao_cadastral = fields.Char(
        string="Registration Status",
        readonly=True,
        copy=False
    )
    cnpjws_tipo = fields.Char(
        string="CNPJ Type",
        readonly=True,
        copy=False
    )

    cnpjws_porte = fields.Char(
        string="Company size",
        readonly=True,
        copy=False
    )

    cnpjws_atualizado_odoo = fields.Datetime(
        string="Updated on Odoo",
        help="Date of last update of information on Odoo",
        readonly=True,
        copy=False
    )

    cnpjws_razao_social = fields.Char(
        string="Full Legal Name",
        readonly=True,
        copy=False
    )

    cnpjws_size_legal_name = fields.Integer(
        string="Legal Name Size",
        readonly=True,
        copy=False,
        default=0
    )

    cnpjws_manual_razao_social = fields.Boolean(
        string="Needs adjustment Legal Name",
        help="If this option is checked, it means that the Corporate Name is longer than 60 characters and you need to manually adjust it.",
        copy=False,
        default=False
    )

    cnpjws_size_adress = fields.Integer(
        string="Address size",
        readonly=True,
        copy=False,
        default=0
    )

    cnpjws_manual_adress = fields.Boolean(
        string="Needs adjustment Address",
        help="If this option is checked, it means that the Address is longer than 60 characters and you need to manually adjust it.",
        copy=False,
        default=False
    )

    cnpjws_other_ies = fields.Text(
        string="Other IEs",
        readonly=True,
        copy=False,
        tracking=True
    )

    def action_consult_cnpj(self):
        cnpjws_url = 'https://publica.cnpj.ws/cnpj/'
        if self.company_type == 'company':
            if self.vat:
                cnpj = re.sub('[^0-9]', '', self.vat)
                response = requests.get(cnpjws_url + cnpj)
                cnpjws_result = json.loads(response.text)
                if response.status_code == 200:
                    if len(cnpjws_result['razao_social']) > 60:
                        self.cnpjws_manual_razao_social = True

                    self.legal_name = cnpjws_result['razao_social']
                    self.cnpjws_razao_social = cnpjws_result['razao_social']

                    self.cnpjws_size_legal_name = len(self.legal_name)

                    cnpjws_estabelecimento = cnpjws_result['estabelecimento']
                    cnpjws_pais = cnpjws_result['estabelecimento']['pais']['comex_id']
                    cnpjws_estado = cnpjws_estabelecimento['estado']
                    cnpjws_cidade = cnpjws_estabelecimento['cidade']


                    search_country = self.env['res.country'].search(
                        [('siscomex_code', '=', cnpjws_pais)])
                    if search_country:
                        self.country_id = search_country.id

                    if cnpjws_estado['ibge_id']:
                        search_state = self.env['res.country.state'].search(
                            [('ibge_code', '=', cnpjws_estado['ibge_id'])])
                        if search_state:
                            self.state_id = search_state.id

                    if cnpjws_cidade:
                        self.city = cnpjws_cidade['nome']
                        search_city = self.env['res.city'].search(
                            [('ibge_code', '=', cnpjws_cidade['ibge_id'])])
                        if search_city:
                            self.city_id = search_city.id

                    self.zip = cnpjws_estabelecimento['cep']
                    fulladress = ""

                    if cnpjws_estabelecimento['tipo_logradouro']:
                        cnpj_t_logra = cnpjws_estabelecimento['tipo_logradouro']
                        cnpj_logra = cnpjws_estabelecimento['logradouro']

                        self.street_name = cnpj_t_logra + " " + cnpj_logra

                    self.street_number = cnpjws_estabelecimento['numero']

                    if self.street_number == "S/N":
                        self.street_number = "SN"

                    self.district = cnpjws_estabelecimento['bairro']

                    self.street2 = cnpjws_estabelecimento['complemento']

                    if cnpjws_estabelecimento['telefone1']:
                        if 'ddd1' in cnpjws_estabelecimento:
                            self.phone =  '(%s) %s' % (cnpjws_estabelecimento['ddd1'], cnpjws_estabelecimento['telefone1'])
                        else:
                            self.phone =  cnpjws_estabelecimento['telefone1']
                    
                    if cnpjws_estabelecimento['email']:
                        self.email = cnpjws_estabelecimento['email']

                    if self.street_name:
                        fulladress = self.street_name
                    if self.street_number:
                        fulladress += ", " + self.street_number
                    if self.street2:
                        self.street2 = re.sub(' +', ' ', self.street2)
                        fulladress += " - " + self.street2

                    self.cnpjws_size_adress = len(fulladress)
                    if self.cnpjws_size_adress > 60:
                        self.cnpjws_manual_adress = True

                    cnpjws_simples = cnpjws_result['simples']
                    cnpjws_ie = cnpjws_estabelecimento['inscricoes_estaduais']
                    cnpjws_cnae = cnpjws_estabelecimento['atividade_principal']

                    fiscal_info = []

                    fiscal_info.append(cnpjws_simples)
                    fiscal_info.append(cnpjws_ie)
                    fiscal_info.append(cnpjws_cnae)

                    self.define_fiscal_profile_id(fiscal_info)
                    self.cnpjws_atualizadoem = datetime.strptime(
                        cnpjws_result['atualizado_em'], '%Y-%m-%dT%H:%M:%S.%fZ')
                    self.cnpjws_nome_fantasia = cnpjws_estabelecimento['nome_fantasia']
                    self.cnpjws_tipo = cnpjws_estabelecimento['tipo']
                    self.cnpjws_situacao_cadastral = cnpjws_estabelecimento['situacao_cadastral']
                    self.cnpjws_porte = cnpjws_result['porte']['descricao'] if cnpjws_result['porte'] else False
                    self.cnpjws_atualizado_odoo = datetime.now()
                    #if language pt_BR is installed
                    if self.env['res.lang'].search([('code', '=', 'pt_BR')]):
                        self.lang = 'pt_BR'
                else:
                    raise ValidationError(_(
                        "Error: " + cnpjws_result['titulo'] + '\n' + cnpjws_result['detalhes'] + '\n' 'If the company is from Brazil, enter the correct CNPJ. Otherwise, complete the registration manually.'))
            else:
                raise UserError(_('Please enter the CNPJ.'))
        else:
            raise ValidationError(_('This option is only available for companies.'))

    def define_inscricao_estadual(self, fiscal_info):
        result_ie = fiscal_info[1]
        others_ie = ''

        if result_ie == []:
            self.inscr_est = ''
        else:
            for ie in result_ie:
                if ie['ativo'] == True:
                    search_state = self.env['res.country.state'].search([('ibge_code', '=', ie['estado']['ibge_id'])])

                    if search_state:
                        if self.state_id.code == ie['estado']['sigla']:
                            self.inscr_est = ie['inscricao_estadual']
                        else:
                            others_ie += "IE: " + ie['inscricao_estadual'] + " - " + ie['estado']['sigla'] + "\n"
                    else:
                        _logger.info("Estado não encontrado: " + ie['estado']['sigla'])

        if others_ie != '':
            self.cnpjws_other_ies = others_ie


    def define_fiscal_profile_id(self, fiscal_info):
        module_l10n_br_fiscal = self.env['ir.module.module'].search(
            [('name', '=', 'l10n_br_fiscal'), ('state', '=', 'installed')])

        result_simples = fiscal_info[0]

        if module_l10n_br_fiscal:
            # SNC - Contribuinte Simples Nacional
            # SNN - Simples Nacional Não Contribuinte
            # CNT - Contribuinte
            # NCN - Não Contribuinte
            search_fiscal_profile_id = self.env["l10n_br_fiscal.partner.profile"]

            profile_snc = search_fiscal_profile_id.search(
                [('code', '=', 'SNC')]).id
            profile_snn = search_fiscal_profile_id.search(
                [('code', '=', 'SNN')]).id
            profile_cnt = search_fiscal_profile_id.search(
                [('code', '=', 'CNT')]).id
            profile_ncn = search_fiscal_profile_id.search(
                [('code', '=', 'NCN')]).id

            self.define_inscricao_estadual(fiscal_info)
            contribuinte_icms = False
            if self.inscr_est:
                contribuinte_icms = True

            simples_nacional = False
            if result_simples:
                if result_simples['simples'] == 'Sim' or result_simples['mei'] == 'Sim':
                    simples_nacional = True

            if contribuinte_icms and result_simples:
                if simples_nacional:
                    self.fiscal_profile_id = profile_snc
                else:
                    self.fiscal_profile_id = profile_cnt
            elif not contribuinte_icms and result_simples:
                if simples_nacional:
                    self.fiscal_profile_id = profile_snn
                else:
                    self.fiscal_profile_id = profile_ncn
            elif contribuinte_icms and not result_simples:
                self.fiscal_profile_id = profile_cnt
            elif not contribuinte_icms and not result_simples:
                self.fiscal_profile_id = profile_ncn

            if fiscal_info[2]:
                search_cnae = self.env["l10n_br_fiscal.cnae"].search(
                    [('code', '=', fiscal_info[2]['subclasse'])])

                if search_cnae:
                    try:
                        incluir_cnae_principal = self.write(
                            {"cnae_main_id": search_cnae.id})
                        _logger.info("CNAE Main added successfully %s",
                                     str(incluir_cnae_principal))
                    except Exception:
                        incluir_cnae_principal = False
                        raise ValidationError(_(
                            "Error to include CNAE Main %s." % fiscal_info[2]['subclasse']))

        else:
            self.define_inscricao_estadual(fiscal_info)

    def write(self, vals):

        if "legal_name" in vals:
            if len(vals["legal_name"]) <= 60:
                self.cnpjws_size_legal_name = len(vals["legal_name"])
                self.cnpjws_manual_razao_social = False
            else:
                self.cnpjws_size_legal_name = len(vals["legal_name"])
                self.cnpjws_manual_razao_social = True

        if "street" in vals:
            if vals["street"] != False and len(vals["street"]) <= 60:
                self.cnpjws_size_adress = len(vals["street"])
                self.cnpjws_manual_adress = False

        return super().write(vals)
