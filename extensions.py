# extensions.py (VERSÃO FINAL)

from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_socketio import SocketIO
from flask_login import LoginManager
from flask_mail import Mail
from sqlalchemy import LargeBinary
from datetime import datetime

# Cria o objeto do banco de dados
db = SQLAlchemy()

# Define os modelos que causam o loop aqui
class CertificadoDigital(db.Model):
    __tablename__ = 'certificado_digital'
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False, unique=True)
    nome_arquivo = db.Column(db.String(255), nullable=False)
    caminho_r2 = db.Column(db.String(500), nullable=False)
    senha_cifrada = db.Column(LargeBinary, nullable=False)
    data_validade = db.Column(db.Date, nullable=False)
    ultimo_nsu = db.Column(db.String(20), default='0')

    empresa = db.relationship('Empresa', backref=db.backref('certificado_digital', uselist=False))

    def set_senha(self, senha, cipher_suite):
        self.senha_cifrada = cipher_suite.encrypt(senha.encode())

    def get_senha(self, cipher_suite):
        return cipher_suite.decrypt(self.senha_cifrada).decode()

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

# Cria os outros objetos de extensão
migrate = Migrate()
socketio = SocketIO()
login_manager = LoginManager()
mail = Mail()