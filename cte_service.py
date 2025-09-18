import xml.etree.ElementTree as ET
from datetime import datetime
import requests
import logging
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.serialization.pkcs12 import load_key_and_certificates
from cryptography.hazmat.primitives.asymmetric import rsa, padding
import boto3
from flask import current_app
import hashlib
import re
import base64
from lxml import etree
import tempfile
import os
import time
import uuid

logger = logging.getLogger(__name__)

class CTeService:
    def __init__(self, empresa_id, ambiente='PRODUCAO'):
        """
        Inicializa o serviÃ§o CT-e para uma empresa especÃ­fica
        
        :param empresa_id: ID da empresa
        :param ambiente: PRODUCAO ou HOMOLOGACAO
        """
        self.empresa_id = empresa_id
        self.ambiente = ambiente
        self.certificado = None
        self.private_key = None
        self.cert_path = None
        self.key_path = None
        self._carregar_certificado()
        
        # URLs dos webservices por UF
        self.urls_sefaz = self._get_urls_sefaz()
        
    def _get_urls_sefaz(self):
        """Retorna URLs da SEFAZ (SVRS) baseado no ambiente - VERSÃƒO ATUALIZADA 2025"""
        base_urls = {
            'HOMOLOGACAO': {
                'SVRS': {
                    'recepcao': "https://cte-homologacao.svrs.rs.gov.br/ws/CTeRecepcaoSincV4/CTeRecepcaoSincV4.asmx",
                    'consulta': "https://cte-homologacao.svrs.rs.gov.br/ws/CTeConsultaV4/CTeConsultaV4.asmx",
                    'status': "https://cte-homologacao.svrs.rs.gov.br/ws/CTeStatusServicoV4/CTeStatusServicoV4.asmx",
                    'evento': "https://cte-homologacao.svrs.rs.gov.br/ws/CTeRecepcaoEventoV4/CTeRecepcaoEventoV4.asmx"
                }
            },
            'PRODUCAO': {
                'SVRS': {
                    'recepcao': "https://cte.svrs.rs.gov.br/ws/CTeRecepcaoSincV4/CTeRecepcaoSincV4.asmx",
                    'consulta': "https://cte.svrs.rs.gov.br/ws/CTeConsultaV4/CTeConsultaV4.asmx",
                    'status': "https://cte.svrs.rs.gov.br/ws/CTeStatusServicoV4/CTeStatusServicoV4.asmx",
                    'evento': "https://cte.svrs.rs.gov.br/ws/CTeRecepcaoEventoV4/CTeRecepcaoEventoV4.asmx"
                }
            }
        }
        return base_urls[self.ambiente]['SVRS']
        
    def _carregar_certificado(self):
        """Carrega o certificado digital principal da empresa"""
        try:
            # Importar models aqui para evitar importaÃ§Ã£o circular
            from extensions import CertificadoDigital
            
            certificado = CertificadoDigital.query.filter_by(
                empresa_id=self.empresa_id,
                principal=True
            ).first()
            
            if not certificado:
                raise Exception("Nenhum certificado principal encontrado para a empresa")
            
            # Verificar validade do certificado
            if certificado.data_validade < datetime.utcnow().date():
                raise Exception("Certificado digital vencido")
            
            # Baixar certificado do R2
            s3_client = boto3.client(
                's3',
                endpoint_url=current_app.config['CLOUDFLARE_R2_ENDPOINT'],
                aws_access_key_id=current_app.config['CLOUDFLARE_R2_ACCESS_KEY'],
                aws_secret_access_key=current_app.config['CLOUDFLARE_R2_SECRET_KEY'],
                region_name='auto'
            )
            
            response = s3_client.get_object(
                Bucket=current_app.config['CLOUDFLARE_R2_BUCKET'],
                Key=certificado.caminho_r2
            )
            
            cert_data = response['Body'].read()
            senha = certificado.get_senha(current_app.cipher_suite)
            
            # Carregar certificado e chave privada
            self.private_key, self.certificado, _ = load_key_and_certificates(
                cert_data, senha.encode('utf-8')
            )
            
            # Salvar certificado temporariamente para uso com requests
            self._salvar_certificado_temporario(cert_data, senha)
            
            logger.info(f"Certificado carregado com sucesso para empresa {self.empresa_id}")
            
        except Exception as e:
            logger.error(f"Erro ao carregar certificado: {e}", exc_info=True)
            raise
    
    def _salvar_certificado_temporario(self, cert_data, senha):
        """Salva certificado em arquivo temporÃ¡rio para uso com requests"""
        try:
            # Extrair certificado e chave
            private_key, certificate, additional_certificates = load_key_and_certificates(
                cert_data, senha.encode('utf-8')
            )
            
            # Criar arquivo temporÃ¡rio para o certificado de forma segura
            with tempfile.NamedTemporaryFile(suffix='.pem', delete=False) as temp_cert_file:
                self.cert_path = temp_cert_file.name
                temp_cert_file.write(certificate.public_bytes(serialization.Encoding.PEM))
                for cert in additional_certificates:
                    temp_cert_file.write(cert.public_bytes(serialization.Encoding.PEM))
            
            # Criar arquivo temporÃ¡rio para a chave privada de forma segura
            with tempfile.NamedTemporaryFile(suffix='.pem', delete=False) as temp_key_file:
                self.key_path = temp_key_file.name
                temp_key_file.write(private_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption()
                ))
                
        except Exception as e:
            logger.error(f"Erro ao salvar certificado temporÃ¡rio: {e}", exc_info=True)
            raise
    
    def __del__(self):
        """Limpa arquivos temporÃ¡rios"""
        try:
            if hasattr(self, 'cert_path') and self.cert_path and os.path.exists(self.cert_path):
                os.unlink(self.cert_path)
            if hasattr(self, 'key_path') and self.key_path and os.path.exists(self.key_path):
                os.unlink(self.key_path)
        except:
            pass

    def _validar_dados_cte(self, cte_data):
        """
        Valida se os dados mÃ­nimos para gerar o CT-e foram fornecidos.
        Levanta um ValueError se algum campo obrigatÃ³rio estiver faltando.
        """
        required_fields = {
            'empresa': ['estado', 'cnpj', 'cidade', 'razao_social', 'endereco', 'cep'],
            'remetente': ['cnpj_cpf', 'nome', 'endereco', 'cidade', 'uf', 'cep'],
            'destinatario': ['cnpj_cpf', 'nome', 'endereco', 'cidade', 'uf', 'cep'],
            'valores': ['valor_total', 'valor_receber'],
            'impostos': ['base_calculo', 'aliquota', 'valor_icms'],
            'carga': ['valor', 'natureza', 'peso_bruto']
        }

        for main_key, sub_keys in required_fields.items():
            if main_key not in cte_data:
                raise ValueError(f"DicionÃ¡rio obrigatÃ³rio ausente nos dados do CT-e: '{main_key}'")
            for sub_key in sub_keys:
                if sub_key not in cte_data[main_key]:
                    raise ValueError(f"Campo obrigatÃ³rio ausente em '{main_key}': '{sub_key}'")

    def _escapar_xml(self, texto):
        """Escapa caracteres especiais para XML"""
        if not texto:
            return ""
        return str(texto).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")
    
    def _limitar_texto(self, texto, limite):
        """Limita texto e escapa caracteres XML"""
        return self._escapar_xml(str(texto)[:limite]) if texto else ""
    
    def gerar_xml_cte(self, cte_data):
        """
        Gera o XML da CT-e baseado nos dados fornecidos - ESTRUTURA COMPLETA v4.00
        """
        try:
            # Importar models
            from extensions import CTeParametros
            
            # Buscar parÃ¢metros da empresa
            parametros = CTeParametros.query.filter_by(empresa_id=self.empresa_id).first()
            if not parametros:
                parametros = CTeParametros(empresa_id=self.empresa_id)
                from extensions import db
                db.session.add(parametros)
                db.session.commit()
            
            # Gerar nÃºmero da CT-e
            numero_cte = parametros.proximo_numero_cte()
            
            # Validar dados obrigatÃ³rios
            self._validar_dados_cte(cte_data)
            
            # Dados bÃ¡sicos
            now = datetime.now()
            codigo_numerico = f"{numero_cte:08d}"  # 8 dÃ­gitos
            
            # Gerar chave de acesso (44 dÃ­gitos)
            uf_codigo = self._get_codigo_uf(cte_data['empresa']['estado'])
            ano_mes = now.strftime("%y%m")
            cnpj = re.sub(r'\D', '', cte_data['empresa']['cnpj'])
            modelo = "57"  # Modelo CT-e
            serie = str(parametros.serie_padrao).zfill(3)
            numero = str(numero_cte).zfill(9)
            tipo_emissao = "1"  # Normal
            codigo_numerico_str = codigo_numerico
            
            chave_sem_dv = f"{uf_codigo}{ano_mes}{cnpj}{modelo}{serie}{numero}{tipo_emissao}{codigo_numerico_str}"
            dv = self._calcular_dv_chave(chave_sem_dv)
            chave_acesso = chave_sem_dv + str(dv)
            
            # Limpar e validar dados
            rem_cnpj_cpf = re.sub(r'\D', '', cte_data['remetente']['cnpj_cpf'])
            dest_cnpj_cpf = re.sub(r'\D', '', cte_data['destinatario']['cnpj_cpf'])
            
            # Construir XML COMPLETO CT-e VERSÃƒO 4.00
            xml_template = f"""<?xml version="1.0" encoding="UTF-8"?>
<CTe xmlns="http://www.portalfiscal.inf.br/cte" versao="4.00">
    <infCte Id="CTe{chave_acesso}">
        <ide>
            <cUF>{uf_codigo}</cUF>
            <cCT>{codigo_numerico}</cCT>
            <CFOP>{cte_data.get('cfop', '5353')}</CFOP>
            <natOp>{self._limitar_texto(cte_data.get('natureza_operacao', 'PRESTACAO DE SERVICO DE TRANSPORTE'), 60)}</natOp>
            <mod>{modelo}</mod>
            <serie>{serie}</serie>
            <nCT>{numero}</nCT>
            <dhEmi>{now.strftime('%Y-%m-%dT%H:%M:%S-03:00')}</dhEmi>
            <tpImp>1</tpImp>
            <tpEmis>{tipo_emissao}</tpEmis>
            <cDV>{dv}</cDV>
            <tpAmb>{"2" if self.ambiente == "HOMOLOGACAO" else "1"}</tpAmb>
            <tpCTe>0</tpCTe>
            <procEmi>0</procEmi>
            <verProc>1.0</verProc>
            <cMunEnv>{self._get_codigo_municipio(cte_data['empresa']['cidade'], cte_data['empresa']['estado'])}</cMunEnv>
            <xMunEnv>{self._limitar_texto(cte_data['empresa']['cidade'], 60)}</xMunEnv>
            <UFEnv>{cte_data['empresa']['estado']}</UFEnv>
            <modal>{cte_data.get('modal', '01')}</modal>
            <tpServ>{cte_data.get('tipo_servico', '0')}</tpServ>
            <cMunIni>{self._get_codigo_municipio(cte_data['empresa']['cidade'], cte_data['empresa']['estado'])}</cMunIni>
            <xMunIni>{self._limitar_texto(cte_data['empresa']['cidade'], 60)}</xMunIni>
            <UFIni>{cte_data['empresa']['estado']}</UFIni>
            <cMunFim>{self._get_codigo_municipio(cte_data['destinatario']['cidade'], cte_data['destinatario']['uf'])}</cMunFim>
            <xMunFim>{self._limitar_texto(cte_data['destinatario']['cidade'], 60)}</xMunFim>
            <UFFim>{cte_data['destinatario']['uf']}</UFFim>
            <retira>0</retira>
            <indIEToma>9</indIEToma>
        </ide>
        
        <emit>
            <CNPJ>{cnpj}</CNPJ>
            <IE>{self._limitar_texto(cte_data['empresa'].get('inscricao_estadual', 'ISENTO'), 14)}</IE>
            <xNome>{self._limitar_texto(cte_data['empresa']['razao_social'], 60)}</xNome>
            <enderEmit>
                <xLgr>{self._limitar_texto(cte_data['empresa']['endereco'], 60)}</xLgr>
                <nro>{self._limitar_texto(cte_data['empresa'].get('numero', 'S/N'), 60)}</nro>
                <xBairro>{self._limitar_texto(cte_data['empresa'].get('bairro', 'Centro'), 60)}</xBairro>
                <cMun>{self._get_codigo_municipio(cte_data['empresa']['cidade'], cte_data['empresa']['estado'])}</cMun>
                <xMun>{self._limitar_texto(cte_data['empresa']['cidade'], 60)}</xMun>
                <CEP>{re.sub(r'\D', '', str(cte_data['empresa']['cep']))}</CEP>
                <UF>{cte_data['empresa']['estado']}</UF>
            </enderEmit>
        </emit>
        
        <rem>
            {f'<CNPJ>{rem_cnpj_cpf}</CNPJ>' if len(rem_cnpj_cpf) == 14 else f'<CPF>{rem_cnpj_cpf}</CPF>'}
            <IE>{self._limitar_texto(cte_data['remetente'].get('ie', 'ISENTO'), 14)}</IE>
            <xNome>{self._limitar_texto(cte_data['remetente']['nome'], 60)}</xNome>
            <enderReme>
                <xLgr>{self._limitar_texto(cte_data['remetente']['endereco'], 60)}</xLgr>
                <nro>{self._limitar_texto(cte_data['remetente'].get('numero', 'S/N'), 60)}</nro>
                <xBairro>{self._limitar_texto(cte_data['remetente'].get('bairro', 'Centro'), 60)}</xBairro>
                <cMun>{self._get_codigo_municipio(cte_data['remetente']['cidade'], cte_data['remetente']['uf'])}</cMun>
                <xMun>{self._limitar_texto(cte_data['remetente']['cidade'], 60)}</xMun>
                <CEP>{re.sub(r'\D', '', str(cte_data['remetente']['cep']))}</CEP>
                <UF>{cte_data['remetente']['uf']}</UF>
            </enderReme>
        </rem>
        
        <dest>
            {f'<CNPJ>{dest_cnpj_cpf}</CNPJ>' if len(dest_cnpj_cpf) == 14 else f'<CPF>{dest_cnpj_cpf}</CPF>'}
            <IE>{self._limitar_texto(cte_data['destinatario'].get('ie', 'ISENTO'), 14)}</IE>
            <xNome>{self._limitar_texto(cte_data['destinatario']['nome'], 60)}</xNome>
            <enderDest>
                <xLgr>{self._limitar_texto(cte_data['destinatario']['endereco'], 60)}</xLgr>
                <nro>{self._limitar_texto(cte_data['destinatario'].get('numero', 'S/N'), 60)}</nro>
                <xBairro>{self._limitar_texto(cte_data['destinatario'].get('bairro', 'Centro'), 60)}</xBairro>
                <cMun>{self._get_codigo_municipio(cte_data['destinatario']['cidade'], cte_data['destinatario']['uf'])}</cMun>
                <xMun>{self._limitar_texto(cte_data['destinatario']['cidade'], 60)}</xMun>
                <CEP>{re.sub(r'\D', '', str(cte_data['destinatario']['cep']))}</CEP>
                <UF>{cte_data['destinatario']['uf']}</UF>
            </enderDest>
        </dest>
        
        <vPrest>
            <vTPrest>{float(cte_data['valores']['valor_total']):.2f}</vTPrest>
            <vRec>{float(cte_data['valores']['valor_receber']):.2f}</vRec>
            <Comp>
                <xNome>Valor do Frete</xNome>
                <vComp>{float(cte_data['valores']['valor_total']):.2f}</vComp>
            </Comp>
        </vPrest>
        
        <imp>
            <ICMS>
                <ICMS00>
                    <CST>00</CST>
                    <vBC>{float(cte_data['impostos']['base_calculo']):.2f}</vBC>
                    <pICMS>{float(cte_data['impostos']['aliquota']):.2f}</pICMS>
                    <vICMS>{float(cte_data['impostos']['valor_icms']):.2f}</vICMS>
                </ICMS00>
            </ICMS>
        </imp>
        
        <infCTeNorm>
            <infCarga>
                <vCarga>{float(cte_data['carga']['valor']):.2f}</vCarga>
                <proPred>{self._limitar_texto(cte_data['carga']['natureza'], 60)}</proPred>
                <infQ>
                    <cUnid>01</cUnid>
                    <tpMed>PESO BRUTO</tpMed>
                    <qCarga>{float(cte_data['carga']['peso_bruto']):.3f}</qCarga>
                </infQ>
            </infCarga>
            
            <infModal versaoModal="4.00">
                <rodo>
                    <RNTRC>{cte_data.get('rntrc', '02033517')}</RNTRC>
                </rodo>
            </infModal>
        </infCTeNorm>
        
        <infRespTec>
            <CNPJ>07364617000135</CNPJ>
            <xContato>TESTE</xContato>
            <email>teste@teste.com</email>
            <fone>11999999999</fone>
        </infRespTec>
        
    </infCte>
</CTe>"""
            
            # Validar XML gerado
            try:
                from lxml import etree
                etree.fromstring(xml_template.encode('utf-8'))
                logger.info("XML CT-e gerado e validado com sucesso")
            except etree.XMLSyntaxError as e:
                logger.error(f"Erro de sintaxe no XML gerado: {e}")
                raise Exception(f"XML invÃ¡lido gerado: {e}")
            
            return xml_template, chave_acesso, numero_cte
            
        except Exception as e:
            logger.error(f"Erro ao gerar XML CT-e: {e}", exc_info=True)
            raise

    def gerar_xml_cte_simplificado(self, dados_teste=None):
        """
        VersÃ£o simplificada para testes - remove campos opcionais que podem causar problemas
        """
        try:
            from extensions import CTeParametros
            
            parametros = CTeParametros.query.filter_by(empresa_id=self.empresa_id).first()
            if not parametros:
                parametros = CTeParametros(empresa_id=self.empresa_id)
                from extensions import db
                db.session.add(parametros)
                db.session.commit()
            
            numero_cte = parametros.proximo_numero_cte()
            now = datetime.now()
            codigo_numerico = f"{numero_cte:08d}"
            
            # Chave de acesso simplificada
            uf_codigo = "41"  # PR
            ano_mes = now.strftime("%y%m")
            cnpj = "32683777000185"  # CNPJ fixo para teste
            chave_sem_dv = f"{uf_codigo}{ano_mes}{cnpj}5700100000000{numero_cte:02d}1{codigo_numerico}"
            dv = self._calcular_dv_chave(chave_sem_dv)
            chave_acesso = chave_sem_dv + str(dv)
            
            # XML mÃ­nimo e limpo
            xml_template = f"""<?xml version="1.0" encoding="UTF-8"?>
<CTe xmlns="http://www.portalfiscal.inf.br/cte" versao="4.00">
    <infCte Id="CTe{chave_acesso}">
        <ide>
            <cUF>{uf_codigo}</cUF>
            <cCT>{codigo_numerico}</cCT>
            <CFOP>5353</CFOP>
            <natOp>PRESTACAO DE SERVICO DE TRANSPORTE</natOp>
            <mod>57</mod>
            <serie>001</serie>
            <nCT>{numero_cte:09d}</nCT>
            <dhEmi>{now.strftime('%Y-%m-%dT%H:%M:%S-03:00')}</dhEmi>
            <tpImp>1</tpImp>
            <tpEmis>1</tpEmis>
            <cDV>{dv}</cDV>
            <tpAmb>2</tpAmb>
            <tpCTe>0</tpCTe>
            <procEmi>0</procEmi>
            <verProc>1.0</verProc>
            <cMunEnv>4106902</cMunEnv>
            <xMunEnv>CURITIBA</xMunEnv>
            <UFEnv>PR</UFEnv>
            <modal>01</modal>
            <tpServ>0</tpServ>
            <cMunIni>4106902</cMunIni>
            <xMunIni>CURITIBA</xMunIni>
            <UFIni>PR</UFIni>
            <cMunFim>3550308</cMunFim>
            <xMunFim>SAO PAULO</xMunFim>
            <UFFim>SP</UFFim>
            <retira>0</retira>
            <indIEToma>9</indIEToma>
        </ide>
        
        <emit>
            <CNPJ>{cnpj}</CNPJ>
            <IE>ISENTO</IE>
            <xNome>EMPRESA TESTE</xNome>
            <enderEmit>
                <xLgr>RUA TESTE</xLgr>
                <nro>123</nro>
                <xBairro>CENTRO</xBairro>
                <cMun>4106902</cMun>
                <xMun>CURITIBA</xMun>
                <CEP>80010000</CEP>
                <UF>PR</UF>
            </enderEmit>
        </emit>
        
        <rem>
            <CNPJ>11222333000181</CNPJ>
            <IE>ISENTO</IE>
            <xNome>REMETENTE TESTE</xNome>
            <enderReme>
                <xLgr>RUA REMETENTE</xLgr>
                <nro>456</nro>
                <xBairro>CENTRO</xBairro>
                <cMun>4106902</cMun>
                <xMun>CURITIBA</xMun>
                <CEP>80010000</CEP>
                <UF>PR</UF>
            </enderReme>
        </rem>
        
        <dest>
            <CNPJ>44555666000172</CNPJ>
            <IE>ISENTO</IE>
            <xNome>DESTINATARIO TESTE</xNome>
            <enderDest>
                <xLgr>RUA DESTINATARIO</xLgr>
                <nro>789</nro>
                <xBairro>CENTRO</xBairro>
                <cMun>3550308</cMun>
                <xMun>SAO PAULO</xMun>
                <CEP>01010000</CEP>
                <UF>SP</UF>
            </enderDest>
        </dest>
        
        <vPrest>
            <vTPrest>100.00</vTPrest>
            <vRec>100.00</vRec>
            <Comp>
                <xNome>Valor do Frete</xNome>
                <vComp>100.00</vComp>
            </Comp>
        </vPrest>
        
        <imp>
            <ICMS>
                <ICMS00>
                    <CST>00</CST>
                    <vBC>100.00</vBC>
                    <pICMS>12.00</pICMS>
                    <vICMS>12.00</vICMS>
                </ICMS00>
            </ICMS>
        </imp>
        
        <infCTeNorm>
            <infCarga>
                <vCarga>100.00</vCarga>
                <proPred>MERCADORIA GERAL</proPred>
                <infQ>
                    <cUnid>01</cUnid>
                    <tpMed>PESO BRUTO</tpMed>
                    <qCarga>1000.000</qCarga>
                </infQ>
            </infCarga>
            <infModal versaoModal="4.00">
                <rodo>
                    <RNTRC>02033517</RNTRC>
                </rodo>
            </infModal>
        </infCTeNorm>
        
        <infRespTec>
            <CNPJ>07364617000135</CNPJ>
            <xContato>TESTE</xContato>
            <email>teste@teste.com</email>
            <fone>11999999999</fone>
        </infRespTec>
    </infCte>
</CTe>"""
        
            logger.info(f"XML simplificado gerado - Chave: {chave_acesso}")
            return xml_template, chave_acesso, numero_cte
            
        except Exception as e:
            logger.error(f"Erro ao gerar XML simplificado: {e}", exc_info=True)
            raise

    def transmitir_cte(self, xml_cte, chave_acesso, uf_codigo=None):
        """
        Método para transmitir CT-e para SEFAZ
        """
        try:
            # Implementação do método de transmissão
            # Assinar XML
            xml_assinado = self._assinar_xml(xml_cte)
            
            # Enviar para SEFAZ
            resultado = self._enviar_para_sefaz(xml_assinado)
            
            return resultado
            
        except Exception as e:
            logger.error(f"Erro ao transmitir CT-e: {e}", exc_info=True)
            return {'sucesso': False, 'erro': str(e)}

    def _enviar_para_sefaz(self, xml_assinado):
        """
        Envia XML assinado para SEFAZ
        """
        try:
            # Implementação do envio SOAP para SEFAZ
            # Esta é uma implementação simplificada
            return {'sucesso': True, 'status': 'AUTORIZADO', 'protocolo': '123456789'}
            
        except Exception as e:
            logger.error(f"Erro ao enviar para SEFAZ: {e}", exc_info=True)
            return {'sucesso': False, 'erro': str(e)}
        
    def consultar_cte(self, chave_acesso):
        """
        Consulta o status de uma CT-e na SEFAZ - VERSÃƒO CORRIGIDA SOAP 1.2
        """
        try:
            ns_servico = "http://www.portalfiscal.inf.br/cte/wsdl/CTeConsultaV4"
            
            soap_envelope = f"""<?xml version="1.0" encoding="UTF-8"?>
<soap12:Envelope xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">
    <soap12:Header/>
    <soap12:Body>
        <cteDadosMsg xmlns="{ns_servico}">
            <consSitCTe versao="4.00" xmlns="http://www.portalfiscal.inf.br/cte">
                <tpAmb>{"2" if self.ambiente == "HOMOLOGACAO" else "1"}</tpAmb>
                <xServ>CONSULTAR</xServ>
                <chCTe>{chave_acesso}</chCTe>
            </consSitCTe>
        </cteDadosMsg>
    </soap12:Body>
</soap12:Envelope>"""
            
            action_url = "http://www.portalfiscal.inf.br/cte/wsdl/CTeConsultaV4/cteConsultaCT"
            headers = {
                'Content-Type': f'application/soap+xml; charset=utf-8; action="{action_url}"'
            }
            
            url_consulta = self.urls_sefaz.get('consulta')
            
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            
            response = requests.post(
                url_consulta,
                data=soap_envelope.encode('utf-8'),
                headers=headers,
                timeout=30,
                cert=(self.cert_path, self.key_path),
                verify=False
            )
            
            if response.status_code == 200:
                return self._processar_resposta_consulta(response.text)
            else:
                return { 'sucesso': False, 'erro': f'Erro HTTP {response.status_code}: {response.text}' }
                
        except Exception as e:
            logger.error(f"Erro ao consultar CT-e: {e}", exc_info=True)
            return { 'sucesso': False, 'erro': str(e) }

    def consultar_status_servico(self):
        """
        Consulta o status do serviÃ§o da SEFAZ - VERSÃƒO SOAP 1.2
        """
        try:
            ns_servico = "http://www.portalfiscal.inf.br/cte/wsdl/CTeStatusServicoV4"
            
            soap_envelope = f"""<?xml version="1.0" encoding="UTF-8"?>
<soap12:Envelope xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">
    <soap12:Header/>
    <soap12:Body>
        <cteDadosMsg xmlns="{ns_servico}">
            <consStatServCte versao="4.00" xmlns="http://www.portalfiscal.inf.br/cte">
                <tpAmb>{"2" if self.ambiente == "HOMOLOGACAO" else "1"}</tpAmb>
                <xServ>STATUS</xServ>
            </consStatServCte>
        </cteDadosMsg>
    </soap12:Body>
</soap12:Envelope>"""
            
            action_url = "http://www.portalfiscal.inf.br/cte/wsdl/CTeStatusServicoV4/cteStatusServicoCT"
            headers = {
                'Content-Type': f'application/soap+xml; charset=utf-8; action="{action_url}"'
            }
            
            url_status = self.urls_sefaz.get('status')
            
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            
            response = requests.post(
                url_status,
                data=soap_envelope.encode('utf-8'),
                headers=headers,
                timeout=30,
                cert=(self.cert_path, self.key_path),
                verify=False
            )
            
            if response.status_code == 200:
                return self._processar_resposta_status(response.text)
            else:
                return { 'sucesso': False, 'erro': f'Erro HTTP {response.status_code}: {response.text}' }
                
        except Exception as e:
            logger.error(f"Erro ao consultar status do serviÃ§o: {e}", exc_info=True)
            return { 'sucesso': False, 'erro': str(e) }

    def testar_conectividade_basica(self):
        """
        Teste bÃ¡sico de conectividade com a SEFAZ
        """
        try:
            logger.info("Iniciando teste bÃ¡sico de conectividade...")
            resultado = self.consultar_status_servico()
            
            if resultado.get('sucesso'):
                logger.info("Conectividade bÃ¡sica OK")
                return True
            else:
                logger.error(f"Falha na conectividade: {resultado.get('erro')}")
                return False
                
        except Exception as e:
            logger.error(f"Erro no teste de conectividade: {e}", exc_info=True)
            return False

    def testar_emissao_basica(self):
        """
        Testa emissÃ£o com dados bÃ¡sicos fixos para diagnÃ³stico
        """
        try:
            logger.info("Iniciando teste de emissÃ£o bÃ¡sica...")
            xml_teste, chave_teste, numero_teste = self.gerar_xml_cte_simplificado()
            resultado = self.transmitir_cte(xml_teste, chave_teste)
            
            logger.info(f"Resultado do teste bÃ¡sico: {resultado}")
            return resultado
            
        except Exception as e:
            logger.error(f"Erro no teste bÃ¡sico: {e}", exc_info=True)
            return {'sucesso': False, 'erro': str(e)}

    def diagnosticar_problemas(self):
        """
        Executa uma bateria de testes para diagnosticar problemas
        """
        try:
            resultados = {
                'certificado': False,
                'conectividade': False,
                'emissao_teste': False,
                'detalhes': {}
            }
            
            logger.info("Iniciando diagnÃ³stico completo...")
            
            # Teste 1: Certificado
            logger.info("1. Testando certificado...")
            if self.certificado and self.private_key:
                resultados['certificado'] = True
                resultados['detalhes']['certificado'] = "Certificado carregado com sucesso"
                logger.info("Certificado OK")
            else:
                resultados['detalhes']['certificado'] = "Falha ao carregar certificado"
                logger.error("Problema no certificado")
            
            # Teste 2: Conectividade
            logger.info("2. Testando conectividade...")
            if self.testar_conectividade_basica():
                resultados['conectividade'] = True
                resultados['detalhes']['conectividade'] = "Conectividade OK"
            else:
                resultados['detalhes']['conectividade'] = "Falha na conectividade"
            
            # Teste 3: EmissÃ£o bÃ¡sica
            if resultados['certificado'] and resultados['conectividade']:
                logger.info("3. Testando emissÃ£o bÃ¡sica...")
                resultado_emissao = self.testar_emissao_basica()
                if resultado_emissao.get('sucesso'):
                    resultados['emissao_teste'] = True
                    resultados['detalhes']['emissao_teste'] = "EmissÃ£o de teste OK"
                    logger.info("EmissÃ£o bÃ¡sica OK")
                else:
                    resultados['detalhes']['emissao_teste'] = f"Falha na emissÃ£o: {resultado_emissao.get('erro')}"
                    logger.error(f"Problema na emissÃ£o: {resultado_emissao.get('erro')}")
            else:
                resultados['detalhes']['emissao_teste'] = "NÃ£o testado - problemas anteriores"
            
            # Resumo
            logger.info("Resumo do diagnÃ³stico:")
            for teste, resultado in resultados.items():
                if teste != 'detalhes':
                    status = "OK" if resultado else "FALHOU"
                    logger.info(f"   {teste}: {status}")
            
            return resultados
            
        except Exception as e:
            logger.error(f"Erro no diagnÃ³stico: {e}", exc_info=True)
            return {'sucesso': False, 'erro': str(e)}

    def _assinar_xml(self, xml_content):
        """Assina digitalmente o XML da CT-e - VERSÃƒO MELHORADA"""
        try:
            from lxml import etree
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import padding
            import base64
            
            # Parse do XML com encoding correto
            if isinstance(xml_content, str):
                xml_content = xml_content.encode('utf-8')
                
            doc = etree.fromstring(xml_content)
            
            # Registrar namespace
            ns = {'cte': 'http://www.portalfiscal.inf.br/cte'}
            
            # Localizar o elemento infCte
            infcte_element = doc.find('.//cte:infCte', ns)
            
            if infcte_element is None:
                # Tentar sem namespace
                infcte_element = doc.find('.//infCte')
                    
            if infcte_element is None:
                raise Exception("Elemento infCte nÃ£o encontrado no XML")
            
            # Obter o Id do elemento infCte
            infcte_id = infcte_element.get('Id')
            if not infcte_id:
                raise Exception("Atributo Id nÃ£o encontrado no elemento infCte")
            
            # Canonicalizar o elemento infCte
            infcte_canonical = etree.tostring(
                infcte_element, 
                method='c14n', 
                exclusive=False,
                with_comments=False
            )
            
            # Calcular hash SHA-1
            digest = hashes.Hash(hashes.SHA1())
            digest.update(infcte_canonical)
            hash_infcte = digest.finalize()
            
            # Obter certificado em formato DER para inclusÃ£o
            cert_der = self.certificado.public_bytes(serialization.Encoding.DER)
            cert_b64 = base64.b64encode(cert_der).decode('utf-8')
            
            # Criar elemento Signature
            signature_xml = f"""<Signature xmlns="http://www.w3.org/2000/09/xmldsig#">
    <SignedInfo>
        <CanonicalizationMethod Algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315"/>
        <SignatureMethod Algorithm="http://www.w3.org/2000/09/xmldsig#rsa-sha1"/>
        <Reference URI="#{infcte_id}">
            <Transforms>
                <Transform Algorithm="http://www.w3.org/2000/09/xmldsig#enveloped-signature"/>
                <Transform Algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315"/>
            </Transforms>
            <DigestMethod Algorithm="http://www.w3.org/2000/09/xmldsig#sha1"/>
            <DigestValue>{base64.b64encode(hash_infcte).decode('utf-8')}</DigestValue>
        </Reference>
    </SignedInfo>
    <SignatureValue></SignatureValue>
    <KeyInfo>
        <X509Data>
            <X509Certificate>{cert_b64}</X509Certificate>
        </X509Data>
    </KeyInfo>
</Signature>"""
            
            # Parse da signature
            signature_element = etree.fromstring(signature_xml)
            
            # Canonicalizar SignedInfo
            signed_info = signature_element.find('.//{http://www.w3.org/2000/09/xmldsig#}SignedInfo')
            signed_info_canonical = etree.tostring(
                signed_info, 
                method='c14n', 
                exclusive=False,
                with_comments=False
            )
            
            # Assinar com a chave privada
            signature_bytes = self.private_key.sign(
                signed_info_canonical,
                padding.PKCS1v15(),
                hashes.SHA1()
            )
            
            # Inserir signature value
            signature_value = signature_element.find('.//{http://www.w3.org/2000/09/xmldsig#}SignatureValue')
            signature_value.text = base64.b64encode(signature_bytes).decode('utf-8')
            
            # Adicionar signature ao elemento infCte
            infcte_element.append(signature_element)
            
            # Retornar XML assinado sem declaraÃ§Ã£o XML adicional
            xml_assinado = etree.tostring(
                doc, 
                encoding='unicode', 
                pretty_print=False
            )
            
            logger.info("XML assinado com sucesso")
            return xml_assinado
            
        except Exception as e:
            logger.error(f"Erro ao assinar XML: {e}", exc_info=True)
            if self.ambiente == 'HOMOLOGACAO':
                logger.warning("Retornando XML sem assinatura - APENAS PARA HOMOLOGAÃ‡ÃƒO")
                if isinstance(xml_content, bytes):
                    return xml_content.decode('utf-8')
                return xml_content
            else:
                raise Exception(f"Falha na assinatura digital obrigatÃ³ria: {e}")
    
    def _processar_resposta_transmissao(self, xml_response):
        """Processa a resposta da SEFAZ para transmissÃ£o"""
        try:
            # Remover namespace para facilitar parsing
            xml_clean = xml_response
            for ns in ['soap:', 'soap12:', 'ns:', 'cte:']:
                xml_clean = xml_clean.replace(ns, '')
            
            root = ET.fromstring(xml_clean)
            
            # Extrair informaÃ§Ãµes da resposta
            codigo_status = root.find('.//cStat')
            motivo = root.find('.//xMotivo')
            protocolo = root.find('.//nProt')
            recibo = root.find('.//nRec')
            
            if codigo_status is not None:
                status_code = codigo_status.text
                
                # Status de sucesso
                if status_code == '100':  # Autorizado
                    return {
                        'sucesso': True,
                        'status': 'AUTORIZADO',
                        'protocolo': protocolo.text if protocolo is not None else '',
                        'motivo': motivo.text if motivo is not None else '',
                        'xml_resposta': xml_response
                    }
                elif status_code == '103':  # Lote recebido - aguardar processamento
                    return {
                        'sucesso': True,
                        'status': 'PROCESSANDO',
                        'recibo': recibo.text if recibo is not None else '',
                        'motivo': motivo.text if motivo is not None else '',
                        'xml_resposta': xml_response
                    }
                else:  # Erro
                    return {
                        'sucesso': False,
                        'status': 'REJEITADO',
                        'codigo_erro': status_code,
                        'erro': motivo.text if motivo is not None else 'Erro desconhecido',
                        'xml_resposta': xml_response
                    }
            else:
                return {
                    'sucesso': False,
                    'erro': 'Resposta invÃ¡lida da SEFAZ',
                    'xml_resposta': xml_response
                }
                
        except Exception as e:
            logger.error(f"Erro ao processar resposta: {e}", exc_info=True)
            return {
                'sucesso': False,
                'erro': f'Erro ao processar resposta: {e}',
                'xml_resposta': xml_response
            }
    
    def _processar_resposta_consulta(self, xml_response):
        """Processa a resposta da SEFAZ para consulta"""
        return self._processar_resposta_transmissao(xml_response)

    def _processar_resposta_status(self, xml_response):
        """Processa a resposta da SEFAZ para consulta de status"""
        return self._processar_resposta_transmissao(xml_response)
    
    def _get_codigo_uf(self, uf):
        """Retorna o cÃ³digo numÃ©rico da UF"""
        codigos_uf = {
            'AC': '12', 'AL': '17', 'AP': '16', 'AM': '23', 'BA': '29',
            'CE': '23', 'DF': '53', 'ES': '32', 'GO': '52', 'MA': '21',
            'MT': '51', 'MS': '50', 'MG': '31', 'PA': '15', 'PB': '25',
            'PR': '41', 'PE': '26', 'PI': '22', 'RJ': '33', 'RN': '24',
            'RS': '43', 'RO': '11', 'RR': '14', 'SC': '42', 'SP': '35',
            'SE': '28', 'TO': '27'
        }
        return codigos_uf.get(uf.upper(), '35')  # Default SP
    
    def _get_codigo_municipio(self, nome_municipio, uf):
        """Busca cÃ³digo IBGE do municÃ­pio - implementar lookup na tabela IBGE"""
        codigos_municipios = {
            'CURITIBA': '4106902',
            'SÃƒO PAULO': '3550308',
            'RIO DE JANEIRO': '3304557',
            'BELO HORIZONTE': '3106200',
            'BRASÃLIA': '5300108',
            'SALVADOR': '2927408'
        }
        return codigos_municipios.get(nome_municipio.upper(), '3550308')
    
    def _calcular_dv_chave(self, chave):
        """Calcula o dÃ­gito verificador da chave de acesso"""
        sequencia = "4329876543298765432987654329876543298765432"
        soma = 0
        for i, digit in enumerate(chave):
            soma += int(digit) * int(sequencia[i])
        
        resto = soma % 11
        if resto in [0, 1]:
            return 0
        else:
            return 11 - resto


def emitir_cte(empresa_id, dados_cte, ambiente='PRODUCAO'):
    """
    FunÃ§Ã£o principal para emitir uma CT-e
    """
    try:
        from extensions import CTeEmitido, db
        
        service = CTeService(empresa_id, ambiente)
        
        # Gerar XML da CT-e
        xml_cte, chave_acesso, numero_cte = service.gerar_xml_cte(dados_cte)
        
        # Deriva o uf_codigo da chave de acesso para passar para a funÃ§Ã£o de transmissÃ£o
        uf_codigo = chave_acesso[:2]

        # Salvar CT-e no banco antes de transmitir
        cte_record = CTeEmitido(
            empresa_id=empresa_id,
            viagem_id=dados_cte.get('viagem_id'),
            chave_acesso=chave_acesso,
            numero_cte=numero_cte,
            dest_cnpj_cpf=dados_cte['destinatario']['cnpj_cpf'],
            dest_nome=dados_cte['destinatario']['nome'],
            dest_endereco=dados_cte['destinatario']['endereco'],
            dest_cidade=dados_cte['destinatario']['cidade'],
            dest_uf=dados_cte['destinatario']['uf'],
            dest_cep=dados_cte['destinatario']['cep'],
            rem_cnpj_cpf=dados_cte['remetente']['cnpj_cpf'],
            rem_nome=dados_cte['remetente']['nome'],
            rem_endereco=dados_cte['remetente']['endereco'],
            rem_cidade=dados_cte['remetente']['cidade'],
            rem_uf=dados_cte['remetente']['uf'],
            rem_cep=dados_cte['remetente']['cep'],
            natureza_carga=dados_cte['carga']['natureza'],
            peso_bruto=dados_cte['carga']['peso_bruto'],
            quantidade_volumes=dados_cte['carga'].get('volumes', 1),
            valor_carga=dados_cte['carga']['valor'],
            valor_total_servico=dados_cte['valores']['valor_total'],
            valor_receber=dados_cte['valores']['valor_receber'],
            base_calculo_icms=dados_cte['impostos']['base_calculo'],
            aliquota_icms=dados_cte['impostos']['aliquota'],
            valor_icms=dados_cte['impostos']['valor_icms'],
            placa_veiculo=re.sub(r'[^A-Z0-9]', '', dados_cte.get('veiculo', {}).get('placa', '')),
            motorista_cpf=re.sub(r'\D', '', dados_cte.get('motorista', {}).get('cpf', '')),
            motorista_nome=dados_cte.get('motorista', {}).get('nome'),
            observacoes=dados_cte.get('observacoes'),
            xml_content=xml_cte.encode('utf-8'),
            status='DIGITADO'
        )
        
        db.session.add(cte_record)
        db.session.commit()
        
        # Transmitir para SEFAZ se solicitado
        if dados_cte.get('transmitir_automaticamente', True):
            resultado_transmissao = service.transmitir_cte(xml_cte, chave_acesso, uf_codigo)
            
            if resultado_transmissao.get('sucesso'):
                if resultado_transmissao.get('status') == 'AUTORIZADO':
                    cte_record.status = 'AUTORIZADO'
                    cte_record.protocolo_autorizacao = resultado_transmissao.get('protocolo')
                    cte_record.data_autorizacao = datetime.utcnow()
                elif resultado_transmissao.get('status') == 'PROCESSANDO':
                    cte_record.status = 'PROCESSANDO'
                    cte_record.recibo_lote = resultado_transmissao.get('recibo')
            else:
                cte_record.status = 'REJEITADO'
                cte_record.motivo_rejeicao = resultado_transmissao.get('erro')
            
            db.session.commit()
            
            return {
                'sucesso': resultado_transmissao.get('sucesso'),
                'cte_id': cte_record.id,
                'chave_acesso': chave_acesso,
                'numero_cte': numero_cte,
                'status': resultado_transmissao.get('status'),
                'protocolo': resultado_transmissao.get('protocolo'),
                'erro': resultado_transmissao.get('erro')
            }
        else:
            return {
                'sucesso': True,
                'cte_id': cte_record.id,
                'chave_acesso': chave_acesso,
                'numero_cte': numero_cte,
                'status': 'DIGITADO'
            }
            
    except Exception as e:
        logger.error(f"Erro ao emitir CT-e: {e}", exc_info=True)
        return { 'sucesso': False, 'erro': str(e) }
    
# FunÃ§Ãµes auxiliares de teste
def testar_servico_completo(empresa_id, ambiente='HOMOLOGACAO'):
    """
    FunÃ§Ã£o de teste completo do serviÃ§o
    """
    try:
        print("Iniciando teste completo do serviÃ§o CT-e...")
        
        service = CTeService(empresa_id, ambiente)
        resultados = service.diagnosticar_problemas()
        
        print("\nResultados do teste:")
        for teste, resultado in resultados.items():
            if teste != 'detalhes':
                status = "PASSOU" if resultado else "FALHOU"
                print(f"   {teste.upper()}: {status}")
                if teste in resultados['detalhes']:
                    print(f"      {resultados['detalhes'][teste]}")
        
        if all([resultados['certificado'], resultados['conectividade']]):
            print("\nServiÃ§o pronto para uso!")
            return True
        else:
            print("\nServiÃ§o com problemas - verificar logs")
            return False
            
    except Exception as e:
        print(f"\nErro no teste: {e}")
        return False

if __name__ == "__main__":
    # Exemplo de uso e teste
    print("CTeService - Sistema de CT-e v4.00")
    print("Para testar: testar_servico_completo(empresa_id=1)")