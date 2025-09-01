# sefaz_service.py (CÓDIGO ANTIGO ADAPTADO PARA MÚLTIPLOS CERTIFICADOS)
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
from extensions import db, CertificadoDigital, NFeImportada
from flask import current_app
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def _get_certificado_obj_from_r2(certificado_info):
    """Baixa certificado do R2 e retorna caminho temporário e senha"""
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


def pode_consultar_sefaz(certificado_info):
    """Verifica se pode fazer consulta à SEFAZ baseado nos limites e bloqueios"""
    agora = datetime.utcnow()
    
    # Verificar se está bloqueado por consumo indevido
    if certificado_info.bloqueado_ate and agora < certificado_info.bloqueado_ate:
        tempo_restante = certificado_info.bloqueado_ate - agora
        minutos_restantes = int(tempo_restante.total_seconds() / 60)
        return False, f"Bloqueado por consumo indevido. Aguarde {minutos_restantes} minutos para nova consulta."
    
    # Verificar intervalo mínimo entre consultas (3 minutos)
    if certificado_info.ultima_consulta_sefaz:
        tempo_desde_ultima = agora - certificado_info.ultima_consulta_sefaz
        if tempo_desde_ultima.total_seconds() < 180:  # 3 minutos
            segundos_restantes = 180 - int(tempo_desde_ultima.total_seconds())
            return False, f"Aguarde {segundos_restantes} segundos antes da próxima consulta."
    
    # Verificar se certificado está vencido
    if certificado_info.data_validade < agora.date():
        return False, "Certificado vencido."
    
    return True, "OK"


def _processar_certificado_individual(certificado_info, empresa_id, ambiente, wsdl_url, tpAmb_valor, uf_para_sefaz):
    """
    Processa um certificado individual usando a LÓGICA ORIGINAL QUE FUNCIONAVA
    """
    logger.info(f"Processando certificado ID {certificado_info.id} - {certificado_info.nome_arquivo or 'Sem nome'}")
    
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
            return {
                'success': False, 
                'message': f'Certificado {certificado_info.id}: Não foi possível encontrar o CNPJ no certificado digital.',
                'certificado_id': certificado_info.id
            }
        
        logger.info(f"CNPJ extraído do certificado {certificado_info.id}: {cnpj_do_certificado}")
        
        # Preparar certificado para conexão - LÓGICA ORIGINAL
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
        
        # Registrar tentativa
        certificado_info.ultima_consulta_sefaz = datetime.utcnow()
        
        # LÓGICA ORIGINAL - SIMPLES E QUE FUNCIONAVA
        nsu_atual = int(certificado_info.ultimo_nsu) if certificado_info.ultimo_nsu else 0
        logger.info(f"Certificado {certificado_info.id} - NSU atual no banco: {nsu_atual}")
        
        # Se NSU for 0, buscar desde o início
        if nsu_atual == 0:
            logger.info(f"Certificado {certificado_info.id} - NSU zerado, buscando desde o início")
            nsu_busca = "000000000000000"
        else:
            nsu_busca = str(nsu_atual).zfill(15)
        
        # Fazer consulta simples - LÓGICA ORIGINAL
        xml_consulta = f'''<distDFeInt xmlns="http://www.portalfiscal.inf.br/nfe" versao="1.01">
    <tpAmb>{tpAmb_valor}</tpAmb>
    <cUFAutor>{uf_para_sefaz}</cUFAutor>
    <CNPJ>{cnpj_do_certificado}</CNPJ>
    <distNSU>
        <ultNSU>{nsu_busca}</ultNSU>
    </distNSU>
</distDFeInt>'''
        
        distDFeInt = ET.fromstring(xml_consulta)
        resposta_bruta = client.service.nfeDistDFeInteresse(nfeDadosMsg=distDFeInt)
        retDistDFeInt = resposta_bruta
        
        # Buscar cStat e xMotivo - LÓGICA ORIGINAL
        cStat = retDistDFeInt.findtext('.//{http://www.portalfiscal.inf.br/nfe}cStat')
        if cStat is None:
            cStat = retDistDFeInt.findtext('cStat')
            
        xMotivo = retDistDFeInt.findtext('.//{http://www.portalfiscal.inf.br/nfe}xMotivo')
        if xMotivo is None:
            xMotivo = retDistDFeInt.findtext('xMotivo')
        
        if cStat is None or xMotivo is None:
            raise Exception(f"Certificado {certificado_info.id}: Não foi possível extrair cStat ou xMotivo da resposta da SEFAZ.")
        
        logger.info(f"Certificado {certificado_info.id} - SEFAZ retornou cStat: {cStat}, xMotivo: {xMotivo}")
        
        # Log adicional para debug
        maxNSU_element = retDistDFeInt.find('.//{http://www.portalfiscal.inf.br/nfe}maxNSU')
        if maxNSU_element is not None:
            logger.info(f"Certificado {certificado_info.id} - maxNSU disponível na SEFAZ: {maxNSU_element.text}")
        
        # TRATAMENTO DE CÓDIGOS - LÓGICA ORIGINAL MELHORADA
        
        if str(cStat) == '589':  # NSU muito alto - resetar para 0
            logger.warning(f"Certificado {certificado_info.id} - NSU enviado era superior ao da base da SEFAZ. Resetando NSU para 0.")
            certificado_info.ultimo_nsu = '0'
            db.session.commit()
            return {
                'success': False, 
                'message': f"Certificado {certificado_info.id}: {xMotivo}. O NSU foi resetado para 0. Tente consultar novamente.",
                'certificado_id': certificado_info.id,
                'motivo': 'nsu_resetado'
            }
        
        elif str(cStat) == '656':  # Consumo indevido
            logger.warning(f"Certificado {certificado_info.id} - Consumo indevido detectado.")
            certificado_info.bloqueado_ate = datetime.utcnow() + timedelta(hours=1)
            
            # CORREÇÃO: Salvar ultNSU se retornado
            ultNSU_retornado = retDistDFeInt.findtext('.//{http://www.portalfiscal.inf.br/nfe}ultNSU')
            if ultNSU_retornado and ultNSU_retornado != '000000000000000':
                certificado_info.ultimo_nsu = ultNSU_retornado
                logger.info(f"Certificado {certificado_info.id} - NSU atualizado para {ultNSU_retornado}")
            
            db.session.commit()
            return {
                'success': False, 
                'message': f"Certificado {certificado_info.id}: {xMotivo}. Por favor, aguarde 1 hora antes de tentar novamente.",
                'certificado_id': certificado_info.id,
                'motivo': 'consumo_indevido'
            }
        
        elif str(cStat) == '137':  # Nenhum documento localizado
            logger.info(f"Certificado {certificado_info.id} - Nenhum documento novo localizado")
            
            # Atualizar NSU se retornado
            ultNSU_retornado = retDistDFeInt.findtext('.//{http://www.portalfiscal.inf.br/nfe}ultNSU')
            if ultNSU_retornado and int(ultNSU_retornado) > nsu_atual:
                certificado_info.ultimo_nsu = ultNSU_retornado
                db.session.commit()
                logger.info(f"Certificado {certificado_info.id} - NSU atualizado para {ultNSU_retornado}")
            
            return {
                'success': True, 
                'message': f'Certificado {certificado_info.id}: Consulta realizada com sucesso. Nenhuma nota nova encontrada.',
                'certificado_id': certificado_info.id,
                'notas_processadas': 0,
                'motivo': 'nenhuma_nota_nova'
            }
        
        elif str(cStat) != '138':  # Outros códigos
            return {
                'success': False, 
                'message': f"Certificado {certificado_info.id} - Código {cStat}: {xMotivo}",
                'certificado_id': certificado_info.id,
                'motivo': f'codigo_{cStat}'
            }
        
        # PROCESSAR DOCUMENTOS - LÓGICA ORIGINAL
        notas_processadas = 0
        lote_docs = retDistDFeInt.findall('.//{http://www.portalfiscal.inf.br/nfe}docZip')
        if not lote_docs:
            lote_docs = retDistDFeInt.findall('.//docZip')
        
        maior_nsu = nsu_atual
        logger.info(f"Certificado {certificado_info.id} - Encontrados {len(lote_docs)} documentos para processar")

        for doc in lote_docs:
            nsu_doc = doc.attrib.get('NSU', '0')
            if int(nsu_doc) > maior_nsu: 
                maior_nsu = int(nsu_doc)
            
            try:
                xml_gz_b64 = doc.text
                if not xml_gz_b64:
                    logger.warning(f"Certificado {certificado_info.id} - Documento NSU {nsu_doc} sem conteúdo")
                    continue
                    
                xml_bytes = gzip.decompress(base64.b64decode(xml_gz_b64))
                xml_str = xml_bytes.decode('utf-8')
                root_nfe = ET.fromstring(xml_str)
                
                # Verificar se é uma NFe
                if 'procNFe' not in root_nfe.tag and 'nfeProc' not in root_nfe.tag:
                    logger.info(f"Certificado {certificado_info.id} - Documento NSU {nsu_doc} não é uma NFe, pulando...")
                    continue
                
                ns_nfe = {'nfe': 'http://www.portalfiscal.inf.br/nfe'}
                infNFe = root_nfe.find('.//nfe:infNFe', namespaces=ns_nfe)
                if infNFe is None:
                    logger.warning(f"Certificado {certificado_info.id} - NFe sem infNFe no NSU {nsu_doc}")
                    continue
                    
                chave_acesso = infNFe.attrib.get('Id', '').replace('NFe', '')
                if not chave_acesso:
                    logger.warning(f"Certificado {certificado_info.id} - NFe sem chave de acesso no NSU {nsu_doc}")
                    continue
                
                # Verificar se já existe no banco
                if db.session.get(NFeImportada, chave_acesso):
                    logger.info(f"Certificado {certificado_info.id} - NFe {chave_acesso} já existe no banco")
                    continue
                
                # Extrair dados essenciais - LÓGICA ORIGINAL
                emit_node = infNFe.find('nfe:emit', namespaces=ns_nfe)
                total_node = infNFe.find('.//nfe:ICMSTot', namespaces=ns_nfe)
                
                if emit_node is None or total_node is None:
                    logger.warning(f"Certificado {certificado_info.id} - Dados incompletos na NFe {chave_acesso}")
                    continue
                
                cnpj_element = emit_node.find('nfe:CNPJ', namespaces=ns_nfe)
                nome_element = emit_node.find('nfe:xNome', namespaces=ns_nfe)
                valor_element = total_node.find('nfe:vNF', namespaces=ns_nfe)
                dhEmi_element = infNFe.find('nfe:ide/nfe:dhEmi', namespaces=ns_nfe)

                if not all([cnpj_element, nome_element, valor_element, dhEmi_element]):
                    logger.warning(f"Certificado {certificado_info.id} - Dados obrigatórios ausentes na NFe {chave_acesso}")
                    continue

                # Processar data
                data_emissao = datetime.utcnow()  # Padrão
                if dhEmi_element.text:
                    try:
                        data_str = dhEmi_element.text.split('T')[0]
                        data_emissao = datetime.strptime(data_str, '%Y-%m-%d')
                    except Exception as e:
                        logger.warning(f"Certificado {certificado_info.id} - Erro ao parsear data de emissão: {e}")
                
                # Criar registro no banco - LÓGICA ORIGINAL
                nova_nfe = NFeImportada(
                    chave_acesso=chave_acesso,
                    empresa_id=empresa_id,
                    certificado_id=certificado_info.id,  # NOVO: Associar ao certificado
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
                logger.info(f"Certificado {certificado_info.id} - NFe processada: {chave_acesso} - {nome_element.text} - R$ {valor_element.text}")
                    
            except Exception as e:
                logger.error(f"Certificado {certificado_info.id} - Erro ao processar documento NSU {nsu_doc}: {str(e)}")
                continue

        # Atualizar NSU - LÓGICA ORIGINAL MELHORADA
        ultNSU_retornado = retDistDFeInt.findtext('.//{http://www.portalfiscal.inf.br/nfe}ultNSU')
        if ultNSU_retornado:
            if int(ultNSU_retornado) > maior_nsu:
                maior_nsu = int(ultNSU_retornado)
        
        certificado_info.ultimo_nsu = str(maior_nsu)
        db.session.commit()
        logger.info(f"Certificado {certificado_info.id} - NSU atualizado para {maior_nsu}")
        
        return {
            'success': True, 
            'message': f'Certificado {certificado_info.id}: {notas_processadas} nova(s) nota(s) processada(s).',
            'certificado_id': certificado_info.id,
            'notas_processadas': notas_processadas
        }

    except Exception as e:
        logger.error(f"Certificado {certificado_info.id} - Erro geral: {str(e)}", exc_info=True)
        return {
            'success': False, 
            'message': f'Certificado {certificado_info.id} - Erro: {str(e)}',
            'certificado_id': certificado_info.id,
            'motivo': 'erro_geral'
        }
    
    finally:
        # Limpar arquivos temporários
        for path in [tmp_path, key_path, cert_path]:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except:
                    pass


def consultar_notas_sefaz(empresa_id):
    """
    Função principal - CÓDIGO ORIGINAL adaptado para MÚLTIPLOS CERTIFICADOS
    """
    
    # Configuração do ambiente - LÓGICA ORIGINAL
    ambiente = current_app.config.get('SEFAZ_AMBIENTE', 'PRODUCAO').upper()
    if ambiente == 'HOMOLOGACAO':
        wsdl_url = 'https://hom1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx?wsdl'
        tpAmb_valor = '2'
        logger.info("Executando em AMBIENTE DE HOMOLOGAÇÃO (TESTES)")
    else:
        wsdl_url = 'https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx?wsdl'
        tpAmb_valor = '1'
        logger.info("Executando em AMBIENTE DE PRODUÇÃO (REAL)")

    uf_para_sefaz = '35'
    
    # NOVO: Buscar TODOS os certificados válidos em vez de apenas um
    certificados = CertificadoDigital.query.filter_by(empresa_id=empresa_id)\
        .filter(CertificadoDigital.data_validade >= datetime.utcnow().date())\
        .order_by(CertificadoDigital.principal.desc(), CertificadoDigital.id.asc()).all()
    
    if not certificados:
        return {
            'success': False, 
            'message': 'Nenhum certificado digital válido configurado para consulta.',
            'detalhes': []
        }

    logger.info(f"Encontrados {len(certificados)} certificado(s) válido(s) para a empresa {empresa_id}")

    # NOVO: Processar cada certificado independentemente
    resultados = []
    total_notas_processadas = 0
    certificados_com_sucesso = 0
    
    for certificado_info in certificados:
        # Verificar se pode consultar este certificado
        pode_consultar, motivo = pode_consultar_sefaz(certificado_info)
        if not pode_consultar:
            logger.info(f"Certificado {certificado_info.id} bloqueado: {motivo}")
            resultados.append({
                'certificado_id': certificado_info.id,
                'success': False,
                'message': f"Certificado {certificado_info.id}: {motivo}",
                'motivo': 'bloqueado',
                'notas_processadas': 0
            })
            continue
        
        # Processar este certificado usando a LÓGICA ORIGINAL
        resultado = _processar_certificado_individual(certificado_info, empresa_id, ambiente, wsdl_url, tpAmb_valor, uf_para_sefaz)
        
        resultados.append(resultado)
        
        # Contabilizar resultados
        if resultado.get('success'):
            certificados_com_sucesso += 1
            if resultado.get('notas_processadas', 0) > 0:
                total_notas_processadas += resultado['notas_processadas']
                logger.info(f"Certificado {certificado_info.id} processou {resultado['notas_processadas']} notas")
        
        # IMPORTANTE: Se foi consumo indevido, tentar próximo certificado
        if resultado.get('motivo') == 'consumo_indevido':
            logger.info(f"Certificado {certificado_info.id} com consumo indevido, tentando próximo certificado...")
            continue
    
    # CONSOLIDAR RESULTADOS FINAIS
    certificados_bloqueados = [r for r in resultados if not r.get('success') and r.get('motivo') == 'bloqueado']
    certificados_com_erro = [r for r in resultados if not r.get('success') and r.get('motivo') != 'bloqueado']
    
    logger.info(f"RESULTADO FINAL:")
    logger.info(f"- Total de notas processadas: {total_notas_processadas}")
    logger.info(f"- Certificados com sucesso: {certificados_com_sucesso}/{len(certificados)}")
    logger.info(f"- Certificados bloqueados: {len(certificados_bloqueados)}")
    logger.info(f"- Certificados com erro: {len(certificados_com_erro)}")
    
    # Determinar resposta final
    if total_notas_processadas > 0:
        return {
            'success': True, 
            'message': f'{total_notas_processadas} nova(s) nota(s) baixada(s) e salva(s) com sucesso!',
            'detalhes': resultados,
            'notas_processadas': total_notas_processadas,
            'certificados_processados': certificados_com_sucesso,
            'certificados_total': len(certificados)
        }
    
    elif certificados_com_sucesso > 0:
        return {
            'success': True, 
            'message': f'Consulta realizada com sucesso em {certificados_com_sucesso} certificado(s). Nenhuma nota nova encontrada.',
            'detalhes': resultados,
            'notas_processadas': 0,
            'certificados_processados': certificados_com_sucesso
        }
    
    elif len(certificados_bloqueados) == len(certificados):
        return {
            'success': False, 
            'message': 'Todos os certificados estão bloqueados ou indisponíveis no momento.',
            'detalhes': resultados
        }
    
    else:
        return {
            'success': False, 
            'message': 'Não foi possível realizar a consulta com nenhum dos certificados disponíveis.',
            'detalhes': resultados
        }


def get_status_consulta_sefaz(empresa_id):
    """Retorna o status atual das consultas SEFAZ para uma empresa"""
    
    certificados = CertificadoDigital.query.filter_by(empresa_id=empresa_id)\
        .filter(CertificadoDigital.data_validade >= datetime.utcnow().date())\
        .order_by(CertificadoDigital.principal.desc(), CertificadoDigital.id.asc()).all()
    
    if not certificados:
        return {
            'pode_consultar': False,
            'motivo_bloqueio': 'Nenhum certificado digital válido configurado',
            'certificados_status': []
        }
    
    agora = datetime.utcnow()
    certificados_status = []
    pode_consultar_algum = False
    
    for cert in certificados:
        pode_consultar, motivo = pode_consultar_sefaz(cert)
        
        if pode_consultar:
            pode_consultar_algum = True
        
        cert_status = {
            'id': cert.id,
            'nome': cert.nome_arquivo or f"Certificado {cert.id}",
            'principal': cert.principal,
            'pode_consultar': pode_consultar,
            'motivo_bloqueio': motivo if not pode_consultar else None,
            'ultima_consulta': cert.ultima_consulta_sefaz.isoformat() if cert.ultima_consulta_sefaz else None,
            'bloqueado_ate': cert.bloqueado_ate.isoformat() if cert.bloqueado_ate else None,
            'ultimo_nsu': cert.ultimo_nsu or '0',
            'data_validade': cert.data_validade.isoformat()
        }
        
        if cert.bloqueado_ate and agora < cert.bloqueado_ate:
            tempo_restante = cert.bloqueado_ate - agora
            cert_status['tempo_restante_bloqueio'] = int(tempo_restante.total_seconds())
        
        certificados_status.append(cert_status)
    
    return {
        'pode_consultar': pode_consultar_algum,
        'motivo_bloqueio': None if pode_consultar_algum else 'Todos os certificados estão bloqueados ou indisponíveis',
        'certificados_status': certificados_status
    }


# FUNÇÕES DE MANUTENÇÃO SIMPLES

def resetar_nsu_certificado(certificado_id, novo_nsu=None):
    """Reset manual do NSU de um certificado específico"""
    try:
        certificado = CertificadoDigital.query.get(certificado_id)
        if not certificado:
            return {'success': False, 'message': 'Certificado não encontrado'}
        
        nsu_anterior = certificado.ultimo_nsu
        
        if novo_nsu is not None:
            certificado.ultimo_nsu = str(novo_nsu)
        else:
            certificado.ultimo_nsu = '0'  # Reset completo
        
        # Limpar bloqueios também
        certificado.bloqueado_ate = None
        certificado.ultima_consulta_sefaz = None
        
        db.session.commit()
        
        logger.info(f"NSU do certificado {certificado_id} resetado de {nsu_anterior} para {certificado.ultimo_nsu}")
        
        return {
            'success': True, 
            'message': f'NSU resetado de {nsu_anterior} para {certificado.ultimo_nsu}',
            'certificado_id': certificado_id,
            'nsu_anterior': nsu_anterior,
            'nsu_novo': certificado.ultimo_nsu
        }
        
    except Exception as e:
        db.session.rollback()
        return {'success': False, 'message': f'Erro ao resetar NSU: {str(e)}'}


def debug_certificados_nsu(empresa_id):
    """Debug detalhado dos NSUs de todos os certificados"""
    certificados = CertificadoDigital.query.filter_by(empresa_id=empresa_id).all()
    
    debug_info = {
        'empresa_id': empresa_id,
        'timestamp': datetime.utcnow().isoformat(),
        'certificados': []
    }
    
    for cert in certificados:
        # Última NFe deste certificado
        ultima_nfe = NFeImportada.query.filter_by(
            empresa_id=empresa_id, 
            certificado_id=cert.id
        ).order_by(NFeImportada.nsu.desc()).first()
        
        # Contagem de NFes por certificado
        total_nfes = NFeImportada.query.filter_by(
            empresa_id=empresa_id, 
            certificado_id=cert.id
        ).count()
        
        cert_info = {
            'id': cert.id,
            'nome_arquivo': cert.nome_arquivo,
            'ultimo_nsu': cert.ultimo_nsu,
            'ultimo_nsu_int': int(cert.ultimo_nsu) if cert.ultimo_nsu else 0,
            'data_validade': cert.data_validade.isoformat(),
            'vencido': cert.data_validade < datetime.utcnow().date(),
            'ultima_consulta': cert.ultima_consulta_sefaz.isoformat() if cert.ultima_consulta_sefaz else None,
            'bloqueado_ate': cert.bloqueado_ate.isoformat() if cert.bloqueado_ate else None,
            'principal': cert.principal,
            'total_nfes_importadas': total_nfes,
            'ultima_nfe': {
                'chave_acesso': ultima_nfe.chave_acesso if ultima_nfe else None,
                'nsu': ultima_nfe.nsu if ultima_nfe else None,
                'data_emissao': ultima_nfe.data_emissao.isoformat() if ultima_nfe else None,
                'emitente': ultima_nfe.emitente_nome if ultima_nfe else None
            } if ultima_nfe else None
        }
        
        debug_info['certificados'].append(cert_info)
    
    debug_info['resumo'] = {
        'total_certificados': len(certificados),
        'certificados_validos': len([c for c in certificados if c.data_validade >= datetime.utcnow().date()]),
        'certificados_bloqueados': len([c for c in certificados if c.bloqueado_ate and c.bloqueado_ate > datetime.utcnow()]),
        'total_nfes_empresa': NFeImportada.query.filter_by(empresa_id=empresa_id).count()
    }
    
    return debug_info


def forcar_desbloqueio_todos_certificados(empresa_id):
    """Remove bloqueios de todos os certificados da empresa"""
    try:
        certificados = CertificadoDigital.query.filter_by(empresa_id=empresa_id).all()
        certificados_desbloqueados = 0
        
        for cert in certificados:
            if cert.bloqueado_ate and cert.bloqueado_ate > datetime.utcnow():
                cert.bloqueado_ate = None
                cert.ultima_consulta_sefaz = None  # Reset tempo também
                certificados_desbloqueados += 1
                logger.info(f"Bloqueio removido do certificado {cert.id}")
        
        db.session.commit()
        
        return {
            'success': True,
            'message': f'Bloqueios removidos de {certificados_desbloqueados} certificado(s)',
            'certificados_desbloqueados': certificados_desbloqueados,
            'total_certificados': len(certificados)
        }
        
    except Exception as e:
        db.session.rollback()
        return {'success': False, 'message': f'Erro: {str(e)}'}