# extensions.py (VERSÃO ATUALIZADA COM CAMPO cnpj_consultado E NSU INDIVIDUAL)

from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_socketio import SocketIO
from flask_login import LoginManager
from flask_mail import Mail
from sqlalchemy import LargeBinary, UniqueConstraint, CheckConstraint, distinct, func
from datetime import datetime


# Cria o objeto do banco de dados
db = SQLAlchemy()

# Define os modelos
class CertificadoDigital(db.Model):
    __tablename__ = 'certificado_digital'
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)
    nome_arquivo = db.Column(db.String(255), nullable=False)
    caminho_r2 = db.Column(db.String(500), nullable=False)
    senha_cifrada = db.Column(LargeBinary, nullable=False)
    data_validade = db.Column(db.Date, nullable=False)
    ultimo_nsu = db.Column(db.String(20), default='0')
    bloqueado_ate = db.Column(db.DateTime, nullable=True)
    ultima_consulta_sefaz = db.Column(db.DateTime, nullable=True)
    principal = db.Column(db.Boolean, default=False, nullable=False, index=True)
    
    __table_args__ = ()

    empresa = db.relationship('Empresa', backref=db.backref('certificados', lazy=True))

    def set_senha(self, senha, cipher_suite):
        self.senha_cifrada = cipher_suite.encrypt(senha.encode())

    def get_senha(self, cipher_suite):
        return cipher_suite.decrypt(self.senha_cifrada).decode()
    
    @classmethod
    def definir_como_principal(cls, certificado_id, empresa_id):
        """Método seguro para definir um certificado como principal"""
        # Remove principal de todos os certificados da empresa
        cls.query.filter_by(empresa_id=empresa_id).update({'principal': False})
        
        # Define o certificado especificado como principal
        certificado = cls.query.filter_by(id=certificado_id, empresa_id=empresa_id).first()
        if certificado:
            certificado.principal = True
            return True
        return False
    
# Adicione estes modelos ao seu arquivo extensions.py ou diretamente no app.py

class CTeEmitido(db.Model):
    """Modelo para armazenar CT-e emitidos"""
    __tablename__ = 'cte_emitido'
    
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)
    viagem_id = db.Column(db.Integer, db.ForeignKey('viagem.id'), nullable=True)
    
    # Dados básicos do CT-e
    chave_acesso = db.Column(db.String(44), unique=True, nullable=True, index=True)
    numero_cte = db.Column(db.String(9), nullable=False, index=True)
    serie = db.Column(db.String(3), default='1', nullable=False)
    data_emissao = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Status do documento
    status = db.Column(db.String(20), default='DIGITADO', nullable=False, index=True)
    # Status: DIGITADO, TRANSMITIDO, AUTORIZADO, REJEITADO, CANCELADO
    
    # Dados do destinatário
    dest_cnpj_cpf = db.Column(db.String(14), nullable=False)
    dest_nome = db.Column(db.String(200), nullable=False)
    dest_endereco = db.Column(db.String(255), nullable=False)
    dest_cidade = db.Column(db.String(100), nullable=False)
    dest_uf = db.Column(db.String(2), nullable=False)
    dest_cep = db.Column(db.String(8), nullable=False)
    
    # Dados do remetente
    rem_cnpj_cpf = db.Column(db.String(14), nullable=False)
    rem_nome = db.Column(db.String(200), nullable=False)
    rem_endereco = db.Column(db.String(255), nullable=False)
    rem_cidade = db.Column(db.String(100), nullable=False)
    rem_uf = db.Column(db.String(2), nullable=False)
    rem_cep = db.Column(db.String(8), nullable=False)

    
    # Dados da carga
    natureza_carga = db.Column(db.String(100), nullable=False)
    peso_bruto = db.Column(db.Float, nullable=False, default=0.0)
    peso_cubado = db.Column(db.Float, nullable=True)
    quantidade_volumes = db.Column(db.Integer, default=1)
    valor_carga = db.Column(db.Float, nullable=False, default=0.0)
    
    # Dados do transporte
    modal = db.Column(db.String(2), default='01', nullable=False)  # 01=Rodoviário
    tipo_servico = db.Column(db.String(1), default='0', nullable=False)  # 0=Normal
    
    # Valores do serviço
    valor_total_servico = db.Column(db.Float, nullable=False, default=0.0)
    valor_receber = db.Column(db.Float, nullable=False, default=0.0)
    
    # Impostos
    base_calculo_icms = db.Column(db.Float, default=0.0)
    aliquota_icms = db.Column(db.Float, default=0.0)
    valor_icms = db.Column(db.Float, default=0.0)
    
    # Dados do veículo
    placa_veiculo = db.Column(db.String(15), nullable=True)
    renavam_veiculo = db.Column(db.String(11), nullable=True)
    
    # Dados do motorista
    motorista_cpf = db.Column(db.String(11), nullable=True)
    motorista_nome = db.Column(db.String(100), nullable=True)
    
    # XML e protocolo
    xml_content = db.Column(db.LargeBinary, nullable=True)
    protocolo_autorizacao = db.Column(db.String(20), nullable=True)
    data_autorizacao = db.Column(db.DateTime, nullable=True)
    
    # Observações
    observacoes = db.Column(db.Text, nullable=True)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relacionamentos
    empresa = db.relationship('Empresa', backref='ctes_emitidos')
    viagem = db.relationship('Viagem', backref='ctes')
    
    def __repr__(self):
        return f'<CTeEmitido {self.numero_cte}/{self.serie}>'
    
    @property
    def numero_formatado(self):
        """Retorna o número formatado da CT-e"""
        return f"{self.numero_cte.zfill(9)}"
    
    @property
    def chave_formatada(self):
        """Retorna a chave de acesso formatada"""
        if self.chave_acesso:
            chave = self.chave_acesso
            return f"{chave[:4]} {chave[4:8]} {chave[8:12]} {chave[12:16]} {chave[16:20]} {chave[20:24]} {chave[24:28]} {chave[28:32]} {chave[32:36]} {chave[36:40]} {chave[40:]}"
        return ""


class CTeItem(db.Model):
    """Modelo para itens/produtos transportados no CT-e"""
    __tablename__ = 'cte_item'
    
    id = db.Column(db.Integer, primary_key=True)
    cte_id = db.Column(db.Integer, db.ForeignKey('cte_emitido.id'), nullable=False)
    
    descricao = db.Column(db.String(255), nullable=False)
    ncm = db.Column(db.String(8), nullable=True)
    quantidade = db.Column(db.Float, default=1.0)
    unidade = db.Column(db.String(10), default='UN')
    peso_item = db.Column(db.Float, nullable=True)
    valor_item = db.Column(db.Float, nullable=True)
    
    cte = db.relationship('CTeEmitido', backref=db.backref('itens', lazy=True, cascade="all, delete-orphan"))
    
    def __repr__(self):
        return f'<CTeItem {self.descricao}>'


class CTeParametros(db.Model):
    """Modelo para armazenar parâmetros de configuração do CT-e por empresa"""
    __tablename__ = 'cte_parametros'
    
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False, unique=True)
    
    # Numeração
    proximo_numero = db.Column(db.Integer, default=1)
    serie_padrao = db.Column(db.String(3), default='1')
    
    # Configurações fiscais
    aliquota_icms_padrao = db.Column(db.Float, default=17.0)
    natureza_operacao = db.Column(db.String(100), default='PRESTAÇÃO DE SERVIÇO DE TRANSPORTE')
    codigo_cfop = db.Column(db.String(4), default='5353')  # Prestação de serviço de transporte
    
    # Dados padrão
    tipo_documento = db.Column(db.String(1), default='0')  # 0=CT-e Normal, 1=CT-e Complementar
    tipo_servico = db.Column(db.String(1), default='0')    # 0=Normal, 1=Subcontratação
    
    # Relacionamentos
    empresa = db.relationship('Empresa', backref=db.backref('cte_parametros', uselist=False))
    
    def __repr__(self):
        return f'<CTeParametros Empresa {self.empresa_id}>'
    
    def proximo_numero_cte(self):
        """Retorna e incrementa o próximo número de CT-e"""
        numero_atual = self.proximo_numero
        self.proximo_numero += 1
        return numero_atual


class CertificadoNSU(db.Model):
    """
    Controle individual de NSU por CNPJ consultado
    Resolve problema de bloqueio quando certificado consulta múltiplos CNPJs
    """
    __tablename__ = 'certificado_nsu'
    
    id = db.Column(db.Integer, primary_key=True)
    certificado_id = db.Column(db.Integer, db.ForeignKey('certificado_digital.id'), nullable=False)
    cnpj_consultado = db.Column(db.String(14), nullable=False)
    ultimo_nsu = db.Column(db.String(20), default='0', nullable=False)
    ultima_atualizacao = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Índice único para evitar duplicatas
    __table_args__ = (
        db.UniqueConstraint('certificado_id', 'cnpj_consultado', name='uq_cert_cnpj'),
        db.Index('idx_cert_nsu_cnpj', 'certificado_id', 'cnpj_consultado'),
    )
    
    certificado = db.relationship('CertificadoDigital', backref=db.backref('nsus_cnpj', cascade="all, delete-orphan"))


class NFeImportada(db.Model):
    __tablename__ = 'nfe_importada'
    chave_acesso = db.Column(db.String(44), primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)
    nsu = db.Column(db.String(20), nullable=False, index=True)
    emitente_cnpj = db.Column(db.String(14), nullable=False)
    emitente_nome = db.Column(db.String(255), nullable=False)
    data_emissao = db.Column(db.DateTime, nullable=False)
    valor_total = db.Column(db.Float, nullable=False)
    xml_content = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default='BAIXADA', nullable=False, index=True)
    data_download = db.Column(db.DateTime, default=datetime.utcnow)
    certificado_id = db.Column(db.Integer, db.ForeignKey('certificado_digital.id'), nullable=True, index=True)
    
    # NOVO CAMPO: CNPJ que foi consultado na SEFAZ para obter esta NFe
    cnpj_consultado = db.Column(db.String(14), nullable=True, index=True)
    
    certificado = db.relationship('CertificadoDigital', backref='notas_importadas')

    def __repr__(self):
        return f'<NFeImportada {self.chave_acesso} - {self.emitente_nome}>'
    
    def to_dict(self):
        """Converte para dicionário incluindo o novo campo"""
        return {
            'chave_acesso': self.chave_acesso,
            'empresa_id': self.empresa_id,
            'certificado_id': self.certificado_id,
            'nsu': self.nsu,
            'emitente_cnpj': self.emitente_cnpj,
            'emitente_nome': self.emitente_nome,
            'cnpj_consultado': self.cnpj_consultado,  # NOVO CAMPO
            'data_emissao': self.data_emissao.isoformat() if self.data_emissao else None,
            'valor_total': float(self.valor_total) if self.valor_total else 0.0,
            'status': self.status,
            'data_download': self.data_download.isoformat() if self.data_download else None
        }
    
    @classmethod
    def buscar_por_empresa_consultada(cls, empresa_id, cnpj_consultado=None):
        """
        NOVO MÉTODO: Busca NFes por empresa consultada
        """
        query = cls.query.filter_by(empresa_id=empresa_id)
        
        if cnpj_consultado:
            query = query.filter_by(cnpj_consultado=cnpj_consultado)
        
        return query
    
    @classmethod
    def get_estatisticas_por_empresa_consultada(cls, empresa_id):
        """
        NOVO MÉTODO: Retorna estatísticas agrupadas por CNPJ consultado
        """
        resultado = db.session.query(
            cls.cnpj_consultado,
            func.count(cls.chave_acesso).label('total_notas'),
            func.count(func.case([(cls.status == 'PROCESSADA', 1)])).label('notas_processadas'),
            func.sum(cls.valor_total).label('valor_total'),
            func.min(cls.data_emissao).label('primeira_emissao'),
            func.max(cls.data_emissao).label('ultima_emissao')
        ).filter(
            cls.empresa_id == empresa_id,
            cls.cnpj_consultado.isnot(None)
        ).group_by(
            cls.cnpj_consultado
        ).order_by(
            func.count(cls.chave_acesso).desc()
        ).all()
        
        return resultado
    
    @classmethod
    def get_cnpjs_consultados_distintos(cls, empresa_id):
        """
        NOVO MÉTODO: Retorna lista de CNPJs consultados únicos para uma empresa
        """
        resultado = db.session.query(distinct(cls.cnpj_consultado)).filter(
            cls.empresa_id == empresa_id,
            cls.cnpj_consultado.isnot(None)
        ).order_by(cls.cnpj_consultado).all()
        
        return [cnpj[0] for cnpj in resultado if cnpj[0]]


# Cria os outros objetos de extensão
migrate = Migrate()
socketio = SocketIO()
login_manager = LoginManager()
mail = Mail()