# © 2018 Danimar Ribeiro, Trustcode
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

import os
from lxml import etree
from requests import Session
from zeep import Client
from zeep.transports import Transport

# from pytrustnfe.certificado import extract_cert_and_key_from_pfx, save_cert_key
from .pytrustnfe.xml import render_xml, sanitize_response
from .assinatura import Assinatura


def _render(codigoUsuario, codigoContribuiente, method, **kwargs):
    path = os.path.join(os.path.dirname(__file__), "templates")
    kwargs["rps"]["codigoUsuario"] = codigoUsuario
    kwargs["rps"]["codigoContribuiente"] = codigoContribuiente
    xml_send = render_xml(path, "%s.xml" % method, False, **kwargs)

    reference = ""
    ref_lote = ""
    if method == "GerarNfse":
        reference = "rps:%s" % kwargs["rps"]["numero"]
        ref_lote = "lote%s" % kwargs["rps"]["numero_lote"]
    elif method == "CancelarNfse":
        reference = "pedidoCancelamento_%s" % kwargs["cancelamento"]["numero_nfse"]

    # signer = Assinatura(codigoUsuario, codigoContribuiente)
    # xml_send = signer.assina_xml(xml_send, reference)
    # if ref_lote:
    #     xml_send = signer.assina_xml(etree.fromstring(xml_send), ref_lote)
    return xml_send.encode("utf-8")


def _send(codigoUsuario, codigoContribuiente, method, **kwargs):
    base_url = ""
    if kwargs["ambiente"] != "producao":
        base_url = "https://nfe.etransparencia.com.br/sp.bragancapaulista/webservice/aws_nfe.aspx?wsdl"
    else:
        raise _('Sem Homologação')
        # base_url = "https://bhisshomologa.pbh.gov.br/bhiss-ws/nfse?wsdl"

    xml_send = kwargs["xml"].decode("utf-8")
    # xml_cabecalho = '<?xmerl vsion="1.0" encoding="UTF-8"?>\
    # <cabecalho xmlns="http://www.abrasf.org.br/nfse.xsd" versao="1.00">\
    # <versaoDados>1.00</versaoDados></cabecalho>'
    # xml_header = '<?xmerl vsion="1.0" encoding="UTF-8"?>\
    #                 xmlns:x="http://schemas.xmlsoap.org/soap/envelope/"\
    #                 xmlns:nfe="NFe">'

    # cert, key = extract_cert_and_key_from_pfx(certificado.pfx, certificado.password)
    # cert, key = save_cert_key(cert, key)

    session = Session()
    # session.cert = (cert, key)
    session.verify = False
    transport = Transport(session=session)

    client = Client(base_url, transport=transport)

    xml_send = {
        # {
            'Login': 
         {'CodigoUsuario': 'e0a81039-3356-4db7-9b07-b9b54da4150f46at35si2746luap000-ac16na5g',
          'CodigoContribuinte': '34a1b22a-ebd7-4296-b6c5-3c9965d12ee256--10--0064----725---36--4-',
         }
        }
    #}

    send_values = {'Login': {'CodigoUsuario': codigoUsuario,
                             'CodigoContribuinte': codigoContribuiente,
                            },
                   'SDTRPS': kwargs['rps'],
    }

    response = client.service[method](send_values)
    # response = client.service[method](xml_send)
    # El sistema recomiendo el uso de certificado en protocolo HTTPS
    response, obj = sanitize_response(response)
    return {"sent_xml": xml_send, "received_xml": response, "object": obj}


def xml_gerar_nfse(codigoUsuario, codigoContribuiente, **kwargs):
    return _render(codigoUsuario, codigoContribuiente, "PROCESSARPS", **kwargs)


def gerar_nfse(codigoUsuario, codigoContribuiente, **kwargs):
    if "xml" not in kwargs:
        kwargs["xml"] = xml_gerar_nfse(codigoUsuario, codigoContribuiente, **kwargs)
    return _send(codigoUsuario, codigoContribuiente, "PROCESSARPS", **kwargs)


def xml_cancelar_nfse(certificado, **kwargs):
    return _render(certificado, "CancelarNfse", **kwargs)


def cancelar_nfse(certificado, **kwargs):
    if "xml" not in kwargs:
        kwargs["xml"] = xml_cancelar_nfse(certificado, **kwargs)
    return _send(certificado, "CancelarNfse", **kwargs)
