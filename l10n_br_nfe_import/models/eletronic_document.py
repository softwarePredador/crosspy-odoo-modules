import base64
import pytz
import logging
import re
import json

from types import SimpleNamespace

from odoo import fields, models, _, api
from dateutil import parser
from datetime import datetime
from lxml import objectify
from odoo.exceptions import UserError
from odoo.tools import float_compare

_logger = logging.getLogger(__name__)

def dict_to_obj(d):
    if isinstance(d, dict):
        return SimpleNamespace(**{k: dict_to_obj(v) for k, v in d.items()})
    elif isinstance(d, list):
        return [dict_to_obj(i) for i in d]
    else:
        return d

def convert(obj, conversion=None):
    if conversion:
        return conversion(obj.text)
    if isinstance(obj, objectify.StringElement):
        return str(obj)
    if isinstance(obj, objectify.IntElement):
        return int(obj)
    if isinstance(obj, objectify.FloatElement):
        return float(obj)
    raise u"Tipo não implementado %s" % str(type(obj))

def get(obj, path, conversion=None):
    if hasattr(obj, path) and not isinstance(obj, objectify.ObjectifiedElement):
        return obj.get(path)
    paths = path.split(".")
    index = 0
    for item in paths:
        if not item:
            continue
        if isinstance(obj, SimpleNamespace) and hasattr(obj, item):
            obj = getattr(obj, item)
            index += 1
        elif not isinstance(obj, SimpleNamespace) and item in obj:
            obj = obj.get(item)
            index += 1
        elif hasattr(obj, item):
            obj = obj[item]
            index += 1
        else:
            return None
    if len(paths) == index:
        return convert(obj, conversion=conversion)
    return None


def remove_none_values(dict):
    res = {}
    res.update({k: v for k, v in dict.items() if v})
    return res


def cnpj_cpf_format(cnpj_cpf):
    if len(cnpj_cpf) == 14:
        cnpj_cpf = (cnpj_cpf[0:2] + '.' + cnpj_cpf[2:5] +
                    '.' + cnpj_cpf[5:8] +
                    '/' + cnpj_cpf[8:12] +
                    '-' + cnpj_cpf[12:14])
    else:
        cnpj_cpf = (cnpj_cpf[0:3] + '.' + cnpj_cpf[3:6] +
                    '.' + cnpj_cpf[6:9] + '-' + cnpj_cpf[9:11])
    return cnpj_cpf


def format_ncm(ncm):
    if len(ncm) == 4:
        ncm = ncm[:2] + '.' + ncm[2:4]
    elif len(ncm) == 6:
        ncm = ncm[:4] + '.' + ncm[4:6]
    else:
        ncm = ncm[:4] + '.' + ncm[4:6] + '.' + ncm[6:8]

    return ncm


class EletronicDocument(models.Model):
    _inherit = 'eletronic.document'

    state = fields.Selection(selection_add=[('imported', 'Importado'),
                                            ('invoice', 'Odoo Invoice'),
                                            ('purchase', 'Odoo Purchase'),
                                            ])

    purchase_vendor_bill_id = fields.Many2one('purchase.bill.union', store=False, readonly=False,
        string='Auto-complete',
        help="Auto-complete from a past bill / purchase order.")
    purchase_id = fields.Many2one('purchase.order', store=False, readonly=False,
        string='Purchase Order',
        help="Auto-complete from a past purchase order.")

    # purchase_ids = fields.Many2many('purchase.order')

    @api.onchange('purchase_vendor_bill_id', 'purchase_id')
    def _onchange_purchase_auto_complete(self):
        r''' Load from either an old purchase order, either an old vendor bill.

        When setting a 'purchase.bill.union' in 'purchase_vendor_bill_id':
        * If it's a vendor bill, 'invoice_vendor_bill_id' is set and the loading is done by '_onchange_invoice_vendor_bill'.
        * If it's a purchase order, 'purchase_id' is set and this method will load lines.

        /!\ All this not-stored fields must be empty at the end of this function.
        '''
        if self.purchase_vendor_bill_id.purchase_order_id:
            self.purchase_id = self.purchase_vendor_bill_id.purchase_order_id
        self.purchase_vendor_bill_id = False

        if not self.purchase_id:
            return
        
        decimal_precision = self.env.company.currency_id.decimal_places
        
        # try to link lines with PO
        for po_line in self.purchase_id.order_line:
            document_lines = []
            for iten in self.document_line_ids:
                if float_compare(po_line.price_total / po_line.product_qty, iten.valor_liquido / iten.quantidade, decimal_precision) == 0:
                                            # and po_line.order_id.date_approve <= iten.eletronic_document_id.data_emissao:
                    document_lines.append((1, iten.id, {'product_id': po_line.product_id.id,
                                                        'uom_id': po_line.product_uom.id,
                                                        'purchase_line_id': po_line.id
                                                        }
                                            ))
                    iten.write({'product_id': po_line.product_id.id,
                                'uom_id': po_line.product_uom.id,
                                'purchase_line_id': po_line.id
                                })
                    iten.update({'product_id': po_line.product_id.id,
                                'uom_id': po_line.product_uom.id,
                                'purchase_line_id': po_line.id
                                })
                    # iten.env.cr.commit()
                    exit
        self.update({'document_line_ids': document_lines})
        self.write({'document_line_ids': document_lines})
        # self.env.cr.commit()
        self.purchase_id = False

    @api.onchange('partner_id', 'company_id')
    def _onchange_partner_id(self):
        currency_id = (
                self.partner_id.property_purchase_currency_id
                or self.env['res.currency'].browse(self.env.context.get("default_currency_id"))
                or self.currency_id
        )

        if self.partner_id and self.move_type in ['in_invoice', 'in_refund'] and self.currency_id != currency_id:
            if not self.env.context.get('default_journal_id'):
                journal_domain = [
                    *self.env['account.journal']._check_company_domain(self.company_id),
                    ('type', '=', 'purchase'),
                    ('currency_id', '=', currency_id.id),
                ]
                default_journal_id = self.env['account.journal'].search(journal_domain, limit=1)
                if default_journal_id:
                    self.journal_id = default_journal_id

            self.currency_id = currency_id

    def get_ide(self, nfe, operacao):
        ''' Importa a seção <ide> do xml'''
        ide = None
        if hasattr(nfe, 'NFe') and hasattr(nfe.NFe, 'infNFe'):
            ide = nfe.NFe.infNFe.ide
        elif hasattr(nfe, 'infNFe'):
            ide = nfe.infNFe.ide
        
        modelo = ide.mod
        serie = ide.serie
        num_controle = ide.cNF
        numero_nfe = ide.nNF
        data_emissao = parser.parse(str(ide.dhEmi))
        dt_entrada_saida = get(ide, 'dhSaiEnt')
        natureza_operacao = ide.natOp
        fpos = self.env['account.fiscal.position'].search(
            [('name', '=', natureza_operacao)], limit=1
        )

        if dt_entrada_saida:
            dt_entrada_saida = parser.parse(str(dt_entrada_saida))
            dt_entrada_saida = dt_entrada_saida.astimezone(pytz.utc).replace(tzinfo=None)
        indicador_destinatario = ide.idDest
        ambiente = 'homologacao' if ide.tpAmb == 2\
            else 'producao'
        finalidade_emissao = str(ide.finNFe)

        return dict(
            tipo_operacao=operacao,
            model='nfce' if str(modelo) == '65' else 'nfe',
            serie_documento=serie,
            numero_controle=num_controle,
            numero=numero_nfe,
            data_emissao=data_emissao.astimezone(pytz.utc).replace(tzinfo=None),
            data_entrada_saida=dt_entrada_saida,
            ind_dest=str(indicador_destinatario),
            ambiente=ambiente,
            finalidade_emissao=finalidade_emissao,
            state='imported',
            name='Documento Eletrônico: n° ' + str(numero_nfe),
            natureza_operacao=natureza_operacao,
            fiscal_position_id=fpos.id
        )

    def get_partner_nfe(self, nfe, destinatary, partner_automation):
        '''Importação da sessão <emit> do xml'''
        tag_nfe = None
        if hasattr(nfe, 'NFe'):
            nfe = nfe.NFe
        if destinatary:
            tag_nfe = nfe.infNFe.emit
        else:
            tag_nfe = nfe.infNFe.dest

        if hasattr(tag_nfe, 'idEstrangeiro'):
            cnpj_cpf = tag_nfe.idEstrangeiro.text
        elif hasattr(tag_nfe, 'CNPJ'):
            cnpj_cpf = cnpj_cpf_format(str(tag_nfe.CNPJ.text).zfill(14))
        else:
            cnpj_cpf = cnpj_cpf_format(str(tag_nfe.CPF.text).zfill(11))

        partner_id = self.env['res.partner'].search([
            ('vat', '=', cnpj_cpf)], limit=1)
        if not partner_id and partner_automation:
            partner_id = self._create_partner(tag_nfe, destinatary)
        elif not partner_id and not partner_automation:
            raise UserError((
                'Parceiro não cadastrado. Selecione a opção cadastrar ' +
                'parceiro, ou realize o cadastro manualmente.'))

        return dict(partner_id=partner_id.id)

    def get_ICMSTot(self, nfe):
        ICMSTot = None
        if hasattr(nfe, 'NFe'):
            ICMSTot = nfe.NFe.infNFe.total.ICMSTot
        elif hasattr(nfe, 'infNFe'):
            ICMSTot = nfe.infNFe.total.ICMSTot
        return dict(
            valor_bc_icms=get(ICMSTot, 'vBC'),
            valor_icms=get(ICMSTot, 'vICMS'),
            valor_icms_deson=get(ICMSTot, 'vICMSDeson'),
            valor_bc_icmsst=get(ICMSTot, 'vBCST'),
            valor_icmsst=get(ICMSTot, 'vST'),
            valor_bruto=get(ICMSTot, 'vProd'),
            valor_produtos=get(ICMSTot, 'vProd'),
            valor_frete=get(ICMSTot, 'vFrete'),
            valor_seguro=get(ICMSTot, 'vSeg'),
            valor_desconto=get(ICMSTot, 'vDesc'),
            valor_ii=get(ICMSTot, 'vII'),
            valor_ipi=get(ICMSTot, 'vIPI'),
            pis_valor=get(ICMSTot, 'vPIS'),
            cofins_valor=get(ICMSTot, 'vCOFINS'),
            valor_final=get(ICMSTot, 'vNF'),
            valor_estimado_tributos=get(ICMSTot, 'vTotTrib'),
            # TODO Inserir novos campos
            # vOutro=ICMSTot.vOutro,
        )

    def get_retTrib(self, nfe):
        retTrib = nfe.NFe.infNFe.total.retTrib
        return dict(
            valor_retencao_pis=retTrib.vRetPIS,
            valor_retencao_cofins=retTrib.vRetCOFINS,
            valor_retencao_csll=retTrib.vRetCSLL,
            valor_retencao_irrf=retTrib.vIRRF,
            valor_retencao_previdencia=retTrib.vRetPrev
            # TODO Inserir novos campos
            # vBCIRRF=retTrib.vBCIRRF,
            # vBCRetPrev=retTrib.vBCRetPrev,
        )

    def get_transp(self, nfe):
        transportadora = {}

        if hasattr(nfe, 'NFe'):
            nfe = nfe.NFe

        if hasattr(nfe.infNFe, 'transp'):
            transp = nfe.infNFe.transp

            modFrete = get(transp, 'modFrete', str)

            if transp.modFrete == 9:
                return dict(
                    modalidade_frete=modFrete
                )

            if hasattr(transp, 'transporta'):
                cnpj_cpf = get(transp, 'transporta.CNPJ', str)

                if cnpj_cpf:
                    cnpj_cpf = cnpj_cpf_format(str(cnpj_cpf).zfill(14))

                transportadora_id = self.env['res.partner'].search([
                    ('vat', '=', cnpj_cpf)], limit=1)

                if not transportadora_id:
                    state_obj = self.env['res.country.state']
                    state_id = state_obj.search([
                        ('code', '=', get(transp, 'transporta.UF')),
                        ('country_id.code', '=', 'BR')])

                    vals = {
                        'vat': cnpj_cpf,
                        'name': get(transp, 'transporta.xNome'),
                        'inscr_est': get(transp, 'transporta.IE', str),
                        'street': get(transp, 'transporta.xEnder'),
                        'city': get(transp, 'transporta.xMun'),
                        'state_id': state_id.id,
                        'legal_name': get(transp, 'transporta.xNome'),
                        'company_type': 'company',
                        'is_company': True,
                        'company_id': None,
                    }
                    transportadora_id = self.env['res.partner'].create(vals)

                transportadora.update({
                    'transportadora_id': transportadora_id.id,
                    'placa_veiculo': get(transp, 'veicTransp.placa'),
                    'uf_veiculo': get(transp, 'veicTransp.UF'),
                    'rntc': get(transp, 'veicTransp.RNTC'),
                })

        return transportadora

    def get_reboque(self, nfe):
        if hasattr(nfe, 'NFe'):
            nfe = nfe.NFe
        if hasattr(nfe.infNFe.transp, 'reboque'):
            reboque = nfe.infNFe.transp.reboque

            reboque_ids = {
                'balsa': get(reboque, '.balsa'),
                'uf_veiculo': get(reboque, '.UF'),
                'vagao': get(reboque, '.vagao'),
                'rntc': get(reboque, '.RNTC'),
                'placa_veiculo': get(reboque, '.placa'),
            }

            return remove_none_values(reboque_ids)

        return {}

    def get_vol(self, nfe):
        if hasattr(nfe, 'NFe'):
            nfe = nfe.NFe
        if hasattr(nfe.infNFe.transp, 'vol'):
            vol = nfe.infNFe.transp.vol
            volume_ids = {
                'especie': get(vol, 'esp'),
                'quantidade_volumes': get(vol, 'qVol'),
                'numeracao': get(vol, 'nVol'),
                'peso_liquido': get(vol, 'pesoL'),
                'peso_bruto': get(vol, 'pesoB'),
                'marca': get(vol, 'marca'),
            }

            return remove_none_values(volume_ids)

        return {}

    def get_cobr_fat(self, nfe):
        if hasattr(nfe, 'NFe'):
            nfe = nfe.NFe
        if hasattr(nfe.infNFe, 'cobr'):
            cobr = nfe.infNFe.cobr

            if hasattr(cobr, 'fat'):
                fatura = {
                    'numero_fatura': get(cobr, 'fat.nFat', str),
                    'fatura_bruto': get(cobr, 'fat.vOrig'),
                    'fatura_desconto': get(cobr, 'fat.vDesc'),
                    'fatura_liquido': get(cobr, 'fat.vLiq'),
                }
                return fatura

        return {}

    def get_cobr_dup(self, nfe):
        if hasattr(nfe.infNFe, 'cobr'):
            cobr = nfe.infNFe.cobr

            if len(cobr) and hasattr(cobr, 'dup'):
                duplicatas = []
                for dup in cobr.dup:
                    duplicata = {
                        'data_vencimento': get(dup, 'dVenc'),
                        'valor': dup.vDup,
                        'numero_duplicata': get(dup, 'nDup'),
                    }
                    duplicatas.append((0, None, remove_none_values(duplicata)))

                return {'duplicata_ids': duplicatas}

        return {}


    def get_infAdic(self, nfe):
        info_adicionais = {
            'informacoes_legais': get(
                nfe, 'NFe.infNFe.infAdic.infAdFisco'),
            'informacoes_complementares': get(
                nfe, 'NFe.infNFe.infAdic.infCpl'),
        }

        return info_adicionais

    def get_main(self, nfe):
        return dict(
            payment_term_id=self.payment_term_id.id,
            fiscal_position_id=self.fiscal_position_id.id,
        )

    def _get_partner_nfse(self, nfe_origin, destinatary, partner_automation):
        nfe_flat = {'NFe': {'infNFe': {'emit': {
            'CNPJ': nfe_origin.CPFCNPJPrestador.CNPJ,
            'xNome': nfe_origin.RazaoSocialPrestador,
            'enderEmit': {
                'xLgr': nfe_origin.EnderecoPrestador.Logradouro,
                'nro': nfe_origin.EnderecoPrestador.NumeroEndereco,
                'xBairro': nfe_origin.EnderecoPrestador.Bairro,
                'cMun': nfe_origin.EnderecoPrestador.Cidade,
                # 'xMun': nfe_origin.EnderecoPrestador,
                'UF': nfe_origin.EnderecoPrestador.UF,
                'CEP': nfe_origin.EnderecoPrestador.CEP,
                # 'cPais': nfe_origin.EnderecoPrestador,
                # 'xPais': nfe_origin.EnderecoPrestador,
                # 'fone': nfe_origin.EnderecoPrestador,
            },
            # 'indIEDest': 
            # 'IE': 
            'email': nfe_origin.EmailPrestador
        }
        }}}
        nfe = dict_to_obj(nfe_flat)

        return self.get_partner_nfe(nfe, destinatary, partner_automation)

    def get_ide_nfse(self, nfe, operacao):
        """
        <StatusNFe>N</StatusNFe>     
              Status da NF-e:
                            N - Normal;
                            C - Cancelada
        <TributacaoNFe>T</TributacaoNFe>
                            T - Tributado em São Paulo
                            F - Tributado Fora de São Paulo
                            A - Tributado em São Paulo,
                            porém Isento
                            B - Tributado Fora de São Paulo,
                            porém Isento
                            D - Tributado em São Paulo com
                            isenção parcial
                            M - Tributado em São Paulo,
                            porém com indicação de
                            imunidade subjetiva
                            N - Tributado Fora de São Paulo,
                            porém com indicação de
                            imunidade subjetiva
                            R - Tributado em São Paulo,
                            porém com indicação de
                            imunidade objetiva
                            S - Tributado fora de São Paulo,
                            porém com indicação de
                            imunidade objetiva
                            X - Tributado em São Paulo,
                            porém Exigibilidade Suspensa
                            V - Tributado Fora de São Paulo,
                            porém Exigibilidade Suspensa
                            P - Exportação de Serviços
        <OpcaoSimples>0</OpcaoSimples>
                        Opção pelo Simples:
                            0 - Normal ou Simples Nacional
                            (DAMSP);
                            1 - Optante pelo Simples Federal
                            (Alíquota de 1,0%);
                            2 - Optante pelo Simples Federal
                            (Alíquota de 0,5%);
                            3 - Optante pelo Simples
                            Municipal.
                            4 - Optante pelo Simples
                            Nacional (DAS).
        <NumeroGuia>0</NumeroGuia>

        <ValorServicos>240.00</ValorServicos>
        <ValorDeducoes>240.00</ValorDeducoes>

        <CodigoServico>3205</CodigoServico>
                    O código informado deverá
                    pertencer à Tabela de Serviços
                    disponibilizada pela Prefeitura de
                    São Paulo.
        <AliquotaServicos>2.00</AliquotaServicos>
        <ValorISS>0.00</ValorISS>
        <ValorCredito>0.00</ValorCredito>
        <ISSRetido>false</ISSRetido>
                        Retenção do ISS. Preencher com:
                            "true" - para NF-e com ISS Retido;
                            "false" - para NF-e sem ISS Retido
        """
        natureza_operacao = 'Serviço'
        valor_final = nfe.ValorServicos.text
        num_controle = nfe.ChaveNFe.CodigoVerificacao.text
        numero_nfe = nfe.ChaveNFe.NumeroNFe.text

        # ide = nfe.NFe.infNFe.ide
        # modelo = ide.mod
        # serie = ide.serie
        data_emissao = parser.parse(str(nfe.DataEmissaoNFe))
        fpos = self.env['account.fiscal.position'].search(
            [('name', '=', natureza_operacao)], limit=1
        )

        # indicador_destinatario = ide.idDest
        ambiente = 'producao'
        finalidade_emissao = '1'
        """
                1 = NF-e normal.
                2 = NF-e complementar.
                3 = NF-e de ajuste.
                4 = Devolução de mercadoria.
        """
        return dict(
            # tipo_operacao=operacao,
            informacoes_complementares=nfe.Discriminacao.text,

            model='nfse',
            # serie_documento=serie,
            # numero_controle=num_controle,
            numero=numero_nfe,
            data_emissao=data_emissao.astimezone(pytz.utc).replace(tzinfo=None),
            # data_entrada_saida=dt_entrada_saida,
            # ind_dest=str(indicador_destinatario),
            ambiente=ambiente,
            # finalidade_emissao=finalidade_emissao,
            state='imported',
            name='Documento Eletrônico: n° ' + str(numero_nfe),
            natureza_operacao=natureza_operacao,
            fiscal_position_id=fpos.id,
            valor_final=valor_final
        )

    def create_invoice_eletronic_item(self, item, company_id, partner_id,
                                      supplier, product_automation, tax_automation):
        codigo = get(item.prod, 'cProd', str)


        seller_id = self.env['product.supplierinfo'].search([
            ('partner_id', '=', partner_id),
            ('product_code', '=', codigo),
            ('product_id.active', '=', True)])

        product = None
        if seller_id:
            product = seller_id.product_id
            if len(product) > 1:
                message = '\n'.join(["Produto: %s - %s" % (x.default_code or '', x.name) for x in product])
                raise UserError("Existem produtos duplicados com mesma codificação, corrija-os antes de prosseguir:\n%s" % message)

        if not product and item.prod.cEAN and \
           str(item.prod.cEAN) != 'SEM GTIN':
            product = self.env['product.product'].search(
                [('barcode', '=', item.prod.cEAN)], limit=1)

        uom_id = self.env['uom.uom'].search([
            ('name', '=', str(item.prod.uCom))], limit=1).id

        if not product and product_automation:
            product = self._create_product(
                company_id, supplier, item.prod, uom_id=uom_id)

        if not uom_id:
            uom_id = product and product.uom_id.id or False
        product_id = product and product.id or False

        quantidade = item.prod.qCom
        preco_unitario = item.prod.vUnCom
        valor_bruto = item.prod.vProd
        desconto = 0
        if hasattr(item.prod, 'vDesc'):
            desconto = item.prod.vDesc
        seguro = 0
        if hasattr(item.prod, 'vSeg'):
            seguro = item.prod.vSeg
        frete = 0
        if hasattr(item.prod, 'vFrete'):
            frete = item.prod.vFrete
        outras_despesas = 0
        if hasattr(item.prod, 'vOutro'):
            outras_despesas = item.prod.vOutro
        indicador_total = str(item.prod.indTot)
        cfop = item.prod.CFOP
        ncm = item.prod.NCM
        cest = get(item, 'item.prod.CEST')
        nItemPed = get(item, 'prod.nItemPed')

        invoice_eletronic_Item = {
            'product_id': product_id, 'uom_id': uom_id,
            'quantidade': quantidade, 'preco_unitario': preco_unitario,
            'valor_bruto': valor_bruto, 'desconto': desconto, 'seguro': seguro,
            'frete': frete, 'outras_despesas': outras_despesas,
            'valor_liquido': valor_bruto - desconto + frete + seguro + outras_despesas,
            'indicador_total': indicador_total, 'unidade_medida': str(item.prod.uCom),
            'cfop': cfop, 'ncm': ncm, 'product_ean': item.prod.cEAN,
            'product_cprod': codigo, 'product_xprod': item.prod.xProd,
            'cest': cest, 'item_pedido_compra': nItemPed,
            'company_id': company_id.id,
        }
        
        tax_items = []
        if hasattr(item.imposto, 'ICMS'):
            invoice_eletronic_Item.update(self._get_icms(item.imposto))
            # if invoice_eletronic_Item.get('icms_cst', False) in ('00',):
            #     tax_items.append(self._get_tax('icms', invoice_eletronic_Item['icms_aliquota'], company_id, tax_automation))
        if hasattr(item.imposto, 'ISSQN'):
            invoice_eletronic_Item.update(self._get_issqn(item.imposto.ISSQN))

        if hasattr(item.imposto, 'IPI'):
            invoice_eletronic_Item.update(self._get_ipi(item.imposto.IPI))

        invoice_eletronic_Item.update(self._get_pis(item.imposto.PIS))
        invoice_eletronic_Item.update(self._get_cofins(item.imposto.COFINS))

        if hasattr(item.imposto, 'II'):
            invoice_eletronic_Item.update(self._get_ii(item.imposto.II))
        if hasattr(item.prod, 'DI'):
            di_ids = []
            for di in item.prod.DI:
                di_ids.append(self._get_di(item.prod.DI))
            invoice_eletronic_Item.update({'import_declaration_ids': di_ids})

        return self.env['eletronic.document.line'].create(
            invoice_eletronic_Item)

    def _get_icms(self, imposto):
        csts = ['00', '10', '20', '30', '40', '41', '50',
                '51', '60', '70', '90']
        csts += ['101', '102', '103', '201', '202', '203',
                 '300', '400', '500', '900']

        cst_item = None
        vals = {}

        for cst in csts:
            tag_icms = None
            if hasattr(imposto.ICMS, 'ICMSSN%s' % cst):
                tag_icms = 'ICMSSN'
                cst_item = get(imposto, 'ICMS.ICMSSN%s.CSOSN' % cst, str)
            elif hasattr(imposto.ICMS, 'ICMS%s' % cst):
                tag_icms = 'ICMS'
                cst_item = get(imposto, 'ICMS.ICMS%s.CST' % cst, str)
                cst_item = str(cst_item).zfill(2)
            if tag_icms:
                icms = imposto.ICMS
                vals = {
                    'icms_cst': cst_item,
                    'origem': get(
                        icms, '%s%s.orig' % (tag_icms, cst), str),
                    'icms_tipo_base': get(
                        icms, '%s%s.modBC' % (tag_icms, cst), str),
                    'icms_aliquota_diferimento': get(
                        icms, '%s%s.pDif' % (tag_icms, cst)),
                    'icms_valor_diferido': get(
                        icms, '%s%s.vICMSDif' % (tag_icms, cst)),
                    'icms_motivo_desoneracao': get(
                        icms, '%s%s.motDesICMS' % (tag_icms, cst)),
                    'icms_valor_desonerado': get(
                        icms, '%s%s.vICMSDeson' % (tag_icms, cst)),
                    'icms_base_calculo': get(
                        icms, '%s%s.vBC' % (tag_icms, cst)),
                    'icms_aliquota_reducao_base': get(
                        icms, '%s%s.pRedBC' % (tag_icms, cst)),
                    'icms_aliquota': get(
                        icms, '%s%s.pICMS' % (tag_icms, cst)),
                    'icms_valor': get(
                        icms, '%s%s.vICMS' % (tag_icms, cst)),
                    'icms_aliquota_credito': get(
                        icms, '%s%s.pCredSN' % (tag_icms, cst)),
                    'icms_valor_credito': get(
                        icms, '%s%s.vCredICMSSN' % (tag_icms, cst)),
                    'icms_st_tipo_base': get(
                        icms, '%s%s.modBCST' % (tag_icms, cst), str),
                    'icms_st_aliquota_mva': get(
                        icms, '%s%s.pMVAST' % (tag_icms, cst)),
                    'icms_st_base_calculo': get(
                        icms, '%s%s.vBCST' % (tag_icms, cst)),
                    'icms_st_aliquota_reducao_base': get(
                        icms, '%s%s.pRedBCST' % (tag_icms, cst)),
                    'icms_st_aliquota': get(
                        icms, '%s%s.pICMSST' % (tag_icms, cst)),
                    'icms_st_valor': get(
                        icms, '%s%s.vICMSST' % (tag_icms, cst)),
                    'icms_bc_uf_dest': get(
                        imposto, 'ICMSUFDest.vBCUFDest'),
                    'icms_aliquota_fcp_uf_dest': get(
                        imposto, 'ICMSUFDest.pFCPUFDest'),
                    'icms_aliquota_uf_dest': get(
                        imposto, 'ICMSUFDest.pICMSUFDest'),
                    'icms_aliquota_interestadual': get(
                        imposto, 'ICMSUFDest.pICMSInter'),
                    'icms_aliquota_inter_part': get(
                        imposto, 'ICMSUFDest.pICMSInterPart'),
                    'icms_fcp_uf_dest': get(
                        imposto, 'ICMSUFDest.vFCPUFDest'),
                    'icms_uf_dest': get(
                        imposto, 'ICMSUFDest.vICMSUFDest'),
                    'icms_uf_remet': get(
                        imposto, 'ICMSUFDest.vICMSUFRemet'),
                }

        return remove_none_values(vals)

    def _get_issqn(self, issqn):
        vals = {
            'item_lista_servico': get(issqn, 'cListServ'),
            'iss_aliquota': get(issqn, 'vAliq'),
            'iss_base_calculo': get(issqn, 'vBC'),
            'iss_valor': get(issqn, 'vISSQN'),
            'iss_valor_retencao': get(issqn, 'vISSRet'),
        }
        return remove_none_values(vals)

    def _get_ipi(self, ipi):
        classe_enquadramento_ipi = get(ipi, 'clEnq')
        codigo_enquadramento_ipi = get(ipi, 'cEnq')

        vals = {}
        for item in ipi.getchildren():
            ipi_cst = get(ipi, '%s.CST' % item.tag[36:])
            ipi_cst = str(ipi_cst).zfill(2) if ipi_cst else None

            vals = {
                'ipi_cst': ipi_cst,
                'ipi_base_calculo': get(ipi, '%s.vBC' % item.tag[36:]),
                'ipi_aliquota': get(ipi, '%s.pIPI' % item.tag[36:]),
                'ipi_valor': get(ipi, '%s.vIPI' % item.tag[36:]),
                'classe_enquadramento_ipi': classe_enquadramento_ipi,
                'codigo_enquadramento_ipi': codigo_enquadramento_ipi,
            }

        return remove_none_values(vals)

    def _get_pis(self, pis):
        vals = {}
        for item in pis.getchildren():
            pis_cst = get(pis, '%s.CST' % item.tag[36:])
            pis_cst = str(pis_cst).zfill(2)

            vals = {
                'pis_cst': pis_cst,
                'pis_base_calculo': get(pis, '%s.vBC' % item.tag[36:]),
                'pis_aliquota': get(pis, '%s.pPIS' % item.tag[36:]),
                'pis_valor': get(pis, '%s.vPIS' % item.tag[36:]),
            }

        return remove_none_values(vals)

    def _get_cofins(self, cofins):
        vals = {}
        for item in cofins.getchildren():
            cofins_cst = get(cofins, '%s.CST' % item.tag[36:])
            cofins_cst = str(cofins_cst).zfill(2)

            vals = {
                'cofins_cst': cofins_cst,
                'cofins_base_calculo': get(cofins, '%s.vBC' % item.tag[36:]),
                'cofins_aliquota': get(cofins, '%s.pCOFINS' % item.tag[36:]),
                'cofins_valor': get(cofins, '%s.vCOFINS' % item.tag[36:]),
            }

        return remove_none_values(vals)

    def _get_ii(self, ii):
        vals = {
            'ii_base_calculo': get(ii, 'vBC'),
            'ii_valor_despesas': get(ii, 'vDespAdu'),
            'ii_valor_iof': get(ii, 'vIOF'),
            'ii_valor': get(ii, 'vII'),
        }
        return remove_none_values(vals)

    def _get_di(self, di):
        state_code = get(di, 'UFDesemb')
        state_id = self.env['res.country.state'].search([
            ('code', '=', state_code),
            ('country_id.code', '=', 'BR')
        ])
        vals = {
            'name': get(di, 'nDI'),
            'date_registration': get(di, 'dDI'),
            'location': get(di, 'xLocDesemb'),
            'state_id': state_id.id,
            'date_release': get(di, 'dDesemb'),
            'type_transportation': get(di, 'tpViaTransp', str),
            'type_import': get(di, 'tpIntermedio', str),
            'exporting_code': get(di, 'cExportador'),
            'line_ids': []
        }

        if hasattr(di, 'adi'):
            for adi in di.adi:
                adi_vals = {
                    'sequence': get(di.adi, 'nSeqAdic'),
                    'name': get(di.adi, 'nAdicao'),
                    'manufacturer_code': get(di.adi, 'cFabricante'),
                }
                # adi_vals = remove_none_values(adi_vals)
                # adi = self.env['nfe.import.declaration.line'].create(adi_vals)
                # vals['line_ids'].append((4, adi.id, False))
                vals['line_ids'].append((0, 0, adi_vals))

        # vals = remove_none_values(vals)
        # di = self.env['nfe.import.declaration'].create(vals)

        return ((0, 0, vals))

    def get_items(self, nfe, company_id, partner_id,
                  supplier, product_automation, tax_automation=False):
        items = []
        if hasattr(nfe, 'NFe'):
            nfe = nfe.NFe
        for det in nfe.infNFe.det:
            item = self.create_invoice_eletronic_item(
                det, company_id, partner_id, supplier, product_automation, tax_automation)
            items.append((4, item.id, False))
        return {'document_line_ids': items}

    def get_compra(self, nfe):
        if hasattr(nfe.infNFe, 'compra'):
            compra = nfe.infNFe.compra

            return {
                'nota_empenho': get(compra, 'xNEmp'),
                'pedido_compra': get(compra, 'xPed'),
                'contrato_compra': get(compra, 'xCont'),
            }

        return {}

    def import_nfse(self, company_id, nfe, nfe_xml, partner_automation=False,
                   account_invoice_automation=False, tax_automation=False,
                   supplierinfo_automation=False, fiscal_position_id=False,
                   payment_term_id=False, invoice_dict=None):
        invoice_dict = invoice_dict or {}
        partner_vals = self._get_company_invoice(nfe, partner_automation)
        company_id = self.env['res.company'].browse(
            partner_vals['company_id'])
        invoice_dict.update(partner_vals)
        invoice_dict.update({
            'nfe_processada': base64.b64encode(nfe_xml),
            'nfe_processada_name': "NFe%08d.xml" % nfe.ChaveNFe.NumeroNFe
        })

        invoice_dict.update(self.getChaveNFSe(nfe))
        invoice_dict.update(self.get_main(nfe))

        partner = self._get_partner_nfse(
            nfe, partner_vals['destinatary'], partner_automation)
        invoice_dict.update(
            self.get_ide_nfse(nfe, partner_vals['tipo_operacao']))
        invoice_dict.update(partner)

        invoice_dict.update(self.get_service(
            nfe, company_id, partner['partner_id'],
            invoice_dict['partner_id'],
            supplierinfo_automation, tax_automation))
 
        invoice_dict.pop('destinatary', False)
        return self.env['eletronic.document'].create(invoice_dict)

    def getChaveNFSe(self, nfe):
        NFe = nfe.ChaveNFe
        chave_nfe = '{}{}{}'.format(NFe.InscricaoPrestador, NFe.NumeroNFe, NFe.CodigoVerificacao)
        return dict(
            chave_nfe=chave_nfe,
            data_autorizacao=parser.parse(
                str(nfe.DataEmissaoNFe)), # 2024-11-11T14:25:51-03:00
        )
    
    def get_service(self, item, company_id, partner_id,
                  supplier, product_automation, tax_automation=False):

        """
        <ValorServicos>240.00</ValorServicos>
        <ValorDeducoes>240.00</ValorDeducoes>

        <CodigoServico>3205</CodigoServico>
                    O código informado deverá
                    pertencer à Tabela de Serviços
                    disponibilizada pela Prefeitura de
                    São Paulo.
        <AliquotaServicos>2.00</AliquotaServicos>
        <ValorISS>0.00</ValorISS>
        <ValorCredito>0.00</ValorCredito>
        <ISSRetido>false</ISSRetido>
                        Retenção do ISS. Preencher com:
                            "true" - para NF-e com ISS Retido;
                            "false" - para NF-e sem ISS Retido
        """

        uom_id = self.env.ref('uom.product_uom_unit').id

        quantidade = 1
        valor_bruto = 0
        preco_unitario = item.ValorServicos
        if hasattr(item, 'ValoresDeducoes'):
            valor_bruto = item.ValorDeducoes
        desconto = 0

        if hasattr(item, 'vDesc'):
            desconto = item.prod.vDesc
 
        indicador_total = '1'

        invoice_eletronic_Item = {
            'uom_id': uom_id,
            'name': item.Discriminacao.text,
            'quantidade': quantidade, 
            'preco_unitario': preco_unitario,
            'valor_liquido': valor_bruto - desconto ,
            'indicador_total': indicador_total, 
            'company_id': company_id.id,
        }

        line =  self.env['eletronic.document.line'].create(
            invoice_eletronic_Item)

        return {'document_line_ids': [(4, line.id, False)]}

    def import_nfe(self, company_id, nfe, nfe_xml, partner_automation=False,
                   account_invoice_automation=False, tax_automation=False,
                   supplierinfo_automation=False, fiscal_position_id=False,
                   payment_term_id=False, invoice_dict=None):
        invoice_dict = invoice_dict or {}
        partner_vals = self._get_company_invoice(nfe, partner_automation)
        company_id = self.env['res.company'].browse(
            partner_vals['company_id'])
        invoice_dict.update(partner_vals)
        nfe_processada_name = 'NFe'
        if hasattr(nfe, 'infNFe'):
            nfe_processada_name = nfe.infNFe.get('Id').replace('NFe', '')
        if hasattr(nfe, 'NFe') and hasattr(nfe.NFe, 'infNFe') :
            nfe_processada_name = nfe.NFe.infNFe.ide.nNF
        digits = ''.join(re.findall(r'[0-9]', nfe_processada_name))
        invoice_dict.update({
            'nfe_processada': base64.b64encode(nfe_xml),
            'nfe_processada_name': "NFe%08d.xml" % int(digits)
        })
        if hasattr(nfe, 'NFe'):
            nfe = nfe.NFe
        invoice_dict.update(self.get_protNFe(nfe, company_id))
        invoice_dict.update(self.get_main(nfe))
        partner = self.get_partner_nfe(
            nfe, partner_vals['destinatary'], partner_automation)
        invoice_dict.update(
            self.get_ide(nfe, partner_vals['tipo_operacao']))
        invoice_dict.update(partner)
        invoice_dict.update(self.get_ICMSTot(nfe))
        invoice_dict.update(self.get_items(
            nfe, company_id, partner['partner_id'],
            invoice_dict['partner_id'],
            supplierinfo_automation, tax_automation))
        invoice_dict.update(self.get_infAdic(nfe))
        invoice_dict.update(self.get_cobr_fat(nfe))
        invoice_dict.update(self.get_transp(nfe))
        invoice_dict.update(
            {'reboque_ids': [(0, None, self.get_reboque(nfe))]})
        invoice_dict.update({'volume_ids': [(0, None, self.get_vol(nfe))]})
        invoice_dict.update(self.get_cobr_dup(nfe))
        invoice_dict.update(self.get_compra(nfe))
        # Clean variables
        invoice_dict.pop('destinatary', False)
        invoice_dict.pop('nfe_type', False)
        invoice_eletronic = self.env['eletronic.document'].create(invoice_dict)

        # if account_invoice_automation:
        #     invoice = invoice_eletronic.prepare_account_invoice_vals(
        #         company_id, tax_automation=tax_automation,
        #         supplierinfo_automation=supplierinfo_automation,
        #         fiscal_position_id=fiscal_position_id,
        #         payment_term_id=payment_term_id)
        #     invoice_eletronic.invoice_id = invoice.id
        return invoice_eletronic

    def get_protNFe(self, nfe, company_id):
        if not hasattr(nfe, 'protNFe'):
            return {}
        protNFe = nfe.protNFe.infProt

        if protNFe.cStat in [100, 150, 110, 101] or\
                protNFe.cStat == 110 and re.sub(r'\D', '', company_id.vat) in protNFe.chNFe:
            # _logger.warning('CrossPy: %s' % protNFe.chNFe)
            return dict(
                chave_nfe=protNFe.chNFe,
                data_autorizacao=parser.parse(
                    str(nfe.protNFe.infProt.dhRecbto)),
                mensagem_retorno=protNFe.xMotivo,
                protocolo_nfe=protNFe.nProt,
                codigo_retorno=protNFe.cStat,
            )

    def existing_invoice(self, nfe):
        if hasattr(nfe, 'protNFe'):
            protNFe = nfe.protNFe.infProt
            chave_nfe = protNFe.chNFe
        elif hasattr(nfe, 'infNFe'):
            chave_nfe = nfe.infNFe.get('Id')
            if chave_nfe:
                chave_nfe = ''.join(re.findall(r'\d+', chave_nfe))
        elif hasattr(nfe, 'NFe') and hasattr(nfe.NFe, 'ChaveNFe'):
            NFe = nfe.NFe.ChaveNFe
            chave_nfe = '{}{}{}'.format(NFe.InscricaoPrestador, NFe.NumeroNFe, NFe.CodigoVerificacao)
        else:
            raise UserError('XML invalido!')

        invoice_eletronic = self.env['eletronic.document'].search([
            ('chave_nfe', '=', chave_nfe)])

        return invoice_eletronic

    def _create_partner(self, tag_nfe, destinatary):
        cnpj_cpf = None
        company_type = None
        is_company = None
        ender_tag = 'enderEmit' if destinatary else 'enderDest'
        if not hasattr(tag_nfe, ender_tag):
            if hasattr(tag_nfe, 'EnderecoPrestador'):
                ender = tag_nfe.EnderecoPrestador
        else:
                ender = tag_nfe.enderEmit

        if hasattr(tag_nfe, 'CNPJ'):
            cnpj_cpf = str(tag_nfe.CNPJ.text).zfill(14)
            company_type = 'company'
            is_company = True
        else:
            cnpj_cpf = str(tag_nfe.CPF.text).zfill(11)
            company_type = 'person'
            is_company = False

        cnpj_cpf = cnpj_cpf_format(cnpj_cpf)

        # ender = get(tag_nfe, ender_tag)
        state_code = None
        if hasattr(ender, 'UF'):
            state_code = ender.UF
        # else:
        #     state_code = get(tag_nfe, ender_tag + '.UF')
        state_id = self.env['res.country.state'].search([
            ('code', '=', state_code.text),
            ('country_id.code', '=', 'BR')])

        city_ibge = None
        if hasattr(ender, 'Cidade'):
            city_ibge = ender.Cidade.text
        elif hasattr(ender, 'cMun'):
            city_ibge = ender.cMun.text

        city_id = self.env['res.city'].search([
            ('ibge_code', '=', city_ibge[2:]),
            ('state_id', '=', state_id.id)])

        tag_nfe = vars(tag_nfe)
        varIE = ''
        varIM = ''
        if hasattr(tag_nfe, 'IE'):
            varIE = tag_nfe.IE.text if get(tag_nfe, 'IE', str) else None
        if hasattr(tag_nfe, 'IM'):
            varIM = tag_nfe.IM.text if get(tag_nfe, 'IM', str) else None
        partner = {
            'name': get(tag_nfe, 'xFant') or get(tag_nfe, 'xNome'),
            'street': get(tag_nfe, ender_tag + '.xLgr'),
            'number': get(tag_nfe, ender_tag + '.nro', str),
            'district': get(tag_nfe, ender_tag + '.xBairro'),
            'city_id': city_id.id,
            'state_id': state_id.id,
            'zip': get(tag_nfe, ender_tag + '.CEP', str),
            'country_id': state_id.country_id.id,
            'phone': get(tag_nfe, ender_tag + '.fone'),
            'inscr_est': varIE,
            'inscr_mun': varIM,
            'vat': str(cnpj_cpf),
            'legal_name': get(tag_nfe, 'xNome'),
            'company_type': company_type,
            'is_company': is_company,
            'company_id': None,
        }
        partner_id = self.env['res.partner'].create(partner)
        partner_id.message_post(body="<ul><li>Parceiro criado através da importação\
                                de xml</li></ul>")

        return partner_id

    def _create_product(self, company_id, supplier, nfe_item, uom_id=False):
        params = self.env['ir.config_parameter'].sudo()
        seq_id = int(params.get_param(
            'br_nfe_import.product_sequence', default=0))
        if not seq_id:
            raise UserError(
                'A empresa não possui uma sequência de produto configurado!')
        ncm = get(nfe_item, 'NCM', str)
        ncm_id = self.env['account.ncm'].search([
            ('code', '=', ncm)])

        category = self.env['product.category'].search(
            [('l10n_br_ncm_category_ids.name', '=', ncm[:4])], limit=1)

        sequence = self.env['ir.sequence'].browse(seq_id)
        code = sequence.next_by_id()
        product = {
            'default_code': code,
            'name': get(nfe_item, 'xProd'),
            'purchase_ok': True,
            'sale_ok': False,
            'type': 'product',
            'l10n_br_ncm_id': ncm_id.id,
            'standard_price': get(nfe_item, 'vUnCom'),
            'lst_price': 0.0,
            'l10n_br_cest': get(nfe_item, 'CEST', str),
            'taxes_id': [],
            'supplier_taxes_id': [],
            'company_id': None,
        }
        if uom_id:
            product.update(dict(uom_id=uom_id))
        if category:
            product.update(dict(categ_id=category.id))
        if category and category.l10n_br_fiscal_category_id:
            product.update({
                'fiscal_category_id': category.l10n_br_fiscal_category_id.id,
            })

        ean = get(nfe_item, 'cEAN', str)
        if ean != 'None' and ean != 'SEM GTIN':
            product['barcode'] = ean
        product_id = self.env['product.product'].create(product)

        self.env['product.supplierinfo'].create({
            'product_id': product_id.id,
            'product_tmpl_id': product_id.product_tmpl_id.id,
            'partner_id': supplier,
            'product_code': get(nfe_item, 'cProd', str),
        })

        product_id.message_post(
            body="<ul><li>Produto criado através da importação \
            de xml</li></ul>")
        return product_id

    def prepare_account_invoice_line_vals(self, item, item_vals=None):
        if item_vals:
            item.product_id = item_vals.get('product_id'),
            item.uom_id = item_vals.get('uom_id')
        else:
            return {}
        if item.product_id:
            product = item.product_id.with_context(force_company=self.company_id.id)
            if product.property_account_expense_id:
                account_id = product.property_account_expense_id
            else:
                account_id =\
                    product.categ_id.property_account_expense_categ_id
        else:
            account_id = self.env['ir.property'].with_context(
                force_company=self.company_id.id).get(
                    'property_account_expense_categ_id', 'product.category')

        # ----------
        tax_items = self.env['account.tax']
        extra_value = 0.0
        if False:
            if item.icms_cst and item.icms_cst in ('00'):
                tax_items |=self._get_tax('icms', item.icms_aliquota, self.company_id, True)[0]
            if item.ipi_valor:
                tax_items |=self._get_tax('ipi', item.ipi_aliquota, self.company_id, True)[0]
            if item.pis_aliquota:
                tax_items |=self._get_tax('pis', item.pis_aliquota, self.company_id, True)[0]
            if item.cofins_aliquota:
                tax_items |=self._get_tax('cofins', item.cofins_aliquota, self.company_id, True)[0]
            if item.ii_aliquota:
                tax_items |=self._get_tax('ii', item.cofins_aliquota, self.company_id, True)[0]
        else:
            # if item.ipi_valor:
            extra_value = item.ipi_valor - item.desconto

                # tax_items |=self._get_tax('ipi', item.ipi_aliquota, self.company_id, True)[0]

        if item.import_declaration_ids:
            raise UserError (_('Nota com declaração de importaçã DI. Contate Administrador do Sistema'))
        
        # vals = self._get_purchase_line_vals()

        preco_unitario = item.preco_unitario + (extra_value / item.quantidade)

        item_vals.update({
            'product_id': item.product_id.id,
            'product_uom_id': item.uom_id.id,
            'name': item.name if item.name else item.product_xprod,
            'quantity': item.quantidade,
            'price_unit': preco_unitario,
            'account_id': account_id.id,
            'tax_ids': tax_items,
        })
        item_vals.pop('uom_id')

        return item_vals

    # def _get_purchase_order_vals(self, po_number):
    #     vals = {}
    #     if po_number:
    #         purchase_order_id = self.env['purchase.order'].search([
    #             ('name', '=', po_number),
    #             ('state', 'in', ('purchase', 'done'))])

    #         if purchase_order_id.id:
    #             if self.partner_id != purchase_order_id.partner_id:
    #                 raise ValueError(_('Purchase Supplier and Invoice Partner are difference'))
    #             return {
    #                 'purchase_ids': purchase_order_id.ids,
    #                 'fiscal_position_id': purchase_order_id.fiscal_position_id.id,
    #                 'invoice_payment_term_id': purchase_order_id.payment_term_id.id,
    #             }

    #     if self.partner_id:
    #         vals.update(self._get_purchase_by_partner_vals(self.partner_id))

    #     return vals

    def _get_purchase_by_partner_vals(self, vendor_id=False):
        vals = {}
        if not vendor_id:
            return vals

        purchase_order_id = self.env['purchase.order'].search([
            ('partner_id', '=', vendor_id.id),
            ('invoice_status', 'in', ('to invoice',)),
            ('state', 'in', ('purchase', 'done'))])

        if purchase_order_id:
            vals.update({
                'purchase_ids': purchase_order_id.ids
            })
        # if len(purchase_order_id) == 1 and purchase_order_id.id:
        #     vals = {
        #         'purchase_id': purchase_order_id.id,
        #         'fiscal_position_id': purchase_order_id.fiscal_position_id.id,
        #         'invoice_payment_term_id': purchase_order_id.payment_term_id.id,
        #     }
        # elif len(purchase_order_id) > 1:
        #     vals['purchase_ids'] = purchase_order_id.ids

        return vals
    
    def _get_purchase_line_vals(self):
        partners = self.env['res.partner'].search([
            ('commercial_partner_id', '=', self.partner_id.id)])
        purchase_lines = self.env['purchase.order.line'].search([
            ('partner_id', 'in', partners.ids),
            ('state', 'in', ('purchase', 'done'))])
        decimal_precision = self.env.company.currency_id.decimal_places
        if not purchase_lines:
            return {}
        purchase_line_vals = {}
        for item in self.document_line_ids:
            if item.purchase_line_id:
                lines = item.purchase_line_id
            else:
                purchase_lines._compute_qty_invoiced()
                lines = purchase_lines.filtered(lambda x: x.product_qty and float_compare(x.price_total / x.product_qty, item.valor_liquido / item.quantidade, decimal_precision) == 0)
                                                # and x.order_id.date_approve <= item.eletronic_document_id.data_emissao)
            if lines:
                min_line = lines[0]
                for line in lines:
                    if min_line.order_id.date_order > line.order_id.date_order:
                        min_line = line
                # line = min_line
                purchase_line_vals.update({item.id: {
                'product_id': min_line.product_id,
                'uom_id': min_line.product_id.uom_po_id,
                'purchase_line_id': min_line.id,
                }})
        return purchase_line_vals

    def _get_tax(self, tax_domain, aliquota, company_id, tax_automation=False):
        tax = self.env['account.tax'].search([
            ('domain', '=', tax_domain),
            ('amount', '=', aliquota),
            ('type_tax_use', '=', 'purchase'),
            ('company_id', '=', company_id.id)], limit=1)

        if tax:
            return tax, ""
        elif tax_automation:
            return self._create_tax(tax_domain, aliquota, company_id)

        return None, ''

    def _create_tax(self, tax_domain, aliquota, company_id):
        group_id = self.env['account.tax.group'].search([('name', '=', tax_domain.upper()), ('country_id', '=', self.env.ref('base.br').id)])
        if not group_id:
            group_id = self.env['account.tax.group'].create({'name': tax_domain.upper(), 'country_id': self.env.ref('base.br').id})
        vals = {
            'domain': tax_domain,
            'type_tax_use': 'purchase',
            'name': "%s (%s%%)" % (tax_domain.upper(), aliquota),
            'description': tax_domain.upper(),
            'amount': aliquota,
            'company_id': company_id.id,
            'tax_group_id': group_id.id,
        }
        if tax_domain in ('icms', 'pis', 'cofins'):
            vals.update(dict(amount_type='division', price_include=True))
        if tax_domain in ('icmsst',):
            vals.update(dict(amount_type='icmsst')) 
        tax = self.env['account.tax'].create(vals)

        message = (u"<ul><li>Aliquota criada através da importação\
                   do xml da nf %s<br/></li></ul>" % self.numero)

        return tax, message

    def _create_supplierinfo(self, item, purchase_order_line,
                             automation=False):
        supplierinfo_id = self.env['product.supplierinfo'].search([
            ('partner_id', '=', purchase_order_line.order_id.partner_id.id),
            ('product_code', '=', item.product_cprod)])

        if not supplierinfo_id:
            vals = {
                'partner_id': purchase_order_line.order_id.partner_id.id,
                'product_name': item.product_xprod,
                'product_code': item.product_cprod,
                'product_tmpl_id': purchase_order_line.product_id.id,
            }

            self.env['product.supplierinfo'].create(vals)

            message = u"<ul><li>Produto do fornecedor criado através da\
                        importação do xml da nf %(nf)s. Produto\
                        do fornecedor %(codigo_produto_fornecedor)s\
                            - %(descricao_produto_fornecedor)s criado\
                        para o produto %(codigo_produto)s - \
                        %(descricao_produto)s<br/></li></ul>" % {
                'nf': self.numero,
                'codigo_produto_fornecedor':
                item.product_cprod,
                'descricao_produto_fornecedor':
                item.product_xprod,
                'codigo_produto':
                purchase_order_line.product_id.default_code,
                'descricao_produto':
                purchase_order_line.product_id.name,
            }

            return message

    def _get_purchase_line_id(
            self, item, purchase_order_id, supplierinfo_automation=False):
        purchase_line_ids = self.env['purchase.order.line'].search([
            ('order_id', '=', purchase_order_id)], order='sequence')

        if not purchase_line_ids:
            return False, "Item de ordem de compra não localizado"

        purchase_line_id = purchase_line_ids[int(
            item.item_pedido_compra) - 1]

        if hasattr(purchase_line_id.product_id, 'seller_id'):
            seller_id = purchase_line_id.product_id.seller_id

            if seller_id and seller_id.product_code == item.product_cprod:
                return purchase_line_id
            else:
                return purchase_line_ids.filtered(
                    lambda x: x.product_id.seller_id.product_code ==
                    item.product_cprod)

        message = self._create_supplierinfo(
            item, purchase_line_id, supplierinfo_automation)
        return purchase_line_id, message

    def _get_company_invoice(self, nfe, partner_automation):
        nfe_type = 'in'
        dest_cnpj_cpf = None
        emit_cnpj_cpf = None
        if hasattr(nfe, 'ChaveNFe') and hasattr(nfe, 'ChaveRPS') :
            emit = nfe.CPFCNPJPrestador
            dest = nfe.CPFCNPJTomador
        elif hasattr(nfe, 'infNFe'):
            emit = nfe.infNFe.emit
            dest = nfe.infNFe.dest
        else: 
            emit = nfe.NFe.infNFe.emit
            dest = nfe.NFe.infNFe.dest
            nfe_type = 'in' if nfe.NFe.infNFe.ide.tpNF.text == '0' else 'out'
        tipo_operacao = ''

        if hasattr(emit, 'CNPJ'):
            emit_cnpj_cpf = cnpj_cpf_format(str(emit.CNPJ.text).zfill(14))
        else:
            emit_cnpj_cpf = cnpj_cpf_format(str(emit.CPF.text).zfill(11))

        if hasattr(dest, 'CNPJ'):
            dest_cnpj_cpf = cnpj_cpf_format(str(dest.CNPJ.text).zfill(14))

        if hasattr(dest, 'idEstrangeiro'):
            idEstrangeiro = dest.idEstrangeiro.text
        else:
            dest_cnpj_cpf = cnpj_cpf_format(str(dest.CPF.text).zfill(11))

        # !Importante
        # 1º pesquisa a empresa através do CNPJ, tanto emitente quanto dest.
        # 2º caso a empresa for destinatária usa o cnpj do emitente
        # para cadastrar parceiro senão usa o do destinatário
        # 3º o tipo de operação depende se a empresa emitiu ou não a nota
        # Se ela emitiu usa do xml o tipo, senão inverte o valor

        cnpj_cpf_partner = False
        destinatary = False
        # company = self.env['res.company'].sudo().search(
        #     [('partner_id.vat', '=', dest_cnpj_cpf)])

        # company = self.env.company

        company = self.env.company

        if company.vat == emit_cnpj_cpf and nfe.infNFe.ide.tpNF == 0 and nfe.infNFe.ide.idDest == 3:
            tipo_operacao = 'entrada'
            destinatary = False
            nfe_type = 'in'
        elif company.vat == dest_cnpj_cpf:
            destinatary = True
            cnpj_cpf_partner = emit_cnpj_cpf
            tipo_operacao = 'entrada' # if nfe_type == 'out' else 'saida' # verificar información de entrada o salida
        elif company.vat == emit_cnpj_cpf:
            cnpj_cpf_partner = dest_cnpj_cpf
            tipo_operacao = 'saida' # if nfe_type == 'in' else 'saida'  # verificar información de entrada o salida
        else:
            raise UserError(
                "XML não destinado nem emitido por esta empresa.")

        partner_id = self.env['res.partner'].search([
            ('vat', '=', cnpj_cpf_partner)], limit=1)

        if not partner_automation and not partner_id:
            raise UserError(
                "Parceiro não encontrado, caso deseje cadastrar " +
                "um parceiro selecione a opção 'Cadastrar Parceiro'.")

        return dict(
            company_id=self.env.company.id,
            tipo_operacao=tipo_operacao,
            partner_id=partner_id.id,
            destinatary=destinatary,
            nfe_type=nfe_type,
        )

    # ==================================================
    # Novos métodos para importação de XML
    def get_basic_info(self, nfe):
        nfe_type = get(nfe.NFe.infNFe.ide, 'tpNF', str)
        total = nfe.NFe.infNFe.total.ICMSTot.vNF
        products = len(nfe.NFe.infNFe.det)
        vals = self.inspect_partner_from_nfe(nfe)
        already_imported = self.existing_invoice(nfe)
        return dict(
            already_imported=already_imported,
            nfe_type=nfe_type,
            amount_total=total,
            total_products=products,
            **vals
        )

    def inspect_partner_from_nfe(self, nfe):
        '''Importação da sessão <emit> do xml'''
        nfe_type = nfe.NFe.infNFe.ide.tpNF
        tag_nfe = None
        if nfe_type == 1:
            tag_nfe = nfe.NFe.infNFe.emit
        else:
            tag_nfe = nfe.NFe.infNFe.dest

        if hasattr(tag_nfe, 'CNPJ'):
            cnpj_cpf = cnpj_cpf_format(str(tag_nfe.CNPJ.text).zfill(14))
        else:
            cnpj_cpf = cnpj_cpf_format(str(tag_nfe.CPF.text).zfill(11))

        partner_id = self.env['res.partner'].search([
            ('vat', '=', cnpj_cpf)], limit=1)

        partner_data = "%s - %s" % (cnpj_cpf, tag_nfe.xNome)
        return dict(partner_id=partner_id.id, partner_data=partner_data)
    
    
    def generate_eletronic_document(self, xml_nfe, create_partner):
        nfe = objectify.fromstring(xml_nfe)
        
        invoice_dict = {}
        if self.existing_invoice(nfe):
            raise UserError('Nota Fiscal já importada para o sistema!')

        partner_vals = self._get_company_invoice(nfe, create_partner)
        company_id = self.env['res.company'].browse(
            partner_vals['company_id'])
        invoice_dict.update(partner_vals)
        invoice_dict.update({
            'nfe_processada': base64.b64encode(xml_nfe),
            'nfe_processada_name': "NFe%08d.xml" % nfe.NFe.infNFe.ide.nNF
        })
        invoice_dict.update(self.get_protNFe(nfe, company_id))
        invoice_dict.update(self.get_main(nfe))
        partner = self.get_partner_nfe(
            nfe, partner_vals['destinatary'], create_partner)
        invoice_dict.update(
            self.get_ide(nfe, partner_vals['tipo_operacao']))
        invoice_dict.update(partner)
        invoice_dict.update(self.get_ICMSTot(nfe))
        invoice_dict.update(self.get_items(
            nfe, company_id, partner['partner_id'],
            invoice_dict['partner_id'],
            False))
        invoice_dict.update(self.get_infAdic(nfe))
        invoice_dict.update(self.get_cobr_fat(nfe))
        invoice_dict.update(self.get_transp(nfe))
        invoice_dict.update(
            {'reboque_ids': [(0, None, self.get_reboque(nfe))]})
        invoice_dict.update({'volume_ids': [(0, None, self.get_vol(nfe))]})
        invoice_dict.update(self.get_cobr_dup(nfe))
        invoice_dict.update(self.get_compra(nfe))
        invoice_dict.pop('destinatary', False)
        return self.env['eletronic.document'].create(invoice_dict)

    def check_inconsistency_and_redirect(self, line_vals={}):
        to_check = []
        for line in self.document_line_ids.filtered(lambda x: x.id not in line_vals.keys()):
            if (not line.product_id or not line.uom_id) :
                to_check.append((0, 0, {
                    'eletronic_line_id': line.id,
                    'uom_id': line.uom_id.id,
                    'product_id': line.product_id.id,
                }))

        if to_check:
            wizard = self.env['wizard.nfe.configuration'].create({
                'eletronic_doc_id': self.id,
                'partner_id': self.partner_id.id,
                'nfe_item_ids': to_check
            })
            return {
                "type": "ir.actions.act_window",
                "res_model": "wizard.nfe.configuration",
                'view_type': 'form',
                'views': [[False, 'form']],
                "name": "Configuracao",
                "res_id": wizard.id,
                'flags': {'mode': 'edit'}
            }
            

    def _prepare_account_invoice_vals(self):
        operation = 'in_invoice' \
            if self.tipo_operacao == 'entrada' else 'out_invoice'
        journal_id = self.env['account.move']._search_default_journal().id
        # with_context(
        #     default_move_type=operation, default_company_id=self.company_id.id
        # ).default_get(['journal_id'])['journal_id']
        partner = self.partner_id.with_context(force_company=self.company_id.id)
        account_id = partner.property_account_payable_id.id \
            if operation == 'in_invoice' else \
            partner.property_account_receivable_id.id

        vals = {
            'eletronic_doc_id': self.id,
            'company_id': self.company_id.id,
            'move_type': operation,
            'state': 'draft',
            'invoice_origin': self.pedido_compra,
            'ref': "%s/%s" % (self.numero, self.serie_documento),
            'invoice_date': self.data_emissao.date(),
            'date': self.data_emissao.date(),
            'partner_id': self.partner_id.id,
            'journal_id': journal_id,
            'amount_total': self.valor_final,
            'invoice_payment_term_id': self.env.ref('l10n_br_nfe_import.payment_term_for_import').id,
        }
        return vals

    def prepare_extra_line_items(self, product, price):
        product = product.with_context(force_company=self.company_id.id)
        if product.property_account_expense_id:
            account_id = product.property_account_expense_id
        else:
            account_id =\
                product.categ_id.property_account_expense_categ_id
        return {
            'product_id': product.id,
            'product_uom_id': product.uom_id.id,
            'name': product.name if product.name else product.product_xprod,
            'quantity': 1.0,
            'price_unit': price,
            'account_id': account_id.id,
        }

    def generate_account_move(self, auto=False):
        # Chek Purchase Order Relation and Product
        purchase_line_vals = self._get_purchase_line_vals()
        if not auto:
            next_action = self.check_inconsistency_and_redirect(line_vals=purchase_line_vals)
            if next_action:
                return next_action
        
        vals = self._prepare_account_invoice_vals()

        items = []
        for item in self.document_line_ids:
            invoice_item = self.prepare_account_invoice_line_vals(item, item_vals=purchase_line_vals.get(item.id))
            items.append((0, 0, invoice_item))

        has_purchase = not any(['purchase_line_id' not in item for x, y, item in items])
        if not has_purchase:
            raise UserError(_('XML não pode ser realacionado com nenhum Pedido'))

        if False:
            if self.valor_ipi:
                product = self.env.ref("l10n_br_nfe_import.product_product_tax_ipi")
                items.append((0, 0, self.prepare_extra_line_items(product, self.valor_ipi)))

            if self.valor_icmsst:
                product = self.env.ref("l10n_br_nfe_import.product_product_tax_icmsst")
                items.append((0, 0, self.prepare_extra_line_items(product, self.valor_icmsst)))

        if self.valor_frete:
            product = self.env.ref("l10n_br_account.product_product_delivery")
            items.append((0, 0, self.prepare_extra_line_items(product, self.valor_frete)))

        if self.valor_despesas:
            product = self.env.ref("l10n_br_account.product_product_expense")
            items.append((0, 0, self.prepare_extra_line_items(product, self.valor_despesas)))

        if self.valor_seguro:
            product = self.env.ref("l10n_br_account.product_product_insurance")
            items.append((0, 0, self.prepare_extra_line_items(product, self.valor_seguro)))

        
        vals['invoice_line_ids'] = items
        # vals.update(self._get_purchase_relation(vals))
        account_invoice = self.env['account.move'].create(vals)
        account_invoice.mapped('invoice_line_ids.purchase_line_id')._compute_qty_invoiced()
        account_invoice.message_post(
            body="<ul><li>Fatura criada através da do xml da NF-e %s</li></ul>" % self.numero)
        
        
            # move.duplicated_ref_ids = move_to_duplicate_move.get(move._origin, self.env['account.move'])
        if account_invoice.duplicated_ref_ids:
            raise UserError(_('XML não pode ser importado, fatura duplicada %s' % account_invoice.duplicated_ref_ids.mapped('display_name')))

        if has_purchase:
            account_invoice.action_post()
            self.state = 'purchase'
        else:
            self.state = 'invoice'

        self.move_id = account_invoice

        return True

class EletronicDocumentLine(models.Model):
    _inherit = 'eletronic.document.line'

    product_ean = fields.Char('EAN do Produto (XML)')
    product_cprod = fields.Char('Cód .Fornecedor (XML)')
    product_xprod = fields.Char('Nome do produto (XML)')

    purchase_line_id = fields.Many2one('purchase.order.line', 'Purchase Order Line', ondelete='set null', index='btree_not_null')
    purchase_order_id = fields.Many2one('purchase.order', 'Purchase Order', related='purchase_line_id.order_id')

class EletronicModelRel(models.Model):
    _name = 'eletronic.document.rel'

    # Tipo de documento
    document_type = fields.Selection(
        [('nf', 'NF'), ('nfe', 'NF-e'), ('cte', 'CT-e'),
            ('nfrural', 'NF Produtor'), ('cf', 'Cupom Fiscal')],
        'Tipo Documento', required=True)
    tipo_produto = fields.Selection(
        [("product", "Produto"), ("service", "Serviço")],
        string="Tipo Produto",
    )

    # UF
    state_id = fields.Many2one(
        'res.country.state', 'Estado',
        domain="[('country_id.code', '=', 'BR')]", required=True)
    # Prefeitura / City
    city_id = fields.Many2one(
        string="City of Address",
        comodel_name="res.city",
        domain="[('state_id', '=', state_id)]",
    )
    # Chave para definir
    variable_ids = fields.One2many(
            'eletronic.document.rel.line',
            'rel_id',
            string='XML Variables')
    
class EletronicModelRelLine(models.Model):
    _name = 'eletronic.document.rel.line'

    rel_id = fields.Many2one('eletronic.document.rel', string='Parent Document')
    key = fields.Char(string='Key', required=True)
    xml_variable = fields.Char(string='XML Variable')

