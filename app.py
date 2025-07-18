import eventlet
eventlet.monkey_patch()
import uuid

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file, make_response, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from datetime import datetime, timedelta, date
import requests
import logging
import os
import math
import re
from dotenv import load_dotenv
import boto3
from werkzeug.utils import secure_filename
import io
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment
from sqlalchemy.exc import IntegrityError
from sqlalchemy import or_
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_mail import Mail, Message
from flask import jsonify
from flask import make_response
from sqlalchemy import UniqueConstraint
from num2words import num2words
from collections import defaultdict
from flask_socketio import SocketIO, emit, join_room, leave_room
import pytesseract
from PIL import Image
import openrouteservice
from sqlalchemy import extract




# ---- Configurações Iniciais ----
load_dotenv()

import os
print("ENDPOINT:", os.getenv('CLOUDFLARE_R2_ENDPOINT'))
print("ACCESS_KEY:", os.getenv('CLOUDFLARE_R2_ACCESS_KEY'))
print("SECRET_KEY:", os.getenv('CLOUDFLARE_R2_SECRET_KEY'))
print("BUCKET:", os.getenv('CLOUDFLARE_R2_BUCKET'))
print("PUBLIC_URL:", os.getenv('CLOUDFLARE_R2_PUBLIC_URL'))

app = Flask(__name__)
                                                                                                                

app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'trackgo789@gmail.com'
app.config['MAIL_PASSWORD'] = 'mmoa moxc juli sfbe'
app.config['MAIL_DEFAULT_SENDER'] = 'trackgo789@gmail.com'

mail = Mail(app)

app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///transport.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'w9z$kL2mNpQvR7tYxJ3hF8gWcPqB5vM2nZ4rT6yU')
Maps_API_KEY = os.getenv('Maps_API_KEY', 'AIzaSyBPdSOZF2maHURmdRmVzLgVo5YO2wliylo')
GEOAPIFY_API_KEY = os.getenv('GEOAPIFY_API_KEY', '7cd423ef184f48f0b770682cbebe11d0') # Usar os.getenv para Geoapify também
OPENROUTESERVICE_API_KEY = 'eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6ImE1NjM5YzEwODU3ZDRjYzI5OWU2ZmQ4YzVhYTk5OTQ4IiwiaCI6Im11cm11cjY0In0='
app.config['CLOUDFLARE_R2_ENDPOINT'] = os.getenv('CLOUDFLARE_R2_ENDPOINT')
app.config['CLOUDFLARE_R2_ACCESS_KEY'] = os.getenv('CLOUDFLARE_R2_ACCESS_KEY')
app.config['CLOUDFLARE_R2_SECRET_KEY'] = os.getenv('CLOUDFLARE_R2_SECRET_KEY')
app.config['CLOUDFLARE_R2_BUCKET'] = os.getenv('CLOUDFLARE_R2_BUCKET')
app.config['CLOUDFLARE_R2_PUBLIC_URL'] = os.getenv('CLOUDFLARE_R2_PUBLIC_URL')

last_geocode_time = {}
GEOCODE_INTERVAL_SECONDS = 600 # 10 minutos


logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

db = SQLAlchemy(app)
migrate = Migrate(app, db)
socketio = SocketIO(app, async_mode='eventlet')

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    """Carrega o usuário pelo ID."""
    # Alterado para Session.get() como recomendado pelo SQLAlchemy 2.0
    return db.session.get(Usuario, int(user_id))


class Motorista(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    data_nascimento = db.Column(db.Date, nullable=False)
    endereco = db.Column(db.String(200), nullable=False)
    pessoa_tipo = db.Column(db.String(10), nullable=False)
    cpf_cnpj = db.Column(db.String(14), unique=True, nullable=False, index=True)
    rg = db.Column(db.String(9), nullable=True)
    telefone = db.Column(db.String(11), nullable=False)
    cnh = db.Column(db.String(11), unique=True, nullable=False, index=True)
    validade_cnh = db.Column(db.Date, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    anexos = db.Column(db.String(500), nullable=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=True)
    
    # --- ADICIONADO ---
    # Garante que cada motorista pertence a uma empresa.
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)
    
    usuario = db.relationship('Usuario', backref='motorista', uselist=False)
    viagens = db.relationship('Viagem', backref='motorista_formal')

    def to_dict(self):
        """Converte o objeto Motorista para um dicionário."""
        return {
            'id': self.id,
            'nome': self.nome,
            'data_nascimento': self.data_nascimento.isoformat() if self.data_nascimento else None,
            'endereco': self.endereco,
            'cpf_cnpj': self.cpf_cnpj,
            'telefone': self.telefone,
            'cnh': self.cnh,
            'validade_cnh': self.validade_cnh.isoformat() if self.validade_cnh else None,
            'anexos': self.anexos.split(',') if self.anexos else []
        }
    
class Manutencao(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    veiculo_id = db.Column(db.Integer, db.ForeignKey('veiculo.id'), nullable=False)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)
    data = db.Column(db.Date, nullable=False, default=date.today)
    odometro = db.Column(db.Integer, nullable=False)
    tipo = db.Column(db.String(100), nullable=False) # Ex: Preventiva, Corretiva, Troca de Óleo, Pneus
    descricao = db.Column(db.Text, nullable=False)
    custo = db.Column(db.Float, nullable=False)
    anexos = db.Column(db.String(1024)) # Armazenará URLs dos comprovantes, separadas por vírgula

    veiculo = db.relationship('Veiculo', backref=db.backref('manutencoes', lazy=True, cascade="all, delete-orphan"))

    def to_dict(self):
        return {
            "id": self.id,
            "data": self.data.strftime('%d/%m/%Y'),
            "odometro": self.odometro,
            "tipo": self.tipo,
            "descricao": self.descricao,
            "custo": self.custo,
            "anexos": self.anexos.split(',') if self.anexos else []
        }

class Licenca(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False, unique=True)
    plano = db.Column(db.String(50), nullable=False, default='Básico')
    max_usuarios = db.Column(db.Integer, nullable=False, default=5)
    max_veiculos = db.Column(db.Integer, nullable=False, default=5)
    data_expiracao = db.Column(db.Date, nullable=True)
    ativo = db.Column(db.Boolean, default=True, nullable=False)

    def __repr__(self):
        return f'<Licenca {self.id} - Plano {self.plano} para Empresa {self.empresa_id}>'

class Convite(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), nullable=False, index=True)
    token = db.Column(db.String(36), unique=True, nullable=False, index=True)
    usado = db.Column(db.Boolean, default=False)
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)
    data_expiracao = db.Column(db.DateTime, nullable=False)
    criado_por = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='Motorista')
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=True)

class Empresa(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    razao_social = db.Column(db.String(200), nullable=False)
    nome_fantasia = db.Column(db.String(200), nullable=True)
    cnpj = db.Column(db.String(14), unique=True, nullable=False, index=True)
    inscricao_estadual = db.Column(db.String(20), nullable=True)
    endereco = db.Column(db.String(255), nullable=False)
    cidade = db.Column(db.String(100), nullable=False)
    estado = db.Column(db.String(2), nullable=False)
    cep = db.Column(db.String(8), nullable=False)
    telefone = db.Column(db.String(11), nullable=True)
    email_contato = db.Column(db.String(120), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    licenca = db.relationship('Licenca', backref='empresa', uselist=False, cascade="all, delete-orphan")
    usuarios = db.relationship('Usuario', backref='empresa', lazy=True)

    def __repr__(self):
        return f'<Empresa {self.razao_social}>'

class Cobranca(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), nullable=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    valor_total = db.Column(db.Float, nullable=False)
    data_emissao = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    data_vencimento = db.Column(db.Date, nullable=False)
    data_pagamento = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), nullable=False, default='Pendente', index=True)
    meio_pagamento = db.Column(db.String(50), nullable=True)
    observacoes = db.Column(db.Text, nullable=True)
    
    # --- ADICIONADO ---
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)
    
    cliente = db.relationship('Cliente', backref='cobrancas')
    usuario = db.relationship('Usuario', backref='cobrancas_geradas')
    viagens = db.relationship('Viagem', backref='cobranca', lazy='dynamic')

    @property
    def is_vencida(self):
        return self.data_vencimento < datetime.utcnow().date() and self.status == 'Pendente'

    def __repr__(self):
        return f'<Cobranca {self.id} - Cliente {self.cliente.nome_razao_social}>'

class Romaneio(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    data_emissao = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    observacoes = db.Column(db.Text, nullable=True)
    viagem_id = db.Column(db.Integer, db.ForeignKey('viagem.id'), nullable=False, unique=True)
    
    # --- ADICIONADO ---
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)
    
    viagem = db.relationship('Viagem', backref=db.backref('romaneio', uselist=False))
    itens = db.relationship('ItemRomaneio', backref='romaneio', lazy=True, cascade="all, delete-orphan")

    @property
    def total_volumes(self):
        return len(self.itens) if self.itens else 0

    @property
    def peso_total(self):
        return sum(item.peso_total_item for item in self.itens) if self.itens else 0.0

class ItemRomaneio(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    produto_descricao = db.Column(db.String(255), nullable=False)
    quantidade = db.Column(db.Integer, nullable=False, default=1)
    embalagem = db.Column(db.String(50), nullable=True)
    peso_kg = db.Column(db.Float, nullable=True, default=0.0)
    romaneio_id = db.Column(db.Integer, db.ForeignKey('romaneio.id'), nullable=False)

    @property
    def peso_total_item(self):
        return (self.peso_kg or 0.0) * (self.quantidade or 0)

class Veiculo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    placa = db.Column(db.String(7), unique=True, nullable=False, index=True)
    categoria = db.Column(db.String(50), nullable=True)
    modelo = db.Column(db.String(50), nullable=False)
    ano = db.Column(db.Integer, nullable=True)
    valor = db.Column(db.Float, nullable=True)
    km_rodados = db.Column(db.Float, nullable=True)
    ultima_manutencao = db.Column(db.Date, nullable=True)
    disponivel = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # --- ADICIONADO ---
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)

    viagens = db.relationship('Viagem', backref='veiculo')

    def to_dict(self):
        return {
            'id': self.id,
            'placa': self.placa,
            'categoria': self.categoria,
            'modelo': self.modelo,
            'ano': self.ano,
            'valor': self.valor,
            'km_rodados': self.km_rodados,
            'ultima_manutencao': self.ultima_manutencao.isoformat() if self.ultima_manutencao else None,
            'disponivel': self.disponivel
        }

from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin

class Usuario(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    sobrenome = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    senha_hash = db.Column(db.String(256), nullable=False)
    telefone = db.Column(db.String(11), nullable=True)
    idioma = db.Column(db.String(20), default='Português')
    two_factor_enabled = db.Column(db.Boolean, default=False)
    two_factor_phone = db.Column(db.String(11), nullable=True)
    is_admin = db.Column(db.Boolean, default=False)
    role = db.Column(db.String(20), nullable=False, default='Motorista')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    cpf_cnpj = db.Column(db.String(14), unique=True, nullable=True, index=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=True)

    def set_password(self, password):
        self.senha_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.senha_hash, password)

class Destino(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    viagem_id = db.Column(db.Integer, db.ForeignKey('viagem.id'), nullable=False)
    endereco = db.Column(db.String(200), nullable=False)
    ordem = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Abastecimento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    veiculo_id = db.Column(db.Integer, db.ForeignKey('veiculo.id'), nullable=False)
    motorista_id = db.Column(db.Integer, db.ForeignKey('motorista.id'), nullable=False)
    data_abastecimento = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    litros = db.Column(db.Float, nullable=False)
    preco_por_litro = db.Column(db.Float, nullable=False)
    custo_total = db.Column(db.Float, nullable=False)
    odometro = db.Column(db.Float, nullable=False)
    anexo_comprovante = db.Column(db.String(500), nullable=True)
    viagem_id = db.Column(db.Integer, db.ForeignKey('viagem.id'), nullable=True, index=True) # Alterado para nullable=True

    veiculo = db.relationship('Veiculo', backref='abastecimentos')
    motorista = db.relationship('Motorista', backref='abastecimentos_registrados')

    # MÉTODO ADICIONADO AQUI:
    def to_dict(self):
        """Converte o objeto para um dicionário."""
        return {
            'id': self.id,
            'data_abastecimento': self.data_abastecimento.strftime('%d/%m/%Y'),
            'litros': self.litros,
            'preco_por_litro': self.preco_por_litro,
            'custo_total': self.custo_total,
            'odometro': self.odometro
        }

class CustoViagem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    viagem_id = db.Column(db.Integer, db.ForeignKey('viagem.id'), nullable=False, unique=True)
    combustivel = db.Column(db.Float, nullable=True)
    pedagios = db.Column(db.Float, nullable=True)
    alimentacao = db.Column(db.Float, nullable=True)
    hospedagem = db.Column(db.Float, nullable=True)
    outros = db.Column(db.Float, nullable=True)
    descricao_outros = db.Column(db.String(300), nullable=True)
    anexos = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    viagem = db.relationship('Viagem', backref=db.backref('custo_viagem', uselist=False))
    

class Cliente(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    pessoa_tipo = db.Column(db.String(10), nullable=False)
    nome_razao_social = db.Column(db.String(200), nullable=False)
    nome_fantasia = db.Column(db.String(200), nullable=True)
    cpf_cnpj = db.Column(db.String(14), unique=True, nullable=False, index=True)
    inscricao_estadual = db.Column(db.String(20), nullable=True)
    cep = db.Column(db.String(8), nullable=False)
    logradouro = db.Column(db.String(255), nullable=False)
    numero = db.Column(db.String(20), nullable=False)
    complemento = db.Column(db.String(100), nullable=True)
    bairro = db.Column(db.String(100), nullable=False)
    cidade = db.Column(db.String(100), nullable=False)
    estado = db.Column(db.String(2), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    telefone = db.Column(db.String(11), nullable=False)
    contato_principal = db.Column(db.String(100), nullable=True)
    anexos = db.Column(db.String(1000), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    cadastrado_por_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    
    # --- ADICIONADO ---
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)

    cadastrado_por = db.relationship('Usuario', backref='clientes_cadastrados')

    def __repr__(self):
        return f'<Cliente {self.id}: {self.nome_razao_social}>'



class Viagem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    motorista_id = db.Column(db.Integer, db.ForeignKey('motorista.id'), nullable=True)
    motorista_cpf_cnpj = db.Column(db.String(14), nullable=True, index=True)
    veiculo_id = db.Column(db.Integer, db.ForeignKey('veiculo.id'), nullable=False)
    cliente = db.Column(db.String(100), nullable=False)
    valor_recebido = db.Column(db.Float, nullable=True)
    forma_pagamento = db.Column(db.String(50), nullable=True)
    endereco_saida = db.Column(db.String(200), nullable=False)
    endereco_destino = db.Column(db.String(200), nullable=False)
    distancia_km = db.Column(db.Float, nullable=True)
    data_inicio = db.Column(db.DateTime, nullable=False)
    data_fim = db.Column(db.DateTime, nullable=True)
    duracao_segundos = db.Column(db.Integer, nullable=True)
    custo = db.Column(db.Float, nullable=True)
    status = db.Column(db.String(50), nullable=False, default='pendente', index=True)
    observacoes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)
    cobranca_id = db.Column(db.Integer, db.ForeignKey('cobranca.id'), nullable=True)
    public_tracking_token = db.Column(db.String(36), default=lambda: str(uuid.uuid4()), nullable=False)
    odometro_inicial = db.Column(db.Float, nullable=True)
    odometro_final = db.Column(db.Float, nullable=True)
    route_geometry = db.Column(db.Text, nullable=True)
    
    # --- LINHA CORRIGIDA ABAIXO ---
    destinos = db.relationship('Destino', backref='viagem', cascade='all, delete-orphan')
    
    abastecimentos = db.relationship('Abastecimento', backref='viagem', lazy=True)
    localizacoes = db.relationship('Localizacao', backref='viagem', lazy=True, cascade="all, delete-orphan")

    @property
    def distancia_percorrida(self):
        if self.odometro_inicial is not None and self.odometro_final is not None:
            if self.odometro_final >= self.odometro_inicial:
                return self.odometro_final - self.odometro_inicial
        return 0.0

    @property
    def consumo_medio(self):
        distancia = self.distancia_percorrida
        if not distancia > 0 or not self.abastecimentos:
            return 0.0
        total_litros = sum(abast.litros for abast in self.abastecimentos if abast.litros is not None)
        if total_litros > 0:
            return distancia / total_litros
        return 0.0

class Localizacao(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    motorista_id = db.Column(db.Integer, db.ForeignKey('motorista.id'), nullable=False)
    viagem_id = db.Column(db.Integer, db.ForeignKey('viagem.id'), nullable=True)
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    endereco = db.Column(db.String(200), nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

from functools import wraps

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash('Acesso restrito ao administrador.', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def owner_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'Owner':
            flash('Acesso restrito ao proprietário do sistema.', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def master_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or (current_user.role not in ['Admin', 'Master']):
            flash('Acesso restrito a administradores ou masters.', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


def validate_cpf_cnpj(cpf_cnpj, pessoa_tipo):
    if pessoa_tipo == 'fisica':
        return bool(re.match(r'^\d{11}$', cpf_cnpj))
    return bool(re.match(r'^\d{14}$', cpf_cnpj))

def validate_telefone(telefone):
    return bool(re.match(r'^\d{10,11}$', telefone))

def validate_cnh(cnh):
    return bool(re.match(r'^\d{11}$', cnh))

def validate_placa(placa):
    return bool(re.match(r'^[A-Z0-9]{7}$', placa.upper()))

def haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

@app.route('/cadastrar_cliente', methods=['GET', 'POST'])
@login_required
def cadastrar_cliente():
    if request.method == 'POST':
        try:
            cpf_cnpj = re.sub(r'\D', '', request.form.get('cpf_cnpj', ''))
            # CORREÇÃO: Impede cadastro de CPF/CNPJ duplicado na mesma empresa.
            if Cliente.query.filter_by(cpf_cnpj=cpf_cnpj, empresa_id=current_user.empresa_id).first():
                flash('Erro: Este CPF/CNPJ já está cadastrado para um cliente em sua empresa.', 'error')
                return redirect(url_for('cadastrar_cliente'))

            novo_cliente = Cliente(
                pessoa_tipo=request.form.get('pessoa_tipo'),
                nome_razao_social=request.form.get('nome_razao_social'),
                cpf_cnpj=cpf_cnpj,
                email=request.form.get('email'),
                telefone=re.sub(r'\D', '', request.form.get('telefone', '')),
                cep=re.sub(r'\D', '', request.form.get('cep', '')),
                logradouro=request.form.get('logradouro'),
                numero=request.form.get('numero'),
                bairro=request.form.get('bairro'),
                cidade=request.form.get('cidade'),
                estado=request.form.get('estado'),
                cadastrado_por_id=current_user.id,
                empresa_id=current_user.empresa_id # ESSENCIAL
            )
            db.session.add(novo_cliente)
            db.session.commit()
            flash('Cliente cadastrado com sucesso!', 'success')
            return redirect(url_for('consultar_clientes'))
        except Exception as e:
            db.session.rollback()
            flash(f'Ocorreu um erro ao cadastrar o cliente: {e}', 'error')
    return render_template('cadastrar_cliente.html', active_page='cadastrar_cliente')

@app.route('/consultar_clientes')
@login_required
def consultar_clientes():
    search_query = request.args.get('search', '').strip()
    query = Cliente.query.filter_by(empresa_id=current_user.empresa_id)
    if search_query:
        search_filter = f"%{search_query}%"
        query = query.filter(
            or_(
                Cliente.nome_razao_social.ilike(search_filter),
                Cliente.cpf_cnpj.ilike(search_filter)
            )
        )
    clientes = query.order_by(Cliente.nome_razao_social.asc()).all()
    return render_template('consultar_clientes.html', clientes=clientes, search_query=search_query, active_page='consultar_clientes')



@app.route('/track/<uuid:token>')
def public_tracking_page(token):
    """Exibe a página pública de acompanhamento para o cliente."""
    # Converte o token para string para buscar no banco
    token_str = str(token)
    
    # Busca a viagem usando o token seguro
    viagem = Viagem.query.filter_by(public_tracking_token=token_str).first_or_404()
    
    # Prepara os dados do motorista para exibição
    motorista_nome = 'Não informado'
    if viagem.motorista_formal:
        motorista_nome = viagem.motorista_formal.nome
    elif viagem.motorista_cpf_cnpj:
        usuario = Usuario.query.filter_by(cpf_cnpj=viagem.motorista_cpf_cnpj).first()
        if usuario:
            motorista_nome = f"{usuario.nome} {usuario.sobrenome}"

    current_year = datetime.utcnow().year

    return render_template('public_tracking.html', 
                           viagem=viagem, 
                           motorista_nome=motorista_nome,
                           Maps_API_KEY=Maps_API_KEY)

from sqlalchemy import or_ 
@app.route('/api/clientes/search')
@login_required
def search_clientes_simplificado():
    search_term = request.args.get('term', '')
    if not search_term or len(search_term) < 2:
        return jsonify([])

    # Agora a busca só acontece nos clientes da empresa do usuário logado
    clientes = Cliente.query.filter(
        Cliente.empresa_id == current_user.empresa_id,  # <-- FILTRO DE SEGURANÇA ADICIONADO AQUI
        or_(
            Cliente.nome_razao_social.ilike(f'%{search_term}%'),
            Cliente.nome_fantasia.ilike(f'%{search_term}%')
        )
    ).limit(10).all()
    
    resultados = set()
    for cliente in clientes:
        resultados.add(cliente.nome_razao_social)
        if cliente.nome_fantasia:
            resultados.add(cliente.nome_fantasia)
    
    return jsonify(list(resultados))

@app.route('/viagem/<int:viagem_id>/iniciar', methods=['POST'])
@login_required
def iniciar_viagem_motorista(viagem_id):
    if current_user.role != 'Motorista':
        return jsonify({'success': False, 'message': 'Acesso negado.'}), 403

    viagem = Viagem.query.filter_by(id=viagem_id, empresa_id=current_user.empresa_id).first_or_404()
    if viagem.status != 'pendente':
        return jsonify({'success': False, 'message': 'Esta viagem não está mais pendente.'}), 400

    data = request.get_json()
    # --- CORREÇÃO AQUI ---
    odometro_str = data.get('odometer') # Alterado de 'odometro' para 'odometer'

    try:
        odometro_inicial = float(odometro_str)
        if odometro_inicial < 0:
            raise ValueError("Odômetro não pode ser negativo.")
    except (ValueError, TypeError):
        return jsonify({'success': False, 'message': 'Odômetro inicial inválido. Por favor, insira um número válido.'}), 400

    viagem.status = 'em_andamento'
    viagem.data_inicio = datetime.utcnow()
    viagem.odometro_inicial = odometro_inicial
    
    motorista_formal = Motorista.query.filter_by(cpf_cnpj=current_user.cpf_cnpj).first()
    if motorista_formal:
        viagem.motorista_id = motorista_formal.id
    viagem.motorista_cpf_cnpj = current_user.cpf_cnpj
    
    db.session.commit()
    
    # Emite evento para atualizar telas de admin em tempo real
    socketio.emit('status_viagem_atualizado', {
        'viagem_id': viagem.id, 
        'status': 'em_andamento'
    }, room='admins')

    return jsonify({'success': True, 'message': 'Viagem iniciada com sucesso!'})

@app.route('/viagem/<int:viagem_id>/despesas_form')
@login_required
def despesas_form_modal(viagem_id):
    """Renderiza o formulário de despesas para ser carregado no modal."""
    Viagem.query.filter_by(id=viagem_id, empresa_id=current_user.empresa_id).first_or_404()
    custo = CustoViagem.query.filter_by(viagem_id=viagem_id).first()
    # Renderiza o NOVO template que criamos
    return render_template('despesas_form_modal.html', viagem=viagem, custo=custo)

@app.route('/registrar_abastecimento', methods=['POST'])
@login_required
def registrar_abastecimento():
    try:
        motorista_formal = Motorista.query.filter_by(cpf_cnpj=current_user.cpf_cnpj).first()
        if not motorista_formal:
             return jsonify({'success': False, 'message': 'Perfil de motorista formal não encontrado para o usuário atual.'}), 400

        viagem_ativa = Viagem.query.filter(
            or_(
                Viagem.motorista_cpf_cnpj == current_user.cpf_cnpj,
                Viagem.motorista_id == motorista_formal.id
            ),
            Viagem.status == 'em_andamento'
        ).first()

        if not viagem_ativa:
            return jsonify({'success': False, 'message': 'Nenhuma viagem ativa encontrada para associar o abastecimento.'}), 400

        litros = float(request.form.get('litros'))
        preco_por_litro = float(request.form.get('preco_por_litro'))
        odometro = float(request.form.get('odometro'))
        custo_total = litros * preco_por_litro

        novo_abastecimento = Abastecimento(
            veiculo_id=viagem_ativa.veiculo_id,
            motorista_id=motorista_formal.id,
            viagem_id=viagem_ativa.id,
            litros=litros,
            preco_por_litro=preco_por_litro,
            custo_total=custo_total,
            odometro=odometro
        )

        anexo_url = None
        anexo = request.files.get('anexo_comprovante')
        
        # Verifica se o arquivo 'anexo_comprovante' foi realmente enviado
        if anexo and anexo.filename:
            s3_client = boto3.client(
                's3',
                endpoint_url=app.config['CLOUDFLARE_R2_ENDPOINT'],
                aws_access_key_id=app.config['CLOUDFLARE_R2_ACCESS_KEY'],
                aws_secret_access_key=app.config['CLOUDFLARE_R2_SECRET_KEY'],
                region_name='auto'
            )
            bucket_name = app.config['CLOUDFLARE_R2_BUCKET']
            filename = secure_filename(anexo.filename)
            s3_path = f"abastecimentos/{viagem_ativa.id}/{uuid.uuid4()}-{filename}"
            
            s3_client.upload_fileobj(
                anexo,
                bucket_name,
                s3_path,
                ExtraArgs={
                    'ContentType': anexo.content_type or 'application/octet-stream',
                    'ContentDisposition': 'attachment'
                }
            )
            anexo_url = f"{app.config['CLOUDFLARE_R2_PUBLIC_URL']}/{s3_path}"
        
        novo_abastecimento.anexo_comprovante = anexo_url

        db.session.add(novo_abastecimento)
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Abastecimento registrado com sucesso!'})

    except Exception as e:
        db.session.rollback()
        logger.error(f"Erro ao registrar abastecimento: {e}", exc_info=True)
        return jsonify({'success': False, 'message': f'Erro interno: {e}'}), 500



@app.route('/registrar/<token>', methods=['GET', 'POST'])
def registrar_com_token(token):
    convite = Convite.query.filter_by(token=token, usado=False).first()

    if not convite or convite.data_expiracao < datetime.utcnow():
        flash('O link de convite é inválido ou já expirou.', 'error')
        return redirect(url_for('login'))

    if request.method == 'POST':
        nome = request.form.get('nome')
        sobrenome = request.form.get('sobrenome')
        email = request.form.get('email')
        senha = request.form.get('senha')
        cpf_cnpj = re.sub(r'\D', '', request.form.get('cpf_cnpj', ''))

        if email != convite.email:
            flash('O e-mail não corresponde ao convite.', 'error')
            return redirect(request.url)

        if Usuario.query.filter_by(email=email).first() or Usuario.query.filter_by(cpf_cnpj=cpf_cnpj).first():
            flash('Este e-mail ou CPF/CNPJ já está cadastrado.', 'error')
            return redirect(request.url)
        
        # Cria o usuário
        usuario = Usuario(
            nome=nome,
            sobrenome=sobrenome,
            email=email,
            role=convite.role,
            is_admin=(convite.role in ['Admin', 'Master']),
            cpf_cnpj=cpf_cnpj,
            empresa_id=convite.empresa_id
        )
        usuario.set_password(senha)
        db.session.add(usuario)

        # Marca o convite como usado
        convite.usado = True

        # Flush para garantir que `usuario.id` seja gerado antes de atualizar Motorista
        db.session.flush()

        # --- AQUI: busca o Motorista existente e vincula o usuario_id ---
        motorista = Motorista.query.filter_by(
            cpf_cnpj=cpf_cnpj,
            empresa_id=convite.empresa_id
        ).first()
        if motorista:
            motorista.usuario_id = usuario.id

        # Finalmente commit de tudo em bloco único
        db.session.commit()

        flash('Conta criada com sucesso! Faça login.', 'success')
        return redirect(url_for('login'))

    return render_template('registrar_token.html', email=convite.email, role=convite.role)


def get_coordinates(endereco):
    url = 'https://maps.googleapis.com/maps/api/geocode/json'
    params = {'address': endereco, 'key': Maps_API_KEY}
    try:
        logger.debug(f"Obtendo coordenadas para: {endereco}")
        response = requests.get(url, params=params, timeout=5)
        response.raise_for_status()
        data = response.json()
        if data['status'] == 'OK' and data['results']:
            location = data['results'][0]['geometry']['location']
            return location['lat'], location['lng']
        logger.warning(f"Endereço não encontrado: {endereco}")
        return None, None
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro ao obter coordenadas: {str(e)}")
        return None, None

@app.route('/enviar_convite', methods=['POST'])
@login_required
@master_required
def enviar_convite():
    # 1. Verifica se o usuário está vinculado a uma empresa
    if not current_user.empresa_id:
        flash('Você precisa estar vinculado a uma empresa para enviar convites.', 'error')
        return redirect(url_for('configuracoes'))

    empresa_admin = current_user.empresa
    if not empresa_admin:
        flash('Empresa não encontrada para o usuário atual.', 'error')
        return redirect(url_for('configuracoes'))

    # 2. Verifica se a empresa tem licença válida e disponível
    if empresa_admin.licenca:
        usuarios_atuais = len(empresa_admin.usuarios)
        max_permitido = empresa_admin.licenca.max_usuarios
        if usuarios_atuais >= max_permitido:
            flash(f'Limite de usuários atingido ({max_permitido}) para o plano da sua empresa.', 'error')
            return redirect(url_for('configuracoes'))

    # 3. Coleta e valida dados do formulário
    email = request.form.get('email')
    role = request.form.get('role')

    if not email or not role:
        flash('E-mail e papel são obrigatórios.', 'error')
        return redirect(url_for('configuracoes'))

    # 4. Restringe os papéis que podem ser atribuídos
    papeis_permitidos = ['Motorista', 'Master', 'Admin']
    if role not in papeis_permitidos:
        flash('Papel inválido. Escolha entre Motorista, Master ou Admin.', 'error')
        return redirect(url_for('configuracoes'))

    # 5. Verifica se o usuário atual tem permissão para o tipo de convite
    if current_user.role == 'Master' and role != 'Motorista':
        flash('Usuários do tipo Master só podem convidar Motoristas.', 'error')
        return redirect(url_for('configuracoes'))

    # 6. Cria o convite com validade de 3 dias
    token = str(uuid.uuid4())
    data_expiracao = datetime.utcnow() + timedelta(days=3)

    convite = Convite(
        email=email,
        token=token,
        criado_por=current_user.id,
        data_expiracao=data_expiracao,
        role=role,
        empresa_id=current_user.empresa_id
    )

    try:
        db.session.add(convite)
        db.session.commit()

        # 7. Envia o e-mail
        link_convite = url_for('registrar_com_token', token=token, _external=True)
        msg = Message(
            subject=f'Convite para acessar o sistema como {role}',
            recipients=[email],
            body=f'Você foi convidado a se registrar no sistema como {role}.\nClique no link abaixo para se cadastrar:\n\n{link_convite}'
        )
        mail.send(msg)

        flash(f'Convite enviado com sucesso para {email} como {role}!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao enviar convite: {str(e)}', 'error')

    return redirect(url_for('configuracoes'))


def validar_endereco(endereco):
    url = 'https://maps.googleapis.com/maps/api/geocode/json'
    params = {'address': endereco, 'key': Maps_API_KEY}
    try:
        logger.debug(f"Validando endereço: {endereco}")
        response = requests.get(url, params=params, timeout=5)
        response.raise_for_status()
        data = response.json()
        return data['status'] == 'OK' and len(data['results']) > 0
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro na validação de endereço: {str(e)}")
        return False




def calcular_rota_otimizada_ors(enderecos):
    if len(enderecos) < 2:
        return None, None, None, None, "São necessários ao menos um endereço de origem e um de destino."

    try:
        coordenadas = []
        for end in enderecos:
            lat, lon = get_coordinates(end)
            if lat is None or lon is None:
                return None, None, None, None, f"Não foi possível encontrar coordenadas para: {end}"
            coordenadas.append((lon, lat))

        client = openrouteservice.Client(key=OPENROUTESERVICE_API_KEY)
        routes = client.directions(
            coordinates=coordenadas,
            profile='driving-car',
            optimize_waypoints=True,
            geometry=True # Pede a geometria da rota
        )

        route_data = routes['routes'][0]
        geometria_rota = route_data.get('geometry') # Captura a geometria
        
        distancia_total_m, duracao_total_s = 0, 0
        if 'summary' in route_data:
            summary = route_data['summary']
            distancia_total_m = summary.get('distance', 0)
            duracao_total_s = summary.get('duration', 0)
        else:
            for segment in route_data.get('segments', []):
                distancia_total_m += segment.get('distance', 0)
                duracao_total_s += segment.get('duration', 0)

        enderecos_processados = []
        if 'waypoint_order' in route_data:
            waypoint_order = route_data['waypoint_order']
            enderecos_processados = [enderecos[0]] 
            enderecos_processados.extend([enderecos[i] for i in waypoint_order])
            if len(enderecos) > 1: enderecos_processados.append(enderecos[-1])
        else:
            enderecos_processados = enderecos

        distancia_km = distancia_total_m / 1000.0
        duracao_segundos = int(duracao_total_s)

        from collections import OrderedDict
        enderecos_otimizados = list(OrderedDict.fromkeys(enderecos_processados))

        return enderecos_otimizados, distancia_km, duracao_segundos, geometria_rota, None

    except Exception as e:
        logger.error(f"Erro inesperado no cálculo de rota ORS: {e}", exc_info=True)
        return None, None, None, None, f"Ocorreu um erro inesperado ao otimizar a rota: {e}"
    
    
@app.route('/api/ocr-process', methods=['POST'])
@login_required
def ocr_process():
    """Processa uma imagem enviada e retorna o texto extraído via OCR."""
    if 'image' not in request.files:
        return jsonify({'success': False, 'message': 'Nenhum arquivo de imagem enviado.'}), 400
    
    file = request.files['image']
    if file.filename == '':
        return jsonify({'success': False, 'message': 'Nenhum arquivo selecionado.'}), 400

    try:
        # Lê a imagem em memória
        image_bytes = file.read()
        image = Image.open(io.BytesIO(image_bytes))
        
        # Usa o Pytesseract para extrair o texto (configure o idioma se necessário)
        texto_extraido = pytesseract.image_to_string(image, lang='por') # 'por' para português
        
        # Limpa o texto, removendo quebras de linha excessivas
        texto_limpo = " ".join(texto_extraido.split()).strip()

        return jsonify({'success': True, 'text': texto_limpo})

    except Exception as e:
        logger.error(f"Erro no processamento OCR: {e}", exc_info=True)
        return jsonify({'success': False, 'message': f'Erro ao processar a imagem: {e}'}), 500
    
@app.route('/editar_viagem/<int:viagem_id>', methods=['GET'])
@login_required
def editar_viagem_page(viagem_id):
    viagem = Viagem.query.options(db.joinedload(Viagem.destinos)).filter_by(id=viagem_id, empresa_id=current_user.empresa_id).first_or_404()

    motoristas = Motorista.query.filter_by(empresa_id=current_user.empresa_id).order_by(Motorista.nome).all()
    
    veiculos_disponiveis = Veiculo.query.filter_by(disponivel=True, empresa_id=current_user.empresa_id).all()
    veiculo_atual = db.session.get(Veiculo, viagem.veiculo_id)
    if veiculo_atual and veiculo_atual not in veiculos_disponiveis:
        veiculos_disponiveis.insert(0, veiculo_atual)

    return render_template('editar_viagem.html', 
                           viagem=viagem,
                           motoristas=motoristas, 
                           veiculos=veiculos_disponiveis,
                           ORS_API_KEY=OPENROUTESERVICE_API_KEY)


@app.route('/api/viagem/editar/<int:viagem_id>', methods=['POST'])
@login_required
def editar_viagem_api(viagem_id):
    viagem = Viagem.query.filter_by(id=viagem_id, empresa_id=current_user.empresa_id).first_or_404()
    
    try:
        data = request.get_json()
        
        viagem.motorista_id = data.get('motorista_id')
        viagem.veiculo_id = data.get('veiculo_id')
        viagem.cliente = data.get('cliente')
        viagem.data_inicio = datetime.strptime(data.get('data_inicio'), '%Y-%m-%dT%H:%M')
        viagem.forma_pagamento = data.get('forma_pagamento')
        viagem.valor_recebido = float(data.get('valor_recebido') or 0)
        viagem.observacoes = data.get('observacoes')
        
        enderecos_destino = data.get('enderecos_destino', [])
        
        if not enderecos_destino:
            return jsonify({'success': False, 'message': 'É necessário pelo menos um endereço de destino.'}), 400

        todos_enderecos = [viagem.endereco_saida] + enderecos_destino
        
        # --- LINHA CORRIGIDA ---
        rota_otimizada, distancia_km, duracao_segundos, geometria, erro = calcular_rota_otimizada_ors(todos_enderecos)

        if erro:
            return jsonify({'success': False, 'message': f'Erro ao recalcular a rota: {erro}'}), 400
        
        viagem.distancia_km = distancia_km
        viagem.duracao_segundos = duracao_segundos
        viagem.endereco_destino = rota_otimizada[-1]
        viagem.route_geometry = geometria

        Destino.query.filter_by(viagem_id=viagem_id).delete()
        db.session.flush()

        for ordem, endereco in enumerate(rota_otimizada[1:], 1):
            destino = Destino(viagem_id=viagem_id, endereco=endereco, ordem=ordem)
            db.session.add(destino)

        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Viagem atualizada com sucesso!',
            'roteiro': rota_otimizada,
            'distancia': f"{distancia_km:.2f}",
            'duracao_minutos': duracao_segundos // 60
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Erro na API ao editar viagem {viagem_id}: {e}", exc_info=True)
        return jsonify({'success': False, 'message': f'Ocorreu um erro inesperado ao salvar: {e}'}), 500


@app.route('/cobrancas')
@login_required
def consultar_cobrancas():
    # Query corrigida para buscar apenas cobranças da empresa do usuário logado
    cobrancas = Cobranca.query.filter(
        Cobranca.empresa_id == current_user.empresa_id
    ).options(
        db.joinedload(Cobranca.cliente)
    ).order_by(Cobranca.data_vencimento.desc()).all()
    
    # Atualiza o status para 'Vencida' se necessário (apenas nas cobranças da empresa)
    for cobranca in cobrancas:
        if cobranca.is_vencida:
            cobranca.status = 'Vencida'
    db.session.commit()

    # Cálculos de totais agora são feitos apenas com os dados da empresa correta
    total_pendente = sum(c.valor_total for c in cobrancas if c.status in ['Pendente', 'Vencida'])
    total_pago = sum(c.valor_total for c in cobrancas if c.status == 'Paga')

    return render_template('consultar_cobrancas.html', 
                           cobrancas=cobrancas,
                           total_pendente=total_pendente,
                           total_pago=total_pago,
                           active_page='cobrancas')

@app.route('/api/viagem/<int:viagem_id>/map_data')
@login_required
def get_viagem_map_data(viagem_id):
    viagem = Viagem.query.filter_by(id=viagem_id, empresa_id=current_user.empresa_id).first_or_404()
    
    ultima_localizacao = Localizacao.query.filter_by(viagem_id=viagem_id).order_by(Localizacao.timestamp.desc()).first()
    
    localizacao_data = None
    if ultima_localizacao:
        localizacao_data = {
            'latitude': ultima_localizacao.latitude,
            'longitude': ultima_localizacao.longitude,
            'endereco': ultima_localizacao.endereco,
            'timestamp': ultima_localizacao.timestamp.strftime('%d/%m/%Y %H:%M')
        }

    # Lógica para buscar as coordenadas de cada destino
    destinos_com_coords = []
    for destino in sorted(viagem.destinos, key=lambda x: x.ordem):
        lat, lon = get_coordinates(destino.endereco)
        destinos_com_coords.append({
            'endereco': destino.endereco,
            'latitude': lat,
            'longitude': lon
        })

    return jsonify({
        'success': True,
        'route_geometry': viagem.route_geometry,
        'ultima_localizacao': localizacao_data,
        'destinos': destinos_com_coords,
        'origem': viagem.endereco_saida
    })


@app.route('/cobranca/gerar', methods=['GET', 'POST'])
@login_required
def gerar_cobranca():
    if request.method == 'POST':
        try:
            cliente_id = request.form.get('cliente_id')
            viagem_ids = request.form.getlist('viagem_ids')
            data_vencimento_str = request.form.get('data_vencimento')
            observacoes = request.form.get('observacoes')

            if not all([cliente_id, viagem_ids, data_vencimento_str]):
                flash('Cliente, data de vencimento e ao menos uma viagem são obrigatórios.', 'error')
                return redirect(url_for('gerar_cobranca'))

            viagens_selecionadas = Viagem.query.filter(Viagem.id.in_(viagem_ids)).all()
            valor_total = sum(v.valor_recebido or 0 for v in viagens_selecionadas)

            nova_cobranca = Cobranca(
                cliente_id=cliente_id,
                usuario_id=current_user.id,
                valor_total=valor_total,
                data_vencimento=datetime.strptime(data_vencimento_str, '%Y-%m-%d').date(),
                observacoes=observacoes,
                empresa_id=current_user.empresa_id
            )
            
            # --- LÓGICA CORRIGIDA E MAIS ROBUSTA ---
            # Em vez de apenas definir o ID, nós explicitamente adicionamos as viagens
            # à coleção da nova cobrança. O SQLAlchemy cuidará do resto.
            for viagem in viagens_selecionadas:
                nova_cobranca.viagens.append(viagem)
            
            db.session.add(nova_cobranca)
            db.session.commit()
            
            flash('Cobrança gerada com sucesso! Visualizando a Nota de Débito.', 'success')
            return redirect(url_for('visualizar_nota_debito', cobranca_id=nova_cobranca.id))

        except Exception as e:
            db.session.rollback()
            logger.error(f"Erro ao gerar cobrança: {e}", exc_info=True)
            flash(f'Ocorreu um erro ao gerar a cobrança: {e}', 'error')
            return redirect(url_for('gerar_cobranca'))
    
    clientes = Cliente.query.filter_by(empresa_id=current_user.empresa_id).order_by(Cliente.nome_razao_social).all()
    return render_template('gerar_cobranca.html', clientes=clientes, active_page='cobrancas')

@app.route('/api/cobranca/<int:cobranca_id>/marcar_paga', methods=['POST'])
@login_required
def api_marcar_paga(cobranca_id):
    Cobranca.query.filter_by(id=cobranca_id, empresa_id=current_user.empresa_id).first_or_404()
    try:
        data = request.get_json()
        meio_pagamento = data.get('meio_pagamento')

        if not meio_pagamento:
            return jsonify({'success': False, 'message': 'Meio de pagamento é obrigatório.'}), 400

        cobranca.status = 'Paga'
        cobranca.data_pagamento = datetime.utcnow()
        cobranca.meio_pagamento = meio_pagamento
        db.session.commit()

        return jsonify({'success': True, 'message': 'Cobrança marcada como paga.'})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Erro ao marcar cobrança como paga: {e}", exc_info=True)
        return jsonify({'success': False, 'message': str(e)}), 500


def get_address_geoapify(lat, lon):
    try:
        url = f'https://api.geoapify.com/v1/geocode/reverse?lat={lat}&lon={lon}&apiKey={GEOAPIFY_API_KEY}'
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            if data['features']:
                return data['features'][0]['properties']['formatted']
    except Exception as e:
        logger.error(f"Erro na geocodificação Geoapify: {str(e)}")
    return "Endereço não encontrado"

@app.route('/api/cliente/<int:cliente_id>/viagens_nao_cobradas')
@login_required
def api_viagens_nao_cobradas(cliente_id):
    cliente = db.session.get(Cliente, cliente_id)
    if not cliente or cliente.empresa_id != current_user.empresa_id:
        return jsonify({'error': 'Cliente não encontrado ou não pertence à sua empresa.'}), 404

    viagens = Viagem.query.filter(
        Viagem.cliente == cliente.nome_razao_social, 
        Viagem.empresa_id == current_user.empresa_id,
        Viagem.cobranca_id.is_(None),         
        Viagem.valor_recebido.isnot(None)   
    ).all()
    
    viagens_data = [{
        'id': v.id,
        'data_inicio': v.data_inicio.strftime('%d/%m/%Y'),
        'destino': v.endereco_destino,
        'valor': v.valor_recebido or 0.0,
        'valor_formatado': f"R$ {v.valor_recebido or 0:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    } for v in viagens]
    
    return jsonify(viagens_data)

@app.route('/')
@login_required
def index():
    # --- LÓGICA DE FILTRAGEM E PROCESSAMENTO DE VIAGENS (EXISTENTE) ---
    viagens_query = Viagem.query.filter_by(empresa_id=current_user.empresa_id).options(
        db.joinedload(Viagem.veiculo),
        db.joinedload(Viagem.motorista_formal)
    ).order_by(Viagem.data_inicio.desc()).all()

    # --- LÓGICA DE KPIs OPERACIONAIS (EXISTENTE) ---
    viagens_em_andamento_kpi = sum(1 for v in viagens_query if v.status == 'em_andamento')
    viagens_pendentes_kpi = sum(1 for v in viagens_query if v.status == 'pendente')
    veiculos_disponiveis_kpi = Veiculo.query.filter_by(empresa_id=current_user.empresa_id, disponivel=True).count()

    # --- LÓGICA DE KPIs FINANCEIROS (CORRIGIDA PARA POSTGRESQL) ---
    receita_mes = 0.0
    custo_mes = 0.0
    lucro_mes = 0.0

    if current_user.role in ['Admin', 'Master']:
        hoje = date.today()
        
        # CORREÇÃO: Usando db.extract, que é compatível com PostgreSQL e outros bancos
        viagens_do_mes = Viagem.query.filter(
            extract('year', Viagem.data_inicio) == hoje.year,
            extract('month', Viagem.data_inicio) == hoje.month,
            Viagem.empresa_id == current_user.empresa_id,
            Viagem.status == 'concluida'
        ).options(
            db.joinedload(Viagem.custo_viagem),
            db.joinedload(Viagem.abastecimentos)
        ).all()

        for v in viagens_do_mes:
            receita_mes += v.valor_recebido or 0.0
            
            custo_despesas = 0
            if v.custo_viagem:
                custo_despesas = (v.custo_viagem.pedagios or 0) + \
                                 (v.custo_viagem.alimentacao or 0) + \
                                 (v.custo_viagem.hospedagem or 0) + \
                                 (v.custo_viagem.outros or 0)
            
            custo_abastecimento = sum(a.custo_total for a in v.abastecimentos)
            custo_mes += custo_despesas + custo_abastecimento

        lucro_mes = receita_mes - custo_mes
    
    # --- LÓGICA DE PROCESSAMENTO PARA LISTAS (EXISTENTE E INALTERADA) ---
    viagens = []
    for viagem in viagens_query:
        motorista_nome = 'N/A'
        if viagem.motorista_formal:
            motorista_nome = viagem.motorista_formal.nome
        elif viagem.motorista_cpf_cnpj:
            usuario_motorista = Usuario.query.filter_by(
                cpf_cnpj=viagem.motorista_cpf_cnpj,
                empresa_id=current_user.empresa_id
            ).first()
            if usuario_motorista:
                motorista_nome = f"{usuario_motorista.nome} {usuario_motorista.sobrenome}"

        destinos_list = sorted(
            [{'endereco': d.endereco, 'ordem': d.ordem} for d in viagem.destinos],
            key=lambda d: d.get('ordem', 0)
        )

        viagens.append({
            'id': viagem.id,
            'cliente': viagem.cliente,
            'motorista_nome': motorista_nome,
            'endereco_saida': viagem.endereco_saida,
            'destinos': destinos_list,
            'status': viagem.status,
            'veiculo_placa': viagem.veiculo.placa,
            'veiculo_modelo': viagem.veiculo.modelo,
            'data_inicio': viagem.data_inicio,
            'data_fim': viagem.data_fim
        })

    # --- RENDERIZAÇÃO DO TEMPLATE ---
    return render_template('index.html',
                           viagens=viagens,
                           Maps_API_KEY=Maps_API_KEY,
                           viagens_em_andamento=viagens_em_andamento_kpi,
                           viagens_pendentes=viagens_pendentes_kpi,
                           veiculos_disponiveis=veiculos_disponiveis_kpi,
                           receita_mes=receita_mes,
                           custo_mes=custo_mes,
                           lucro_mes=lucro_mes)

@app.route('/cadastrar_motorista', methods=['GET', 'POST'])
@login_required # --- CORREÇÃO --- Adicionado para garantir que 'current_user' esteja sempre disponível.
def cadastrar_motorista():
    if request.method == 'POST':
        nome = request.form.get('nome', '').strip()
        data_nascimento = request.form.get('data_nascimento', '').strip()
        
        # --- INÍCIO DA COLETA DE ENDEREÇO MODIFICADA ---
        cep = request.form.get('cep', '').strip()
        logradouro = request.form.get('logradouro', '').strip()
        numero = request.form.get('numero', '').strip()
        complemento = request.form.get('complemento', '').strip() # Campo novo
        bairro = request.form.get('bairro', '').strip()
        cidade = request.form.get('cidade', '').strip()
        estado = request.form.get('estado', '').strip().upper()

        # Monta a string de endereço de forma mais robusta
        endereco_parts = [f"{logradouro}, {numero}"]
        if complemento:
            endereco_parts.append(complemento)
        endereco_parts.append(bairro)
        endereco_parts.append(f"{cidade}/{estado}")
        endereco_parts.append(f"CEP: {cep}")
        endereco = " - ".join(endereco_parts)
        # --- FIM DA COLETA DE ENDEREÇO MODIFICADA ---

        pessoa_tipo = request.form.get('pessoa_tipo', '').strip()
        cpf_cnpj = request.form.get('cpf_cnpj', '').strip()
        rg = request.form.get('rg', '').strip() or None
        telefone = request.form.get('telefone', '').strip()
        cnh = request.form.get('cnh', '').strip()
        validade_cnh = request.form.get('validade_cnh', '').strip()
        files = request.files.getlist('anexos')

        if not all([nome, data_nascimento, cep, logradouro, numero, bairro, cidade, estado, pessoa_tipo, cpf_cnpj, telefone, cnh, validade_cnh]):
            flash('Todos os campos obrigatórios devem ser preenchidos.', 'error')
            return redirect(url_for('cadastrar_motorista'))

        try:
            data_nascimento = datetime.strptime(data_nascimento, '%Y-%m-%d').date()
            validade_cnh = datetime.strptime(validade_cnh, '%Y-%m-%d').date()
        except ValueError:
            flash('Formato de data inválido.', 'error')
            return redirect(url_for('cadastrar_motorista'))

        if not validate_cpf_cnpj(cpf_cnpj, pessoa_tipo):
            flash(f"{'CPF' if pessoa_tipo == 'fisica' else 'CNPJ'} inválido.", 'error')
            return redirect(url_for('cadastrar_motorista'))

        if not validate_telefone(telefone):
            flash('Telefone inválido. Deve conter 10 ou 11 dígitos numéricos.', 'error')
            return redirect(url_for('cadastrar_motorista'))

        if not validate_cnh(cnh):
            flash('CNH inválida. Deve conter 11 dígitos numéricos.', 'error')
            return redirect(url_for('cadastrar_motorista'))

        if Motorista.query.filter_by(cpf_cnpj=cpf_cnpj, empresa_id=current_user.empresa_id).first():
            flash('Um perfil de motorista com este CPF/CNPJ já foi cadastrado nesta empresa.', 'error')
            return redirect(url_for('cadastrar_motorista'))
        if Motorista.query.filter_by(cnh=cnh, empresa_id=current_user.empresa_id).first():
            flash('Um perfil de motorista com esta CNH já foi cadastrado nesta empresa.', 'error')
            return redirect(url_for('cadastrar_motorista'))

        usuario_correspondente = Usuario.query.filter_by(
            cpf_cnpj=cpf_cnpj,
            empresa_id=current_user.empresa_id
        ).first()

        anexos_urls = []
        allowed_extensions = {'.pdf', '.jpg', '.jpeg', '.png'}
        if files and any(f.filename for f in files):
            try:
                s3_client = boto3.client(
                    's3',
                    endpoint_url=app.config['CLOUDFLARE_R2_ENDPOINT'],
                    aws_access_key_id=app.config['CLOUDFLARE_R2_ACCESS_KEY'],
                    aws_secret_access_key=app.config['CLOUDFLARE_R2_SECRET_KEY'],
                    region_name='auto'
                )
                bucket_name = app.config['CLOUDFLARE_R2_BUCKET']

                for file in files:
                    if file and file.filename:
                        extension = os.path.splitext(file.filename)[1].lower()
                        if extension not in allowed_extensions:
                            flash(f'Arquivo {file.filename} não permitido. Use PDF, JPG ou PNG.', 'error')
                            continue

                        filename = secure_filename(file.filename)
                        s3_path = f"motoristas/{cpf_cnpj}/{filename}"

                        s3_client.upload_fileobj(
                            file,
                            bucket_name,
                            s3_path,
                            ExtraArgs={
                                'ContentType': file.content_type or 'application/octet-stream',
                                'ContentDisposition': 'attachment'
                            }
                        )
                        public_url = f"{app.config['CLOUDFLARE_R2_PUBLIC_URL']}/{s3_path}"
                        anexos_urls.append(public_url)
            except Exception as e:
                flash(f'Erro ao fazer upload dos arquivos: {str(e)}', 'error')
                return redirect(url_for('cadastrar_motorista'))

        motorista = Motorista(
            nome=nome,
            data_nascimento=data_nascimento,
            endereco=endereco,
            pessoa_tipo=pessoa_tipo,
            cpf_cnpj=cpf_cnpj,
            rg=rg,
            telefone=telefone,
            cnh=cnh,
            validade_cnh=validade_cnh,
            anexos=','.join(anexos_urls) if anexos_urls else None,
            empresa_id=current_user.empresa_id,
            usuario_id=usuario_correspondente.id if usuario_correspondente else None
        )

        try:
            db.session.add(motorista)
            db.session.commit()
            flash('Motorista cadastrado com sucesso!', 'success')
            return redirect(url_for('consultar_motoristas'))
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao cadastrar motorista: {str(e)}', 'error')
            return redirect(url_for('cadastrar_motorista'))

    return render_template('cadastrar_motorista.html', active_page='cadastrar_motorista')


@app.route('/editar_cliente/<int:cliente_id>', methods=['GET', 'POST'])
@login_required
def editar_cliente(cliente_id):
    cliente = Cliente.query.filter_by(id=cliente_id, empresa_id=current_user.empresa_id).first_or_404()

    if request.method == 'POST':
        try:
            novo_cpf_cnpj = re.sub(r'\D', '', request.form.get('cpf_cnpj', ''))

            cliente_existente = Cliente.query.filter(Cliente.cpf_cnpj == novo_cpf_cnpj, Cliente.id != cliente_id).first()
            if cliente_existente:
                flash('Erro: O CPF/CNPJ informado já pertence a outro cliente.', 'error')
                return redirect(url_for('editar_cliente', cliente_id=cliente_id))

            cliente.pessoa_tipo = request.form.get('pessoa_tipo')
            cliente.nome_razao_social = request.form.get('nome_razao_social')
            cliente.nome_fantasia = request.form.get('nome_fantasia') if cliente.pessoa_tipo == 'juridica' else None
            cliente.cpf_cnpj = novo_cpf_cnpj
            cliente.inscricao_estadual = request.form.get('inscricao_estadual') if cliente.pessoa_tipo == 'juridica' else None
            cliente.cep = re.sub(r'\D', '', request.form.get('cep', ''))
            cliente.logradouro = request.form.get('logradouro')
            cliente.numero = request.form.get('numero')
            cliente.complemento = request.form.get('complemento')
            cliente.bairro = request.form.get('bairro')
            cliente.cidade = request.form.get('cidade')
            cliente.estado = request.form.get('estado')
            cliente.email = request.form.get('email')
            cliente.telefone = re.sub(r'\D', '', request.form.get('telefone', ''))
            cliente.contato_principal = request.form.get('contato_principal')

            db.session.commit()
            flash('Cliente atualizado com sucesso!', 'success')
            return redirect(url_for('consultar_clientes'))

        except Exception as e:
            db.session.rollback()
            logger.error(f"Erro ao editar cliente: {e}", exc_info=True)
            flash(f'Ocorreu um erro inesperado ao salvar as alterações: {e}', 'error')
            return redirect(url_for('editar_cliente', cliente_id=cliente_id))

    return render_template('editar_cliente.html', cliente=cliente, active_page='consultar_clientes')

@app.route('/nota_debito/<int:cobranca_id>')
@login_required
def visualizar_nota_debito(cobranca_id):
    
    
    cobranca = Cobranca.query.filter_by(id=cobranca_id, empresa_id=current_user.empresa_id).first_or_404()
    empresa_emissora = db.session.get(Empresa, cobranca.usuario.empresa_id) if cobranca.usuario else None   
    valor_por_extenso = num2words(cobranca.valor_total, lang='pt_BR', to='currency')
    return render_template(
        'nota_debito.html',
        cobranca=cobranca,
        cliente=cobranca.cliente,
        empresa=empresa_emissora,
        valor_extenso=valor_por_extenso
    )
@app.route('/criar_admin')
def criar_admin():
    if not Usuario.query.filter_by(email='adminadmin@admin.com').first():
        admin = Usuario(
            nome='Admin',
            sobrenome='Master',
            email='adminadmin@admin.com'
        )
        admin.set_password('admin123')
        db.session.add(admin)
        db.session.commit()
        return "Admin criado com sucesso"
    return "Admin já existe"




@app.route('/consultar_motoristas', methods=['GET'])
@login_required
def consultar_motoristas():
    search_query = request.args.get('search', '').strip()
    query = Motorista.query.filter_by(empresa_id=current_user.empresa_id)

    if search_query:
        search_filter = f"%{search_query}%"
        query = query.filter(
            or_(
                Motorista.nome.ilike(search_filter),
                Motorista.cpf_cnpj.ilike(search_filter),
                Motorista.cnh.ilike(search_filter)
            )
        )

    motoristas_list = query.order_by(Motorista.nome.asc()).all()

    # Adiciona o status a cada objeto motorista
    for motorista in motoristas_list:
        viagem_ativa = Viagem.query.filter(
            Viagem.motorista_id == motorista.id,
            Viagem.status == 'em_andamento'
        ).first()
        motorista.status = 'Em Viagem' if viagem_ativa else 'Disponível'

    # Prepara os dados de data para o template
    contexto = {
        'motoristas': motoristas_list,
        'search_query': search_query,
        'active_page': 'consultar_motoristas',
        'hoje': date.today(),
        'data_alerta_cnh': date.today() + timedelta(days=30) # Define o período de alerta para 30 dias
    }
    
    return render_template('consultar_motoristas.html', **contexto)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        # Redireciona com base no papel se o usuário já estiver logado
        if current_user.role == 'Owner':
            return redirect(url_for('owner_dashboard'))
        elif current_user.role == 'Motorista':
            return redirect(url_for('motorista_dashboard'))
        return redirect(url_for('index'))

    if request.method == 'POST':
        email = request.form.get('email')
        senha = request.form.get('senha')
        
        usuario = Usuario.query.filter_by(email=email).first()
        
        if not usuario or not usuario.check_password(senha):
            flash('Email ou senha incorretos. Por favor, tente novamente.', 'error')
            return redirect(url_for('login'))
            
        login_user(usuario)
        flash('Login realizado com sucesso!', 'success')
        
        # CORREÇÃO: Direcionamento claro e funcional após o login.
        if usuario.role == 'Owner':
            return redirect(url_for('owner_dashboard'))
        elif usuario.role == 'Motorista':
            return redirect(url_for('motorista_dashboard'))
        else:
            return redirect(url_for('index'))
            
    return render_template('login.html')

@app.route('/registrar', methods=['GET', 'POST'])
def registrar():
    if request.method == 'POST':
        nome = request.form.get('nome')
        sobrenome = request.form.get('sobrenome')
        email = request.form.get('email')
        senha = request.form.get('senha')
        
        if Usuario.query.filter_by(email=email).first():
            flash('Email já cadastrado', 'error')
            return redirect(url_for('registrar'))
            
        novo_usuario = Usuario(
            nome=nome,
            sobrenome=sobrenome,
            email=email
        )
        novo_usuario.set_password(senha)
        
        try:
            db.session.add(novo_usuario)
            db.session.commit()
            flash('Conta criada com sucesso! Faça login.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao criar conta: {str(e)}', 'error')
            
    return render_template('registrar.html')

@app.route('/promover_admin')
def promover_admin():
    user = Usuario.query.filter_by(email='adminadmin@admin.com').first()
    if user:
        user.is_admin = True
        db.session.commit()
        return "Admin atualizado!"
    return "Usuário não encontrado."


@app.template_filter('dateformat')
def dateformat(value):
    if value:
        return value.strftime('%Y-%m-%d')
    return ''

@app.route('/editar_motorista/<int:motorista_id>', methods=['GET', 'POST'])
@login_required
def editar_motorista(motorista_id):
    # CORREÇÃO: Garante que o admin só possa editar motoristas da sua empresa.
    motorista = Motorista.query.filter_by(id=motorista_id, empresa_id=current_user.empresa_id).first_or_404()

    if request.method == 'POST':
        try:
            # Validação de dados (CPF/CNPJ, CNH, etc.) para evitar duplicatas.
            # ...
            
            # Atualização dos campos do motorista.
            motorista.nome = request.form.get('nome').strip()
            motorista.data_nascimento = datetime.strptime(request.form.get('data_nascimento'), '%Y-%m-%d').date()
            motorista.endereco = request.form.get('endereco').strip()
            motorista.telefone = request.form.get('telefone').strip()
            motorista.validade_cnh = datetime.strptime(request.form.get('validade_cnh'), '%Y-%m-%d').date()
            
            # Lógica para upload de novos anexos, se houver.
            # ...
            
            db.session.commit()
            flash('Dados do motorista atualizados com sucesso!', 'success')
            return redirect(url_for('consultar_motoristas'))
        except Exception as e:
            db.session.rollback()
            flash(f'Ocorreu um erro ao salvar as alterações: {e}', 'error')

    return render_template('editar_motorista.html', motorista=motorista)

@app.route('/owner/dashboard')
@login_required
@owner_required # Decorador personalizado para garantir que apenas o Owner acesse.
def owner_dashboard():
    # A rota original estava correta, listando todas as empresas para o Owner.
    empresas = Empresa.query.options(
        db.joinedload(Empresa.licenca)
    ).order_by(Empresa.razao_social).all()
    return render_template('owner_dashboard.html', empresas=empresas)

@app.route('/owner/create_client', methods=['POST'])
@login_required
@owner_required
def owner_create_client():
    """
    Rota para o Owner criar uma nova Empresa e enviar um convite para o primeiro Admin.
    """
    try:
        razao_social = request.form.get('razao_social')
        cnpj = re.sub(r'\D', '', request.form.get('cnpj', ''))
        admin_email = request.form.get('admin_email')
        admin_nome = request.form.get('admin_nome')

        if Empresa.query.filter_by(cnpj=cnpj).first():
            flash('Erro: Já existe uma empresa com este CNPJ.', 'error')
            return redirect(url_for('owner_dashboard'))
        
        if Usuario.query.filter_by(email=admin_email).first():
            flash('Erro: Este e-mail de administrador já está em uso.', 'error')
            return redirect(url_for('owner_dashboard'))

        nova_empresa = Empresa(
            razao_social=razao_social,
            cnpj=cnpj,
            endereco="A ser preenchido pelo admin",
            cidade="A ser preenchido",
            estado="XX",
            cep="00000000"
        )
        db.session.add(nova_empresa)
        db.session.commit()

        token = str(uuid.uuid4())
        data_expiracao = datetime.utcnow() + timedelta(days=7)
        convite = Convite(
            email=admin_email,
            token=token,
            criado_por=current_user.id,
            data_expiracao=data_expiracao,
            role='Admin',
            empresa_id=nova_empresa.id
        )
        db.session.add(convite)
        db.session.commit()

        link_convite = url_for('registrar_com_token', token=token, _external=True)
        msg = Message(
            subject=f'Bem-vindo ao TrackGo, {admin_nome}!',
            recipients=[admin_email],
            body=f'''Olá {admin_nome},\n\nSua empresa, {razao_social}, foi cadastrada em nossa plataforma TrackGo!\n\nPara começar a gerenciar sua equipe e operações, por favor, clique no link abaixo para criar sua senha e finalizar seu cadastro como Administrador:\n\n{link_convite}\n\nEste link é válido por 7 dias.\n\nAtenciosamente,\nEquipe TrackGo'''
        )
        mail.send(msg)

        flash(f'Empresa "{razao_social}" criada e convite enviado para {admin_email} com sucesso!', 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'Ocorreu um erro inesperado: {str(e)}', 'error')
        logger.error(f"Erro ao criar cliente pelo owner: {e}", exc_info=True)
    
    return redirect(url_for('owner_dashboard'))

@app.route('/owner/empresa/<int:empresa_id>', methods=['GET', 'POST'])
@login_required
@owner_required
def owner_empresa_detalhes(empresa_id):
    empresa = Empresa.query.get_or_404(empresa_id)
    # Garante que a empresa tenha uma licença; cria uma se não tiver
    if not empresa.licenca:
        licenca = Licenca(empresa_id=empresa.id)
        db.session.add(licenca)
        db.session.commit()
        # Recarrega a empresa para obter a licença recém-criada
        empresa = Empresa.query.get_or_404(empresa_id)

    if request.method == 'POST':
        try:
            licenca = empresa.licenca
            licenca.plano = request.form.get('plano')
            licenca.max_usuarios = int(request.form.get('max_usuarios'))
            licenca.max_veiculos = int(request.form.get('max_veiculos'))
            data_expiracao_str = request.form.get('data_expiracao')
            licenca.data_expiracao = datetime.strptime(data_expiracao_str, '%Y-%m-%d').date() if data_expiracao_str else None
            licenca.ativo = 'ativo' in request.form

            db.session.commit()
            flash('Licença da empresa atualizada com sucesso!', 'success')
            return redirect(url_for('owner_dashboard'))
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao atualizar a licença: {e}', 'error')

    return render_template('owner_empresa_detalhes.html', empresa=empresa)


@app.route('/excluir_anexo/<int:motorista_id>/<path:anexo>', methods=['GET'])
@login_required
def excluir_anexo(motorista_id, anexo):
    
    motorista = Motorista.query.filter_by(id=motorista_id, empresa_id=current_user.empresa_id).first_or_404()
    anexos_urls = motorista.anexos.split(',') if motorista.anexos else []
    if anexo in anexos_urls:
        try:
            s3_client = boto3.client(
                's3',
                endpoint_url=app.config['CLOUDFLARE_R2_ENDPOINT'],
                aws_access_key_id=app.config['CLOUDFLARE_R2_ACCESS_KEY'],
                aws_secret_access_key=app.config['CLOUDFLARE_R2_SECRET_KEY'],
                region_name='auto'
            )
            bucket_name = app.config['CLOUDFLARE_R2_BUCKET']
            filename = anexo.replace(app.config['CLOUDFLARE_R2_PUBLIC_URL'] + '/', '')
            try:
                s3_client.delete_object(Bucket=bucket_name, Key=filename)
            except Exception as e:
                logger.error(f"Erro ao excluir anexo {filename}: {str(e)}")
            anexos_urls.remove(anexo)
            motorista.anexos = ','.join(anexos_urls) if anexos_urls else None
            db.session.commit()
            flash('Anexo excluído com sucesso!', 'success')
        except Exception as e:
            flash(f'Erro ao excluir o anexo: {str(e)}', 'error')
    else:
        flash('Anexo não encontrado.', 'error')
    return redirect(url_for('editar_motorista', motorista_id=motorista_id))

@app.route('/excluir_motorista/<int:motorista_id>')
@login_required
def excluir_motorista(motorista_id):
    motorista = Motorista.query.filter_by(id=motorista_id, empresa_id=current_user.empresa_id).first_or_404()
    if Viagem.query.filter_by(motorista_id=motorista_id).first():
        flash('Erro: Motorista possui viagens associadas.', 'error')
        return redirect(url_for('consultar_motoristas'))
    try:
        if motorista.anexos:
            s3_client = boto3.client(
                's3',
                endpoint_url=app.config['CLOUDFLARE_R2_ENDPOINT'],
                aws_access_key_id=app.config['CLOUDFLARE_R2_ACCESS_KEY'],
                aws_secret_access_key=app.config['CLOUDFLARE_R2_SECRET_KEY'],
                region_name='auto'
            )
            bucket_name = app.config['CLOUDFLARE_R2_BUCKET']
            for anexo in motorista.anexos.split(','):
                filename = anexo.replace(app.config['CLOUDFLARE_R2_PUBLIC_URL'] + '/', '')
                try:
                    s3_client.delete_object(Bucket=bucket_name, Key=filename)
                except Exception as e:
                    logger.error(f"Erro ao excluir anexo {filename}: {str(e)}")
        db.session.delete(motorista)
        db.session.commit()
        flash('Motorista excluído com sucesso!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao excluir motorista: {str(e)}', 'error')
    return redirect(url_for('consultar_motoristas'))

@app.route('/cadastrar_veiculo', methods=['GET', 'POST'])
@login_required
def cadastrar_veiculo():
    if request.method == 'POST':
        placa = request.form.get('placa', '').strip().upper()
        categoria = request.form.get('categoria', '').strip()
        modelo = request.form.get('modelo', '').strip()
        ano = request.form.get('ano', '').strip()
        valor = request.form.get('valor', '').strip()
        km_rodados = request.form.get('km_rodados', '').strip()
        ultima_manutencao = request.form.get('ultima_manutencao', '').strip()

        if not placa or not modelo:
            flash('Placa e modelo são obrigatórios.', 'error')
            return redirect(url_for('cadastrar_veiculo'))
        if not validate_placa(placa):
            flash('Placa inválida. Deve conter 7 caracteres alfanuméricos.', 'error')
            return redirect(url_for('cadastrar_veiculo'))
        if ano:
            try:
                ano = int(ano)
                if ano < 1900 or ano > datetime.now().year:
                    flash('Ano inválido.', 'error')
                    return redirect(url_for('cadastrar_veiculo'))
            except ValueError:
                flash('Ano deve ser um número válido.', 'error')
                return redirect(url_for('cadastrar_veiculo'))
        if valor:
            try:
                valor = float(valor)
                if valor < 0:
                    flash('Valor deve ser positivo.', 'error')
                    return redirect(url_for('cadastrar_veiculo'))
            except ValueError:
                flash('Valor deve ser um número válido.', 'error')
                return redirect(url_for('cadastrar_veiculo'))
        if km_rodados:
            try:
                km_rodados = float(km_rodados)
                if km_rodados < 0:
                    flash('Km rodados deve ser positivo.', 'error')
                    return redirect(url_for('cadastrar_veiculo'))
            except ValueError:
                flash('Km rodados deve ser um número válido.', 'error')
                return redirect(url_for('cadastrar_veiculo'))
        if ultima_manutencao:
            try:
                ultima_manutencao = datetime.strptime(ultima_manutencao, '%Y-%m-%d').date()
                if ultima_manutencao > datetime.now().date():
                    flash('Data de última manutenção não pode ser no futuro.', 'error')
                    return redirect(url_for('cadastrar_veiculo'))
            except ValueError:
                flash('Formato de data inválido para última manutenção.', 'error')
                return redirect(url_for('cadastrar_veiculo'))

        veiculo = Veiculo(
            placa=placa,
            categoria=categoria or None,
            modelo=modelo,
            ano=ano if ano else None,
            valor=valor if valor else None,
            km_rodados=km_rodados if km_rodados else None,
            ultima_manutencao=ultima_manutencao if ultima_manutencao else None,
            empresa_id=current_user.empresa_id
        )
        try:
            db.session.add(veiculo)
            db.session.commit()
            flash('Veículo cadastrado com sucesso!', 'success')
            return redirect(url_for('index'))
        except IntegrityError:
            db.session.rollback()
            flash('Erro: Placa já cadastrada.', 'error')
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao cadastrar veículo: {str(e)}', 'error')
            print(f"Erro ao cadastrar veículo: {str(e)}")
    return render_template('cadastrar_veiculo.html', active_page='cadastrar_veiculo')

@app.route('/consultar_veiculos', methods=['GET'])
@login_required
def consultar_veiculos():
    search_query = request.args.get('search', '').strip()  # Obtém o parâmetro 'search' da query string
    query = Veiculo.query.filter_by(empresa_id=current_user.empresa_id)  # Filtra por empresa do usuário logado
    
    if search_query:
        search_filter = f"%{search_query}%"
        query = query.filter(
            or_(
                Veiculo.placa.ilike(search_filter),
                Veiculo.modelo.ilike(search_filter),
                Veiculo.categoria.ilike(search_filter)
            )
        )
    
    veiculos = query.order_by(Veiculo.placa.asc()).all()
    return render_template('consultar_veiculos.html', veiculos=veiculos, search_query=search_query, active_page='consultar_veiculos')

# Em app.py

@app.route('/editar_veiculo/<int:veiculo_id>', methods=['GET', 'POST'])
def editar_veiculo(veiculo_id):
    veiculo = Veiculo.query.filter_by(id=veiculo_id, empresa_id=current_user.empresa_id).first_or_404()

    if request.method == 'POST':
        try:
            # 1. Obter todos os dados do formulário
            placa = request.form.get('placa', '').strip().upper()
            categoria = request.form.get('categoria', '').strip()
            modelo = request.form.get('modelo', '').strip()
            ano_str = request.form.get('ano', '').strip()
            valor_str = request.form.get('valor', '').strip()
            km_rodados_str = request.form.get('km_rodados', '').strip()
            ultima_manutencao_str = request.form.get('ultima_manutencao', '').strip()

            # 2. Validações (essencial para integridade dos dados)
            if not placa or not modelo:
                flash('Placa e modelo são obrigatórios.', 'error')
                return redirect(url_for('consultar_veiculos'))

            if not validate_placa(placa):
                flash('Placa inválida. Deve conter 7 caracteres alfanuméricos.', 'error')
                return redirect(url_for('consultar_veiculos'))
            
            veiculo_existente = Veiculo.query.filter(Veiculo.placa == placa, Veiculo.id != veiculo_id, Veiculo.empresa_id == current_user.empresa_id).first()
            if veiculo_existente:
                flash('Erro: Placa já cadastrada para outro veículo.', 'error')
                return redirect(url_for('consultar_veiculos'))

            # =================================================================
            # 3. ATRIBUIR OS NOVOS VALORES AO OBJETO (A PARTE CRÍTICA)
            # =================================================================
            veiculo.placa = placa
            veiculo.categoria = categoria or None
            veiculo.modelo = modelo

            # Tratar conversão de tipos para campos numéricos e de data
            veiculo.ano = int(ano_str) if ano_str else None
            veiculo.valor = float(valor_str) if valor_str else None
            veiculo.km_rodados = float(km_rodados_str) if km_rodados_str else None
            
            if ultima_manutencao_str:
                veiculo.ultima_manutencao = datetime.strptime(ultima_manutencao_str, '%Y-%m-%d').date()
            else:
                veiculo.ultima_manutencao = None

            # 4. SALVAR (COMMIT) AS ALTERAÇÕES NO BANCO DE DADOS
            # Agora o commit terá o que salvar.
            db.session.commit()
            flash('Veículo atualizado com sucesso!', 'success')

        except Exception as e:
            db.session.rollback()
            logger.error(f"Erro ao editar o veículo {veiculo_id}: {e}", exc_info=True)
            flash(f'Ocorreu um erro inesperado ao salvar: {str(e)}', 'error')
        
        # O redirecionamento acontece após o try/except
        return redirect(url_for('consultar_veiculos'))

    # Para requisições GET, a função continua a mesma, renderizando a página de edição
    return render_template('editar_veiculo.html', veiculo=veiculo, active_page='consultar_veiculos')

@app.route('/excluir_veiculo/<int:veiculo_id>')
@login_required
def excluir_veiculo(veiculo_id):
    veiculo = Veiculo.query.filter_by(id=veiculo_id, empresa_id=current_user.empresa_id).first_or_404()
    if Viagem.query.filter_by(veiculo_id=veiculo_id).first():
        flash('Erro: Veículo possui viagens associadas.', 'error')
        return redirect(url_for('consultar_veiculos'))
    try:
        db.session.delete(veiculo)
        db.session.commit()
        flash('Veículo excluído com sucesso!', 'success')
    except:
        db.session.rollback()
        flash('Erro ao excluir veículo.', 'error')
    return redirect(url_for('consultar_veiculos'))

@app.route('/iniciar_viagem', methods=['GET'])
@login_required
def iniciar_viagem_page():
    """Apenas renderiza a página do formulário de iniciar viagem."""
    motoristas = Motorista.query.filter_by(empresa_id=current_user.empresa_id).order_by(Motorista.nome).all()
    veiculos = Veiculo.query.filter_by(disponivel=True, empresa_id=current_user.empresa_id).order_by(Veiculo.placa).all()
    # A lógica de POST foi movida para uma rota de API separada.
    return render_template('iniciar_viagem.html', motoristas=motoristas, veiculos=veiculos, ORS_API_KEY=OPENROUTESERVICE_API_KEY)

@app.route('/api/motorista/<int:motorista_id>/details')
@login_required
def get_motorista_details_api(motorista_id):
    """
    Esta rota de API retorna as estatísticas e o histórico de viagens
    de um motorista em formato JSON para ser consumido pelo modal.
    """
    # Garante que o usuário só possa ver motoristas da sua própria empresa
    motorista = Motorista.query.filter_by(id=motorista_id, empresa_id=current_user.empresa_id).first_or_404()

    # Busca as viagens do motorista, carregando os dados relacionados para o cálculo de custos
    viagens = Viagem.query.filter(Viagem.motorista_id == motorista.id).options(
        db.joinedload(Viagem.custo_viagem),
        db.joinedload(Viagem.abastecimentos)
    ).order_by(Viagem.data_inicio.desc()).all()

    # Lógica de cálculo de estatísticas (reaproveitando e melhorando a da página de perfil)
    total_receita = 0
    total_custo_detalhado = 0
    
    for v in viagens:
        total_receita += v.valor_recebido or 0
        
        # Calcula o custo detalhado da viagem
        custo_despesas = 0
        if v.custo_viagem:
            custo_despesas = (v.custo_viagem.pedagios or 0) + (v.custo_viagem.alimentacao or 0) + (v.custo_viagem.hospedagem or 0) + (v.custo_viagem.outros or 0)
        
        custo_abastecimento = sum(a.custo_total for a in v.abastecimentos)
        total_custo_detalhado += custo_despesas + custo_abastecimento

    stats = {
        'total_viagens': len(viagens),
        'total_distancia': round(sum(v.distancia_km or 0 for v in viagens), 2),
        'total_receita': round(total_receita, 2),
        'total_custo': round(total_custo_detalhado, 2),
        'lucro_total': round(total_receita - total_custo_detalhado, 2)
    }

    # Formata os dados das viagens para o JSON
    viagens_data = []
    for v in viagens:
        viagens_data.append({
            'id': v.id,
            'cliente': v.cliente,
            'data_inicio': v.data_inicio.isoformat(),
            'endereco_saida': v.endereco_saida,
            'endereco_destino': v.endereco_destino,
            'status': v.status
        })

    # Retorna o JSON completo que o JavaScript espera
    return jsonify({
        'success': True,
        'stats': stats,
        'viagens': viagens_data
    })


@app.route('/api/viagem/criar', methods=['POST'])
@login_required
def criar_viagem_api():
    try:
        data = request.get_json()
        
        motorista_id = data.get('motorista_id')
        veiculo_id = data.get('veiculo_id')
        cliente = data.get('cliente')
        endereco_saida = data.get('endereco_saida')
        enderecos_destino = data.get('enderecos_destino', [])
        data_inicio_str = data.get('data_inicio')
        
        if not all([motorista_id, veiculo_id, cliente, endereco_saida, enderecos_destino, data_inicio_str]):
            return jsonify({'success': False, 'message': 'Todos os campos são obrigatórios.'}), 400

        motorista = db.session.get(Motorista, int(motorista_id))
        veiculo = db.session.get(Veiculo, int(veiculo_id))
        if not motorista or not veiculo:
            return jsonify({'success': False, 'message': 'Motorista ou Veículo não encontrado.'}), 404
        if not veiculo.disponivel:
            return jsonify({'success': False, 'message': f'Veículo {veiculo.placa} já está em viagem.'}), 409
            
        todos_enderecos = [endereco_saida] + enderecos_destino
        
        # --- LINHA CORRIGIDA ---
        rota_otimizada, distancia_km, duracao_segundos, geometria, erro = calcular_rota_otimizada_ors(todos_enderecos)

        if erro:
            return jsonify({'success': False, 'message': erro}), 400

        nova_viagem = Viagem(
            motorista_id=motorista_id,
            motorista_cpf_cnpj=motorista.cpf_cnpj,
            veiculo_id=veiculo_id,
            cliente=cliente,
            valor_recebido=float(data.get('valor_recebido') or 0),
            forma_pagamento=data.get('forma_pagamento'),
            endereco_saida=endereco_saida,
            endereco_destino=rota_otimizada[-1],
            distancia_km=distancia_km,
            data_inicio=datetime.strptime(data_inicio_str, '%Y-%m-%dT%H:%M'),
            duracao_segundos=duracao_segundos,
            status='pendente',
            observacoes=data.get('observacoes'),
            route_geometry=geometria,
            empresa_id=current_user.empresa_id
        )
        veiculo.disponivel = False
        db.session.add(nova_viagem)
        db.session.flush()

        for ordem, endereco in enumerate(rota_otimizada[1:], 1):
            destino = Destino(viagem_id=nova_viagem.id, endereco=endereco, ordem=ordem)
            db.session.add(destino)

        db.session.commit()

        return jsonify({
            'success': True,
            'message': 'Viagem criada com sucesso!',
            'viagem_id': nova_viagem.id,
            'roteiro': rota_otimizada,
            'distancia': f"{distancia_km:.2f}",
            'duracao_minutos': duracao_segundos // 60
        })

    except Exception as e:
        db.session.rollback()
        logger.error(f"Erro na API ao criar viagem: {e}", exc_info=True)
        return jsonify({'success': False, 'message': f'Ocorreu um erro interno: {e}'}), 500

@app.route('/excluir_viagem/<int:viagem_id>')
@login_required 
def excluir_viagem(viagem_id):
    
    viagem = Viagem.query.filter_by(id=viagem_id, empresa_id=current_user.empresa_id).first_or_404()

    try:
       
        if not viagem.data_fim and viagem.veiculo:
            viagem.veiculo.disponivel = True
        
        
        db.session.delete(viagem)
        db.session.commit()
        flash('Viagem excluída com sucesso!', 'success')

    except Exception as e:
        db.session.rollback()
        logger.error(f"Erro ao excluir viagem: {str(e)}")
        flash(f'Erro ao excluir viagem: {str(e)}', 'error')

    
    return redirect(url_for('consultar_viagens'))

@app.route('/salvar_custo_viagem', methods=['POST'])
@login_required
def salvar_custo_viagem():
    # --- INÍCIO DA CORREÇÃO ---
    # 1. Pega o ID da viagem que vem escondido no formulário
    viagem_id = request.form.get('viagem_id', type=int)
    if not viagem_id:
        return jsonify({'success': False, 'message': 'ID da viagem não foi fornecido.'}), 400

    # 2. Garante que a viagem pertence à empresa do usuário logado
    viagem = Viagem.query.filter_by(id=viagem_id, empresa_id=current_user.empresa_id).first()
    if not viagem:
        return jsonify({'success': False, 'message': 'Viagem não encontrada ou acesso negado.'}), 404
    # --- FIM DA CORREÇÃO ---
    
    try:
        # LÓGICA DE 'UPDATE OR CREATE' (ATUALIZAR OU CRIAR)
        custo = CustoViagem.query.filter_by(viagem_id=viagem_id).first()
        if not custo:
            custo = CustoViagem(viagem_id=viagem_id)
            db.session.add(custo)

        custo.pedagios = float(request.form.get('pedagios') or 0)
        custo.alimentacao = float(request.form.get('alimentacao') or 0)
        custo.hospedagem = float(request.form.get('hospedagem') or 0)
        custo.outros = float(request.form.get('outros') or 0)
        custo.descricao_outros = request.form.get('descricao_outros', '').strip()
        
        # Lógica para salvar anexos (se houver)
        urls_anexos = custo.anexos.split(',') if custo.anexos else []
        if 'anexos_despesa' in request.files:
            for anexo in request.files.getlist('anexos_despesa'):
                if anexo and anexo.filename != '':
                    filename = f"custos/{viagem_id}/{uuid.uuid4()}_{secure_filename(anexo.filename)}"
                    s3_client.upload_fileobj(anexo, R2_BUCKET_NAME, filename, ExtraArgs={'ContentType': anexo.content_type})
                    urls_anexos.append(f"{R2_PUBLIC_URL}/{filename}")
        if urls_anexos:
            custo.anexos = ",".join(urls_anexos)

        # Atualiza o custo total na viagem principal para consistência
        custo_total_geral = (custo.pedagios or 0) + (custo.alimentacao or 0) + (custo.hospedagem or 0) + (custo.outros or 0)
        viagem.custo = custo_total_geral

        db.session.commit()
        return jsonify({'success': True, 'message': 'Despesas salvas com sucesso!'})

    except Exception as e:
        db.session.rollback()
        logger.error(f"Erro ao salvar custo da viagem {viagem_id}: {str(e)}")
        return jsonify({'success': False, 'message': f'Erro interno do servidor: {str(e)}'}), 500

@app.route('/excluir_anexo_custo', methods=['POST'])
@login_required
def excluir_anexo_custo():
    data = request.get_json()
    viagem_id = data.get('viagem_id')
    anexo_url = data.get('anexo_url')

    if not viagem_id or not anexo_url:
        return jsonify({'success': False, 'message': 'Dados incompletos.'}), 400

    try:
        custo = CustoViagem.query.filter_by(viagem_id=viagem_id).first()
        if not custo or not custo.anexos:
            return jsonify({'success': False, 'message': 'Anexo não encontrado.'}), 404

        anexos_atuais = custo.anexos.split(',')
        if anexo_url not in anexos_atuais:
            return jsonify({'success': False, 'message': 'URL do anexo não corresponde.'}), 404

        # 1. Excluir do Cloudflare R2
        s3_client = boto3.client(
            's3',
            endpoint_url=app.config['CLOUDFLARE_R2_ENDPOINT'],
            aws_access_key_id=app.config['CLOUDFLARE_R2_ACCESS_KEY'],
            aws_secret_access_key=app.config['CLOUDFLARE_R2_SECRET_KEY'],
            region_name='auto'
        )
        bucket_name = app.config['CLOUDFLARE_R2_BUCKET']
        key = anexo_url.replace(app.config['CLOUDFLARE_R2_PUBLIC_URL'] + '/', '')
        s3_client.delete_object(Bucket=bucket_name, Key=key)

        # 2. Excluir do Banco de Dados
        anexos_atuais.remove(anexo_url)
        custo.anexos = ','.join(anexos_atuais) if anexos_atuais else None
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Anexo excluído com sucesso!'})

    except Exception as e:
        db.session.rollback()
        logger.error(f"Erro ao excluir anexo da viagem {viagem_id}: {str(e)}")
        return jsonify({'success': False, 'message': f'Erro interno do servidor: {str(e)}'}), 500

@app.route('/consultar_despesas/<int:viagem_id>', methods=['GET'])
@login_required
def consultar_despesas(viagem_id):
    try:
        viagem = Viagem.query.get_or_404(viagem_id)
        custo_viagem = CustoViagem.query.filter_by(viagem_id=viagem_id).first()
        custo_dict = {
            'combustivel': custo_viagem.combustivel if custo_viagem else 0.0,
            'pedagios': custo_viagem.pedagios if custo_viagem else 0.0,
            'alimentacao': custo_viagem.alimentacao if custo_viagem else 0.0,
            'hospedagem': custo_viagem.hospedagem if custo_viagem else 0.0,
            'outros': custo_viagem.outros if custo_viagem else 0.0,
            'descricao_outros': custo_viagem.descricao_outros if custo_viagem else 'Nenhuma',
            'anexos': custo_viagem.anexos.split(',') if custo_viagem and custo_viagem.anexos else []
        }
        return jsonify(custo_dict)
    except Exception as e:
        logger.error(f"Erro ao consultar despesas da viagem {viagem_id}: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/atualizar_status_viagem/<int:viagem_id>', methods=['POST'])
@login_required # Garante que apenas usuários logados possam alterar o status
def atualizar_status_viagem(viagem_id):
    try:
        data = request.get_json()
        novo_status = data.get('status')

        if novo_status not in ['pendente', 'em_andamento', 'concluida', 'cancelada']:
            return jsonify({'success': False, 'message': 'Status inválido'}), 400

        # Blindagem de Segurança: Busca a viagem garantindo que ela pertence à empresa do usuário.
        viagem = Viagem.query.filter_by(id=viagem_id, empresa_id=current_user.empresa_id).first_or_404()
        
        status_antigo = viagem.status
        viagem.status = novo_status

        # --- LÓGICA DE NEGÓCIO CORRIGIDA E ADICIONADA ---

        # 1. Se a viagem for finalizada (concluída ou cancelada)
        if novo_status in ['concluida', 'cancelada']:
            # Define a data de fim se ainda não tiver
            if not viagem.data_fim:
                viagem.data_fim = datetime.utcnow()
            # Libera o veículo para a próxima viagem
            if viagem.veiculo:
                viagem.veiculo.disponivel = True
        
        # 2. Se uma viagem finalizada for reaberta para "Em Andamento"
        elif novo_status == 'em_andamento' and status_antigo in ['concluida', 'cancelada']:
            viagem.data_fim = None # Remove a data de fim, pois a viagem foi reaberta
            if viagem.veiculo:
                # Antes de ocupar o veículo, verifica se ele já não foi alocado para outra viagem
                outra_viagem_ativa = Viagem.query.filter(
                    Viagem.veiculo_id == viagem.veiculo_id,
                    Viagem.status == 'em_andamento',
                    Viagem.id != viagem.id  # Exclui a própria viagem da busca
                ).first()

                if outra_viagem_ativa:
                    # Impede a ação se o veículo já estiver em uso
                    flash(f'Erro: O veículo {viagem.veiculo.placa} já está em uso na viagem #{outra_viagem_ativa.id}.', 'error')
                    db.session.rollback() # Desfaz a mudança de status
                    return jsonify({'success': False, 'message': f'Veículo já está em outra viagem.'}), 409 # 'Conflict'
                
                # Se o veículo estiver livre, ocupa-o novamente
                viagem.veiculo.disponivel = False

        db.session.commit()
        flash('Status da viagem atualizado com sucesso!', 'success')
        return jsonify({'success': True, 'message': 'Status atualizado com sucesso.'})
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Erro ao atualizar status da viagem {viagem_id}: {e}", exc_info=True)
        return jsonify({'success': False, 'message': str(e)}), 500

    
@app.route('/api/viagem/<int:viagem_id>/despesas_detalhes')
@login_required
def api_despesas_detalhes(viagem_id):
    """Retorna um HTML renderizado com os detalhes de todas as despesas da viagem."""
    viagem = Viagem.query.options(
        db.joinedload(Viagem.custo_viagem),
        db.joinedload(Viagem.abastecimentos)
    ).get_or_404(viagem_id)

    # Prepara uma lista combinada de despesas para o template
    despesas_detalhadas = []
    
    # Adiciona abastecimentos
    for abast in viagem.abastecimentos:
        despesas_detalhadas.append({
            'tipo': 'Abastecimento',
            'data': abast.data_abastecimento.strftime('%d/%m/%Y'),
            'descricao': f"{abast.litros:.2f}L @ R$ {abast.preco_por_litro:.2f}/L",
            'valor': abast.custo_total
        })

    # Adiciona outras despesas
    if viagem.custo_viagem:
        if viagem.custo_viagem.pedagios:
            despesas_detalhadas.append({'tipo': 'Pedágios', 'descricao': 'Total em pedágios', 'valor': viagem.custo_viagem.pedagios})
        if viagem.custo_viagem.alimentacao:
            despesas_detalhadas.append({'tipo': 'Alimentação', 'descricao': 'Total em alimentação', 'valor': viagem.custo_viagem.alimentacao})
        if viagem.custo_viagem.hospedagem:
            despesas_detalhadas.append({'tipo': 'Hospedagem', 'descricao': 'Total em hospedagem', 'valor': viagem.custo_viagem.hospedagem})
        if viagem.custo_viagem.outros:
            despesas_detalhadas.append({'tipo': 'Outros', 'descricao': viagem.custo_viagem.descricao_outros or 'Sem descrição', 'valor': viagem.custo_viagem.outros})

    # Renderiza um novo template com os detalhes
    return render_template('detalhes_despesas_modal.html', despesas=despesas_detalhadas, viagem_id=viagem_id)

@app.route('/consultar_viagens')
@login_required
def consultar_viagens():
    # Pega todos os argumentos do request de uma só vez
    args = request.args
    status_filter = args.get('status', '')
    search_query = args.get('search', '')
    data_inicio = args.get('data_inicio', '')
    data_fim = args.get('data_fim', '')
    motorista_id_filter = args.get('motorista_id', type=int)
    veiculo_id_filter = args.get('veiculo_id', type=int)

    # A query base já começa filtrando pela empresa do usuário
    query = Viagem.query.filter_by(empresa_id=current_user.empresa_id).options(
        db.joinedload(Viagem.motorista_formal),
        db.joinedload(Viagem.veiculo),
        db.joinedload(Viagem.custo_viagem),
        db.joinedload(Viagem.abastecimentos)
    )

    # Aplica os filtros
    if status_filter:
        query = query.filter(Viagem.status == status_filter)
    if motorista_id_filter:
        query = query.filter(Viagem.motorista_id == motorista_id_filter)
    if veiculo_id_filter:
        query = query.filter(Viagem.veiculo_id == veiculo_id_filter)

    if search_query:
        search_term = f'%{search_query}%'
        query = query.outerjoin(Motorista, Viagem.motorista_id == Motorista.id).filter(
            or_(
                Viagem.cliente.ilike(search_term),
                Motorista.nome.ilike(search_term),
                Viagem.veiculo.has(Veiculo.placa.ilike(search_term)),
                Viagem.endereco_saida.ilike(search_term),
                Viagem.endereco_destino.ilike(search_term)
            )
        )
        
    if data_inicio:
        try:
            query = query.filter(Viagem.data_inicio >= datetime.strptime(data_inicio, '%Y-%m-%d'))
        except ValueError:
            flash('Data de início inválida.', 'error')
            
    if data_fim:
        try:
            # Adiciona 1 dia e subtrai 1 segundo para incluir o dia inteiro
            data_fim_obj = datetime.strptime(data_fim, '%Y-%m-%d') + timedelta(days=1, seconds=-1)
            query = query.filter(Viagem.data_inicio <= data_fim_obj)
        except ValueError:
            flash('Data de fim inválida.', 'error')

    viagens_objetos = query.order_by(Viagem.data_inicio.desc()).all()

    # Processa os dados para o template
    for v in viagens_objetos:
        # ... (cálculo de custo total continua igual) ...
        custo_despesas = 0
        if v.custo_viagem:
            custo_despesas = (v.custo_viagem.pedagios or 0) + (v.custo_viagem.alimentacao or 0) + (v.custo_viagem.hospedagem or 0) + (v.custo_viagem.outros or 0)
        custo_abastecimento = sum(a.custo_total for a in v.abastecimentos)
        v.custo_total_calculado = custo_despesas + custo_abastecimento
        
        v.motorista_nome = v.motorista_formal.nome if v.motorista_formal else 'N/A'
        v.motorista_telefone = v.motorista_formal.telefone if v.motorista_formal else None

        # Busca o cliente no banco de dados para obter o telefone
        cliente_obj = Cliente.query.filter_by(nome_razao_social=v.cliente, empresa_id=current_user.empresa_id).first()
        v.cliente_telefone = cliente_obj.telefone if cliente_obj else None

    # Busca os dados para os dropdowns de filtro
    motoristas_filtro = Motorista.query.filter_by(empresa_id=current_user.empresa_id).order_by(Motorista.nome).all()
    veiculos_filtro = Veiculo.query.filter_by(empresa_id=current_user.empresa_id).order_by(Veiculo.placa).all()
    
    return render_template(
        'consultar_viagens.html',
        active_page='consultar_viagens',
        viagens=viagens_objetos,
        motoristas=motoristas_filtro,
        veiculos=veiculos_filtro,
        request=request
    )

@app.route('/viagem/<int:viagem_id>/gerenciar_despesas')
@login_required
def gerenciar_despesas_viagem(viagem_id):
    # Garante que a viagem pertence à empresa do usuário
    viagem = Viagem.query.filter_by(id=viagem_id, empresa_id=current_user.empresa_id).first_or_404()
    
    # Busca os custos e abastecimentos associados
    custo = CustoViagem.query.filter_by(viagem_id=viagem_id).first()
    abastecimentos = Abastecimento.query.filter_by(viagem_id=viagem_id).order_by(Abastecimento.data_abastecimento.desc()).all()

    # Renderiza o novo template do modal
    return render_template('gerenciar_despesas_modal.html', 
                           viagem=viagem, 
                           custo=custo, 
                           abastecimentos=abastecimentos)

from flask import jsonify, request
from datetime import datetime

@app.route('/viagem/<int:viagem_id>/finalizar', methods=['POST'])
@login_required
def finalizar_viagem(viagem_id):
    if current_user.role != 'Motorista':
        return jsonify({'success': False, 'message': 'Acesso negado.'}), 403

    viagem = Viagem.query.filter_by(id=viagem_id, empresa_id=current_user.empresa_id, status='em_andamento').first_or_404()
    
    data = request.get_json()
    # --- CORREÇÃO AQUI ---
    odometro_str = data.get('odometer') # Alterado de 'odometro' para 'odometer'

    try:
        odometro_final = float(odometro_str)
        if odometro_final < viagem.odometro_inicial:
            return jsonify({'success': False, 'message': 'Odômetro final não pode ser menor que o inicial.'}), 400
    except (ValueError, TypeError):
        return jsonify({'success': False, 'message': 'Odômetro final inválido. Por favor, insira um número válido.'}), 400

    viagem.status = 'concluida'
    viagem.data_fim = datetime.utcnow()
    viagem.odometro_final = odometro_final
    
    if viagem.veiculo:
        viagem.veiculo.disponivel = True
        
    db.session.commit()
    
    # Emite evento para atualizar telas de admin em tempo real
    socketio.emit('status_viagem_atualizado', {
        'viagem_id': viagem.id, 
        'status': 'concluida'
    }, room='admins')

    return jsonify({'success': True, 'message': 'Viagem finalizada com sucesso!'})
    

from collections import defaultdict

from collections import defaultdict

@app.route('/relatorios')
@login_required
def relatorios():
    try:
        data_inicio_str = request.args.get('data_inicio', '')
        data_fim_str = request.args.get('data_fim', '')
        motorista_id_filter = request.args.get('motorista_id', '')
        veiculo_id_filter = request.args.get('veiculo_id', '')
        
        query = Viagem.query.filter_by(empresa_id=current_user.empresa_id).options(
            db.joinedload(Viagem.custo_viagem),
            db.joinedload(Viagem.motorista_formal),
            db.joinedload(Viagem.veiculo),
            db.joinedload(Viagem.abastecimentos)
        )

        if data_inicio_str:
            query = query.filter(Viagem.data_inicio >= datetime.strptime(data_inicio_str, '%Y-%m-%d'))
        if data_fim_str:
            data_fim_obj = datetime.strptime(data_fim_str, '%Y-%m-%d') + timedelta(days=1)
            query = query.filter(Viagem.data_inicio < data_fim_obj)
        if motorista_id_filter:
            query = query.filter(Viagem.motorista_id == int(motorista_id_filter))
        if veiculo_id_filter:
            query = query.filter(Viagem.veiculo_id == int(veiculo_id_filter))

        viagens_filtradas = query.order_by(Viagem.data_inicio.desc()).all()

        total_receita = 0.0
        total_custo_outros = 0.0
        total_custo_combustivel = 0.0
        total_distancia = 0.0
        total_litros = 0.0
        
        dados_grafico_mensal = defaultdict(lambda: {'receita': 0.0, 'custo': 0.0})
        dados_grafico_categorias = defaultdict(float)
        clientes_stats = defaultdict(lambda: {'nome': '', 'viagens': 0, 'receita': 0.0, 'custo': 0.0, 'lucro': 0.0})
        motoristas_stats_dict = defaultdict(lambda: {'id': None, 'nome': 'N/A', 'viagens': 0, 'receita': 0.0}) # Renomeado para clareza
        veiculos_stats = defaultdict(lambda: {'id': None, 'modelo': 'N/A', 'placa': 'N/A', 'km': 0.0, 'custo': 0.0, 'litros': 0.0})

        for v in viagens_filtradas:
            receita_viagem = v.valor_recebido or 0.0
            custo_combustivel_viagem = sum(a.custo_total for a in v.abastecimentos)
            litros_viagem = sum(a.litros for a in v.abastecimentos)
            custo_outros_viagem = 0
            if v.custo_viagem:
                custo_outros_viagem += (v.custo_viagem.pedagios or 0) + (v.custo_viagem.alimentacao or 0) + (v.custo_viagem.hospedagem or 0) + (v.custo_viagem.outros or 0)
            
            custo_total_viagem = custo_combustivel_viagem + custo_outros_viagem
            total_receita += receita_viagem
            total_custo_combustivel += custo_combustivel_viagem
            total_custo_outros += custo_outros_viagem
            total_distancia += v.distancia_km or 0.0
            total_litros += litros_viagem

            if v.data_inicio:
                mes = v.data_inicio.strftime('%Y-%m')
                dados_grafico_mensal[mes]['receita'] += receita_viagem
                dados_grafico_mensal[mes]['custo'] += custo_total_viagem

            dados_grafico_categorias['Combustível'] += custo_combustivel_viagem
            if v.custo_viagem:
                dados_grafico_categorias['Pedágios'] += v.custo_viagem.pedagios or 0
                dados_grafico_categorias['Alimentação'] += v.custo_viagem.alimentacao or 0
                dados_grafico_categorias['Hospedagem'] += v.custo_viagem.hospedagem or 0
                dados_grafico_categorias['Outros'] += v.custo_viagem.outros or 0
            
            if v.cliente:
                clientes_stats[v.cliente]['nome'] = v.cliente
                clientes_stats[v.cliente]['viagens'] += 1
                clientes_stats[v.cliente]['receita'] += receita_viagem
                clientes_stats[v.cliente]['custo'] += custo_total_viagem
                clientes_stats[v.cliente]['lucro'] = clientes_stats[v.cliente]['receita'] - clientes_stats[v.cliente]['custo']

            if v.motorista_formal:
                motoristas_stats_dict[v.motorista_formal.id].update({'id': v.motorista_formal.id, 'nome': v.motorista_formal.nome})
                motoristas_stats_dict[v.motorista_formal.id]['viagens'] += 1
                motoristas_stats_dict[v.motorista_formal.id]['receita'] += receita_viagem

            if v.veiculo:
                veiculos_stats[v.veiculo.id].update({'id': v.veiculo.id, 'placa': v.veiculo.placa, 'modelo': v.veiculo.modelo})
                veiculos_stats[v.veiculo.id]['km'] += v.distancia_km or 0.0
                veiculos_stats[v.veiculo.id]['custo'] += custo_total_viagem
                veiculos_stats[v.veiculo.id]['litros'] += litros_viagem
        
        # --- LÓGICA DE ORDENAÇÃO ADICIONADA AQUI ---
        motoristas_ordenados = sorted(motoristas_stats_dict.values(), key=lambda m: m['receita'], reverse=True)

        total_custo = total_custo_combustivel + total_custo_outros
        consumo_medio_geral = (total_distancia / total_litros) if total_litros > 0 else 0

        motoristas_para_filtro = Motorista.query.filter_by(empresa_id=current_user.empresa_id).order_by(Motorista.nome).all()
        veiculos_para_filtro = Veiculo.query.filter_by(empresa_id=current_user.empresa_id).order_by(Veiculo.placa).all()

        return render_template(
            'relatorios.html',
            request=request,
            total_viagens=len(viagens_filtradas),
            total_receita=total_receita,
            total_custo=total_custo,
            consumo_medio_geral=consumo_medio_geral,
            motoristas_filtro=motoristas_para_filtro,
            veiculos_filtro=veiculos_para_filtro,
            dados_grafico_mensal=dict(sorted(dados_grafico_mensal.items())),
            dados_grafico_categorias=dict(dados_grafico_categorias),
            clientes_stats=list(clientes_stats.values()),
            motoristas_stats=motoristas_ordenados, # Enviando a LISTA JÁ ORDENADA
            veiculos_stats=list(veiculos_stats.values())
        )

    except Exception as e:
        logger.error(f"Erro ao gerar relatórios: {e}", exc_info=True)
        flash(f"Ocorreu um erro inesperado ao gerar os relatórios: {e}", "error")
        return redirect(url_for('index'))


@app.route('/api/viagem/<int:viagem_id>/despesas', methods=['GET'])
@login_required
def get_viagem_despesas(viagem_id):
    """Busca os custos de uma viagem para preencher o formulário de edição."""
    custo = CustoViagem.query.filter_by(viagem_id=viagem_id).first()
    if custo:
        return jsonify({
            'success': True,
            'pedagios': custo.pedagios,
            'alimentacao': custo.alimentacao,
            'hospedagem': custo.hospedagem,
            'outros': custo.outros,
            'descricao_outros': custo.descricao_outros
        })
    return jsonify({'success': False, 'message': 'Nenhuma despesa encontrada.'})

@app.route('/api/viagem/<int:viagem_id>/abastecimentos', methods=['GET'])
@login_required
def get_viagem_abastecimentos(viagem_id):
    """Busca o último abastecimento de uma viagem para preencher o formulário."""
    abastecimento = Abastecimento.query.filter_by(viagem_id=viagem_id).order_by(Abastecimento.data_abastecimento.desc()).first()
    if abastecimento:
        return jsonify({
            'success': True,
            'litros': abastecimento.litros,
            'preco_por_litro': abastecimento.preco_por_litro,
            'odometro': abastecimento.odometro
        })
    return jsonify({'success': False, 'message': 'Nenhum abastecimento encontrado.'})



@app.route('/exportar_relatorio')
@login_required
def exportar_relatorio():
    try:
        data_inicio = request.args.get('data_inicio', '')
        data_fim = request.args.get('data_fim', '')
        motorista_id_filter = request.args.get('motorista_id', '') # Renomeado
        status_filter = request.args.get('status', '')

        query = Viagem.query

        if data_inicio:
            query = query.filter(Viagem.data_inicio >= datetime.strptime(data_inicio, '%Y-%m-%d'))
        if data_fim:
            query = query.filter(Viagem.data_inicio <= datetime.strptime(data_fim, '%Y-%m-%d'))
        if motorista_id_filter:
            query = query.filter_by(motorista_id=motorista_id_filter)
        if status_filter:
            query = query.filter_by(status=status_filter)

        viagens = query.outerjoin(Motorista).outerjoin(Veiculo).all() # Usar outerjoin para não excluir viagens sem motorista formal

        output = io.BytesIO()
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Relatório Financeiro"

        headers = [
            "ID", "Data", "Cliente", "Motorista", "Veículo",
            "Distância (km)", "Receita (R$)", "Custo (R$)", "Lucro (R$)",
            "Forma Pagamento", "Status"
        ]
        
        for col_num, header in enumerate(headers, 1):
            sheet.cell(row=1, column=col_num, value=header).font = Font(bold=True)

        for row_num, viagem in enumerate(viagens, 2):
            receita = viagem.valor_recebido or 0
            custo = viagem.custo or 0
            lucro = receita - custo
            
            motorista_nome = 'N/A'
            if viagem.motorista_id:
                motorista_nome = viagem.motorista_formal.nome if viagem.motorista_formal else 'N/A'
            elif viagem.motorista_cpf_cnpj:
                usuario_com_cpf = Usuario.query.filter_by(cpf_cnpj=viagem.motorista_cpf_cnpj).first()
                if usuario_com_cpf:
                    motorista_nome = f"{usuario_com_cpf.nome} {usuario_com_cpf.sobrenome}"
                else:
                    motorista_formal_cpf = Motorista.query.filter_by(cpf_cnpj=viagem.motorista_cpf_cnpj).first()
                    if motorista_formal_cpf:
                        motorista_nome = motorista_formal_cpf.nome
            
            veiculo_info = f"{viagem.veiculo.placa} - {viagem.veiculo.modelo}" if viagem.veiculo else 'N/A'

            sheet.cell(row=row_num, column=1, value=viagem.id)
            sheet.cell(row=row_num, column=2, value=viagem.data_inicio.strftime('%d/%m/%Y'))
            sheet.cell(row=row_num, column=3, value=viagem.cliente)
            sheet.cell(row=row_num, column=4, value=motorista_nome) # Usar o nome processado
            sheet.cell(row=row_num, column=5, value=veiculo_info) # Usar info do veículo processada
            sheet.cell(row=row_num, column=6, value=viagem.distancia_km or 0)
            sheet.cell(row=row_num, column=7, value=receita)
            sheet.cell(row=row_num, column=8, value=custo)
            sheet.cell(row=row_num, column=9, value=lucro)
            sheet.cell(row=row_num, column=10, value=viagem.forma_pagamento or '')
            sheet.cell(row=row_num, column=11, value=viagem.status)

        workbook.save(output)
        output.seek(0)

        return send_file(
            output,
            as_attachment=True,
            download_name=f"relatorio_financeiro_{datetime.now().strftime('%Y%m%d')}.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except Exception as e:
        logger.error(f"Erro ao exportar relatório: {str(e)}", exc_info=True)
        flash('Erro ao gerar relatório em Excel', 'error')
        return redirect(url_for('relatorios'))

@app.route('/get_active_trip')
@login_required
def get_active_trip():
    viagem = Viagem.query.filter_by(data_fim=None, status='em_andamento').first()
    if viagem:
        horario_chegada = (viagem.data_inicio + timedelta(seconds=viagem.duracao_segundos)).strftime('%d/%m/%Y %H:%M') if viagem.duracao_segundos else 'Não calculado'
        
        motorista_nome = 'N/A'
        if viagem.motorista_id:
            motorista_nome = viagem.motorista_formal.nome if viagem.motorista_formal else 'N/A'
        elif viagem.motorista_cpf_cnpj:
            usuario_com_cpf = Usuario.query.filter_by(cpf_cnpj=viagem.motorista_cpf_cnpj).first()
            if usuario_com_cpf:
                motorista_nome = f"{usuario_com_cpf.nome} {usuario_com_cpf.sobrenome}"
            else:
                motorista_formal_cpf = Motorista.query.filter_by(cpf_cnpj=viagem.motorista_cpf_cnpj).first()
                if motorista_formal_cpf:
                    motorista_nome = motorista_formal_cpf.nome

        trip_data = {
            'trip': {
                'motorista_nome': motorista_nome,
                'veiculo_placa': viagem.veiculo.placa if viagem.veiculo else 'N/A',
                'veiculo_modelo': viagem.veiculo.modelo if viagem.veiculo else 'N/A',
                'endereco_saida': viagem.endereco_saida,
                'endereco_destino': viagem.endereco_destino,
                'horario_chegada': horario_chegada
            }
        }
        return jsonify(trip_data)
    return jsonify({'trip': None})

@app.route('/create_admin')
def create_admin():
    if not Usuario.query.filter_by(email='adminadmin@admin.com').first():
        admin = Usuario(
            nome='Admin',
            sobrenome='Admin',
            email='adminadmin@admin.com',
            telefone='11999999999',
            role='Admin',
            is_admin=True,
            cpf_cnpj='00000000000' # CPF/CNPJ padrão para admin
        )
        admin.set_password('admin123')
        db.session.add(admin)
        db.session.commit()
        return 'Usuário admin criado!'
    return 'Usuário já existe'


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Você saiu do sistema com segurança.', 'success')
    return redirect(url_for('login'))

@app.route('/configuracoes', methods=['GET', 'POST'])
@login_required
def configuracoes():
    if request.method == 'POST':
        nome = request.form.get('nome', '').strip()
        sobrenome = request.form.get('sobrenome', '').strip()
        idioma = request.form.get('idioma', '').strip()

        if not nome or not sobrenome:
            flash('Nome e sobrenome são obrigatórios.', 'error')
            return redirect(url_for('configuracoes'))
        
        if idioma not in ['Português', 'Inglês', 'Espanhol']:
            flash('Idioma inválido.', 'error')
            return redirect(url_for('configuracoes'))

        current_user.nome = nome
        current_user.sobrenome = sobrenome
        current_user.idioma = idioma

        try:
            db.session.commit()
            flash('Configurações pessoais atualizadas com sucesso!', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao atualizar configurações: {str(e)}', 'error')

        return redirect(url_for('configuracoes'))

    usuarios = []
    empresa = None

    if current_user.empresa_id:
        empresa = db.session.get(Empresa, current_user.empresa_id)

    if current_user.is_admin and current_user.empresa_id:
        usuarios = Usuario.query.filter_by(empresa_id=current_user.empresa_id).all()
    elif current_user.is_admin:
        usuarios = [current_user]

    return render_template('configuracoes.html', usuarios=usuarios, empresa=empresa)


@app.route('/editar_usuario/<int:usuario_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def editar_usuario(usuario_id):
    usuario = Usuario.query.get_or_404(usuario_id)

    if request.method == 'POST':
        nome = request.form.get('nome', '').strip()
        sobrenome = request.form.get('sobrenome', '').strip()
        email = request.form.get('email', '').strip()
        role = request.form.get('role', '').strip()
        senha = request.form.get('senha', '').strip()
        cpf_cnpj = request.form.get('cpf_cnpj', '').strip() # Pega CPF/CNPJ do form

        if not nome or not sobrenome or not email or not role:
            flash('Todos os campos obrigatórios devem ser preenchidos.', 'error')
            return redirect(url_for('editar_usuario', usuario_id=usuario_id))

        if role not in ['Motorista', 'Master', 'Admin']:
            flash('Papel inválido.', 'error')
            return redirect(url_for('editar_usuario', usuario_id=usuario_id))

        if email != usuario.email and Usuario.query.filter_by(email=email).first():
            flash('E-mail já cadastrado.', 'error')
            return redirect(url_for('editar_usuario', usuario_id=usuario_id))
        
        if cpf_cnpj and cpf_cnpj != usuario.cpf_cnpj and Usuario.query.filter_by(cpf_cnpj=cpf_cnpj).first():
            flash('CPF/CNPJ já cadastrado para outro usuário.', 'error')
            return redirect(url_for('editar_usuario', usuario_id=usuario_id))

        usuario.nome = nome
        usuario.sobrenome = sobrenome
        usuario.email = email
        usuario.role = role
        usuario.is_admin = (role == 'Admin')
        usuario.cpf_cnpj = cpf_cnpj if cpf_cnpj else None # Atualiza CPF/CNPJ do usuário

        if senha:
            usuario.set_password(senha)

        try:
            db.session.commit()
            flash('Usuário atualizado com sucesso!', 'success')
            return redirect(url_for('configuracoes'))
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao atualizar usuário: {str(e)}', 'error')
            return redirect(url_for('editar_usuario', usuario_id=usuario_id))

    return render_template('editar_usuario.html', usuario=usuario)




@app.route('/gerenciar_empresa', methods=['GET', 'POST'])
@login_required
def gerenciar_empresa():
    
    if current_user.role not in ['Admin', 'Master']:
        flash('Acesso negado. Apenas administradores podem gerenciar a empresa.', 'error')
        return redirect(url_for('index'))

    empresa = db.session.get(Empresa, current_user.empresa_id) if current_user.empresa_id else None

    if request.method == 'POST':
        cnpj = re.sub(r'\D', '', request.form.get('cnpj', '')) 

        if not validate_cpf_cnpj(cnpj, 'juridica'):
            flash('CNPJ inválido. Deve conter 14 dígitos numéricos.', 'error')
            
            return render_template('gerenciar_empresa.html', empresa=request.form)

        
        empresa_existente = Empresa.query.filter(Empresa.cnpj == cnpj).first()
        if empresa_existente and (not empresa or empresa.id != empresa_existente.id):
            flash('Este CNPJ já está cadastrado em outra empresa.', 'error')
            return render_template('gerenciar_empresa.html', empresa=request.form)

        if empresa:
            # --- LÓGICA DE ATUALIZAÇÃO ---
            empresa.razao_social = request.form.get('razao_social').strip()
            empresa.nome_fantasia = request.form.get('nome_fantasia').strip()
            empresa.cnpj = cnpj
            empresa.inscricao_estadual = request.form.get('inscricao_estadual').strip()
            empresa.endereco = request.form.get('endereco').strip()
            empresa.cidade = request.form.get('cidade').strip()
            empresa.estado = request.form.get('estado').strip().upper()
            empresa.cep = re.sub(r'\D', '', request.form.get('cep', ''))
            empresa.telefone = re.sub(r'\D', '', request.form.get('telefone', ''))
            empresa.email_contato = request.form.get('email_contato').strip()
            flash('Dados da empresa atualizados com sucesso!', 'success')
        else:
            # --- LÓGICA DE CRIAÇÃO ---
            nova_empresa = Empresa(
                razao_social=request.form.get('razao_social').strip(),
                nome_fantasia=request.form.get('nome_fantasia').strip(),
                cnpj=cnpj,
                inscricao_estadual=request.form.get('inscricao_estadual').strip(),
                endereco=request.form.get('endereco').strip(),
                cidade=request.form.get('cidade').strip(),
                estado=request.form.get('estado').strip().upper(),
                cep=re.sub(r'\D', '', request.form.get('cep', '')),
                telefone=re.sub(r'\D', '', request.form.get('telefone', '')),
                email_contato=request.form.get('email_contato').strip()
            )
            db.session.add(nova_empresa)
            db.session.flush() 

            
            current_user.empresa_id = nova_empresa.id
            flash('Empresa cadastrada com sucesso!', 'success')
        
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            flash(f'Ocorreu um erro ao salvar os dados: {e}', 'error')
            return render_template('gerenciar_empresa.html', empresa=request.form)
            
        return redirect(url_for('configuracoes'))

    # --- LÓGICA GET ---
    # Mostra o formulário preenchido para edição ou vazio para criação
    return render_template('gerenciar_empresa.html', empresa=empresa)


@app.route('/excluir_usuario/<int:usuario_id>')
@login_required
@admin_required
def excluir_usuario(usuario_id):
    usuario = Usuario.query.get_or_404(usuario_id)
    if usuario.id == current_user.id:
        flash('Você não pode excluir sua própria conta.', 'error')
        return redirect(url_for('configuracoes'))

    try:
        db.session.delete(usuario)
        db.session.commit()
        flash('Usuário excluído com sucesso!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao excluir usuário: {str(e)}', 'error')
    return redirect(url_for('configuracoes'))

def master_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or (current_user.role not in ['Admin', 'Master']):
            flash('Acesso restrito a administradores ou masters.', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

from sqlalchemy import or_

@app.route('/motorista_dashboard')
@login_required
def motorista_dashboard():
    if current_user.role != 'Motorista':
        flash('Acesso negado. Esta página é restrita a motoristas.', 'error')
        return redirect(url_for('index'))

    motorista = Motorista.query.filter_by(
        cpf_cnpj=current_user.cpf_cnpj,
        empresa_id=current_user.empresa_id
    ).first()

    # CORREÇÃO AQUI: Buscamos a viagem primeiro, sem o 'joinedload'
    viagem_ativa = Viagem.query.filter(
        or_(
            Viagem.motorista_id == (motorista.id if motorista else None),
            Viagem.motorista_cpf_cnpj == current_user.cpf_cnpj
        ),
        Viagem.status == 'em_andamento'
    ).first()

    destinos_com_coords = []
    # Só processamos os destinos se a viagem realmente existir
    if viagem_ativa:
        for destino in sorted(viagem_ativa.destinos, key=lambda x: x.ordem):
            lat, lon = get_coordinates(destino.endereco)
            destinos_com_coords.append({
                'endereco': destino.endereco,
                'latitude': lat,
                'longitude': lon
            })

    # A busca pelo histórico permanece a mesma
    viagens_concluidas = Viagem.query.filter(
        or_(
            Viagem.motorista_id == (motorista.id if motorista else None),
            Viagem.motorista_cpf_cnpj == current_user.cpf_cnpj
        ),
        Viagem.status.in_(['concluida', 'cancelada'])
    ).order_by(Viagem.data_inicio.desc()).all()

    return render_template(
        'motorista_dashboard.html',
        viagem_ativa=viagem_ativa,
        viagens=viagens_concluidas,
        destinos_viagem_ativa=destinos_com_coords
    )


@app.route('/atualizar_localizacao', methods=['POST'])
@login_required
def atualizar_localizacao():
    data = request.get_json()
    lat = data.get('latitude')
    lon = data.get('longitude')
    viagem_id = data.get('viagem_id')

    if not lat or not lon:
        return jsonify({'success': False, 'message': 'Coordenadas inválidas.'})

    try:
        endereco = get_address_geoapify(lat, lon)

        # Buscar o motorista formal vinculado ao usuário logado pelo cpf_cnpj
        motorista_formal = Motorista.query.filter_by(cpf_cnpj=current_user.cpf_cnpj).first()
        motorista_id_para_localizacao = motorista_formal.id if motorista_formal else None

        if not motorista_id_para_localizacao:
            logger.warning(f"Usuário {current_user.email} tentou atualizar localização sem motorista formal vinculado por CPF/CNPJ.")
            return jsonify({'success': False, 'message': 'Motorista formal não encontrado para vincular localização.'})


        nova_localizacao = Localizacao(
            motorista_id=motorista_id_para_localizacao,
            viagem_id=viagem_id,
            latitude=lat,
            longitude=lon,
            endereco=endereco
        )
        db.session.add(nova_localizacao)
        db.session.commit()

        return jsonify({'success': True, 'endereco': endereco})
    except Exception as e:
        logger.error(f"Erro ao atualizar localização: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'message': str(e)})


@app.route('/selecionar_viagem/<int:viagem_id>', methods=['POST'])
@login_required
def selecionar_viagem(viagem_id):
    if current_user.role != 'Motorista':
        return jsonify({'success': False, 'message': 'Acesso negado'})
    
    if not current_user.cpf_cnpj:
        return jsonify({'success': False, 'message': 'Seu perfil de usuário não possui CPF/CNPJ. Preencha-o nas configurações para iniciar viagens.'})

    Viagem.query.filter_by(id=viagem_id, empresa_id=current_user.empresa_id).first_or_404()

    if not viagem:
        return jsonify({'success': False, 'message': 'Viagem não encontrada'})

    if viagem.status != 'pendente': # 'Pendente' precisa ser 'pendente' conforme o default do modelo
        return jsonify({'success': False, 'message': 'Viagem já foi iniciada ou está em outro status'})

    viagem.motorista_cpf_cnpj = current_user.cpf_cnpj # Vincula pelo CPF/CNPJ do usuário
    
    # Opcional: Se o usuário logado tiver um motorista formal vinculado, use o ID desse motorista também
    motorista_formal = Motorista.query.filter_by(usuario_id=current_user.id, cpf_cnpj=current_user.cpf_cnpj).first()
    if motorista_formal:
        viagem.motorista_id = motorista_formal.id # Linka com o ID do motorista formal se ele existir

    viagem.status = 'em_andamento' # 'Ativa' precisa ser 'em_andamento' conforme o default do modelo
    viagem.data_inicio = datetime.utcnow()

    db.session.commit()

    return jsonify({'success': True})

@app.route('/viagens_pendentes', methods=['GET'])
@login_required
def viagens_pendentes():
    if current_user.role != 'Motorista':
        return jsonify({'success': False, 'message': 'Acesso restrito a motoristas.'}), 403

    print("\n--- INICIANDO DIAGNÓSTICO DE VIAGENS PENDENTES ---")
    try:
        print(f"[INFO] Buscando para o usuário: {current_user.email} (Empresa ID: {current_user.empresa_id}, CPF: {current_user.cpf_cnpj})")

        # PASSO 1: O sistema tenta encontrar o perfil "Motorista" que corresponde ao "Usuário" logado.
        motorista_formal = Motorista.query.filter_by(
            cpf_cnpj=current_user.cpf_cnpj, 
            empresa_id=current_user.empresa_id
        ).first()

        if not motorista_formal:
            print("[ERRO CRÍTICO] Nenhum perfil de 'Motorista' encontrado com o CPF/CNPJ e Empresa do usuário logado. A busca não pode continuar.")
            return jsonify({'success': True, 'viagens': []})
        
        motorista_id = motorista_formal.id
        print(f"[INFO] Perfil de motorista formal encontrado: ID={motorista_id}, Nome='{motorista_formal.nome}'")

        # PASSO 2: O sistema agora busca no banco de dados por viagens que cumpram as 3 regras.
        print("[INFO] Executando a query final para encontrar viagens que sejam:")
        print(f"       1. Da empresa ID: {current_user.empresa_id}")
        print(f"       2. Com status: 'pendente'")
        print(f"       3. E para o motorista ID: {motorista_id} (ou CPF/CNPJ: {current_user.cpf_cnpj})")

        viagens_encontradas = Viagem.query.filter(
            Viagem.status == 'pendente',
            Viagem.empresa_id == current_user.empresa_id,
            or_(
                Viagem.motorista_id == motorista_id,
                Viagem.motorista_cpf_cnpj == current_user.cpf_cnpj
            )
        ).all()
        
        if viagens_encontradas:
            print(f"[SUCESSO] Foram encontradas {len(viagens_encontradas)} viagem(ns) pendentes para este motorista.")
        else:
            print("[FALHA] Nenhuma viagem encontrada que cumpra TODOS os três critérios acima.")

        print("--- FIM DO DIAGNÓSTICO ---\n")

        # A lógica para retornar o JSON continua a mesma
        viagens_data = []
        for v in viagens_encontradas:
            lista_de_destinos = [d.endereco for d in sorted(v.destinos, key=lambda x: x.ordem)]
            viagens_data.append({'id': v.id, 'cliente': v.cliente, 'endereco_saida': v.endereco_saida, 'destinos': lista_de_destinos})
        return jsonify({'success': True, 'viagens': viagens_data})
        
    except Exception as e:
        print(f"[ERRO GERAL] Ocorreu uma exceção inesperada: {str(e)}")
        logger.error(f"Erro ao obter viagens pendentes para o motorista {current_user.id}: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'message': 'Erro interno ao processar a solicitação.'}), 500
    
def seed_database(force=False):
    try:
        if force:
            logger.info("Forçando recriação de todas as tabelas do banco de dados...")
            # O jeito correto e seguro de apagar e recriar as tabelas com SQLAlchemy
            db.drop_all()
            db.create_all()
            logger.info("Tabelas recriadas com sucesso.")

        with app.app_context():
            # Se o banco de dados não tiver usuários, ele será populado.
            if Usuario.query.count() == 0:
                logger.info("Iniciando semeação completa do banco de dados...")

                # 1. Criar Empresa de Exemplo
                empresa_exemplo = Empresa(
                    razao_social="TrackGo Logistica LTDA",
                    nome_fantasia="TrackGo",
                    cnpj="11222333000144",
                    inscricao_estadual="123456789",
                    endereco="Rua da Tecnologia, 123",
                    cidade="Curitiba",
                    estado="PR",
                    cep="80000100",
                    telefone="41999998888",
                    email_contato="contato@trackgo.com"
                )
                db.session.add(empresa_exemplo)
                db.session.commit()
                logger.info("Empresa de exemplo criada.")

                # 2. Criar Usuários
                admin = Usuario(
                    nome="João", sobrenome="Admin", email="admin@trackgo.com",
                    role="Admin", is_admin=True, telefone="11987654321",
                    cpf_cnpj="00000000000", empresa_id=empresa_exemplo.id
                )
                admin.set_password("admin123")

                master = Usuario(
                    nome="Maria", sobrenome="Master", email="master@trackgo.com",
                    role="Master", telefone="11987654322",
                    cpf_cnpj="11111111111", empresa_id=empresa_exemplo.id
                )
                master.set_password("master123")

                motorista1_user = Usuario(
                    nome="Carlos", sobrenome="Silva", email="carlos@trackgo.com",
                    role="Motorista", telefone="11987654323",
                    cpf_cnpj="12345678901", empresa_id=empresa_exemplo.id
                )
                motorista1_user.set_password("motorista123")

                db.session.add_all([admin, master, motorista1_user])
                db.session.commit()
                logger.info("Usuários criados.")

                # 3. Criar Clientes
                cliente_exemplo_1 = Cliente(
                    pessoa_tipo="juridica", nome_razao_social="Indústrias ACME S.A.",
                    nome_fantasia="ACME", cpf_cnpj="99888777000166", inscricao_estadual="ISENTO",
                    cep="80230010", logradouro="Avenida Sete de Setembro", numero="3000",
                    bairro="Centro", cidade="Curitiba", estado="PR", email="compras@acme.com",
                    telefone="4133221100", cadastrado_por_id=admin.id,
                    empresa_id=empresa_exemplo.id
                )
                db.session.add(cliente_exemplo_1)
                db.session.commit()
                logger.info("Clientes criados.")

                # 4. Criar Motoristas
                motorista1_db = Motorista(
                    nome="Carlos Silva", data_nascimento=datetime(1985, 5, 15).date(),
                    endereco="Rua das Flores, 123, São Paulo, SP", pessoa_tipo="fisica",
                    cpf_cnpj="12345678901", rg="123456789", telefone="11987654323",
                    cnh="98765432101", validade_cnh=datetime(2026, 12, 31).date(),
                    usuario_id=motorista1_user.id,
                    empresa_id=empresa_exemplo.id
                )
                db.session.add(motorista1_db)
                db.session.commit()
                logger.info("Motoristas formais criados.")

                # 5. Criar Veículos
                veiculo1 = Veiculo(
                    placa="ABC1234", categoria="Caminhão", modelo="Volvo FH", ano=2020,
                    empresa_id=empresa_exemplo.id
                )
                db.session.add(veiculo1)
                db.session.commit()
                logger.info("Veículos criados.")

                logger.info("Semeação do banco de dados concluída com sucesso!")
            else:
                logger.info("Banco de dados já contém dados. Semeação não foi executada.")

    except Exception as e:
        db.session.rollback()
        logger.error(f"Erro ao semear o banco de dados: {e}", exc_info=True)
        raise

def get_address_geoapify(lat, lon):
    try:
        url = f'https://api.geoapify.com/v1/geocode/reverse?lat={lat}&lon={lon}&apiKey={GEOAPIFY_API_KEY}'
        response = requests.get(url)
        response.raise_for_status() 
        data = response.json()
        if data['features']:
            return data['features'][0]['properties']['formatted']
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro de rede/API na geocodificação Geoapify: {str(e)}", exc_info=True)
    except Exception as e:
        logger.error(f"Erro inesperado na geocodificação Geoapify: {str(e)}", exc_info=True)
    return "Endereço não encontrado"



@app.route('/ultima_localizacao/<int:viagem_id>', methods=['GET'])
def ultima_localizacao(viagem_id):
    """Retorna a última localização registrada para uma viagem, incluindo coordenadas."""
    localizacao = Localizacao.query.filter_by(viagem_id=viagem_id).order_by(Localizacao.timestamp.desc()).first()
    
    if localizacao:
        return jsonify({
            'success': True, 
            'endereco': localizacao.endereco,
            'latitude': localizacao.latitude,    # <-- LINHA ADICIONADA
            'longitude': localizacao.longitude   # <-- LINHA ADICIONADA
        })
    
    return jsonify({'success': False, 'message': 'Nenhuma localização encontrada para esta viagem.'})


@app.route('/motorista/<int:motorista_id>/perfil')
@login_required
def perfil_motorista(motorista_id):
    # CORREÇÃO: Garante que o admin só veja motoristas da sua empresa.
    motorista = Motorista.query.filter_by(id=motorista_id, empresa_id=current_user.empresa_id).first_or_404()

    viagens = Viagem.query.filter(Viagem.motorista_id == motorista.id)\
        .order_by(Viagem.data_inicio.desc()).all()

    # Lógica de cálculo de estatísticas.
    total_receita = sum(v.valor_recebido or 0 for v in viagens)
    total_custo = sum(v.custo or 0 for v in viagens)

    stats = {
        'total_viagens': len(viagens),
        'total_distancia': round(sum(v.distancia_km or 0 for v in viagens), 2),
        'total_receita': round(total_receita, 2),
        'total_custo': round(total_custo, 2),
        'lucro_total': round(total_receita - total_custo, 2)
    }

    return render_template('perfil_motorista.html', motorista=motorista, viagens=viagens, stats=stats)

@app.route('/romaneio/viagem/<int:viagem_id>', methods=['GET', 'POST'])
@login_required
def gerar_romaneio(viagem_id):
    viagem = Viagem.query.filter_by(id=viagem_id, empresa_id=current_user.empresa_id).first_or_404()
    romaneio = Romaneio.query.filter_by(viagem_id=viagem_id, empresa_id=current_user.empresa_id).first()

    if request.method == 'POST':
        try:
            data_emissao_str = request.form.get('data_emissao')
            observacoes = request.form.get('observacoes')
            data_emissao = datetime.strptime(data_emissao_str, '%Y-%m-%d').date() if data_emissao_str else datetime.utcnow().date()

            if romaneio:
                romaneio.data_emissao = data_emissao
                romaneio.observacoes = observacoes
                ItemRomaneio.query.filter_by(romaneio_id=romaneio.id).delete()
                flash_message = 'Romaneio atualizado com sucesso!'
            else:
                romaneio = Romaneio(
                    viagem_id=viagem.id,
                    data_emissao=data_emissao,
                    observacoes=observacoes,
                    empresa_id=current_user.empresa_id
                )
                db.session.add(romaneio)
                db.session.flush()
                flash_message = 'Romaneio salvo com sucesso!'

            item_counter = 1
            while f'produto_{item_counter}' in request.form:
                produto = request.form.get(f'produto_{item_counter}')
                if produto:
                    item = ItemRomaneio(
                        romaneio_id=romaneio.id,
                        produto_descricao=produto,
                        quantidade=int(request.form.get(f'qtd_{item_counter}', 1)),
                        embalagem=request.form.get(f'embalagem_{item_counter}'),
                        peso_kg=float(request.form.get(f'peso_{item_counter}', 0))
                    )
                    db.session.add(item)
                item_counter += 1
                
            db.session.commit()
            flash(flash_message, 'success')
            return redirect(url_for('gerar_romaneio', viagem_id=viagem_id))
        
        except Exception as e:
            db.session.rollback()
            logger.error(f"Erro ao salvar romaneio para viagem {viagem_id}: {e}", exc_info=True)
            flash(f"Ocorreu um erro inesperado ao salvar o romaneio: {e}", "error")
            return redirect(url_for('gerar_romaneio', viagem_id=viagem_id))

    if romaneio:
        return render_template('cadastro_romaneio.html', viagem=viagem, romaneio=romaneio)
    else:
        motorista_nome = 'N/A'
        if viagem.motorista_formal:
            motorista_nome = viagem.motorista_formal.nome
        elif viagem.motorista_cpf_cnpj:
            usuario = Usuario.query.filter_by(cpf_cnpj=viagem.motorista_cpf_cnpj).first()
            if usuario: motorista_nome = f"{usuario.nome} {usuario.sobrenome}"
        
        dados_novo_romaneio = {
            'dest_nome': viagem.cliente,
            'dest_endereco': viagem.endereco_destino,
            'transportadora': motorista_nome,
            'placa_veiculo': viagem.veiculo.placa
        }
        ultimo_id = db.session.query(db.func.max(Romaneio.id)).scalar() or 0
        
        return render_template(
            'cadastro_romaneio.html', 
            viagem=viagem, 
            romaneio=None,
            dados=dados_novo_romaneio,
            numero_romaneio=ultimo_id + 1
        )
@app.route('/consultar_romaneios')
@login_required
def consultar_romaneios():
    search_query = request.args.get('search', '').strip()
    query = Romaneio.query.join(Viagem)  # Join com Viagem para filtros adicionais
    
    if search_query:
        query = query.filter(
            or_(
                Viagem.cliente.ilike(f'%{search_query}%'),
                Viagem.motorista_formal.has(Motorista.nome.ilike(f'%{search_query}%')),
                Viagem.veiculo.has(Veiculo.placa.ilike(f'%{search_query}%'))
            )
        )
    
    romaneios = query.order_by(Romaneio.data_emissao.desc()).all()
    
    return render_template(
        'consultar_romaneios.html',
        romaneios=romaneios,
        search_query=search_query,
        active_page='consultar_romaneios'
    )

@socketio.on('join_trip_room')
def handle_join_trip_room(data):
    viagem_id = data.get('viagem_id')
    if viagem_id:
        # Sala para o admin/consultas
        join_room(f"trip_{viagem_id}")
        logger.info(f"Cliente {request.sid} entrou na sala de consulta trip_{viagem_id}")
        
        # Sala específica para o motorista da viagem
        join_room(f"driver_{viagem_id}")
        logger.info(f"Cliente {request.sid} entrou na sala do motorista driver_{viagem_id}")

@app.route('/api/cliente/<int:cliente_id>/details')
@login_required
def get_cliente_details_api(cliente_id):

    cliente = Cliente.query.filter_by(id=cliente_id, empresa_id=current_user.empresa_id).first_or_404()
    
    
    viagens = Viagem.query.filter_by(cliente=cliente.nome_razao_social, empresa_id=current_user.empresa_id).options(
        db.joinedload(Viagem.custo_viagem),
        db.joinedload(Viagem.abastecimentos)
    ).order_by(Viagem.data_inicio.desc()).all()

    total_receita = 0
    total_custo_detalhado = 0
    for v in viagens:
        total_receita += v.valor_recebido or 0
        custo_despesas = (v.custo_viagem.pedagios or 0) + (v.custo_viagem.alimentacao or 0) + (v.custo_viagem.hospedagem or 0) + (v.custo_viagem.outros or 0) if v.custo_viagem else 0
        custo_abastecimento = sum(a.custo_total for a in v.abastecimentos)
        total_custo_detalhado += custo_despesas + custo_abastecimento
    
    stats = {
        'total_viagens': len(viagens),
        'total_receita': round(total_receita, 2),
        'total_custo': round(total_custo_detalhado, 2),
        'lucro_total': round(total_receita - total_custo_detalhado, 2)
    }

    viagens_data = [{
        'id': v.id,
        'data_inicio': v.data_inicio.strftime('%d/%m/%Y'),
        'origem': v.endereco_saida,
        'destino': v.endereco_destino,
        'status': v.status,
        'receita': v.valor_recebido or 0
    } for v in viagens]

    return jsonify({'success': True, 'stats': stats, 'viagens': viagens_data})



@app.template_filter('get_usuario')
def get_usuario(cpf_cnpj):
    return Usuario.query.filter_by(cpf_cnpj=cpf_cnpj).first()

@app.route('/romaneio/<int:romaneio_id>', methods=['GET'])
@login_required
def visualizar_romaneio(romaneio_id):
    # 1. Busca o romaneio garantindo que pertence à empresa do usuário
    romaneio = (
        Romaneio.query
        .join(Viagem)
        .filter(Romaneio.id == romaneio_id, Viagem.empresa_id == current_user.empresa_id)
        .first_or_404()
    )

    # 2. CORREÇÃO DA BUSCA PELA EMPRESA: Usa o método moderno e seguro
    empresa = db.session.get(Empresa, romaneio.viagem.empresa_id)

    # 3. CORREÇÃO DA BUSCA PELO CLIENTE: Busca o objeto Cliente completo para ter acesso ao CNPJ
    cliente_obj = Cliente.query.filter_by(
        nome_razao_social=romaneio.viagem.cliente, 
        empresa_id=current_user.empresa_id
    ).first()

    # 4. CORREÇÃO DA ORDENAÇÃO: Usa a função sorted() do Python para ordenar a lista de destinos
    destinos_ordenados = sorted(romaneio.viagem.destinos, key=lambda d: d.ordem)
    
    # Monta a lista de endereços para o template
    lista_enderecos = [d.endereco for d in destinos_ordenados]

    return render_template(
        'visualizar_romaneio.html',
        romaneio=romaneio,
        empresa=empresa,
        cliente=cliente_obj,  # Passa o objeto cliente completo para o template
        destinos=lista_enderecos, # Passa a lista de endereços já ordenada
        active_page='consultar_romaneios'
    )

import click

@app.cli.command("create-owner")
@click.argument("email")
@click.argument("password")
def create_owner_command(email, password):
    """Cria um novo usuário com o papel de Owner."""
    
    if Usuario.query.filter_by(email=email).first():
        print(f"Erro: O usuário com o e-mail '{email}' já existe.")
        return

    try:
        owner = Usuario(
            nome='Proprietário',
            sobrenome='do Sistema',
            email=email,
            role='Owner',
            is_admin=True # Um Owner também pode ser admin
        )
        owner.set_password(password)
        db.session.add(owner)
        db.session.commit()
        print(f"Usuário Owner '{email}' criado com sucesso!")
    except Exception as e:
        db.session.rollback()
        print(f"Erro ao criar o usuário Owner: {e}")

@socketio.on('leave_trip_room')
def handle_leave_trip_room(data):
    viagem_id = data.get('viagem_id')
    if viagem_id:
        leave_room(f"trip_{viagem_id}")
        logger.info(f"Cliente {request.sid} saiu da sala trip_{viagem_id}")




@socketio.on('atualizar_localizacao_socket')
def handle_atualizar_localizacao_socket(data):
    latitude = data.get('latitude')
    longitude = data.get('longitude')
    viagem_id = data.get('viagem_id')

    if not all([latitude, longitude, viagem_id]):
        return

    try:
        viagem = db.session.get(Viagem, int(viagem_id))
        if not viagem or not viagem.motorista_id:
            return

        current_time = datetime.utcnow()
        trip_last_geocode = last_geocode_time.get(viagem.id)
        
        should_geocode = False
        if trip_last_geocode is None or (current_time - trip_last_geocode).total_seconds() > GEOCODE_INTERVAL_SECONDS:
            should_geocode = True

        endereco = None
        if should_geocode:
            endereco = get_address_geoapify(latitude, longitude)
            last_geocode_time[viagem.id] = current_time # Atualiza o tempo do último geocode
        
        # Salva a localização no banco de dados. Se não geocodificou, o endereço fica nulo.
        nova_localizacao = Localizacao(
            motorista_id=viagem.motorista_id,
            viagem_id=viagem.id,
            latitude=latitude,
            longitude=longitude,
            endereco=endereco # Pode ser None se should_geocode for False
        )
        db.session.add(nova_localizacao)
        db.session.commit()

        # Prepara os dados para enviar via socket
        socket_data = {
            'viagem_id': viagem.id,
            'latitude': latitude,
            'longitude': longitude
        }
        # Só envia o endereço se ele foi realmente buscado nesta chamada
        if endereco:
            socket_data['endereco'] = endereco

        # Emite para a sala da viagem (para o admin em consultar_viagens)
        emit('localizacao_atualizada', socket_data, room=f"trip_{viagem.id}")
        
        # Emite também para a sala do motorista para atualizar o display de endereço
        # Isso garante que o "Iniciando rastreamento..." seja substituído pelo primeiro endereço
        if endereco:
             emit('localizacao_atualizada', {'endereco': endereco, 'viagem_id': viagem.id}, room=f"driver_{viagem.id}")


    except Exception as e:
        logger.error(f"Erro ao salvar localização via socket: {e}", exc_info=True)
        db.session.rollback()

@app.route('/api/veiculo/<int:veiculo_id>/details')
@login_required
def get_veiculo_details_api(veiculo_id):
    """API que busca todos os detalhes de um veículo para o modal."""
    veiculo = Veiculo.query.filter_by(id=veiculo_id, empresa_id=current_user.empresa_id).first_or_404()
    
    # 1. Busca todos os dados necessários
    viagens = Viagem.query.filter_by(veiculo_id=veiculo.id).options(
        db.joinedload(Viagem.custo_viagem),
        db.joinedload(Viagem.abastecimentos)
    ).all()
    
    manutencoes = Manutencao.query.filter_by(veiculo_id=veiculo.id).order_by(Manutencao.data.desc()).all()
    
    # Busca abastecimentos diretamente pelo veículo, não só pela viagem
    abastecimentos = Abastecimento.query.filter_by(veiculo_id=veiculo.id).order_by(Abastecimento.data_abastecimento.desc()).all()

    # 2. LÓGICA DE CÁLCULO DE KPI CORRIGIDA
    total_km = sum(v.distancia_km for v in viagens if v.distancia_km)
    
    # Soma os custos de todas as viagens
    total_custo_viagens = 0
    for v in viagens:
        custo_despesas = 0
        if v.custo_viagem:
            custo_despesas = (v.custo_viagem.pedagios or 0) + (v.custo_viagem.alimentacao or 0) + (v.custo_viagem.hospedagem or 0) + (v.custo_viagem.outros or 0)
        custo_abastecimento_viagem = sum(a.custo_total for a in v.abastecimentos)
        total_custo_viagens += custo_despesas + custo_abastecimento_viagem

    # Soma os custos de manutenções
    total_custo_manutencao = sum(m.custo for m in manutencoes)
    
    custo_geral = total_custo_viagens + total_custo_manutencao
    custo_por_km = (custo_geral / total_km) if total_km > 0 else 0
    
    kpis = {
        "total_viagens": len(viagens),
        "total_km": round(total_km, 2),
        "custo_geral": round(custo_geral, 2),
        "custo_por_km": round(custo_por_km, 2)
    }

    # 3. Retorna o JSON completo
    return jsonify({
        "success": True,
        "kpis": kpis,
        "manutencoes": [m.to_dict() for m in manutencoes],
        "abastecimentos": [a.to_dict() for a in abastecimentos]
    })


@app.route('/veiculo/<int:veiculo_id>/adicionar_manutencao', methods=['POST'])
@login_required
def adicionar_manutencao(veiculo_id):
    veiculo = Veiculo.query.filter_by(id=veiculo_id, empresa_id=current_user.empresa_id).first_or_404()
    
    try:
        nova_manutencao = Manutencao(
            veiculo_id=veiculo.id,
            empresa_id=current_user.empresa_id,
            data=datetime.strptime(request.form['data'], '%Y-%m-%d').date(),
            odometro=int(request.form['odometro']),
            tipo=request.form['tipo'],
            descricao=request.form['descricao'],
            custo=float(request.form['custo'])
        )

        urls_anexos = []
        if 'anexos' in request.files:
            anexos = request.files.getlist('anexos')
            if anexos and any(anexo and anexo.filename for anexo in anexos):
                
                # --- INÍCIO DA CORREÇÃO ---
                # Este bloco, que cria o s3_client, estava faltando.
                s3_client = boto3.client(
                    's3',
                    endpoint_url=app.config['CLOUDFLARE_R2_ENDPOINT'],
                    aws_access_key_id=app.config['CLOUDFLARE_R2_ACCESS_KEY'],
                    aws_secret_access_key=app.config['CLOUDFLARE_R2_SECRET_KEY'],
                    region_name='auto'
                )
                R2_BUCKET_NAME = app.config['CLOUDFLARE_R2_BUCKET']
                R2_PUBLIC_URL = app.config['CLOUDFLARE_R2_PUBLIC_URL']
                # --- FIM DA CORREÇÃO ---

                for anexo in anexos:
                    if anexo and anexo.filename != '':
                        filename = f"manutencao/{veiculo.id}/{uuid.uuid4()}_{secure_filename(anexo.filename)}"
                        s3_client.upload_fileobj(anexo, R2_BUCKET_NAME, filename, ExtraArgs={'ContentType': anexo.content_type})
                        urls_anexos.append(f"{R2_PUBLIC_URL}/{filename}")
                
                if urls_anexos:
                    nova_manutencao.anexos = ",".join(urls_anexos)

        db.session.add(nova_manutencao)
        db.session.commit()
        flash('Manutenção registrada com sucesso!', 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao registrar manutenção: {str(e)}', 'error')
        logger.error(f"Erro ao adicionar manutenção para veiculo {veiculo_id}: {e}", exc_info=True)

    return redirect(url_for('consultar_veiculos'))


@app.route('/manutencao/<int:manutencao_id>/excluir', methods=['POST'])
@login_required
def excluir_manutencao(manutencao_id):
    manutencao = Manutencao.query.join(Veiculo).filter(
        Manutencao.id == manutencao_id,
        Veiculo.empresa_id == current_user.empresa_id
    ).first_or_404()
    
    # Excluir anexos do R2 se existirem
    if manutencao.anexos:
        for url in manutencao.anexos.split(','):
            try:
                key = url.replace(f"{R2_PUBLIC_URL}/", "")
                s3_client.delete_object(Bucket=R2_BUCKET_NAME, Key=key)
            except Exception as e:
                logger.error(f"Erro ao excluir anexo {url} do R2: {e}")

    db.session.delete(manutencao)
    db.session.commit()
    flash('Registro de manutenção excluído com sucesso.', 'success')
    return redirect(url_for('consultar_veiculos'))

        
@app.route('/manifest.json')
def manifest():
    return send_from_directory('templates', 'manifest.json')

@app.route('/reset-password-tool/<secret_key>', methods=['GET', 'POST'])
def secret_password_reset(secret_key):
    # CHAVE DE SEGURANÇA: Mude isso para qualquer coisa que só você saiba
    # e não compartilhe com ninguém.
    SUPER_SECRET_KEY = "trocar_para_uma_chave_muito_secreta_12345"

    if secret_key != SUPER_SECRET_KEY:
        return "Acesso Negado.", 403

    if request.method == 'POST':
        email = request.form.get('email')
        new_password = request.form.get('new_password')
        
        user = Usuario.query.filter_by(email=email).first()
        
        if not user:
            flash(f"Usuário com e-mail '{email}' não encontrado.", 'error')
            return redirect(url_for('secret_password_reset', secret_key=secret_key))

        try:
            user.set_password(new_password)
            db.session.commit()
            flash(f"Senha para '{email}' foi redefinida com sucesso!", 'success')
        except Exception as e:
            db.session.rollback()
            flash(f"Erro ao redefinir a senha: {e}", 'error')

        return redirect(url_for('secret_password_reset', secret_key=secret_key))

    return render_template('reset_password_page.html')

# Rota para servir o sw.js a partir da raiz do projeto
@app.route('/sw.js')
def service_worker():
    response = make_response(send_from_directory('.', 'sw.js'))
    response.headers['Content-Type'] = 'application/javascript'
    return response



@app.route('/fix-missing-tokens/f9a7b3c8d2e1f4a0b9c8d7e6f5a4b3c2')
def fix_old_trip_tokens():
    """
    Esta rota é uma correção única para gerar tokens para viagens antigas.
    DEVE SER REMOVIDA APÓS O PRIMEIRO USO EM PRODUÇÃO.
    """
    try:
        viagens_sem_token = Viagem.query.filter(Viagem.public_tracking_token.is_(None)).all()
        
        if not viagens_sem_token:
            return "<h1>Tudo certo!</h1><p>Nenhuma viagem precisava de correção.</p>", 200

        count = len(viagens_sem_token)
        for viagem in viagens_sem_token:
            viagem.public_tracking_token = str(uuid.uuid4())
        
        db.session.commit()
        
        return f"<h1>Correção Concluída!</h1><p>{count} viagem(ns) foram atualizadas com sucesso.</p>", 200

    except Exception as e:
        db.session.rollback()
        logger.error(f"Erro ao executar a correção de tokens: {e}")
        return f"<h1>Erro ao executar a correção.</h1><p>Detalhes: {e}</p>", 500
    
@app.after_request
def add_header(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


if __name__ == '__main__':
    # monkey_patch foi removido daqui porque já está no topo do arquivo.

    with app.app_context():
        # A linha abaixo garante que as tabelas sejam criadas se não existirem.
        db.create_all() 
        # A linha abaixo popula o banco com dados de exemplo se ele estiver vazio.
        seed_database(False) 
   
    # Inicia o servidor com suporte a SocketIO e debug
    print("Iniciando o servidor com SocketIO...")
    socketio.run(app, debug=True)