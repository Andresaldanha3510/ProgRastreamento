# sefaz_service.py (VERSÃO CORRIGIDA FINAL)

import os
import tempfile
import boto3
import gzip
import base64
from datetime import datetime
from lxml import etree as ET
import requests
from zeep import Client, Settings
from zeep.transports import Transport
from zeep.wsse.signature import BinarySignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization.pkcs12 import load_key_and_certificates
from extensions import db, CertificadoDigital, NFeImportada
from flask import current_app
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def extrair_cnpj_do_certificado(certificate):
    """Extrai CNPJ do certificado digital"""
    try:
        # Busca o CNPJ no subject do certificado
        for attribute in certificate.subject:
            if attribute.oid._name == 'serialNumber':
                # O serialNumber no certificado A1 contém o CNPJ
                serial = attribute.value
                # Pode vir no formato "12345678000123" ou "12345678000123:PESSOA JURIDICA"
                cnpj = serial.split(':')[0] if ':' in serial else serial
                # Remove caracteres não numéricos
                cnpj = ''.join(filter(str.isdigit, cnpj))
                if len(cnpj) == 14:
                    return cnpj
        
        # Busca alternativa no campo CN (Common Name)
        cn = certificate.subject.rfc4514_string()
        if 'CN=' in cn:
            cn_value = cn.split('CN=')[1].split(',')[0]
            # Extrai números que podem ser CNPJ
            numeros = ''.join(filter(str.isdigit, cn_value))
            if len(numeros) == 14:
                return numeros
                
        return None
    except Exception as e:
        logger.error("Erro ao extrair CNPJ do certificado: %s", str(e))
        return None

def validar_cnpj(cnpj):
    """Valida CNPJ calculando os dígitos verificadores"""
    # Remove caracteres não numéricos
    cnpj = ''.join(filter(str.isdigit, cnpj))
    
    # Verifica se tem 14 dígitos
    if len(cnpj) != 14:
        return False
    
    # Verifica se não é uma sequência de números iguais
    if cnpj == cnpj[0] * 14:
        return False
    
    # Calcula o primeiro dígito verificador
    soma = 0
    pesos = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    for i in range(12):
        soma += int(cnpj[i]) * pesos[i]
    
    resto = soma % 11
    dv1 = 0 if resto < 2 else 11 - resto
    
    # Calcula o segundo dígito verificador
    soma = 0
    pesos = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    for i in range(13):
        soma += int(cnpj[i]) * pesos[i]
    
    resto = soma % 11
    dv2 = 0 if resto < 2 else 11 - resto
    
    # Verifica se os dígitos calculados conferem com os informados
    return int(cnpj[12]) == dv1 and int(cnpj[13]) == dv2

def _get_certificado_obj_from_r2(certificado_info):
    s3_client = boto3.client(
        's3',
        endpoint_url=current_app.config['CLOUDFLARE_R2_ENDPOINT'],
        aws_access_key_id=current_app.config['CLOUDFLARE_R2_ACCESS_KEY'],
        aws_secret_access_key=current_app.config['CLOUDFLARE_R2_SECRET_KEY'],
        region_name='auto'
    )
    bucket_name = current_app.config['CLOUDFLARE_R2_BUCKET']
    
    with tempfile.NamedTemporaryFile(delete=False, suffix='.pfx') as tmp:
        s3_client.download_fileobj(bucket_name, certificado_info.caminho_r2, tmp)
        tmp_path = tmp.name

    senha_decriptada = certificado_info.get_senha(current_app.cipher_suite)
    return tmp_path, senha_decriptada


def consultar_notas_sefaz(empresa_id):
    certificado_info = CertificadoDigital.query.filter_by(empresa_id=empresa_id).first()
    if not certificado_info:
        return {'success': False, 'message': 'Nenhum certificado digital configurado.'}

    uf_para_sefaz = '35' # Código IBGE de um estado autorizador
    empresa = certificado_info.empresa
    
    tmp_path, senha_decriptada = _get_certificado_obj_from_r2(certificado_info)
    
    key_path = None
    cert_path = None
    
    try:
        with open(tmp_path, 'rb') as f: pfx_data = f.read()
        
        private_key, certificate, _ = load_key_and_certificates(pfx_data, senha_decriptada.encode('utf-8'))
        
        # Extrair CNPJ do certificado
        cnpj_certificado = extrair_cnpj_do_certificado(certificate)
        if not cnpj_certificado:
            return {'success': False, 'message': 'Não foi possível extrair CNPJ do certificado digital.'}
        
        # Validar CNPJ do certificado
        if not validar_cnpj(cnpj_certificado):
            return {'success': False, 'message': f'CNPJ do certificado inválido: {cnpj_certificado}'}
        
        logger.info("Usando CNPJ do certificado: %s", cnpj_certificado)
        
        private_key_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        )
        certificate_pem = certificate.public_bytes(serialization.Encoding.PEM)
        
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pem', mode='w+b') as key_tmp:
            key_tmp.write(private_key_pem)
            key_path = key_tmp.name
        
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pem', mode='w+b') as cert_tmp:
            cert_tmp.write(certificate_pem)
            cert_path = cert_tmp.name
        
        wsdl_url = 'https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx?wsdl'
        
        session = requests.Session()
        session.headers.update({'Connection': 'close'})
        session.cert = (cert_path, key_path)
        transport = Transport(session=session, timeout=60)
        
        settings = Settings(strict=False, xml_huge_tree=True)
        client = Client(wsdl_url, transport=transport, wsse=BinarySignature(key_path, cert_path, 'sha1'), settings=settings)
        client.wsse = None
        
        # --- CORREÇÃO APLICADA AQUI ---
        # Criar o XML exatamente como a SEFAZ espera, sem espaços extras
        # Garantir que o NSU seja formatado com 15 dígitos
        nsu_formatado = str(certificado_info.ultimo_nsu).zfill(15)
        
        # Usar CNPJ do certificado (já limpo e validado)
        xml_request_str = f'<distDFeInt versao="1.01" xmlns="http://www.portalfiscal.inf.br/nfe"><tpAmb>1</tpAmb><cUFAutor>{uf_para_sefaz}</cUFAutor><CNPJ>{cnpj_certificado}</CNPJ><distNSU><ultNSU>{nsu_formatado}</ultNSU></distNSU></distDFeInt>'
        
        # Converter string para elemento XML que o Zeep espera
        xml_element = ET.fromstring(xml_request_str)
        
        # Passar o elemento XML dentro do parâmetro nfeDadosMsg com _value_1
        resposta_bruta = client.service.nfeDistDFeInteresse(nfeDadosMsg={'_value_1': xml_element})
        
        # A resposta já vem como elemento XML, não precisa fazer fromstring
        if isinstance(resposta_bruta, str):
            root = ET.fromstring(resposta_bruta)
        else:
            root = resposta_bruta  # Já é um elemento XML
            
        ns = {"nfe": "http://www.portalfiscal.inf.br/nfe"}
        
        # Procurar retDistDFeInt tanto com namespace quanto sem (resposta pode vir de formas diferentes)
        retDistDFeInt = root.find('.//nfe:retDistDFeInt', namespaces=ns)
        if retDistDFeInt is None:
            # Tentar sem namespace específico
            retDistDFeInt = root.find('.//retDistDFeInt')
        if retDistDFeInt is None:
            # Tentar buscar diretamente se já é o elemento
            if hasattr(root, 'tag') and 'retDistDFeInt' in root.tag:
                retDistDFeInt = root
        
        # --- FIM DA CORREÇÃO ---
        
        if retDistDFeInt is None:
            # Log da resposta para debug
            logger.error("Resposta da SEFAZ não contém retDistDFeInt. Resposta completa: %s", 
                        ET.tostring(root, encoding='unicode') if hasattr(root, 'tag') else str(root))
            raise Exception("Estrutura da resposta da SEFAZ inesperada.")
            
        # Buscar cStat e xMotivo tanto com namespace quanto sem
        cStat = retDistDFeInt.findtext('nfe:cStat', namespaces=ns) or retDistDFeInt.findtext('cStat')
        xMotivo = retDistDFeInt.findtext('nfe:xMotivo', namespaces=ns) or retDistDFeInt.findtext('xMotivo')
        
        # Log do status da resposta
        logger.info("SEFAZ retornou cStat: %s, xMotivo: %s", cStat, xMotivo)
        
        if str(cStat) != '138':
            return {'success': False, 'message': f'SEFAZ: {xMotivo} (Código: {cStat})'}

        notas_processadas = 0
        lote_docs = retDistDFeInt.findall('.//nfe:docZip', namespaces=ns)
        maior_nsu = certificado_info.ultimo_nsu
        
        for doc in lote_docs:
            nsu_atual = doc.attrib['NSU']
            if int(nsu_atual) > int(maior_nsu): maior_nsu = nsu_atual
            
            xml_gz_b64 = doc.text
            xml_bytes = gzip.decompress(base64.b64decode(xml_gz_b64))
            xml_str = xml_bytes.decode('utf-8')
            root_nfe = ET.fromstring(xml_str)
            
            if 'procNFe' in ET.QName(root_nfe.tag).localname:
                ns_nfe = {'nfe': 'http://www.portalfiscal.inf.br/nfe'}
                infNFe = root_nfe.find('.//nfe:infNFe', namespaces=ns_nfe)
                if infNFe is None: continue
                chave_acesso = infNFe.attrib['Id'].replace('NFe', '')
                if db.session.get(NFeImportada, chave_acesso): continue
                
                emit_node = infNFe.find('nfe:emit', namespaces=ns_nfe)
                total_node = infNFe.find('.//nfe:ICMSTot', namespaces=ns_nfe)
                nova_nfe = NFeImportada(
                    chave_acesso=chave_acesso, empresa_id=empresa_id, nsu=nsu_atual,
                    emitente_cnpj=emit_node.find('nfe:CNPJ', namespaces=ns_nfe).text,
                    emitente_nome=emit_node.find('nfe:xNome', namespaces=ns_nfe).text,
                    data_emissao=datetime.fromisoformat(infNFe.find('nfe:ide/nfe:dhEmi', namespaces=ns_nfe).text),
                    valor_total=float(total_node.find('nfe:vNF', namespaces=ns_nfe).text),
                    xml_content=xml_str, status='BAIXADA'
                )
                db.session.add(nova_nfe)
                notas_processadas += 1

        certificado_info.ultimo_nsu = maior_nsu
        db.session.commit()
        return {'success': True, 'message': f'{notas_processadas} nova(s) nota(s) baixada(s).'}

    except Exception as e:
        logger.error("General error in consultar_notas_sefaz: %s", str(e), exc_info=True)
        return {'success': False, 'message': f'Erro: {str(e)}'}
    finally:
        if tmp_path and os.path.exists(tmp_path): os.remove(tmp_path)
        if key_path and os.path.exists(key_path): os.remove(key_path)
        if cert_path and os.path.exists(cert_path): os.remove(cert_path)