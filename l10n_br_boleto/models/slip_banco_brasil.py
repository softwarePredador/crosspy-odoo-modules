# -*- coding: utf-8 -*-

import json
import logging
import re
import requests
import base64
from odoo.addons.phone_validation.tools import phone_validation
from requests.auth import HTTPBasicAuth


from requests.exceptions import RequestException
from werkzeug.urls import url_join
from collections import defaultdict
from datetime import datetime, timedelta
import certifi

from odoo import fields, _
from odoo.exceptions import UserError, ValidationError, RedirectWarning
from odoo.tools import format_date

_logger = logging.getLogger(__name__)

class BBrasilProvider:

    def __init__(self, carrier, logger, prod_environment=False):
        """
        Initial parameters for making api requests.
        """
        self.logger = logger
        # Homologación - self.url = 'https://api.hm.bb.com.br/testes-portal-desenvolvedor/v2/'
        self.url = 'https://api.bb.com.br/cobrancas/v2/'
        if prod_environment:
            self.url = ''
        self.session = requests.Session()
        self._get_access_token()
        

    def _make_api_request(self, endpoint, method='GET', auth=None, params=None, access_token=None):
        """
        make an api call, return response for multiple api requests of Banco do Brasil
        """
        if self.expires_at and self.expires_at < datetime.now():
            self._get_access_token()
            # Include authorization header if a token is provided
            # headers['Authorization'] = 'Bearer {}' .format(access_token)
            # auth = None
        gw_key = '584ca071f4716cbb16ad569ee75f8f38'

        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json',
            'gw-dev-app-key': gw_key
        }
        json_params = {}    
        if method == 'POST':
            json_params = params.copy()
            params = {}
            
        access_url = endpoint
        if endpoint[:4] != 'http':
            access_url = url_join(self.url, endpoint)
        
        try:
            # Log the request details for debugging purposes
            # self.logger("%s\n%s\n%s" % (access_url, method, data), 'correios_request_%s' % endpoint)
            # Make the API request
            response = self.session.request(method, access_url, auth=auth, params=params, json=json_params, headers=headers)
            # Parse the response as JSON
            response_json = response.json()
            # Log the response details for debugging purposes
            # self.logger("%s\n%s" % (response.status_code, response.text), 'correios_response_%s' % endpoint)
            return response_json
        except requests.exceptions.ConnectionError as error:
            _logger.warning('Connection Error: %s with the given URL: %s', error, access_url)
            return {'errors': {'timeout': "Cannot reach the server. Please try again later."}}
        except json.decoder.JSONDecodeError as error:
            _logger.warning('JSONDecodeError: %s', error)
            return {'errors': {'JSONDecodeError': str(error)}}
        

    def _send_slip(self, slip):
        """
        Returns the dictionary with slip data
        """

        res = {
            'exact_price': 0.00,
            'tracking_numbers': [],
            'order_ids': [],
            'all_pack': defaultdict(lambda: {'response': {}, 'order_details': {}})
        }

        res = False

        if slip.status == 'REGISTRADO':
            payload = self._make_api_request('boletos', 'POST', 
                                                params=self._get_slip_params(slip), 
                                                access_token=self.access_token
            )
            if payload.get('erros'):
                slip.message_post(body=payload.get('erros'))
                return res
            if not payload:
                slip.message_post(body=_('AWB assignment was unsuccessful: %s') % (self._correios_get_error_message(payload)))
                return res
            if not payload or payload.get('errors'):
                raise ValidationError(_('Erro no processo de registro no correios'))
            if payload.get('numero'):
                slip.titulo_nosso_numero = payload.get('numero')[-10:]
                slip.status = 'EMITIDO'
        return slip.id

    def _get_slip_params(self, slip):
        """
        Returns the slip data from slip to send to bank

        Homologação data

        Nome da Empresa	                    CNPJ
        TECIDOS FARIA DUARTE 	        74910037000193
        LIVRARIA CUNHA DA CUNHA	        98959112000179
        DOCERIA BARBOSA DE ALMEIDA	    92862701000158
        DEPOSITO ALVES BRAGA	        94491202000127
        PAPELARIA FILARDES GARRIDO	    97257206000133
        
        Nome	                            CPF
        VALERIO DE AGUIAR ZORZATO 	    96050176876
        JOAO DA COSTA ANTUNES	        88398158808
        VALERIO ALVES BARROS	        71943984190
        JOÃO DA COSTA ANTUNES	        97965940132
        JOÃO DA COSTA ANTUNES	        75069056123

        """
        if not slip:
            return {}
        
        dataVencimento = slip.due_date
        if slip.due_date < fields.Date.today():
            dataVencimento = fields.Date.today()

        next_nosso_numero = ('{}'.format(slip.env.company.next_bank_number)).rjust(10, '0')
        slip.titulo_nosso_numero = next_nosso_numero
        slip.env.company.next_bank_number += 1
        numeroConvenio = slip.env.company.bank_contract.strip()


        parcel_dict = {
            "numeroConvenio": numeroConvenio,
            "numeroCarteira": 17,
            'numeroVariacaoCarteira': 19,
            'codigoModalidade': 1,
            'dataEmissao': fields.Date.today().strftime("%d.%m.%Y"),
            'dataVencimento': dataVencimento.strftime("%d.%m.%Y"),
            'valorOriginal': slip.value,
            # 'valorAbatimento': 0.0,
            'quantidadeDiasProtesto': 15,
            'indicadorAceiteTituloVencido': 'S',
            'numeroDiasLimiteRecebimento': 15,
            'codigoAceite': 'A',
            'codigoTipoTitulo': 2,
            'descricaoTipoTitulo': 'DM',
            'indicadorPermissaoRecebimentoParcial': 'N',
            'numeroTituloBeneficiario': re.sub(r'[^a-zA-Z0-9]', '', slip.invoice_line_id.display_name or ''),
            # 'campoUtilizacaoBeneficiario': 'Teste CroossPy',
            'numeroTituloCliente': '{:010d}{:010d}'.format(int(numeroConvenio), int(slip.titulo_nosso_numero)),
            # "{:010d}".format(number)
            # 'desconto': {
            #     'tipo': 0,
            #     'dataExpiracao': 'string',
            #     'porcentagem': 0,
            #     'valor': 0
            # },
            # 'segundoDesconto': {
            #     'dataExpiracao': 'string',
            #     'porcentagem': 0,
            #     'valor': 0
            # },
            # 'terceiroDesconto': {
            #     'dataExpiracao': '20.04.2025',
            #     'porcentagem': 5.00,
            #     'valor': slip.value * 5/100
            # },
            'jurosMora': {
                'tipo': 2,
                'porcentagem': 0.39,
                # 'valor': slip.value * 1/100
            },
            'multa': {
                'tipo': 2,
                'data': (dataVencimento + timedelta(days=1)).strftime("%d.%m.%Y"),
                'porcentagem': 3.00,
                # 'valor': slip.value * 2/100
            },
            'pagador': {
                'tipoInscricao': '2' if slip.customer.is_company else '1',
                'numeroInscricao': re.sub(r'[^0-9]', '', slip.customer.vat),
                'nome': slip.customer.legal_name or slip.customer.name,
                'endereco': '{}'.format(slip.customer.street),
                'cep': int('{}'.format(re.sub(r'[^0-9]', '', slip.customer.zip))),
                'cidade': '{}'.format(slip.customer.city_id.name),
                'bairro': '{}'.format(slip.customer.district),
                'uf': '{}'.format(slip.customer.state_id.code),
                'telefone': '{}'.format(slip.customer.phone or ''),
                'email': '{}'.format(slip.customer.email or ''),
            },
            'beneficiarioFinal': {
                'tipoInscricao': '2' if slip.env.company.partner_id.is_company else '1',
                'numeroInscricao': re.sub(r'[^0-9]', '', slip.env.company.partner_id.vat),
                'nome': slip.env.company.partner_id.legal_name or slip.env.company.partner_id.name
            },
        }
        return parcel_dict

    def _get_query_params(self, slip, baixados=False):
        """
        Returns the query slip parameters
        Required:
        - _get_query_params: not need because its standard
        - indicadorSituacao: Situação do boleto. Campo obrigatoriamente MAIÚSCULO. 
                Domínios: A - Em ser B - Baixados/Protestados/Liquidados
        - agenciaBeneficiario: Número da agência do beneficiário, sem o dígito verificador. 
                Ex: 452. CAMPO OBRIGATÓRIO.
        - contaBeneficiario: Número da conta do beneficiário, sem o dígito verificador. 
                Ex: 123873. CAMPO OBRIGATÓRIO.
        - Authorization: not need because its standard
        """
        # if not slip:
        #     return {}
        bra_number = re.sub(r'[^a-zA-Z0-9]', '', slip.env.company.bank_account_id.bra_number or '')
        acc_number = re.sub(r'[^a-zA-Z0-9]', '', slip.env.company.bank_account_id.acc_number or '')
        if not (bra_number and acc_number):
            raise ValidationError(_('Account configuration error'))

        params_dict = {
            'gw-dev-app-key': '584ca071f4716cbb16ad569ee75f8f38',
            'indicadorSituacao': 'B' if baixados else 'A',
            'agenciaBeneficiario': bra_number,
            'contaBeneficiario': acc_number,
        }
        return params_dict


    def _get_access_token(self):
        """
        Generate an access token for correios.
        The token is automatically generates after 9 days as it expires.
        """
        client_id = 'eyJpZCI6IjdmYjE0MjQtOTkwYy00ZjY4LTkiLCJjb2RpZ29QdWJsaWNhZG9yIjowLCJjb2RpZ29Tb2Z0d2FyZSI6MTEwOTYyLCJzZXF1ZW5jaWFsSW5zdGFsYWNhbyI6Mn0'
        client_secret = 'eyJpZCI6IjE2NDEwYWYtYzciLCJjb2RpZ29QdWJsaWNhZG9yIjowLCJjb2RpZ29Tb2Z0d2FyZSI6MTEwOTYyLCJzZXF1ZW5jaWFsSW5zdGFsYWNhbyI6Miwic2VxdWVuY2lhbENyZWRlbmNpYWwiOjEsImFtYmllbnRlIjoicHJvZHVjYW8iLCJpYXQiOjE3NDQ1ODIyMjI4NjB9'

        auth_url = 'https://oauth.bb.com.br/oauth/token'

        data = {
            'grant_type': 'client_credentials',
            'scope': 'cobrancas.boletos-info cobrancas.boletos-requisicao'
        }

        # auth=base64.b64encode()
        # Autenticación via Basic Auth
        response = requests.post(
            auth_url,
            data=data,
            auth=HTTPBasicAuth(client_id, client_secret)
        )
        res = response.json()
        if res.get('access_token', False):
            self.expires_at = datetime.now() + timedelta(seconds=res.get('expires_in') - 10)
            self.access_token = res.get('access_token')
        return res

    def _send_cancelling(self, orders_data, pickup_request):
        """
        Cancelling bannk slip in BBrasil
        """
        cancel_result = {}
        for order in orders_data:
            if pickup_request:
                endpoint = 'external/orders/cancel'
                data = {'ids': [order]}
            else:
                endpoint = 'external/orders/cancel/shipment/awbs'
                data = {'awbs': [order]}
            cancel_result.update({
                order: self._make_api_request(endpoint, 'POST', data, access_token=self._get_access_token()
            )})
        return cancel_result

    def _check_required_value(self, recipient, shipper, products):
        """
        Check if the required value are not present in order to process an API call.
        return True or return an error if configuration is missing.
        """
        error_msg = {'Customer': [], 'Shipper': []}
        if not recipient.street:
            error_msg['Customer'].append(_("Street is required!"))
        if not recipient.zip:
            error_msg['Customer'].append(_("Pincode is required!"))
        if not recipient.country_id:
            error_msg['Customer'].append(_("Country is required!"))
        if not recipient.email:
            error_msg['Customer'].append(_("Email is required!"))
        if not recipient.phone and not recipient.mobile:
            error_msg['Customer'].append(_("Phone or Mobile is required!"))
        if not shipper.street:
            error_msg['Shipper'].append(_("Street is required!"))
        if not shipper.zip:
            error_msg['Shipper'].append(_("Pincode is required!"))
        if not shipper.country_id:
            error_msg['Shipper'].append(_("Country is required!"))
        if not shipper.email:
            error_msg['Shipper'].append(_("Email is required!"))
        if not shipper.phone and not shipper.mobile:
            error_msg['Shipper'].append(_("Phone or Mobile is required!"))
        for product in products:
            if not product.weight:
                error_msg.setdefault(product.name, [])
                error_msg[product.name].append(_("Weight is missing!"))
            if not product.default_code:
                error_msg.setdefault(product.name, [])
                error_msg[product.name].append(_("SKU is missing!"))
        if error_msg:
            msg = "".join(e_for + "\n- " + "\n- ".join(e) + "\n" for e_for, e in error_msg.items() if e)
            if msg:
                raise ValidationError(msg)
        return True

    def _bbrasil_get_error_message(self, json_data):
        """
        Return error message(s) from correios requests.
        """
        errors = json_data.get('errors', {})
        payload = json_data.get('payload', {})
        message = ''

        if errors:
            for value in errors.values():
                sub_msg = "\n".join(value) if isinstance(value, list) else value or ''
                message += _("Correios Error: %s", sub_msg) + '\n'
        elif 'message' in json_data:
            message = _("Correios Error: %s", json_data['message'])
        elif 'error_message' in payload:
            message = _("Correios Error: %s", payload['error_message'])
        elif 'awb_assign_error' in payload:
            message = _('Correios Error: %s', payload['awb_assign_error'])
        elif not json_data.get('label_created') and 'response' in json_data:
            message = _('Correios Error: %s', json_data['response'])
        return message

    @staticmethod
    def _validate_partner_fields(partner):
        """ Make sure that the essential fields are filled in. Other error specific to each carrier could still arise,
        but this should prevent too many errors with starshipit.
        """
        required_address_fields = ['street', 'city', 'country_id', 'state_id']
        fields_details = partner.fields_get(required_address_fields, ['string'])
        for field in required_address_fields:
            if not partner[field]:
                field_name = fields_details[field]['string']
                raise UserError(_('Please fill in the fields %s on the %s partner.', field_name, partner.name))

    def _get_slips(self, slip, params={}):
        """
        Returns the dictionary with slip data
        """
        payload = self._make_api_request('boletos', 'GET', 
                                            params=params, 
                                            access_token=self.access_token
        )
        if payload.get('erros'):
            slip.message_post(body=payload.get('erros'))
        if not payload:
            slip.message_post(body=_('AWB assignment was unsuccessful: %s') % (self._correios_get_error_message(payload)))
        if not payload or payload.get('errors'):
            raise ValidationError(_('Erro no processo de registro no bb'))
        if payload.get('numero'):
            slip.titulo_nosso_numero = payload.get('numero')[-10:]
        return payload

    def _get_slip_info(self, slip):
        company = slip.env.company
        numeroConvenio = company.bank_contract.strip()
        nosso_numero = slip.titulo_nosso_numero
        params = {
            'numeroConvenio': int(numeroConvenio)
            }
        endpoint = 'boletos/{:010d}{:010d}'.format(int(numeroConvenio), int(nosso_numero))
        payload = self._make_api_request(endpoint, 'GET', 
                                            params=params, 
                                            access_token=self.access_token
        )

        if payload.get('erros'):
            slip.message_post(body=payload.get('erros'))
        return payload
      