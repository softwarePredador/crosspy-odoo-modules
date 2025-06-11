import re
import base64
from .bragancapaulista import gerar_nfse
from .bragancapaulista import cancelar_nfse

# from pytrustnfe.certificado import Certificado
from odoo.exceptions import UserError

import re

def _convert_values(vals):

    reg20items_val = []

    reg30items_val = []
    if vals['valor_pis']:
        reg30items_val.append({
            'TributoSigla': 'PIS',
            'TributoValor': '',
        })
    if vals['valor_cofins']:
        reg30items_val.append({
            'TributoSigla': 'COFINS',
            'TributoValor': vals['valor_cofins'],
        })
    if vals['valor_inss']:
        reg30items_val.append({
            'TributoSigla': 'INSS',
            'TributoValor': vals['valor_inss'],
        })
    if vals['valor_ir']:
        reg30items_val.append({
            'TributoSigla': 'IR',
            'TributoValor': vals['valor_ir'],
        })
    if vals['valor_csll']:
        reg30items_val.append({
            'TributoSigla': 'CSLL',
            'TributoValor': vals['valor_csll'],
        })

    reg30_val = {'Reg30Item': reg30items_val}

    for item in vals['itens_servico']:
        reg20items_val.append({	
                'TipoNFS': 'RPS',
                'NumRps': vals['numero_rps'],
                'SerRps': vals['serie'],
                'DtEmi': '{}/{}/{}'.format(vals['data_emissao'][8:10], vals['data_emissao'][5:7], vals['data_emissao'][:4]),
                'RetFonte': 'SIM' if vals['iss_retido'] else 'NAO',
                'CodSrv': item['codigo_servico'],
                'DiscrSrv': vals['discriminacao'],
                'VlNFS': '{:.2f}'.format(vals['valor_total']).replace('.',','),
                'VlDed': '{:.2f}'.format(0.0).replace('.',','),
                'DiscrDed': vals['discriminacao'],
                'VlBasCalc':  '{:.2f}'.format(vals['valor_total']).replace('.',','),
                'AlqIss': '{:.2f}'.format(item['aliquota']).replace('.',','),
                'VlIss': '{:.2f}'.format(vals['valor_iss']).replace('.',','),
                'VlIssRet': '{:.2f}'.format(vals['iss_valor_retencao']).replace('.',','),
                'CpfCnpTom': vals['tomador']['cnpj_cpf'],
                'RazSocTom': vals['tomador']['razao_social'],
                'TipoLogtom':  'RUA',
                'LogTom': vals['tomador']['endereco']['logradouro'],
                'NumEndTom': vals['tomador']['endereco']['numero'] or '1111',
                'ComplEndTom': vals['tomador']['endereco']['complemento'],
                'BairroTom': vals['tomador']['endereco']['bairro'],
                'MunTom': vals['tomador']['endereco']['municipio'],
                'SiglaUFTom': vals['tomador']['endereco']['uf'],
                'CepTom': vals['tomador']['endereco']['cep'],
                'Telefone': vals['tomador']['telefone'],
                'InscricaoMunicipal': vals['tomador']['inscricao_municipal'],
                'TipoLogLocPre': 'RUA',
                'LogLocPre': vals['emissor']['endereco']['logradouro'],
                'NumEndLocPre': vals['emissor']['endereco']['numero'],
                'ComplEndLocPre': vals['emissor']['endereco']['complemento'],
                'BairroLocPre': vals['emissor']['endereco']['bairro'],
                'MunLocPre': vals['emissor']['endereco']['municipio'],
                'SiglaUFLocpre': vals['emissor']['endereco']['uf'],
                'CepLocPre': vals['emissor']['endereco']['cep'],
                'Email1': vals['emissor']['email'],
                'Email2': vals['tomador']['email'],
                'Email3': '',
                'Reg30': reg30_val,
                'Reg40': {'Reg40Item': []},
            })

    reg20_val = {'Reg20Item': reg20items_val}

    reg90_val = {
        'QtdRegNormal': len(reg20items_val),	
        'ValorNFS':'{:.2f}'.format(vals['valor_total']).replace('.',','),	
        'ValorISS': '{:.2f}'.format(vals['valor_inss']).replace('.',','),	
        'ValorDed': '{:.2f}'.format(0.0).replace('.',','),	
        'ValorIssRetTom': '{:.2f}'.format(vals['iss_valor_retencao']).replace('.',','),
        'QtdReg30': len(reg30items_val),
        'ValorTributos': '{:.2f}'.format(sum( item['TributoValor'] for item in reg30items_val)).replace('.',','),
        'QtdReg40': 0,

    }

    vals.update({
    'Ano': vals['data_emissao'][:4],
    'Mes': vals['data_emissao'][5:7],
    'CPFCNPJ': vals['emissor']['cnpj'],
    'DTIni': '{}/{}/{}'.format(vals['data_emissao'][8:10], vals['data_emissao'][5:7], vals['data_emissao'][:4]),
    'DTFin': '{}/{}/{}'.format(vals['data_emissao'][8:10], vals['data_emissao'][5:7], vals['data_emissao'][:4]),
    'TipoTrib': 1,
    'DtAdeSN': '',
    'AlqIssSN_IP': '',
    'Versao': '3.00',
    'Reg20': reg20_val,
    'Reg90': reg90_val,    
    })

    return vals



    TXT = """
<ns0:SDTRPS>
    <ns0:Ano>string</ns0:Ano>
    <ns0:Mes>string</ns0:Mes>
    <ns0:CPFCNPJ>string</ns0:CPFCNPJ>
    <ns0:DTIni>string</ns0:DTIni>
    <ns0:DTFin>string</ns0:DTFin>
    <ns0:TipoTrib>string</ns0:TipoTrib>
    <ns0:DtAdeSN>string</ns0:DtAdeSN>
    <ns0:AlqIssSN_IP>string</ns0:AlqIssSN_IP>
    <ns0:Versao>string</ns0:Versao>
    <ns0:Reg20>
        <ns0:Reg20Item>
        <ns0:TipoNFS>string</ns0:TipoNFS>
        <ns0:NumRps>string</ns0:NumRps>
        <ns0:SerRps>string</ns0:SerRps>
        <ns0:DtEmi>string</ns0:DtEmi>
        <ns0:RetFonte>string</ns0:RetFonte>
        <ns0:CodSrv>string</ns0:CodSrv>
        <ns0:DiscrSrv>string</ns0:DiscrSrv>
        <ns0:VlNFS>string</ns0:VlNFS>
        <ns0:VlDed>string</ns0:VlDed>
        <ns0:VlBasCalc>string</ns0:VlBasCalc>
        <ns0:AlqIss>string</ns0:AlqIss>
        <ns0:VlIss>string</ns0:VlIss>
        <ns0:VlIssRet>string</ns0:VlIssRet>
        <ns0:CpfCnpTom>string</ns0:CpfCnpTom>
        <ns0:RazSocTom>string</ns0:RazSocTom>
        <ns0:TipoLogtom>string</ns0:TipoLogtom>
        <ns0:LogTom>string</ns0:LogTom>
        <ns0:NumEndTom>string</ns0:NumEndTom>
        <ns0:ComplEndTom>string</ns0:ComplEndTom>
        <ns0:BairroTom>string</ns0:BairroTom>
        <ns0:MunTom>string</ns0:MunTom>
        <ns0:SiglaUFTom>string</ns0:SiglaUFTom>
        <ns0:CepTom>string</ns0:CepTom>
        <ns0:Telefone>string</ns0:Telefone>
        <ns0:InscricaoMunicipal>string</ns0:InscricaoMunicipal>
        <ns0:TipoLogLocPre>string</ns0:TipoLogLocPre>
        <ns0:LogLocPre>string</ns0:LogLocPre>
        <ns0:NumEndLocPre>string</ns0:NumEndLocPre>
        <ns0:ComplEndLocPre>string</ns0:ComplEndLocPre>
        <ns0:BairroLocPre>string</ns0:BairroLocPre>
        <ns0:MunLocPre>string</ns0:MunLocPre>
        <ns0:SiglaUFLocpre>string</ns0:SiglaUFLocpre>
        <ns0:CepLocPre>string</ns0:CepLocPre>
        <ns0:Email1>string</ns0:Email1>
        <ns0:Email2>string</ns0:Email2>
        <ns0:Email3>string</ns0:Email3>
        <ns0:Reg30>
            <ns0:Reg30Item>
                <ns0:TributoSigla>string</ns0:TributoSigla>
                <ns0:TributoAliquota>string</ns0:TributoAliquota>
                <ns0:TributoValor>string</ns0:TributoValor>
            </ns0:Reg30Item>
            <ns0:Reg30Item>
                <ns0:TributoSigla>string</ns0:TributoSigla>
                <ns0:TributoAliquota>string</ns0:TributoAliquota>
                <ns0:TributoValor>string</ns0:TributoValor>
            </ns0:Reg30Item>
        </ns0:Reg30>
        <ns0:Reg40>
            <ns0:Reg40Item>
                <ns0:SiglaCpoAdc>string</ns0:SiglaCpoAdc>
                <ns0:ConteudoCpoAdc>string</ns0:ConteudoCpoAdc>
            </ns0:Reg40Item>
            <ns0:Reg40Item>
                <ns0:SiglaCpoAdc>string</ns0:SiglaCpoAdc>
                <ns0:ConteudoCpoAdc>string</ns0:ConteudoCpoAdc>
            </ns0:Reg40Item>
        </ns0:Reg40>
        </ns0:Reg20Item>
        <ns0:Reg20Item>
            <ns0:TipoNFS>string</ns0:TipoNFS>
            <ns0:NumRps>string</ns0:NumRps>
            <ns0:SerRps>string</ns0:SerRps>
            <ns0:DtEmi>string</ns0:DtEmi>
            <ns0:RetFonte>string</ns0:RetFonte>
            <ns0:CodSrv>string</ns0:CodSrv>
            <ns0:DiscrSrv>string</ns0:DiscrSrv>
            <ns0:VlNFS>string</ns0:VlNFS>
            <ns0:VlDed>string</ns0:VlDed>
            <ns0:DiscrDed>string</ns0:DiscrDed>
            <ns0:AlqIss>string</ns0:AlqIss>
            <ns0:VlIss>string</ns0:VlIss>
            <ns0:VlIssRet>string</ns0:VlIssRet>
            <ns0:CpfCnpTom>string</ns0:CpfCnpTom>
            <ns0:RazSocTom>string</ns0:RazSocTom>
            <ns0:TipoLogtom>string</ns0:TipoLogtom>
            <ns0:LogTom>string</ns0:LogTom>
            <ns0:NumEndTom>string</ns0:NumEndTom>
            <ns0:ComplEndTom>string</ns0:ComplEndTom>
            <ns0:BairroTom>string</ns0:BairroTom>
            <ns0:MunTom>string</ns0:MunTom>
            <ns0:SiglaUFTom>string</ns0:SiglaUFTom>
            <ns0:CepTom>string</ns0:CepTom>
            <ns0:Telefone>string</ns0:Telefone>
            <ns0:InscricaoMunicipal>string</ns0:InscricaoMunicipal>
            <ns0:TipoLogLocPre>string</ns0:TipoLogLocPre>
            <ns0:LogLocPre>string</ns0:LogLocPre>
            <ns0:NumEndLocPre>string</ns0:NumEndLocPre>
            <ns0:ComplEndLocPre>string</ns0:ComplEndLocPre>
            <ns0:BairroLocPre>string</ns0:BairroLocPre>
            <ns0:MunLocPre>string</ns0:MunLocPre>
            <ns0:SiglaUFLocpre>string</ns0:SiglaUFLocpre>
            <ns0:CepLocPre>string</ns0:CepLocPre>
            <ns0:Email1>string</ns0:Email1>
            <ns0:Email2>string</ns0:Email2>
            <ns0:Email3>string</ns0:Email3>
            <ns0:Reg30>
                <ns0:Reg30Item>
                    <ns0:TributoSigla>string</ns0:TributoSigla>
                    <ns0:TributoAliquota>string</ns0:TributoAliquota>
                    <ns0:TributoValor>string</ns0:TributoValor>
                </ns0:Reg30Item>
                <ns0:Reg30Item>
                    <ns0:TributoSigla>string</ns0:TributoSigla>
                    <ns0:TributoAliquota>string</ns0:TributoAliquota>
                    <ns0:TributoValor>string</ns0:TributoValor>
                </ns0:Reg30Item>
            </ns0:Reg30>
            <ns0:Reg40>
                <ns0:Reg40Item>
                    <ns0:SiglaCpoAdc>string</ns0:SiglaCpoAdc>
                    <ns0:ConteudoCpoAdc>string</ns0:ConteudoCpoAdc>
                </ns0:Reg40Item>
                <ns0:Reg40Item>
                    <ns0:SiglaCpoAdc>string</ns0:SiglaCpoAdc>
                    <ns0:ConteudoCpoAdc>string</ns0:ConteudoCpoAdc>
                </ns0:Reg40Item>
            </ns0:Reg40>
        </ns0:Reg20Item>
    </ns0:Reg20>
    <ns0:Reg90>
        <ns0:QtdRegNormal>string</ns0:QtdRegNormal>
        <ns0:ValorNFS>string</ns0:ValorNFS>
        <ns0:ValorISS>string</ns0:ValorISS>
        <ns0:ValorDed>string</ns0:ValorDed>
        <ns0:ValorIssRetTom>string</ns0:ValorIssRetTom>
        <ns0:QtdReg30>string</ns0:QtdReg30>
        <ns0:ValorTributos>string</ns0:ValorTributos>
        <ns0:QtdReg40>string</ns0:QtdReg40>
    </ns0:Reg90>
</ns0:SDTRPS>

"""

def XXXXXXX_convert_values(vals):
    # Numero lote
    vals['numero_lote'] =  vals['numero_rps']

    # IdentificacaoRps ~ Status
    vals['numero'] = vals['numero_rps']
    vals['tipo_rps'] = '1'
    vals['natureza_operacao'] = '1'

    if vals['regime_tributario'] == 'simples':
        vals['regime_tributacao'] = '6'
        vals['base_calculo'] = 0
        vals['aliquota_issqn'] = 0
    else:
        vals['regime_tributacao'] = ''
        vals['valor_issqn'] = abs(vals['valor_iss'])

    vals['optante_simples'] = '1' if vals['regime_tributario'] == 'simples' else '2'
    vals['incentivador_cultural'] = '2'
    vals['status'] = '1'

    # Rps Substituído - não está sendo usado no momento

    # Valores
    vals['valor_deducao'] = 0.00
    if vals['valor_iss'] < 0:
        vals['iss_retido'] = '1'
        vals['valor_iss_retido'] = vals['iss_valor_retencao'] = abs(vals['valor_iss'])
        vals['valor_iss'] = 0
    else:
        vals['iss_retido'] = '2'
    vals['aliquota_issqn'] = "%.4f" % abs(vals['itens_servico'][0]['aliquota'])
    vals['descricao'] = vals['discriminacao']

    # Código Serviço
    cod_servico = vals['itens_servico'][0]['codigo_servico']
    for item_servico in vals['itens_servico']:
        if item_servico['codigo_servico'] != cod_servico:
            raise UserError('Não é possível gerar notas de serviço com linhas que possuem código de serviço diferentes.'
                            + '\nPor favor, verifique se todas as linhas de serviço possuem o mesmo código de serviço.'
                            + '\nNome: %s: Código de serviço: %s\nNome: %s: Código de serviço: %s'
                            % (vals['itens_servico'][0]['name'], cod_servico,
                             item_servico['name'], item_servico['codigo_servico']))
    vals['codigo_servico'] = cod_servico
    vals['codigo_tributacao_municipio'] = vals['itens_servico'][0]['codigo_servico_municipio']

    # Prestador
    vals['prestador'] = {}
    vals['prestador']['cnpj'] = re.sub('[^0-9]', '', vals['emissor']['cnpj'])
    vals['prestador']['inscricao_municipal'] = re.sub('\W+','', vals['emissor']['inscricao_municipal'])
    vals['codigo_municipio'] = vals['emissor']['codigo_municipio']

    # Tomador
    vals['tomador'].update(
        vals['tomador']['endereco']
    )
    vals['tomador']['cidade'] =vals['tomador']['codigo_municipio']

    # ValorServicos - ValorPIS - ValorCOFINS - ValorINSS - ValorIR - ValorCSLL - OutrasRetençoes
    # - ValorISSRetido - DescontoIncondicionado - DescontoCondicionado)
    vals['valor_liquido_nfse'] = vals['valor_servico'] \
                                 - (vals.get('valor_pis') or 0) \
                                 - (vals.get('valor_cofins') or 0) \
                                 - (vals.get('valor_inss') or 0) \
                                 - (vals.get('valor_ir') or 0) \
                                 - (vals.get('valor_csll') or 0) \
                                 - (vals.get('outras_retencoes') or 0) \
                                 - (vals.get('valor_iss_retido') or 0)

    # Intermediario e ConstrucaoCivil - não está sendo usado no momento

    return vals


def send_api(codigoUsuario, codigoContribuiente, list_rps):
    # cert_pfx = base64.b64decode(certificate)
    # certificado = Certificado(cert_pfx, password)

    vals = list_rps[0]
    vals = _convert_values(vals)

    recebe_lote = gerar_nfse(
        codigoUsuario, codigoContribuiente, 
        rps=vals, 
        ambiente=vals['ambiente'],
        client_id=vals['client_id'],
        secret_id=vals['client_secret'],
        username=vals['emissor']['inscricao_municipal'],
        # password=vals['user_password'],
        )

    retorno = recebe_lote['object']
    if "ListaNfse" in dir(retorno):
        inf_nfse = retorno.ListaNfse.CompNfse.Nfse.InfNfse
        return {
            'code': 201,
            'entity': {
                'protocolo_nfe': inf_nfse.CodigoVerificacao,
                # get last 9 digits :)
                'numero_nfe': inf_nfse.Numero % 1000000000,
            },
            'xml': recebe_lote['received_xml'].encode('utf-8'),
        }
    else:
        if "ListaMensagemRetornoLote" in dir(retorno):
            mensagem_retorno = retorno.ListaMensagemRetornoLote.MensagemRetorno
        else:
            mensagem_retorno = retorno.ListaMensagemRetorno.MensagemRetorno
        return {
            'code': 400,
            'api_code': mensagem_retorno.Codigo,
            'message': mensagem_retorno.Mensagem,
        }

def cancel_api(certificate, password, vals):
    cert_pfx = base64.b64decode(certificate)
    certificado = Certificado(cert_pfx, password)
    canc = {
        'numero_nfse': vals['numero'],
        'cnpj_prestador': vals['cnpj_cpf'],
        'inscricao_municipal': vals['inscricao_municipal'],
        'cidade': vals['codigo_municipio'],
    }
    resposta = cancelar_nfse(
        certificado, cancelamento=canc,
        ambiente=vals['ambiente'],
        client_id=vals['client_id'],
        secret_id=vals['client_secret'],
        username=vals['inscricao_municipal'],
        password=vals['user_password']
    )
    retorno = resposta['object']
    if "RetCancelamento" in dir(retorno):
        return {
            'code': 200,
            'message': 'Nota Fiscal Cancelada',
            'xml': resposta['received_xml'].split("<?xml version='1.0' encoding='UTF-8'?>")[1].encode('utf - 8')
        }
    else:
        erro_retorno = retorno.ListaMensagemRetorno.MensagemRetorno
        return {
            'code': 400,
            'api_code': erro_retorno.Codigo,
            'message': erro_retorno.Mensagem,
        }
