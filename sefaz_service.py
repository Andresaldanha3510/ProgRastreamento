# sefaz_service.py (VERSÃO CORRIGIDA COM CONSULTA UNIFICADA PARA MÚLTIPLOS CNPJs)
# -*- coding: utf-8 -*-

import os
import tempfile
import boto3
import gzip
import base64
from datetime import datetime, timedelta
from lxml import etree as ET
import requests
import re
from zeep import Client, Settings
from zeep.transports import Transport
from zeep.wsse.signature import BinarySignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization.pkcs12 import load_key_and_certificates
from cryptography.x509.oid import NameOID
from extensions import db, CertificadoDigital, NFeImportada, CertificadoNSU
from flask import current_app
import logging
import time
from sqlalchemy import text

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _get_certificado_obj_from_r2(certificado_info):
    s3_client = boto3.client( 's3', endpoint_url=current_app.config['CLOUDFLARE_R2_ENDPOINT'], aws_access_key_id=current_app.config['CLOUDFLARE_R2_ACCESS_KEY'], aws_secret_access_key=current_app.config['CLOUDFLARE_R2_SECRET_KEY'], region_name='auto' )
    bucket_name = current_app.config['CLOUDFLARE_R2_BUCKET']
    with tempfile.NamedTemporaryFile(delete=False, suffix='.pfx') as tmp:
        s3_client.download_fileobj(bucket_name, certificado_info.caminho_r2, tmp)
        tmp_path = tmp.name
    senha_decriptada = certificado_info.get_senha(current_app.cipher_suite)
    return tmp_path, senha_decriptada


def obter_cnpjs_relacionados(cnpj_certificado):
    cnpj_raiz = cnpj_certificado[:8]
    cnpjs_grupos = { '32683777': ['32683777000275', '32683777000194', '32683777000356'] }
    if cnpj_raiz in cnpjs_grupos:
        cnpjs_relacionados = cnpjs_grupos[cnpj_raiz]
        logger.info(f"CNPJ {cnpj_certificado} - Encontrado grupo com {len(cnpjs_relacionados)} CNPJs: {cnpjs_relacionados}")
        return cnpjs_relacionados
    logger.info(f"CNPJ {cnpj_certificado} - Nenhum grupo configurado, consultando apenas este CNPJ")
    return [cnpj_certificado]


def pode_consultar_sefaz(certificado_info):
    agora = datetime.utcnow()
    if certificado_info.bloqueado_ate and agora < certificado_info.bloqueado_ate:
        minutos_restantes = int((certificado_info.bloqueado_ate - agora).total_seconds() / 60)
        return False, f"Bloqueado por consumo indevido. Aguarde {minutos_restantes} minutos."
    if certificado_info.ultima_consulta_sefaz and (agora - certificado_info.ultima_consulta_sefaz).total_seconds() < 180:
        segundos_restantes = 180 - int((agora - certificado_info.ultima_consulta_sefaz).total_seconds())
        return False, f"Aguarde {segundos_restantes} segundos antes da próxima consulta."
    if certificado_info.data_validade < agora.date():
        return False, "Certificado vencido."
    return True, "OK"


def deve_processar_documento(schema_doc):
    schemas_nfe_validas = {'procNFe_v4.00.xsd', 'procNFe_v3.10.xsd', 'nfe_v4.00.xsd', 'nfe_v3.10.xsd'}
    schemas_eventos = {'procEventoNFe_v1.00.xsd', 'resEvento_v1.01.xsd', 'resNFe_v1.01.xsd'}
    if schema_doc in schemas_eventos: return False, "documento_evento"
    if schema_doc in schemas_nfe_validas: return True, "nfe_valida"
    return True, "schema_desconhecido"


def validar_elementos_obrigatorios_xml(infNFe, ns_nfe):
    emit_node = infNFe.find('nfe:emit', namespaces=ns_nfe)
    if emit_node is None: emit_node = infNFe.find('.//emit')
    total_node = infNFe.find('.//nfe:ICMSTot', namespaces=ns_nfe)
    if total_node is None: total_node = infNFe.find('.//ICMSTot')
    if emit_node is None: return False, None, None, None, None, "Nó emit não encontrado"
    if total_node is None: return False, None, None, None, None, "Nó ICMSTot não encontrado"
    cnpj_element = emit_node.find('nfe:CNPJ', namespaces=ns_nfe)
    if cnpj_element is None: cnpj_element = emit_node.find('.//CNPJ')
    nome_element = emit_node.find('nfe:xNome', namespaces=ns_nfe)
    if nome_element is None: nome_element = emit_node.find('.//xNome')
    valor_element = total_node.find('nfe:vNF', namespaces=ns_nfe)
    if valor_element is None: valor_element = total_node.find('.//vNF')
    dhEmi_element = infNFe.find('nfe:ide/nfe:dhEmi', namespaces=ns_nfe)
    if dhEmi_element is None: dhEmi_element = infNFe.find('.//dhEmi')
    elementos = [cnpj_element, nome_element, valor_element, dhEmi_element]
    nomes = ['CNPJ', 'xNome', 'vNF', 'dhEmi']
    if not all(e is not None for e in elementos):
        faltando = [nome for nome, e in zip(nomes, elementos) if e is None]
        return False, None, None, None, None, f"Elementos não encontrados: {', '.join(faltando)}"
    if not all(e.text and e.text.strip() for e in elementos):
        return False, None, None, None, None, "Campos obrigatórios vazios ou None"
    return True, cnpj_element, nome_element, valor_element, dhEmi_element, "OK"


def processar_nfe_individual(xml_str, nsu_doc, certificado_info, empresa_id, cnpj_consultado):
    """
    Função alterada para salvar o CNPJ do DESTINATÁRIO no campo 'cnpj_consultado'.
    """
    try:
        root_nfe = ET.fromstring(xml_str)
        ns_nfe = {'nfe': 'http://www.portalfiscal.inf.br/nfe'}
        infNFe = root_nfe.find('.//nfe:infNFe', namespaces=ns_nfe)
        if infNFe is None: infNFe = root_nfe.find('.//infNFe')
        if infNFe is None: return {'success': False, 'reason': 'infNFe_nao_encontrado', 'message': 'Elemento infNFe não encontrado'}
        
        chave_acesso = infNFe.attrib.get('Id', '').replace('NFe', '')
        if not chave_acesso or len(chave_acesso) != 44: return {'success': False, 'reason': 'chave_invalida', 'message': f'Chave de acesso inválida: {chave_acesso}'}
        
        if db.session.get(NFeImportada, chave_acesso): return {'success': False, 'reason': 'ja_existe', 'message': f'NFe {chave_acesso} já existe'}

        # --- INÍCIO DA ALTERAÇÃO ---
        # Busca o CNPJ do destinatário dentro do XML
        dest_node = root_nfe.find('.//nfe:dest', namespaces=ns_nfe)
        if dest_node is None: dest_node = root_nfe.find('.//dest')
        
        cnpj_destinatario = None
        if dest_node is not None:
            # Tenta pegar CNPJ, se não encontrar, tenta CPF
            cnpj_node = dest_node.find('nfe:CNPJ', namespaces=ns_nfe)
            if cnpj_node is None: 
                cnpj_node = dest_node.find('nfe:CPF', namespaces=ns_nfe)
            
            if cnpj_node is not None and cnpj_node.text:
                cnpj_destinatario = cnpj_node.text.strip()
        
        # Define qual CNPJ será salvo. Prioriza o do destinatário, mas usa o da consulta como fallback.
        cnpj_para_salvar = cnpj_destinatario if cnpj_destinatario else cnpj_consultado
        # --- FIM DA ALTERAÇÃO ---

        validos, cnpj_elem, nome_elem, valor_elem, dhEmi_elem, erro_msg = validar_elementos_obrigatorios_xml(infNFe, ns_nfe)
        if not validos: return {'success': False, 'reason': 'dados_obrigatorios_ausentes', 'message': erro_msg}
        
        data_emissao = datetime.utcnow()
        if dhEmi_elem.text:
            try: data_emissao = datetime.fromisoformat(dhEmi_elem.text)
            except ValueError: data_emissao = datetime.strptime(dhEmi_elem.text.split('T')[0], '%Y-%m-%d')
            except Exception as e: logger.warning(f"[CERT {certificado_info.id}] NSU {nsu_doc}: Erro ao parsear data '{dhEmi_elem.text}': {e}")
        
        valor_total = 0.0
        try: valor_total = float(valor_elem.text)
        except (ValueError, TypeError): logger.warning(f"[CERT {certificado_info.id}] NSU {nsu_doc}: Erro ao converter valor '{valor_elem.text}'")
        
        nova_nfe = NFeImportada(
            chave_acesso=chave_acesso, 
            empresa_id=empresa_id, 
            certificado_id=certificado_info.id, 
            nsu=nsu_doc, 
            emitente_cnpj=cnpj_elem.text.strip(), 
            emitente_nome=nome_elem.text.strip(), 
            data_emissao=data_emissao, 
            valor_total=valor_total, 
            xml_content=xml_str, 
            status='BAIXADA', 
            # Usa a variável com o CNPJ do destinatário aqui
            cnpj_consultado=cnpj_para_salvar 
        )
        db.session.add(nova_nfe)
        return {'success': True, 'message': 'NFe processada com sucesso'}

    except Exception as e:
        logger.error(f"[CERT {certificado_info.id}] NSU {nsu_doc}: Erro no processamento individual: {e}", exc_info=True)
        return {'success': False, 'reason': 'erro_processamento', 'message': str(e)}


def _consultar_cnpj_com_paginacao(cnpj_consulta, nsu_inicial, client, tpAmb_valor, uf_para_sefaz, certificado_id_log):
    todos_documentos = []
    nsu_corrente = int(nsu_inicial)
    max_consultas = 10 # Limite de segurança para evitar loops infinitos
    cStat_final, xMotivo_final = None, None

    for i in range(max_consultas):
        nsu_busca = str(nsu_corrente).zfill(15)
        logger.info(f"[CERT {certificado_id_log}] CNPJ {cnpj_consulta} (Consulta {i+1}): Enviando com NSU: {nsu_busca}")
        xml_consulta = f'''<distDFeInt xmlns="http://www.portalfiscal.inf.br/nfe" versao="1.01"><tpAmb>{tpAmb_valor}</tpAmb><cUFAutor>{uf_para_sefaz}</cUFAutor><CNPJ>{cnpj_consulta}</CNPJ><distNSU><ultNSU>{nsu_busca}</ultNSU></distNSU></distDFeInt>'''
        
        http_response = client.service.nfeDistDFeInteresse(nfeDadosMsg=ET.fromstring(xml_consulta))
        resposta_bruta = ET.fromstring(http_response.content)

        cStat = resposta_bruta.findtext('.//{*}cStat')
        xMotivo = resposta_bruta.findtext('.//{*}xMotivo')
        ultNSU_resposta = resposta_bruta.findtext('.//{*}ultNSU')
        maxNSU = resposta_bruta.findtext('.//{*}maxNSU')
        
        cStat_final, xMotivo_final = cStat, xMotivo

        if cStat is None:
            xMotivo_final = 'Resposta inválida da SEFAZ (cStat não encontrado)'
            logger.error(f"[CERT {certificado_id_log}] CNPJ {cnpj_consulta}: {xMotivo_final}")
            break

        logger.info(f"[CERT {certificado_id_log}] CNPJ {cnpj_consulta}: SEFAZ cStat: {cStat}, xMotivo: {xMotivo}, ultNSU: {ultNSU_resposta}, maxNSU: {maxNSU}")
        
        if ultNSU_resposta and int(ultNSU_resposta) > nsu_corrente:
            nsu_corrente = int(ultNSU_resposta)
        
        if cStat == '138': # Lote de documentos recebido
            documentos = resposta_bruta.findall('.//{*}docZip')
            todos_documentos.extend(documentos)
            if ultNSU_resposta == maxNSU:
                logger.info(f"[CERT {certificado_id_log}] Fim da paginação. maxNSU ({maxNSU}) alcançado.")
                break
            else:
                logger.info(f"[CERT {certificado_id_log}] Mais docs disponíveis. Paginação...")
                time.sleep(2)
        else:
            break

    return {'cStat': cStat_final, 'xMotivo': xMotivo_final, 'documentos': todos_documentos, 'nsu_final': nsu_corrente}


def _processar_certificado_individual(certificado_info, empresa_id, wsdl_url, tpAmb_valor, uf_para_sefaz):
    """
    VERSÃO CORRIGIDA (2025-09-02): Usa consulta UNIFICADA para múltiplos CNPJs.
    Isso evita quebrar a sequência de paginação da SEFAZ, resolvendo o erro 656.
    """
    logger.info(f"[CERT {certificado_info.id}] Iniciando processamento com consulta unificada.")
    
    tmp_path, senha_decriptada = _get_certificado_obj_from_r2(certificado_info)
    key_path, cert_path = None, None
    
    try:
        with open(tmp_path, 'rb') as f: pfx_data = f.read()
        
        private_key, certificate, _ = load_key_and_certificates(pfx_data, senha_decriptada.encode('utf-8'))
        
        # Extrair CNPJ do certificado para usar como principal na consulta
        cnpj_do_certificado = None
        serial_attrs = certificate.subject.get_attributes_for_oid(NameOID.SERIAL_NUMBER)
        if serial_attrs: cnpj_do_certificado = serial_attrs[0].value.split(':')[0]
        
        if not cnpj_do_certificado:
            common_name_attrs = certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
            if common_name_attrs and (match := re.search(r':(\d{14})', common_name_attrs[0].value)):
                cnpj_do_certificado = match.group(1)
        
        if not cnpj_do_certificado: 
            return {'success': False, 'message': f'Certificado {certificado_info.id}: CNPJ não encontrado.'}
        
        # *** LÓGICA DE CONSULTA UNIFICADA CORRIGIDA ***
        # Obtém a lista de CNPJs relacionados, mas usará apenas o principal para a consulta.
        cnpjs_relacionados = obter_cnpjs_relacionados(cnpj_do_certificado)
        cnpj_para_consulta_unificada = cnpj_do_certificado
        
        logger.info(f"[CERT {certificado_info.id}] Lógica unificada: Usando CNPJ {cnpj_para_consulta_unificada} para consultar o grupo: {cnpjs_relacionados}")
        
        # Setup do cliente SOAP
        private_key_pem = private_key.private_bytes(encoding=serialization.Encoding.PEM, format=serialization.PrivateFormat.TraditionalOpenSSL, encryption_algorithm=serialization.NoEncryption())
        certificate_pem = certificate.public_bytes(serialization.Encoding.PEM)
        
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pem', mode='w+b') as key_tmp, \
             tempfile.NamedTemporaryFile(delete=False, suffix='.pem', mode='w+b') as cert_tmp:
            key_tmp.write(private_key_pem)
            key_path = key_tmp.name
            cert_tmp.write(certificate_pem)
            cert_path = cert_tmp.name
        
        session = requests.Session()
        session.cert = (cert_path, key_path)
        transport = Transport(session=session, timeout=60)
        settings = Settings(strict=False, xml_huge_tree=True, raw_response=True)
        client = Client(wsdl_url, transport=transport, wsse=BinarySignature(key_path, cert_path, 'sha1'), settings=settings)
        
        certificado_info.ultima_consulta_sefaz = datetime.utcnow()
        
        # Obter NSU inicial para o CNPJ principal da consulta
        nsu_inicial = _get_nsu_para_cnpj(certificado_info.id, cnpj_para_consulta_unificada)
        logger.info(f"[CERT {certificado_info.id}] Consultando com NSU inicial específico: {nsu_inicial}")

        # Chamar a função de paginação UMA VEZ e deixá-la rodar até o fim
        resultado_consulta = _consultar_cnpj_com_paginacao(
            cnpj_para_consulta_unificada, nsu_inicial, client, tpAmb_valor, uf_para_sefaz, certificado_info.id
        )

        novo_nsu = resultado_consulta.get('nsu_final', 0)
        
        # Atualizar o NSU individual do CNPJ usado na consulta
        if novo_nsu > nsu_inicial:
            _atualizar_nsu_cnpj(certificado_info.id, cnpj_para_consulta_unificada, novo_nsu)
            logger.info(f"[CERT {certificado_info.id}] NSU do CNPJ {cnpj_para_consulta_unificada} atualizado: {nsu_inicial} → {novo_nsu}")

        # Tratamento para código 656 (consumo indevido)
        if resultado_consulta.get('cStat') == '656':
            logger.warning(f"[CERT {certificado_info.id}] CNPJ {cnpj_para_consulta_unificada}: Consumo indevido detectado")
            certificado_info.bloqueado_ate = datetime.utcnow() + timedelta(hours=1)
            
            # Autocorreção do NSU para o CNPJ consultado
            _atualizar_nsu_cnpj(certificado_info.id, cnpj_para_consulta_unificada, novo_nsu)
            logger.info(f"[CERT {certificado_info.id}] CNPJ {cnpj_para_consulta_unificada}: NSU autocorrigido para {novo_nsu}")
            
            db.session.commit()
            return {
                'success': False, 
                'message': f"CNPJ {cnpj_para_consulta_unificada}: {resultado_consulta['xMotivo']}. NSU corrigido.",
                'motivo': 'consumo_indevido'
            }
        
        documentos = resultado_consulta.get('documentos', [])
        total_documentos = len(documentos)
        
        # Atualiza o NSU geral com o maior valor retornado pela consulta, mesmo que não haja notas
        maior_nsu_retornado = novo_nsu
        maior_nsu_documento = 0 # Será calculado abaixo
        
        if total_documentos == 0:
            certificado_info.ultimo_nsu = str(max(maior_nsu_retornado, int(certificado_info.ultimo_nsu or 0)))
            db.session.commit()
            return {
                'success': True, 
                'message': f'Nenhuma nota nova encontrada para o grupo do CNPJ {cnpj_para_consulta_unificada}. NSU atualizado.',
                'notas_processadas': 0
            }

        logger.info(f"[CERT {certificado_info.id}] Processando {total_documentos} documentos baixados via CNPJ {cnpj_para_consulta_unificada}")
        
        # Processamento dos documentos
        notas_processadas, documentos_pulados, notas_com_erro = 0, 0, 0
        
        with db.session.no_autoflush:
            for doc in documentos:
                nsu_doc = int(doc.attrib.get('NSU', '0'))
                if nsu_doc > maior_nsu_documento:
                    maior_nsu_documento = nsu_doc
                
                deve_proc, _ = deve_processar_documento(doc.attrib.get('schema', ''))
                if not deve_proc:
                    documentos_pulados += 1
                    continue
                
                try:
                    xml_gz_b64 = doc.text.strip() if doc.text else ''
                    if not xml_gz_b64:
                        notas_com_erro += 1
                        continue
                    
                    xml_str = gzip.decompress(base64.b64decode(xml_gz_b64)).decode('utf-8')
                    # O cnpj_consultado é sempre o que foi usado na query
                    resultado = processar_nfe_individual(
                        xml_str, str(nsu_doc).zfill(15), 
                        certificado_info, empresa_id, cnpj_para_consulta_unificada 
                    )
                    
                    if resultado['success']: 
                        notas_processadas += 1
                    elif resultado.get('reason') not in ['ja_existe', 'infNFe_nao_encontrado']:
                        notas_com_erro += 1
                        logger.warning(f"[CERT {certificado_info.id}] NSU {nsu_doc}: {resultado['message']}")
                        
                except Exception as e:
                    notas_com_erro += 1
                    logger.error(f"[CERT {certificado_info.id}] NSU {nsu_doc}: Erro no processamento: {e}", exc_info=True)
        
        db.session.commit()
        
        # Atualizar NSU geral do certificado com o maior valor visto
        nsu_final_real = max(maior_nsu_documento, maior_nsu_retornado, int(certificado_info.ultimo_nsu or 0))
        certificado_info.ultimo_nsu = str(nsu_final_real)
        db.session.commit()
        
        logger.info(f"[CERT {certificado_info.id}] Processamento concluído:")
        logger.info(f"  - {notas_processadas} notas salvas")
        logger.info(f"  - {documentos_pulados} documentos pulados")
        logger.info(f"  - {notas_com_erro} erros")
        logger.info(f"  - NSU final geral: {nsu_final_real}")
        
        return {
            'success': True, 
            'message': f'{notas_processadas} nova(s) nota(s) processada(s).',
            'notas_processadas': notas_processadas,
            'nsu_final': nsu_final_real
        }
        
    except Exception as e:
        logger.error(f"[CERT {certificado_info.id}] ERRO GERAL: {e}", exc_info=True)
        db.session.rollback()
        return {'success': False, 'message': f'Certificado {certificado_info.id} - Erro: {e}'}
        
    finally:
        for path in [tmp_path, key_path, cert_path]:
            if path and os.path.exists(path):
                try: os.remove(path)
                except: pass


def consultar_notas_sefaz(empresa_id):
    logger.info(f"=== INICIANDO CONSULTA SEFAZ PARA EMPRESA {empresa_id} ===")
    ambiente = current_app.config.get('SEFAZ_AMBIENTE', 'PRODUCAO').upper()
    wsdl_url = 'https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx?wsdl'
    tpAmb_valor = '1'
    if ambiente == 'HOMOLOGACAO':
        wsdl_url = 'https://hom1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx?wsdl'
        tpAmb_valor = '2'
        logger.info("🧪 Ambiente de HOMOLOGAÇÃO")
    else:
        logger.info("🏭 Ambiente de PRODUÇÃO")
    uf_para_sefaz = '35'
    certificados = CertificadoDigital.query.filter(CertificadoDigital.empresa_id == empresa_id, CertificadoDigital.data_validade >= datetime.utcnow().date()).order_by(CertificadoDigital.principal.desc(), CertificadoDigital.id.asc()).all()
    if not certificados: return {'success': False, 'message': 'Nenhum certificado válido configurado.'}
    resultados, total_notas = [], 0
    for cert in certificados:
        pode, motivo = pode_consultar_sefaz(cert)
        if not pode:
            logger.warning(f"⌛ Certificado {cert.id} pulado: {motivo}")
            resultados.append({'success': False, 'message': motivo})
            continue
        resultado = _processar_certificado_individual(cert, empresa_id, wsdl_url, tpAmb_valor, uf_para_sefaz)
        resultados.append(resultado)
        if resultado.get('success'): total_notas += resultado.get('notas_processadas', 0)
        if resultado.get('motivo') == 'consumo_indevido': break
    sucessos = sum(1 for r in resultados if r.get('success'))
    if total_notas > 0: return {'success': True, 'message': f'{total_notas} nova(s) nota(s) baixada(s)!', 'notas_processadas': total_notas}
    elif sucessos > 0: return {'success': True, 'message': 'Consulta realizada com sucesso. Nenhuma nota nova encontrada.', 'notas_processadas': 0}
    else:
        msg_erro = resultados[0].get('message') if resultados else 'Erro desconhecido.'
        return {'success': False, 'message': msg_erro}


def get_status_consulta_sefaz(empresa_id):
    certificados = CertificadoDigital.query.filter_by(empresa_id=empresa_id).filter(CertificadoDigital.data_validade >= datetime.utcnow().date()).order_by(CertificadoDigital.principal.desc(), CertificadoDigital.id.asc()).all()
    if not certificados: return {'pode_consultar': False, 'motivo_bloqueio': 'Nenhum certificado válido configurado', 'certificados_status': []}
    pode_consultar_algum = False
    certificados_status = []
    for cert in certificados:
        pode, motivo = pode_consultar_sefaz(cert)
        if pode: pode_consultar_algum = True
        cert_status = {'id': cert.id, 'nome': cert.nome_arquivo or f"Certificado {cert.id}", 'principal': cert.principal, 'pode_consultar': pode, 'motivo_bloqueio': motivo if not pode else None, 'ultima_consulta': cert.ultima_consulta_sefaz.isoformat() if cert.ultima_consulta_sefaz else None, 'bloqueado_ate': cert.bloqueado_ate.isoformat() if cert.bloqueado_ate else None, 'ultimo_nsu': cert.ultimo_nsu or '0', 'data_validade': cert.data_validade.isoformat()}
        certificados_status.append(cert_status)
    return {'pode_consultar': pode_consultar_algum, 'motivo_bloqueio': None if pode_consultar_algum else 'Todos os certificados estão bloqueados ou indisponíveis', 'certificados_status': certificados_status}


def resetar_nsu_certificado(certificado_id, novo_nsu=None):
    try:
        certificado = db.session.get(CertificadoDigital, certificado_id)
        if not certificado: 
            return {'success': False, 'message': 'Certificado não encontrado'}
        
        nsu_anterior = certificado.ultimo_nsu
        certificado.ultimo_nsu = str(novo_nsu) if novo_nsu is not None else '0'
        certificado.bloqueado_ate = None
        certificado.ultima_consulta_sefaz = None
        
        # Resetar NSUs individuais - POSTGRESQL
        try:
            db.session.execute(
                text("UPDATE certificado_nsu SET ultimo_nsu = :nsu, ultima_atualizacao = :data WHERE certificado_id = :cert_id"),
                {"nsu": str(novo_nsu) if novo_nsu is not None else '0', "data": datetime.utcnow(), "cert_id": certificado_id}
            )
            logger.info(f"NSUs individuais do certificado {certificado_id} também foram resetados")
        except Exception as e:
            logger.warning(f"Erro ao resetar NSUs individuais: {e}")
        
        db.session.commit()
        logger.info(f"NSU do certificado {certificado_id} resetado de {nsu_anterior} para {certificado.ultimo_nsu}")
        return {'success': True, 'message': f'NSU resetado de {nsu_anterior} para {certificado.ultimo_nsu}'}
        
    except Exception as e:
        db.session.rollback()
        return {'success': False, 'message': f'Erro ao resetar NSU: {str(e)}'}


def forcar_desbloqueio_todos_certificados(empresa_id):
    try:
        certificados_afetados = CertificadoDigital.query.filter_by(empresa_id=empresa_id).update({'bloqueado_ate': None})
        db.session.commit()
        msg = f"{certificados_afetados} certificado(s) da empresa {empresa_id} foram desbloqueados no sistema."
        logger.info(msg)
        return {'success': True, 'message': msg}
    except Exception as e:
        db.session.rollback()
        msg = f"Erro ao forçar desbloqueio para empresa {empresa_id}: {e}"
        logger.error(msg)
        return {'success': False, 'message': msg}


# ===============================================
# FUNÇÕES PARA NSU INDIVIDUAL POR CNPJ (JÁ CORRETAS)
# ===============================================

def _get_nsu_para_cnpj(certificado_id, cnpj):
    """
    Obtém o NSU específico para um CNPJ - VERSÃO POSTGRESQL
    """
    try:
        result = db.session.execute(
            text("SELECT ultimo_nsu FROM certificado_nsu WHERE certificado_id = :cert_id AND cnpj_consultado = :cnpj"),
            {"cert_id": certificado_id, "cnpj": cnpj}
        ).fetchone()
        
        if result:
            nsu = int(result[0])
            logger.info(f"[NSU] CERT {certificado_id}, CNPJ {cnpj}: NSU específico encontrado: {nsu}")
            return nsu
        else:
            try:
                db.session.execute(
                    text("INSERT INTO certificado_nsu (certificado_id, cnpj_consultado, ultimo_nsu, ultima_atualizacao) VALUES (:cert_id, :cnpj, '0', :data)"),
                    {"cert_id": certificado_id, "cnpj": cnpj, "data": datetime.utcnow()}
                )
                db.session.commit()
                logger.info(f"[NSU] CERT {certificado_id}, CNPJ {cnpj}: Primeira consulta, iniciando com NSU 0")
                return 0
            except Exception as e:
                db.session.rollback()
                logger.warning(f"[NSU] Erro ao criar registro inicial: {e}")
                return 0
            
    except Exception as e:
        db.session.rollback()
        logger.warning(f"[NSU] Erro ao buscar NSU individual (usando fallback): {e}")
        
        certificado = db.session.get(CertificadoDigital, certificado_id)
        if certificado and certificado.ultimo_nsu:
            nsu_fallback = int(certificado.ultimo_nsu)
            logger.info(f"[NSU] CERT {certificado_id}, CNPJ {cnpj}: Usando NSU geral como fallback: {nsu_fallback}")
            return nsu_fallback
        
        return 0


def _atualizar_nsu_cnpj(certificado_id, cnpj, novo_nsu):
    """
    Atualiza o NSU específico para um CNPJ - VERSÃO POSTGRESQL
    """
    try:
        db.session.execute(
            text("""
                INSERT INTO certificado_nsu (certificado_id, cnpj_consultado, ultimo_nsu, ultima_atualizacao)
                VALUES (:cert_id, :cnpj, :nsu, :data)
                ON CONFLICT (certificado_id, cnpj_consultado)
                DO UPDATE SET 
                    ultimo_nsu = EXCLUDED.ultimo_nsu,
                    ultima_atualizacao = EXCLUDED.ultima_atualizacao
            """),
            {"cert_id": certificado_id, "cnpj": cnpj, "nsu": str(novo_nsu), "data": datetime.utcnow()}
        )
        
        db.session.commit()
        logger.info(f"[NSU] CERT {certificado_id}, CNPJ {cnpj}: NSU atualizado para {novo_nsu}")
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"[NSU] Erro ao atualizar NSU individual: {e}")
        
        try:
            certificado = db.session.get(CertificadoDigital, certificado_id)
            if certificado:
                if not certificado.ultimo_nsu or int(certificado.ultimo_nsu) < novo_nsu:
                    certificado.ultimo_nsu = str(novo_nsu)
                    db.session.commit()
                    logger.info(f"[NSU] CERT {certificado_id}: NSU geral atualizado para {novo_nsu} (fallback)")
        except Exception as e2:
            db.session.rollback()
            logger.error(f"[NSU] Falha total ao atualizar NSU: {e2}")

def get_status_nsus_detalhado(certificado_id):
    """
    Diagnóstico de NSUs - VERSÃO POSTGRESQL
    """
    try:
        certificado = db.session.get(CertificadoDigital, certificado_id)
        if not certificado:
            return {"error": "Certificado não encontrado"}
        
        nsus_individuais = db.session.execute(
            text("SELECT cnpj_consultado, ultimo_nsu, ultima_atualizacao FROM certificado_nsu WHERE certificado_id = :cert_id ORDER BY cnpj_consultado"),
            {"cert_id": certificado_id}
        ).fetchall()
        
        resultado = {
            "certificado_id": certificado_id,
            "nome_arquivo": certificado.nome_arquivo,
            "nsu_geral": certificado.ultimo_nsu,
            "ultima_consulta_sefaz": certificado.ultima_consulta_sefaz.isoformat() if certificado.ultima_consulta_sefaz else None,
            "bloqueado_ate": certificado.bloqueado_ate.isoformat() if certificado.bloqueado_ate else None,
            "nsus_por_cnpj": []
        }
        
        for cnpj, nsu, ultima_att in nsus_individuais:
            resultado["nsus_por_cnpj"].append({
                "cnpj": cnpj,
                "nsu": nsu,
                "ultima_atualizacao": str(ultima_att) if ultima_att else None
            })
        
        return resultado
        
    except Exception as e:
        db.session.rollback()
        return {"error": f"Erro ao buscar status: {e}"}


def diagnosticar_nsus_certificado(certificado_id):
    """
    Diagnóstica o estado dos NSUs de um certificado (função auxiliar)
    """
    return get_status_nsus_detalhado(certificado_id)