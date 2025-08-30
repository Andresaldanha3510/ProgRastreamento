# sefaz_service.py (VERSÃO CORRIGIDA - ENCODING E LÓGICA AJUSTADOS)
# -*- coding: utf-8 -*-

import os
import tempfile
import boto3
import gzip
import base64
from datetime import datetime
from lxml import etree as ET
import requests
import re
from zeep import Client, Settings
from zeep.transports import Transport
from zeep.wsse.signature import BinarySignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization.pkcs12 import load_key_and_certificates
from cryptography.x509.oid import NameOID
from extensions import db, CertificadoDigital, NFeImportada
from flask import current_app
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
    ambiente = current_app.config.get('SEFAZ_AMBIENTE', 'PRODUCAO').upper()
    if ambiente == 'HOMOLOGACAO':
        wsdl_url = 'https://hom1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx?wsdl'
        tpAmb_valor = '2'
        logger.info("Executando em AMBIENTE DE HOMOLOGAÇÃO (TESTES)")
    else:
        wsdl_url = 'https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx?wsdl'
        tpAmb_valor = '1'
        logger.info("Executando em AMBIENTE DE PRODUÇÃO (REAL)")

    certificado_info = CertificadoDigital.query.filter_by(empresa_id=empresa_id).first()
    if not certificado_info:
        return {'success': False, 'message': 'Nenhum certificado digital configurado.'}

    uf_para_sefaz = '35'  # SP
    empresa = certificado_info.empresa
    
    tmp_path, senha_decriptada = _get_certificado_obj_from_r2(certificado_info)
    
    key_path = None
    cert_path = None
    
    try:
        with open(tmp_path, 'rb') as f: 
            pfx_data = f.read()
        
        private_key, certificate, _ = load_key_and_certificates(pfx_data, senha_decriptada.encode('utf-8'))
        
        # Extrair CNPJ do certificado
        cnpj_do_certificado = None
        serial_number_attrs = certificate.subject.get_attributes_for_oid(NameOID.SERIAL_NUMBER)
        if serial_number_attrs:
            cnpj_do_certificado = serial_number_attrs[0].value.split(':')[0]
        
        if not cnpj_do_certificado:
            common_name_attrs = certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
            if common_name_attrs:
                match = re.search(r':(\d{14})', common_name_attrs[0].value)
                if match: 
                    cnpj_do_certificado = match.group(1)

        if not cnpj_do_certificado:
            return {'success': False, 'message': 'Não foi possível encontrar o CNPJ no certificado digital.'}
        
        logger.info(f"CNPJ extraído do certificado: {cnpj_do_certificado}")
        
        # Preparar certificado para conexão
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
        
        session = requests.Session()
        session.headers.update({'Connection': 'close'})
        session.cert = (cert_path, key_path)
        transport = Transport(session=session, timeout=60)
        
        settings = Settings(strict=False, xml_huge_tree=True)
        client = Client(wsdl_url, transport=transport, wsse=BinarySignature(key_path, cert_path, 'sha1'), settings=settings)
        client.wsse = None
        
        # ===== LÓGICA CORRIGIDA =====
        
        # Garantir que ultimo_nsu seja válido
        nsu_atual = int(certificado_info.ultimo_nsu) if certificado_info.ultimo_nsu else 0
        logger.info(f"NSU atual no banco: {nsu_atual}")
        
        # Se NSU for 0, buscar desde o início
        if nsu_atual == 0:
            logger.info("NSU zerado, buscando desde o início")
            nsu_busca = "000000000000000"
        else:
            nsu_busca = str(nsu_atual).zfill(15)
        
        # Fazer consulta simples primeiro
        xml_consulta = f'''<distDFeInt xmlns="http://www.portalfiscal.inf.br/nfe" versao="1.01">
    <tpAmb>{tpAmb_valor}</tpAmb>
    <cUFAutor>{uf_para_sefaz}</cUFAutor>
    <CNPJ>{cnpj_do_certificado}</CNPJ>
    <distNSU>
        <ultNSU>{nsu_busca}</ultNSU>
    </distNSU>
</distDFeInt>'''
        
        distDFeInt = ET.fromstring(xml_consulta)
        xml_enviado = ET.tostring(distDFeInt, encoding='unicode', pretty_print=True)
        logger.info(f"XML enviado:\n{xml_enviado}")
        
        resposta_bruta = client.service.nfeDistDFeInteresse(nfeDadosMsg=distDFeInt)
        retDistDFeInt = resposta_bruta
        
        # Log da resposta para debug
        resposta_str = ET.tostring(retDistDFeInt, encoding='unicode', pretty_print=True)
        logger.info(f"Resposta recebida (primeiros 1000 chars):\n{resposta_str[:1000]}")
        
        ns = {"nfe": "http://www.portalfiscal.inf.br/nfe"}
        
        # Buscar cStat e xMotivo
        cStat = retDistDFeInt.findtext('.//{http://www.portalfiscal.inf.br/nfe}cStat')
        if cStat is None:
            cStat = retDistDFeInt.findtext('cStat')
            
        xMotivo = retDistDFeInt.findtext('.//{http://www.portalfiscal.inf.br/nfe}xMotivo')
        if xMotivo is None:
            xMotivo = retDistDFeInt.findtext('xMotivo')
        
        if cStat is None or xMotivo is None:
            logger.error(f"Estrutura da resposta: {resposta_str[:500]}...")
            raise Exception("Não foi possível extrair cStat ou xMotivo da resposta da SEFAZ.")
        
        logger.info(f"SEFAZ retornou cStat: {cStat}, xMotivo: {xMotivo}")
        
        # Extrair maxNSU para logging
        maxNSU_element = retDistDFeInt.find('.//{http://www.portalfiscal.inf.br/nfe}maxNSU')
        if maxNSU_element is not None:
            maxNSU_disponivel = int(maxNSU_element.text)
            logger.info(f"maxNSU disponível na SEFAZ: {maxNSU_disponivel}")
            logger.info(f"Diferença de NSU: {maxNSU_disponivel - nsu_atual}")
        
        # --- TRATAMENTO DE ERROS E CÓDIGOS DE RETORNO ---
        
        # 589: NSU muito alto - resetar para 0
        if str(cStat) == '589':
            logger.warning("NSU enviado era superior ao da base da SEFAZ. Resetando NSU para 0.")
            certificado_info.ultimo_nsu = '0'
            db.session.commit()
            return {'success': False, 'message': f"{xMotivo}. O NSU foi resetado para 0. Tente consultar novamente."}
        
        # 656: Consumo indevido (BLOCO CORRIGIDO)
        if str(cStat) == '656':
            logger.warning("Consumo indevido detectado. Tentando auto-corrigir o NSU.")
            
            # Tenta extrair o ultNSU que a SEFAZ enviou na resposta do erro
            ultNSU_retornado = retDistDFeInt.findtext('.//{http://www.portalfiscal.inf.br/nfe}ultNSU')
            if ultNSU_retornado and int(ultNSU_retornado) > nsu_atual:
                certificado_info.ultimo_nsu = ultNSU_retornado
                db.session.commit()
                logger.info(f"NSU auto-corrigido para {ultNSU_retornado} com base na resposta de erro 656.")
            
            return {'success': False, 'message': f"{xMotivo}. O sistema tentou se corrigir. Por favor, aguarde 1 hora antes de tentar novamente."}
        
        # 137: Nenhum documento localizado (não é erro, apenas não há notas novas)
        if str(cStat) == '137':
            logger.info("Nenhum documento novo localizado para o NSU informado")
            # Ainda assim, atualizar o NSU se retornado
            ultNSU_retornado = retDistDFeInt.findtext('.//{http://www.portalfiscal.inf.br/nfe}ultNSU')
            if ultNSU_retornado and int(ultNSU_retornado) > nsu_atual:
                certificado_info.ultimo_nsu = ultNSU_retornado
                db.session.commit()
                logger.info(f"NSU atualizado para {ultNSU_retornado}")
            return {'success': True, 'message': 'Consulta realizada com sucesso. Nenhuma nota nova encontrada.'}
        
        # 138: Documentos localizados (sucesso)
        if str(cStat) != '138':
            # Outro código não tratado
            return {'success': False, 'message': f"Código {cStat}: {xMotivo}"}
        
        # --- PROCESSAR DOCUMENTOS RETORNADOS ---
        notas_processadas = 0
        lote_docs = retDistDFeInt.findall('.//{http://www.portalfiscal.inf.br/nfe}docZip')
        if not lote_docs:
            lote_docs = retDistDFeInt.findall('.//docZip')
        
        maior_nsu = nsu_atual
        
        logger.info(f"Encontrados {len(lote_docs)} documentos para processar")

        for doc in lote_docs:
            nsu_doc = doc.attrib.get('NSU', '0')
            if int(nsu_doc) > maior_nsu: 
                maior_nsu = int(nsu_doc)
            
            try:
                xml_gz_b64 = doc.text
                if not xml_gz_b64:
                    logger.warning(f"Documento NSU {nsu_doc} sem conteúdo")
                    continue
                    
                xml_bytes = gzip.decompress(base64.b64decode(xml_gz_b64))
                xml_str = xml_bytes.decode('utf-8')
                root_nfe = ET.fromstring(xml_str)
                
                # Verificar se é uma NFe (procNFe ou nfeProc)
                if 'procNFe' not in root_nfe.tag and 'nfeProc' not in root_nfe.tag:
                    logger.info(f"Documento NSU {nsu_doc} não é uma NFe, pulando...")
                    continue
                
                ns_nfe = {'nfe': 'http://www.portalfiscal.inf.br/nfe'}
                infNFe = root_nfe.find('.//nfe:infNFe', namespaces=ns_nfe)
                if infNFe is None:
                    logger.warning(f"NFe sem infNFe no NSU {nsu_doc}")
                    continue
                    
                chave_acesso = infNFe.attrib.get('Id', '').replace('NFe', '')
                if not chave_acesso:
                    logger.warning(f"NFe sem chave de acesso no NSU {nsu_doc}")
                    continue
                
                # Verificar se já existe no banco
                if db.session.get(NFeImportada, chave_acesso):
                    logger.info(f"NFe {chave_acesso} já existe no banco")
                    continue
                
                # Extrair dados essenciais
                emit_node = infNFe.find('nfe:emit', namespaces=ns_nfe)
                total_node = infNFe.find('.//nfe:ICMSTot', namespaces=ns_nfe)
                
                if emit_node is None or total_node is None:
                    logger.warning(f"Dados incompletos na NFe {chave_acesso}")
                    continue
                
                # Extrair CNPJ do emitente
                cnpj_element = emit_node.find('nfe:CNPJ', namespaces=ns_nfe)
                if cnpj_element is None:
                    logger.warning(f"NFe {chave_acesso} sem CNPJ do emitente")
                    continue
                
                # Extrair nome do emitente
                nome_element = emit_node.find('nfe:xNome', namespaces=ns_nfe)
                if nome_element is None:
                    logger.warning(f"NFe {chave_acesso} sem nome do emitente")
                    continue
                
                # Extrair valor total
                valor_element = total_node.find('nfe:vNF', namespaces=ns_nfe)
                if valor_element is None:
                    logger.warning(f"NFe {chave_acesso} sem valor total")
                    continue
                
                # Extrair data de emissão
                dhEmi_element = infNFe.find('nfe:ide/nfe:dhEmi', namespaces=ns_nfe)
                data_emissao = datetime.utcnow()  # Padrão
                if dhEmi_element is not None and dhEmi_element.text:
                    try:
                        # Remover timezone para simplificar
                        data_str = dhEmi_element.text.split('T')[0]
                        data_emissao = datetime.strptime(data_str, '%Y-%m-%d')
                    except Exception as e:
                        logger.warning(f"Erro ao parsear data de emissão: {e}")
                
                # Criar registro no banco
                nova_nfe = NFeImportada(
                    chave_acesso=chave_acesso,
                    empresa_id=empresa_id,
                    nsu=nsu_doc,
                    emitente_cnpj=cnpj_element.text,
                    emitente_nome=nome_element.text,
                    data_emissao=data_emissao,
                    valor_total=float(valor_element.text),
                    xml_content=xml_str,
                    status='BAIXADA'
                )
                db.session.add(nova_nfe)
                notas_processadas += 1
                logger.info(f"NFe processada: {chave_acesso} - {nome_element.text} - R$ {valor_element.text}")
                    
            except Exception as e:
                logger.error(f"Erro ao processar documento NSU {nsu_doc}: {str(e)}", exc_info=True)
                continue

        # Atualizar NSU mesmo se não processou notas
        ultNSU_retornado = retDistDFeInt.findtext('.//{http://www.portalfiscal.inf.br/nfe}ultNSU')
        if ultNSU_retornado:
            if int(ultNSU_retornado) > maior_nsu:
                maior_nsu = int(ultNSU_retornado)
        
        certificado_info.ultimo_nsu = str(maior_nsu)
        db.session.commit()
        logger.info(f"NSU atualizado para {maior_nsu}")
        
        if notas_processadas > 0:
            return {'success': True, 'message': f'{notas_processadas} nova(s) nota(s) baixada(s) com sucesso!'}
        else:
            return {'success': True, 'message': 'Consulta realizada com sucesso. Nenhuma nota nova encontrada.'}

    except Exception as e:
        logger.error(f"Erro geral em consultar_notas_sefaz: {str(e)}", exc_info=True)
        db.session.rollback()
        return {'success': False, 'message': f'Erro: {str(e)}'}
    finally:
        # Limpar arquivos temporários
        for path in [tmp_path, key_path, cert_path]:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except:
                    pass