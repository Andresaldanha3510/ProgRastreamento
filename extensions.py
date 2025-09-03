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
    
    certificado = db.relationship('CertificadoDigital', backref='nsus_cnpj')


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