import eventlet
eventlet.monkey_patch()

import uuid
import xml.etree.ElementTree as ET
import json
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file, make_response, send_from_directory
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
from sqlalchemy import or_, and_, func, extract, UniqueConstraint, LargeBinary
from flask_login import UserMixin, login_user, logout_user, login_required, current_user
from num2words import num2words
from collections import defaultdict
import pytesseract
from PIL import Image
import openrouteservice
import click
from pathlib import Path
from functools import wraps
from cryptography.fernet import Fernet
from werkzeug.security import generate_password_hash, check_password_hash

# 1. Importa as extensões E os modelos do novo arquivo 'extensions.py'
from extensions import db, migrate, socketio, login_manager, mail, CertificadoDigital, NFeImportada

# Seus decoradores (sem alterações)
# ... (cole seus 3 decoradores aqui: admin_required, owner_required, master_required) ...

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
    
# Lógica do .env (sem alterações)
env_path = Path(__file__).resolve().with_name('.env')
if not env_path.exists(): raise FileNotFoundError(f'Arquivo .env não encontrado em {env_path}')
load_dotenv(dotenv_path=env_path)

# ... (seu código de validação do .env continua aqui) ...
required_r2 = [
    'CLOUDFLARE_R2_ENDPOINT', 'CLOUDFLARE_R2_ACCESS_KEY', 'CLOUDFLARE_R2_SECRET_KEY',
    'CLOUDFLARE_R2_BUCKET', 'CLOUDFLARE_R2_PUBLIC_URL',
]
missing = [k for k in required_r2 if not os.getenv(k)]
if missing:
    raise ValueError('Variáveis faltando no .env: ' + ', '.join(missing))

print('R2 config carregada:')
for k in required_r2:
    print(f'  {k}: {os.getenv(k)}')

# Criação e configuração da aplicação
app = Flask(__name__)
app.config.update(
    # ... (seu bloco de config continua aqui) ...
    MAIL_SERVER='smtp.gmail.com', MAIL_PORT=587, MAIL_USE_TLS=True,
    MAIL_USERNAME='trackgo789@gmail.com', MAIL_PASSWORD='mmoa moxc juli sfbe',
    MAIL_DEFAULT_SENDER='trackgo789@gmail.com',
    SQLALCHEMY_DATABASE_URI=os.getenv('DATABASE_URL', 'sqlite:///transport.db'),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SECRET_KEY=os.getenv('SECRET_KEY', 'w9z$kL2mNpQvR7tYxJ3hF8gWcPqB5vM2nZ4rT6yU'),
    CLOUDFLARE_R2_ENDPOINT=os.getenv('CLOUDFLARE_R2_ENDPOINT'),
    CLOUDFLARE_R2_ACCESS_KEY=os.getenv('CLOUDFLARE_R2_ACCESS_KEY'),
    CLOUDFLARE_R2_SECRET_KEY=os.getenv('CLOUDFLARE_R2_SECRET_KEY'),
    CLOUDFLARE_R2_BUCKET=os.getenv('CLOUDFLARE_R2_BUCKET'),
    CLOUDFLARE_R2_PUBLIC_URL=os.getenv('CLOUDFLARE_R2_PUBLIC_URL'),
    SEFAZ_AMBIENTE=os.getenv('SEFAZ_AMBIENTE', 'PRODUCAO'),
    NFE_API_URL=os.getenv('NFE_API_URL'),
)
GEOAPIFY_API_KEY = os.getenv('GEOAPIFY_API_KEY', '7cd423ef184f48f0b770682cbebe11d0')
OPENROUTESERVICE_API_KEY = os.getenv('OPENROUTESERVICE_API_KEY')
Maps_API_KEY = os.getenv('Maps_API_KEY')

# Lógica da chave de criptografia
ENCRYPTION_KEY = os.getenv('ENCRYPTION_KEY')
if not ENCRYPTION_KEY: raise ValueError("ENCRYPTION_KEY não definida no .env!")
cipher_suite = Fernet(ENCRYPTION_KEY.encode())
app.cipher_suite = cipher_suite # Associa o cipher ao app para uso posterior



# 2. Seção correta de inicialização das extensões
db.init_app(app)
migrate.init_app(app, db)
socketio.init_app(app, async_mode='eventlet')
mail.init_app(app)
login_manager.init_app(app)
login_manager.login_view = 'login'

# Configurações de logging e variáveis globais
last_geocode_time = {}
GEOCODE_INTERVAL_SECONDS = 600
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 3. Import do sefaz_service após a inicialização
from sefaz_service import consultar_notas_sefaz

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(Usuario, int(user_id))

class Motorista(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Aba: Dados Pessoais
    nome = db.Column(db.String(100), nullable=False)
    telefone = db.Column(db.String(20), nullable=True)
    cpf = db.Column(db.String(14), unique=True, nullable=False, index=True)
    data_nascimento = db.Column(db.Date, nullable=True)  # Mantido como opcional para flexibilidade
    nacionalidade = db.Column(db.String(50), default='Nacional')
    naturalidade = db.Column(db.String(100), nullable=True)
    estado_civil = db.Column(db.String(50), nullable=True)
    sexo = db.Column(db.String(20), nullable=True)
    pai = db.Column(db.String(100), nullable=True)
    mae = db.Column(db.String(100), nullable=True)
    data_admissao = db.Column(db.Date, nullable=True)
    situacao = db.Column(db.String(50), default='NORMAL / LIBERADO')
    data_desativacao = db.Column(db.Date, nullable=True)
    classificacao = db.Column(db.String(50), nullable=True)
    cod_departamento = db.Column(db.String(50), nullable=True)
    numero_ficha = db.Column(db.String(50), nullable=True)
    foto = db.Column(db.String(500), nullable=True)
    anexos = db.Column(db.String(2048), nullable=True)

    # Aba: Endereço
    cep = db.Column(db.String(9), nullable=True)
    tipo_logradouro = db.Column(db.String(50), nullable=True)
    logradouro = db.Column(db.String(255), nullable=True)
    numero = db.Column(db.String(20), nullable=True)
    complemento = db.Column(db.String(100), nullable=True)
    bairro = db.Column(db.String(100), nullable=True)
    cidade = db.Column(db.String(100), nullable=True)
    uf = db.Column(db.String(2), nullable=True)
    email = db.Column(db.String(120), nullable=True)
    tipo_imovel = db.Column(db.String(50), nullable=True)
    tempo_residencia = db.Column(db.String(50), nullable=True)

    # Aba: Documentação
    cnh_numero = db.Column(db.String(30), unique=True, nullable=False, index=True)
    cnh_data_primeira = db.Column(db.Date, nullable=True)
    
    
    cnh_data_vencimento = db.Column(db.Date, nullable=True)
    cnh_categoria = db.Column(db.String(10), nullable=True)
    # --- FIM DA CORREÇÃO ---
    
    cnh_cod_seguranca = db.Column(db.String(20), nullable=True)
    rg = db.Column(db.String(20), nullable=True)
    rg_uf = db.Column(db.String(2), nullable=True)
    pis = db.Column(db.String(20), nullable=True)
    inss = db.Column(db.String(20), nullable=True)
    titulo_eleitor = db.Column(db.String(20), nullable=True)
    ctps = db.Column(db.String(20), nullable=True)
    funcao = db.Column(db.String(100), nullable=True)
    mopp_numero = db.Column(db.String(20), nullable=True)
    mopp_vencimento = db.Column(db.Date, nullable=True)
    salario_base = db.Column(db.Float, nullable=True, default=0.0)

    # Aba: Contatos de Referência
    contato_nome = db.Column(db.String(100), nullable=True)
    contato_tipo_ref = db.Column(db.String(50), nullable=True)
    contato_tipo_fone = db.Column(db.String(50), nullable=True)
    contato_telefone = db.Column(db.String(20), nullable=True)
    contato_operadora = db.Column(db.String(50), nullable=True)
    contato_obs = db.Column(db.String(255), nullable=True)


    
    
    usuario = db.relationship('Usuario', backref='motorista', uselist=False)
    viagens = db.relationship('Viagem', foreign_keys='Viagem.motorista_id', backref='motorista_formal')

    # Propriedades para compatibilidade
    @property
    def cpf_cnpj(self):
        return self.cpf

    @property
    def cnh(self):
        return self.cnh_numero

    @property
    def validade_cnh(self):
        return self.cnh_data_vencimento
    

    
class FolhaPagamento(db.Model):
    __tablename__ = 'folha_pagamento'
    id = db.Column(db.Integer, primary_key=True)
    motorista_id = db.Column(db.Integer, db.ForeignKey('motorista.id'), nullable=False)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)
    
    mes_referencia = db.Column(db.Integer, nullable=False, index=True)
    ano_referencia = db.Column(db.Integer, nullable=False, index=True)
    
    salario_base_registro = db.Column(db.Float, default=0.0) # Salva o salário base no momento da criação
    total_proventos = db.Column(db.Float, default=0.0)
    total_descontos = db.Column(db.Float, default=0.0)
    salario_liquido = db.Column(db.Float, default=0.0)
    
    status = db.Column(db.String(20), default='Em Aberto', index=True) # Em Aberto, Fechada, Paga
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)
    data_fechamento = db.Column(db.DateTime, nullable=True)
    data_pagamento = db.Column(db.DateTime, nullable=True)
    meio_pagamento = db.Column(db.String(50), nullable=True)
    comprovante_url = db.Column(db.String(500), nullable=True)
    observacoes = db.Column(db.Text, nullable=True)
    viagem_id = db.Column(db.Integer, db.ForeignKey('viagem.id'), nullable=True)
    viagem = db.relationship('Viagem') # Facilita o acesso aos dados da viagem

    motorista = db.relationship('Motorista', backref='folhas_pagamento')
    itens = db.relationship('ItemFolhaPagamento', backref='folha', lazy='dynamic', cascade="all, delete-orphan")

    __table_args__ = (db.UniqueConstraint('motorista_id', 'mes_referencia', 'ano_referencia', name='_motorista_mes_ano_uc'),)

    def __repr__(self):
        return f'<FolhaPagamento {self.id} para Motorista {self.motorista_id} - {self.mes_referencia}/{self.ano_referencia}>'

    def recalcular_totais(self):
        """Recalcula os totais com base nos itens."""
        proventos = db.session.query(func.sum(ItemFolhaPagamento.valor)).filter(
            ItemFolhaPagamento.folha_pagamento_id == self.id,
            ItemFolhaPagamento.tipo == 'Provento'
        ).scalar() or 0.0

        descontos = db.session.query(func.sum(ItemFolhaPagamento.valor)).filter(
            ItemFolhaPagamento.folha_pagamento_id == self.id,
            ItemFolhaPagamento.tipo == 'Desconto'
        ).scalar() or 0.0
        
        self.total_proventos = self.salario_base_registro + proventos
        self.total_descontos = descontos
        self.salario_liquido = self.total_proventos - self.total_descontos


class ItemFolhaPagamento(db.Model):
    __tablename__ = 'item_folha_pagamento'
    id = db.Column(db.Integer, primary_key=True)
    folha_pagamento_id = db.Column(db.Integer, db.ForeignKey('folha_pagamento.id'), nullable=False)
    
    # --- LINHA ADICIONADA AQUI ---
    viagem_id = db.Column(db.Integer, db.ForeignKey('viagem.id'), nullable=True, index=True)
    
    tipo = db.Column(db.String(10), nullable=False, index=True) # 'Provento' ou 'Desconto'
    descricao = db.Column(db.String(255), nullable=False)
    valor = db.Column(db.Float, nullable=False)

    def __repr__(self):
        return f'<ItemFolhaPagamento {self.id} - {self.tipo}: {self.descricao}>'

class Manutencao(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    veiculo_id = db.Column(db.Integer, db.ForeignKey('veiculo.id'), nullable=False)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)
    itens = db.relationship('ManutencaoItem', backref='manutencao', lazy=True, cascade="all, delete-orphan")

    data_entrada = db.Column(db.DateTime, default=datetime.utcnow)
    data_saida = db.Column(db.DateTime, nullable=True)
    odometro = db.Column(db.Integer, nullable=False)
    custo_total = db.Column(db.Float, nullable=True)
    servicos_executados = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(50), nullable=False) # Ex: Agendada, Em Andamento, Concluída
    tipo_manutencao = db.Column(db.String(50), nullable=False, default='Corretiva') # Ex: Preventiva, Corretiva
    descricao_problema = db.Column(db.Text, nullable=True)

    veiculo_plano_veiculo_id = db.Column(db.Integer)
    veiculo_plano_plano_id = db.Column(db.Integer)

    __table_args__ = (db.ForeignKeyConstraint(
        ['veiculo_plano_veiculo_id', 'veiculo_plano_plano_id'],
        ['veiculo_plano.veiculo_id', 'veiculo_plano.plano_id'],
    ),)

    veiculo = db.relationship('Veiculo', back_populates='manutencoes')
    veiculo_plano_associado = db.relationship('VeiculoPlano', back_populates='manutencoes')

    def __repr__(self):
        return f'<Manutencao {self.id} para Veiculo {self.veiculo_id}>'


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
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    placa = db.Column(db.String(7), unique=True, nullable=False, index=True)
    modelo = db.Column(db.String(100), nullable=False)
    categoria = db.Column(db.String(50), nullable=True)
    status = db.Column(db.String(50), nullable=True, default='Disponível')
    marca = db.Column(db.String(100), nullable=True)
    ano_fabricacao = db.Column(db.Integer, nullable=True)
    ano_modelo = db.Column(db.Integer, nullable=True)
    cor = db.Column(db.String(50), nullable=True)
    combustivel = db.Column(db.String(50), nullable=True)

    renavam = db.Column(db.String(11), nullable=True, unique=True)
    chassi = db.Column(db.String(17), nullable=True, unique=True)
    numero_motor = db.Column(db.String(50), nullable=True)
    crlv_numero = db.Column(db.String(50), nullable=True)
    crlv_vencimento = db.Column(db.Date, nullable=True)
    seguro_numero = db.Column(db.String(100), nullable=True)
    seguro_seguradora = db.Column(db.String(100), nullable=True)
    seguro_vencimento = db.Column(db.Date, nullable=True)

    capacidade_carga_kg = db.Column(db.Float, nullable=True)
    peso_bruto_total_kg = db.Column(db.Float, nullable=True)
    eixos = db.Column(db.Integer, nullable=True)
    cilindrada = db.Column(db.String(20), nullable=True)
    potencia_cv = db.Column(db.Integer, nullable=True)
    tanque_combustivel_litros = db.Column(db.Integer, nullable=True)
    consumo_medio_km_l = db.Column(db.Float, nullable=True)

    km_atual = db.Column(db.Float, nullable=True)
    valor_aquisicao = db.Column(db.Float, nullable=True)
    data_aquisicao = db.Column(db.Date, nullable=True)
    ultima_manutencao = db.Column(db.Date, nullable=True)
    km_ultima_manutencao = db.Column(db.Float, nullable=True)
    proxima_manutencao = db.Column(db.Date, nullable=True)
    motorista_padrao_id = db.Column(db.Integer, db.ForeignKey('motorista.id'), nullable=True)

    observacoes = db.Column(db.Text, nullable=True)
    fotos_urls = db.Column(db.Text, nullable=True)

    viagens = db.relationship('Viagem', backref='veiculo', lazy=True, cascade="all, delete-orphan")
    manutencoes = db.relationship('Manutencao', back_populates='veiculo', lazy='dynamic', cascade="all, delete-orphan")
    planos_associados = db.relationship('VeiculoPlano', back_populates='veiculo', cascade="all, delete-orphan")

    @property
    def km_rodados(self):
        return self.km_atual

    @property
    def ano(self):
        return self.ano_modelo
        
    def to_dict(self):
        veiculo_display = f"{self.marca or ''} {self.modelo or ''}"
        ano_display = self.ano_modelo or self.ano_fabricacao or "N/A"
        km_display = f"{int(self.km_atual)} km" if self.km_atual is not None else "Não informado"
        data_cadastro_str = self.created_at.isoformat() if self.created_at else None

        return {
            'id': self.id,
            'placa': self.placa,
            'veiculo': veiculo_display.strip(),
            'modelo': self.modelo or '',
            'marca': self.marca or '',
            'ano': ano_display,
            'status': self.status or 'Disponível',
            'created_at': data_cadastro_str,
            'quilometragem': self.km_atual
        }
    
    def __repr__(self):
        return f'<Veiculo {self.marca} {self.modelo} {self.placa}>'
    
class PlanoDeManutencao(db.Model):
    __tablename__ = 'plano_de_manutencao'
    id = db.Column(db.Integer, primary_key=True)
    descricao = db.Column(db.String(150), unique=True, nullable=False)
    intervalo_km_padrao = db.Column(db.Integer)
    intervalo_meses_padrao = db.Column(db.Integer)
    veiculos = db.relationship('VeiculoPlano', back_populates='plano', cascade="all, delete-orphan")
    insumos_associados = db.relationship('PlanoInsumo', back_populates='plano', cascade="all, delete-orphan")

    def __repr__(self):
        return f'<PlanoDeManutencao {self.descricao}>'

class VeiculoPlano(db.Model):
    __tablename__ = 'veiculo_plano'
    veiculo_id = db.Column(db.Integer, db.ForeignKey('veiculo.id'), primary_key=True)
    plano_id = db.Column(db.Integer, db.ForeignKey('plano_de_manutencao.id'), primary_key=True)
    
    intervalo_km = db.Column(db.Integer)
    intervalo_meses = db.Column(db.Integer)
    
    km_ultima_manutencao = db.Column(db.Integer, nullable=True)
    data_ultima_manutencao = db.Column(db.Date, nullable=True)

    veiculo = db.relationship('Veiculo', back_populates='planos_associados')
    plano = db.relationship('PlanoDeManutencao', back_populates='veiculos')
    manutencoes = db.relationship('Manutencao', back_populates='veiculo_plano_associado')

class ManutencaoItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    manutencao_id = db.Column(db.Integer, db.ForeignKey('manutencao.id'), nullable=False)
    data = db.Column(db.Date, nullable=False, default=date.today)
    descricao = db.Column(db.String(255), nullable=False)
    quantidade = db.Column(db.Float, nullable=False, default=1)
    custo_unitario = db.Column(db.Float, nullable=False, default=0)

    @property
    def custo_total_item(self):
        return self.quantidade * self.custo_unitario

class Insumo(db.Model):
    __tablename__ = 'insumo'
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)
    
    descricao = db.Column(db.String(200), nullable=False)
    codigo_peca = db.Column(db.String(50), nullable=True)
    custo_unitario_medio = db.Column(db.Float, nullable=True, default=0.0)
    
    quantidade_em_estoque = db.Column(db.Float, nullable=False, server_default='0')
    ponto_ressuprimento = db.Column(db.Float, nullable=True)

    __table_args__ = (db.UniqueConstraint('descricao', 'empresa_id', name='uq_insumo_descricao_empresa'),)

    def __repr__(self):
        return f'<Insumo {self.descricao}>'

class PlanoInsumo(db.Model):
    __tablename__ = 'plano_insumo'
    plano_id = db.Column(db.Integer, db.ForeignKey('plano_de_manutencao.id'), primary_key=True)
    insumo_id = db.Column(db.Integer, db.ForeignKey('insumo.id'), primary_key=True)
    
    quantidade = db.Column(db.Float, nullable=False, default=1)
    
    plano = db.relationship('PlanoDeManutencao', back_populates='insumos_associados')
    insumo = db.relationship('Insumo')

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
     self.senha_hash = generate_password_hash(password, method='pbkdf2:sha256')

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
    

    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)

    cadastrado_por = db.relationship('Usuario', backref='clientes_cadastrados')

    def __repr__(self):
        return f'<Cliente {self.id}: {self.nome_razao_social}>'
    

class LancamentoFluxoCaixa(db.Model):
    """Lançamentos manuais no fluxo de caixa (receitas e despesas não vinculadas a NFe)"""
    __tablename__ = 'lancamento_fluxo_caixa'
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)
    
    # Dados básicos do lançamento
    tipo = db.Column(db.String(10), nullable=False, index=True)  # 'RECEITA' ou 'DESPESA'
    descricao = db.Column(db.String(255), nullable=False)
    categoria = db.Column(db.String(100), nullable=True)  # Ex: Combustível, Manutenção, Frete, etc.
    valor_total = db.Column(db.Float, nullable=False)
    
    # Controle de datas
    data_lancamento = db.Column(db.DateTime, default=datetime.utcnow)
    data_vencimento = db.Column(db.Date, nullable=False, index=True)
    data_pagamento = db.Column(db.Date, nullable=True)
    
    # Status e controle
    status_pagamento = db.Column(db.String(20), default='PENDENTE', nullable=False, index=True)
    # Status: PENDENTE, PAGO, CANCELADO, VENCIDO
    
    # Dados opcionais
    fornecedor_cliente = db.Column(db.String(255), nullable=True)  # Nome do fornecedor/cliente
    documento_numero = db.Column(db.String(50), nullable=True)  # Número da nota, boleto, etc.
    observacoes = db.Column(db.Text, nullable=True)
    
    # Controle de parcelas
    parcela_numero = db.Column(db.Integer, default=1)
    parcela_total = db.Column(db.Integer, default=1)
    lancamento_pai_id = db.Column(db.Integer, db.ForeignKey('lancamento_fluxo_caixa.id'), nullable=True)
    
    # Relacionamentos
    parcelas = db.relationship('LancamentoFluxoCaixa', remote_side=[id], backref='lancamento_pai')
    
    # Anexos de comprovantes
    anexos_urls = db.Column(db.String(2048), nullable=True)  # URLs separadas por vírgula
    
    def __repr__(self):
        return f'<LancamentoFluxoCaixa {self.id} - {self.tipo}: {self.descricao}>'

    @property
    def is_vencido(self):
        """Verifica se o lançamento está vencido"""
        return (self.data_vencimento < date.today() and 
                self.status_pagamento == 'PENDENTE')
    
class LancamentoNotaFiscal(db.Model):
    __tablename__ = 'lancamento_nota_fiscal'
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)
    
    # --- CORREÇÃO APLICADA AQUI ---
    # O tipo da coluna agora é String(44) para ser igual ao da tabela NFeImportada.
    nfe_importada_chave_acesso = db.Column(db.String(44), db.ForeignKey('nfe_importada.chave_acesso'), nullable=False)
    
    # Dados extraídos para fácil acesso
    chave_acesso = db.Column(db.String(44), nullable=False, index=True)
    emitente_cnpj = db.Column(db.String(14), nullable=False)
    emitente_nome = db.Column(db.String(255), nullable=False)
    valor_total = db.Column(db.Float, nullable=False)
    data_emissao = db.Column(db.DateTime, nullable=False)
    
    # Dados financeiros preenchidos pelo usuário
    data_vencimento = db.Column(db.Date, nullable=False, index=True)
    
    # Controle do fluxo de caixa
    status_pagamento = db.Column(db.String(20), default='A Pagar', nullable=False, index=True) # Ex: A Pagar, Pago
    data_pagamento = db.Column(db.Date, nullable=True)
    
    # Data em que o lançamento foi feito no sistema
    data_lancamento = db.Column(db.DateTime, default=datetime.utcnow)

    # O relacionamento agora usa a coluna corrigida
    nfe_original = db.relationship('NFeImportada', foreign_keys=[nfe_importada_chave_acesso])

    def __repr__(self):
        return f'<LancamentoNotaFiscal {self.id} - {self.emitente_nome} R$ {self.valor_total}>'
    parcela_numero = db.Column(db.Integer, default=1)
    parcela_total = db.Column(db.Integer, default=1)
    lancamento_pai_id = db.Column(db.Integer, db.ForeignKey('lancamento_nota_fiscal.id'))
    observacoes = db.Column(db.Text)
    

    
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
    distancia_km = db.Column(db.Float, nullable=True) # Distância da API (pode ser usada como estimativa)
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
    
    destinos = db.relationship('Destino', backref='viagem', cascade='all, delete-orphan')
    abastecimentos = db.relationship('Abastecimento', backref='viagem', lazy=True)
    localizacoes = db.relationship('Localizacao', backref='viagem', lazy=True, cascade="all, delete-orphan")
    peso_toneladas = db.Column(db.Float, nullable=True)
    custo_motorista_variavel = db.Column(db.Float, nullable=True) 


    

  
    @property
    def distancia_percorrida(self):
        if self.odometro_inicial is not None and self.odometro_final is not None:
            if self.odometro_final >= self.odometro_inicial:
                return self.odometro_final - self.odometro_inicial
        return 0.0 # Retorna 0 se os dados do odômetro não estiverem disponíveis

    @property
    def consumo_medio(self):
        distancia = self.distancia_percorrida # Usa a nova propriedade
        if not distancia > 0 or not self.abastecimentos:
            return 0.0
        total_litros = sum(abast.litros for abast in self.abastecimentos if abast.litros is not None)
        if total_litros > 0:
            return distancia / total_litros
        return 0.0

  

    @property
    def custo_real_completo(self):
        """
        Calcula o custo REAL e completo da viagem, incluindo custos diretos e rateados DINAMICAMENTE.
        """
        
        # --- 1. Custos Diretos da Viagem ---
        # Soma despesas como pedágio, alimentação, hospedagem, etc., registradas na viagem.
        custos_diretos_viagem = 0
        if self.custo_viagem:
            custos_diretos_viagem = (self.custo_viagem.pedagios or 0) + \
                                    (self.custo_viagem.alimentacao or 0) + \
                                    (self.custo_viagem.hospedagem or 0) + \
                                    (self.custo_viagem.outros or 0)

        # Soma o custo real de todos os abastecimentos registrados para esta viagem.
        custo_combustivel_real = sum(abast.custo_total for abast in self.abastecimentos)

        
        custo_desgaste_veiculo = 0
        if self.veiculo_id and self.distancia_percorrida > 0:
            # Calcula o custo fixo médio por KM com base no histórico do veículo
            custo_fixo_km = calcular_custo_fixo_por_km(self.veiculo_id)
            # Calcula o custo de manutenção médio por KM com base no histórico do veículo
            custo_manutencao_km = calcular_custo_manutencao_por_km(self.veiculo_id)
            
            # Multiplica a média de custo pela distância real percorrida na viagem
            custo_desgaste_veiculo = self.distancia_percorrida * (custo_fixo_km + custo_manutencao_km)

        # Custo do Motorista, com lógica flexível para pagamento variável ou fixo.
        custo_motorista = 0
        if self.custo_motorista_variavel and self.custo_motorista_variavel > 0:
            # Se houver um custo variável registrado (ex: frete por tonelada), usa-o.
            custo_motorista = self.custo_motorista_variavel
        elif self.motorista_formal and self.duracao_segundos and self.duracao_segundos > 0:
            # Senão, calcula com base no salário fixo (para viagens normais).
            salario_base = self.motorista_formal.salario_base or 0.0
            custo_hora_motorista = salario_base / 220  # Custo por hora, considerando 220h/mês
            duracao_horas = self.duracao_segundos / 3600
            custo_motorista = duracao_horas * custo_hora_motorista

        # --- 3. Soma Total ---
        # Soma todos os componentes para obter o custo real completo.
        custo_total = custos_diretos_viagem + custo_combustivel_real + custo_desgaste_veiculo + custo_motorista
        
        return custo_total

    @property
    def lucro_real(self):
        """Calcula o lucro real com base no custo completo."""
        if self.valor_recebido is None:
            # Se não houver receita, o "lucro" é negativo, igual ao custo total.
            return -self.custo_real_completo
        return self.valor_recebido - self.custo_real_completo
    @property
    def lucro_real(self):
        """Calcula o lucro real com base no custo completo."""
        if self.valor_recebido is None:
            # Se não houver receita, o "lucro" é negativo, igual ao custo total.
            return -self.custo_real_completo
        return self.valor_recebido - self.custo_real_completo
    @property
    def lucro_real(self):
        """Calcula o lucro real com base no custo completo."""
        if self.valor_recebido is None:
            # Se não houver receita, o "lucro" é negativo, igual ao custo total.
            return -self.custo_real_completo
        return self.valor_recebido - self.custo_real_completo
    

def get_distancia_total_periodo(veiculo_id, dias=365):
    """
    Calcula o total de KM percorridos por um veículo em viagens concluídas
    nos últimos X dias.
    """
    try:
        data_limite = datetime.utcnow() - timedelta(days=dias)
        
        # Soma a 'distancia_percorrida' (odometro_final - odometro_inicial) de todas as viagens no período.
        distancia_query = db.session.query(
            func.sum(Viagem.odometro_final - Viagem.odometro_inicial)
        ).filter(
            Viagem.veiculo_id == veiculo_id,
            Viagem.status == 'concluida',
            Viagem.data_fim >= data_limite,
            Viagem.odometro_final.isnot(None),
            Viagem.odometro_inicial.isnot(None)
        ).scalar()
        
        return distancia_query or 0.0
    except Exception as e:
        logger.error(f"Erro ao calcular distância total para veículo {veiculo_id}: {e}", exc_info=True)
        return 0.0
@app.route('/api/fiscal/salvar_lancamento', methods=['POST'])
@login_required
def api_salvar_lancamento():
    data = request.get_json()
    chave_acesso = data.get('chave_acesso')
    data_vencimento_str = data.get('data_vencimento')

    if not all([chave_acesso, data_vencimento_str]):
        return jsonify({'success': False, 'message': 'Dados incompletos.'}), 400

    nota = NFeImportada.query.filter_by(chave_acesso=chave_acesso, empresa_id=current_user.empresa_id).first()
    if not nota:
        return jsonify({'success': False, 'message': 'Nota fiscal original não encontrada.'}), 404
    if nota.status == 'PROCESSADA':
        return jsonify({'success': False, 'message': 'Esta nota já foi processada.'}), 409

    try:
        data_vencimento = datetime.strptime(data_vencimento_str, '%Y-%m-%d').date()

        # CORREÇÃO: usar nfe_importada_chave_acesso em vez de nfe_importada_id
        novo_lancamento = LancamentoNotaFiscal(
            empresa_id=current_user.empresa_id,
            nfe_importada_chave_acesso=nota.chave_acesso,  # Corrigido aqui
            chave_acesso=nota.chave_acesso,
            emitente_cnpj=nota.emitente_cnpj,
            emitente_nome=nota.emitente_nome,
            valor_total=nota.valor_total,
            data_emissao=nota.data_emissao,
            data_vencimento=data_vencimento,
            status_pagamento='A Pagar'
        )
        db.session.add(novo_lancamento)

        # Atualiza o status da nota original para "Processada"
        nota.status = 'PROCESSADA'
        
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Lançamento financeiro criado com sucesso!'})

    except Exception as e:
        db.session.rollback()
        logger.error(f"Erro ao salvar lançamento para NFe {chave_acesso}: {e}", exc_info=True)
        return jsonify({'success': False, 'message': f'Erro interno: {e}'}), 500


@app.route('/api/fiscal/visualizar_xml/<string:chave_acesso>')
@login_required
def api_visualizar_xml(chave_acesso):
    """
    Busca e retorna o conteúdo XML de uma NF-e para visualização.
    """
    nota = NFeImportada.query.filter_by(
        chave_acesso=chave_acesso,
        empresa_id=current_user.empresa_id
    ).first()

    if not nota:
        return jsonify({'success': False, 'message': 'Nota fiscal não encontrada.'}), 404

    if not nota.xml_content:
        return jsonify({'success': False, 'message': 'Conteúdo XML não disponível para esta nota.'}), 404

    try:
        return jsonify({
            'success': True, 
            'xml_content': nota.xml_content,
            'emitente_nome': nota.emitente_nome,
            'chave_acesso': nota.chave_acesso
        })

    except Exception as e:
        logger.error(f"Erro ao visualizar XML da NFe {chave_acesso}: {e}", exc_info=True)
        return jsonify({'success': False, 'message': f'Erro ao processar XML: {e}'}), 500

@app.route('/fiscal/xml/<string:chave_acesso>')
@login_required
def visualizar_xml_page(chave_acesso):
    """
    Página para visualização formatada do XML da NF-e.
    """
    nota = NFeImportada.query.filter_by(
        chave_acesso=chave_acesso,
        empresa_id=current_user.empresa_id
    ).first_or_404()

    if not nota.xml_content:
        flash('Conteúdo XML não disponível para esta nota.', 'error')
        return redirect(url_for('importar_notas_fiscais'))

    try:
        import xml.dom.minidom as minidom
        
        # Formatar o XML para exibição
        try:
            dom = minidom.parseString(nota.xml_content)
            xml_formatado = dom.toprettyxml(indent="  ")
            # Remove linhas vazias extras
            xml_formatado = '\n'.join([line for line in xml_formatado.split('\n') if line.strip()])
        except:
            xml_formatado = nota.xml_content

        return render_template('visualizar_xml.html', 
                             nota=nota, 
                             xml_content=xml_formatado)

    except Exception as e:
        logger.error(f"Erro ao formatar XML da NFe {chave_acesso}: {e}", exc_info=True)
        flash('Erro ao processar o XML da nota fiscal.', 'error')
        return redirect(url_for('importar_notas_fiscais'))

def calcular_custo_manutencao_por_km(veiculo_id, dias=365):
    """
    Calcula o custo médio de manutenção por KM para um veículo.
    Custo Total de Manutenções / KM Totais Rodados no período.
    """
    # 1. Calcula o custo total de manutenções concluídas no período.
    data_limite = datetime.utcnow() - timedelta(days=dias)
    custo_total_manutencoes = db.session.query(
        func.sum(Manutencao.custo_total)
    ).filter(
        Manutencao.veiculo_id == veiculo_id,
        Manutencao.status == 'Concluída',
        Manutencao.data_saida >= data_limite
    ).scalar() or 0.0

    # 2. Pega a distância total rodada no mesmo período.
    distancia_total = get_distancia_total_periodo(veiculo_id, dias)

    # 3. Calcula a média e retorna (evitando divisão por zero).
    if distancia_total > 0:
        return custo_total_manutencoes / distancia_total
    return 0.0

def calcular_custo_fixo_por_km(veiculo_id, dias=365):
    """
    Calcula o custo médio de despesas fixas (IPVA, Seguro) por KM.
    Custo Total de Despesas Fixas / KM Totais Rodados no período.
    """
    # 1. Calcula o custo total de despesas diversas (fixas) no período.
    data_limite = (datetime.utcnow() - timedelta(days=dias)).date()
    custo_total_fixo = db.session.query(
        func.sum(DespesaVeiculo.valor)
    ).filter(
        DespesaVeiculo.veiculo_id == veiculo_id,
        DespesaVeiculo.data >= data_limite
    ).scalar() or 0.0

    # 2. Pega a distância total rodada no mesmo período.
    distancia_total = get_distancia_total_periodo(veiculo_id, dias)
    
    # 3. Calcula a média e retorna.
    if distancia_total > 0:
        return custo_total_fixo / distancia_total
    return 0.0


@app.route('/lancar_frete_rapido', methods=['POST'])
@login_required
def lancar_frete_rapido():
    try:
        # 1. Obter todos os dados do formulário de frete
        data_str = request.form.get('data')
        veiculo_id = request.form.get('veiculo_id')
        motorista_id = request.form.get('motorista_id')
        cliente = request.form.get('cliente')
        origem = request.form.get('origem')
        material = request.form.get('material')
        peso = float(request.form.get('peso_toneladas'))
        valor_frete_total = float(request.form.get('valor_frete_total'))
        valor_por_tonelada_motorista = float(request.form.get('valor_por_tonelada_motorista'))
        
        # 2. Calcular o pagamento variável do motorista para esta viagem específica
        pagamento_motorista = peso * valor_por_tonelada_motorista

        # 3. Criar o objeto Viagem com os novos campos preenchidos
        novo_frete = Viagem(
            motorista_id=int(motorista_id),
            veiculo_id=int(veiculo_id),
            cliente=cliente,
            valor_recebido=valor_frete_total,
            endereco_saida=origem,
            endereco_destino=cliente, # Simplificando, o destino é a empresa cliente
            data_inicio=datetime.strptime(data_str, '%Y-%m-%d'),
            status='concluida', # Lançamentos de frete já entram como concluídos
            observacoes=f"Material: {material}", # Usamos observações para o material
            empresa_id=current_user.empresa_id,
            # Preenchendo os novos campos do banco de dados:
            peso_toneladas=peso,
            custo_motorista_variavel=pagamento_motorista
        )
        
        db.session.add(novo_frete)
        db.session.commit()
        
        flash('Frete lançado com sucesso!', 'success')
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Erro ao lançar frete rápido: {e}", exc_info=True)
        flash(f'Erro ao lançar frete: {e}', 'error')
        
    return redirect(url_for('iniciar_viagem_page'))

@app.route('/financeiro/folha_pagamento')
@login_required
@master_required # Apenas Admins e Masters podem ver
def folha_pagamento_dashboard():
    hoje = date.today()
    
    # Filtros
    mes_filtro = request.args.get('mes', hoje.month, type=int)
    ano_filtro = request.args.get('ano', hoje.year, type=int)
    motorista_filtro = request.args.get('search', '').strip()

    query = FolhaPagamento.query.filter(
        FolhaPagamento.empresa_id == current_user.empresa_id,
        FolhaPagamento.mes_referencia == mes_filtro,
        FolhaPagamento.ano_referencia == ano_filtro
    ).join(Motorista)

    if motorista_filtro:
        query = query.filter(Motorista.nome.ilike(f'%{motorista_filtro}%'))
    
    folhas = query.order_by(Motorista.nome).all()
    
    # Cálculos para o dashboard
    total_proventos_geral = sum(f.total_proventos for f in folhas)
    total_descontos_geral = sum(f.total_descontos for f in folhas)
    total_liquido_geral = sum(f.salario_liquido for f in folhas)

    return render_template('folha_pagamento_dashboard.html', 
                           folhas=folhas,
                           mes_filtro=mes_filtro,
                           ano_filtro=ano_filtro,
                           search_query=motorista_filtro,
                           total_proventos=total_proventos_geral,
                           total_descontos=total_descontos_geral,
                           total_liquido=total_liquido_geral,
                           ano_atual=hoje.year,
                           active_page='folha_pagamento')


def calcular_consumo_medio_real(veiculo_id, periodo_dias=90):
  
    try:
        # 1. Define o período de análise (padrão: últimos 90 dias)
        data_limite = datetime.utcnow() - timedelta(days=periodo_dias)

        # 2. Busca todos os abastecimentos do veículo no período, ordenados pelo odômetro
        abastecimentos = Abastecimento.query.filter(
            Abastecimento.veiculo_id == veiculo_id,
            Abastecimento.data_abastecimento >= data_limite
        ).order_by(Abastecimento.odometro.asc()).all()

        # 3. Se não houver pelo menos 2 registros, não é possível calcular a média
        if len(abastecimentos) < 2:
            return 0.0  # Retorna 0 se não houver dados suficientes

        total_km_rodados = 0.0
        total_litros_consumidos = 0.0

        # 4. Itera entre os abastecimentos para calcular a distância e o consumo por trecho
        for i in range(len(abastecimentos) - 1):
            abastecimento_anterior = abastecimentos[i]
            abastecimento_atual = abastecimentos[i+1]

            # Distância percorrida entre os dois abastecimentos
            distancia_trecho = abastecimento_atual.odometro - abastecimento_anterior.odometro
            
            # Consideramos que os litros do abastecimento ANTERIOR foram consumidos neste trecho
            litros_trecho = abastecimento_anterior.litros

            if distancia_trecho > 0 and litros_trecho > 0:
                total_km_rodados += distancia_trecho
                total_litros_consumidos += litros_trecho

        # 5. Calcula a média final
        if total_litros_consumidos > 0:
            media_consumo = total_km_rodados / total_litros_consumidos
            return media_consumo
        
        return 0.0

    except Exception as e:
        logger.error(f"Erro ao calcular consumo médio para veiculo {veiculo_id}: {e}", exc_info=True)
        return 0.0 # Retorna 0 em caso de erro

def obter_preco_medio_combustivel_recente(empresa_id, default=5.80, limit=20):
    """
    Busca a média de preço por litro dos últimos 'limit' abastecimentos da empresa.
    """
    try:
        # Busca o preço médio diretamente no banco de dados
        preco_medio_query = db.session.query(
            func.avg(Abastecimento.preco_por_litro)
        ).join(Veiculo).filter(
            Veiculo.empresa_id == empresa_id
        ).scalar()

        # Se houver um resultado, retorna ele. Senão, usa o valor padrão.
        return preco_medio_query if preco_medio_query else default
    except Exception as e:
        logger.error(f"Erro ao obter preço médio do combustível para empresa {empresa_id}: {e}")
        return default
    

def calcular_consumo_medio_real(veiculo_id, periodo_dias=90):
    """
    Calcula o consumo médio real (km/l) de um veículo com base no histórico
    de abastecimentos nos últimos 'periodo_dias'.
    """
    try:
        data_limite = datetime.utcnow() - timedelta(days=periodo_dias)

        abastecimentos = Abastecimento.query.filter(
            Abastecimento.veiculo_id == veiculo_id,
            Abastecimento.data_abastecimento >= data_limite
        ).order_by(Abastecimento.odometro.asc()).all()

        if len(abastecimentos) < 2:
            return 0.0

        total_km_rodados = 0.0
        total_litros_consumidos = 0.0

        for i in range(len(abastecimentos) - 1):
            abastecimento_anterior = abastecimentos[i]
            abastecimento_atual = abastecimentos[i+1]

            distancia_trecho = abastecimento_atual.odometro - abastecimento_anterior.odometro
            litros_trecho = abastecimento_anterior.litros

            if distancia_trecho > 0 and litros_trecho > 0:
                total_km_rodados += distancia_trecho
                total_litros_consumidos += litros_trecho

        if total_litros_consumidos > 0:
            media_consumo = total_km_rodados / total_litros_consumidos
            return media_consumo
        
        return 0.0

    except Exception as e:
        logger.error(f"Erro ao calcular consumo médio para veiculo {veiculo_id}: {e}", exc_info=True)
        return 0.0

def obter_preco_medio_combustivel_recente(empresa_id, default=5.80):
    """
    Busca a média de preço por litro dos últimos abastecimentos da empresa.
    """
    try:
        preco_medio_query = db.session.query(
            func.avg(Abastecimento.preco_por_litro)
        ).join(Veiculo).filter(
            Veiculo.empresa_id == empresa_id
        ).scalar()

        return float(preco_medio_query) if preco_medio_query else default
    except Exception as e:
        logger.error(f"Erro ao obter preço médio do combustível para empresa {empresa_id}: {e}")
        return default

# --- Rota Principal da API de Estimativa ---

@app.route('/api/viagem/estimar_custo', methods=['POST'])
@login_required
def estimar_custo_viagem_api():
    """
    API para estimar o custo total de uma viagem (Combustível + Motorista + Desgaste do Veículo).
    Utiliza dados históricos para maior precisão.
    """
    try:
        data = request.get_json()
        veiculo_id = data.get('veiculo_id')
        motorista_id = data.get('motorista_id')
        distancia_km = data.get('distancia_km')
        duracao_segundos = data.get('duracao_segundos')

        if not all([veiculo_id, motorista_id, distancia_km is not None, duracao_segundos is not None]):
            return jsonify({'success': False, 'message': 'Dados insuficientes para estimar o custo.'}), 400

        veiculo = db.session.get(Veiculo, int(veiculo_id))
        motorista = db.session.get(Motorista, int(motorista_id))

        if not veiculo or not motorista:
            return jsonify({'success': False, 'message': 'Veículo ou motorista não encontrado.'}), 404

        # 1. Custo de Combustível (Lógica Inteligente com Fallback)
        consumo_real = calcular_consumo_medio_real(veiculo.id)
        consumo_a_ser_usado = consumo_real or veiculo.consumo_medio_km_l or 1.0
        preco_combustivel_para_calculo = obter_preco_medio_combustivel_recente(current_user.empresa_id)
        
        litros_estimados = distancia_km / consumo_a_ser_usado
        custo_combustivel = litros_estimados * preco_combustivel_para_calculo

        # 2. Custo do Motorista (Rateado por hora)
        salario_base = motorista.salario_base or 0.0
        custo_hora_motorista = salario_base / 220  # Custo/hora considerando 220h/mês
        duracao_horas = duracao_segundos / 3600
        custo_motorista = duracao_horas * custo_hora_motorista

        # 3. Custo Fixo e Manutenção do Veículo (Rateado por KM)
        # Estes valores são placeholders. No futuro, podem ser substituídos por
        # funções que calculam a média real com base nos modelos Manutencao e DespesaVeiculo.
        CUSTO_FIXO_POR_KM = 0.55       # Ex: IPVA, Seguro, etc.
        CUSTO_MANUTENCAO_POR_KM = 0.40 # Ex: Pneus, óleo, peças de desgaste.
        custo_desgaste_veiculo = (CUSTO_FIXO_POR_KM + CUSTO_MANUTENCAO_POR_KM) * distancia_km

        # 4. Totalização dos custos
        custo_total_estimado = custo_combustivel + custo_motorista + custo_desgaste_veiculo

        # 5. Retorno dos dados em formato JSON para o Frontend
        return jsonify({
            'success': True,
            'custos': {
                'combustivel': round(custo_combustivel, 2),
                'motorista': round(custo_motorista, 2),
                'veiculo': round(custo_desgaste_veiculo, 2),
                'total': round(custo_total_estimado, 2)
            }
        })

    except Exception as e:
        logger.error(f"Erro na API de estimar custo: {e}", exc_info=True)
        return jsonify({'success': False, 'message': f'Ocorreu um erro interno: {e}'}), 500




@app.route('/financeiro/folha_pagamento/gerar', methods=['POST'])
@login_required
@master_required
def gerar_folhas_mes():
    mes = request.form.get('mes', type=int)
    ano = request.form.get('ano', type=int)

    if not mes or not ano:
        flash('Mês e Ano são obrigatórios para gerar as folhas.', 'error')
        return redirect(url_for('folha_pagamento_dashboard'))

    motoristas_ativos = Motorista.query.filter_by(empresa_id=current_user.empresa_id, situacao='NORMAL / LIBERADO').all()
    criadas = 0
    atualizadas = 0
    
    for motorista in motoristas_ativos:
        folha = FolhaPagamento.query.filter_by(
            motorista_id=motorista.id,
            mes_referencia=mes,
            ano_referencia=ano
        ).first()

        if not folha:
            folha = FolhaPagamento(
                motorista_id=motorista.id,
                empresa_id=current_user.empresa_id,
                mes_referencia=mes,
                ano_referencia=ano,
                salario_base_registro=motorista.salario_base or 0.0,
                status='Em Aberto'
            )
            db.session.add(folha)
            db.session.flush() # Garante que a folha tenha um ID antes de adicionar itens
            criadas += 1
        elif folha.status != 'Em Aberto':
            continue # Pula folhas já fechadas ou pagas
        else:
            atualizadas += 1

        # --- LÓGICA DE INTEGRAÇÃO DE FRETES ---
        # 1. Busca todas as viagens concluídas do motorista no mês que são fretes (custo > 0)
        viagens_do_mes = Viagem.query.filter(
            Viagem.motorista_id == motorista.id,
            Viagem.status == 'concluida',
            Viagem.custo_motorista_variavel > 0, # Filtra apenas fretes com valor
            extract('month', Viagem.data_fim) == mes,
            extract('year', Viagem.data_fim) == ano
        ).all()

        # 2. Para cada viagem, adiciona um provento na folha se ainda não existir
        for viagem in viagens_do_mes:
            item_existente = ItemFolhaPagamento.query.filter_by(
                folha_pagamento_id=folha.id,
                viagem_id=viagem.id # Checa se o item já foi lançado para essa viagem específica
            ).first()

            if not item_existente:
                novo_provento = ItemFolhaPagamento(
                    folha_pagamento_id=folha.id,
                    tipo='Provento',
                    descricao=f"Frete: {viagem.cliente} ({viagem.data_inicio.strftime('%d/%m')})",
                    valor=viagem.custo_motorista_variavel,
                    viagem_id=viagem.id # Linka o item à viagem
                )
                db.session.add(novo_provento)
        
        # 3. Recalcula os totais da folha após adicionar os proventos
        folha.recalcular_totais()
            
    try:
        db.session.commit()
        flash(f'{criadas} folha(s) criada(s) e {atualizadas} atualizada(s) com os fretes do mês.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao gerar/atualizar folhas de pagamento: {e}', 'error')
        
    return redirect(url_for('folha_pagamento_dashboard', mes=mes, ano=ano))


@app.route('/financeiro/folha_pagamento/<int:folha_id>', methods=['GET', 'POST'])
@login_required
@master_required
def detalhes_folha_pagamento(folha_id):
    folha = FolhaPagamento.query.options(
        db.joinedload(FolhaPagamento.motorista)
    ).filter_by(id=folha_id, empresa_id=current_user.empresa_id).first_or_404()

    if request.method == 'POST':
        if folha.status != 'Em Aberto':
            flash('Esta folha de pagamento está fechada e não pode ser editada.', 'error')
            return redirect(url_for('detalhes_folha_pagamento', folha_id=folha.id))

        # Deleta itens antigos para recriar
        folha.itens.delete()

        # Processa Proventos
        i = 1
        while f'provento_descricao_{i}' in request.form:
            descricao = request.form[f'provento_descricao_{i}']
            valor = request.form.get(f'provento_valor_{i}', type=float)
            if descricao and valor is not None:
                item = ItemFolhaPagamento(folha_pagamento_id=folha.id, tipo='Provento', descricao=descricao, valor=valor)
                db.session.add(item)
            i += 1
        
        # Processa Descontos
        i = 1
        while f'desconto_descricao_{i}' in request.form:
            descricao = request.form[f'desconto_descricao_{i}']
            valor = request.form.get(f'desconto_valor_{i}', type=float)
            if descricao and valor is not None:
                item = ItemFolhaPagamento(folha_pagamento_id=folha.id, tipo='Desconto', descricao=descricao, valor=valor)
                db.session.add(item)
            i += 1

        folha.salario_base_registro = request.form.get('salario_base', type=float, default=0.0)
        folha.observacoes = request.form.get('observacoes')
        folha.recalcular_totais()
        
        # Lógica de Ações (Salvar, Fechar, Pagar)
        action = request.form.get('action')
        if action == 'fechar':
            folha.status = 'Fechada'
            folha.data_fechamento = datetime.utcnow()
            flash_msg = 'Folha de pagamento fechada com sucesso!'
        elif action == 'pagar':
            folha.status = 'Paga'
            folha.data_pagamento = datetime.strptime(request.form.get('data_pagamento'), '%Y-%m-%d')
            folha.meio_pagamento = request.form.get('meio_pagamento')
            # Lógica de upload de comprovante aqui (se necessário)
            flash_msg = 'Folha de pagamento marcada como paga!'
        else: # Salvar
            flash_msg = 'Alterações salvas com sucesso!'
        
        try:
            db.session.commit()
            flash(flash_msg, 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao salvar: {e}', 'error')

        return redirect(url_for('detalhes_folha_pagamento', folha_id=folha.id))

    proventos = folha.itens.filter_by(tipo='Provento').all()
    descontos = folha.itens.filter_by(tipo='Desconto').all()
    
    return render_template('detalhes_folha_pagamento.html', 
                           folha=folha,
                           proventos=proventos,
                           descontos=descontos,
                           active_page='folha_pagamento')


@app.route('/financeiro/folha_pagamento/<int:folha_id>/holerite')
@login_required
@master_required
def gerar_holerite(folha_id):
    folha = FolhaPagamento.query.filter_by(id=folha_id, empresa_id=current_user.empresa_id).first_or_404()
    proventos = folha.itens.filter_by(tipo='Provento').all()
    descontos = folha.itens.filter_by(tipo='Desconto').all()
    
    return render_template('holerite.html', 
                           folha=folha,
                           proventos=proventos,
                           descontos=descontos)

class DespesaVeiculo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    veiculo_id = db.Column(db.Integer, db.ForeignKey('veiculo.id'), nullable=False)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)
    data = db.Column(db.Date, nullable=False, default=date.today)
    
    # Categoria: 'IPVA', 'Seguro', 'Multa', 'Salário Fixo', 'Pneus', 'Outros'
    categoria = db.Column(db.String(100), nullable=False, index=True)
    descricao = db.Column(db.Text, nullable=True)
    valor = db.Column(db.Float, nullable=False)
    
    # Armazenará URLs dos comprovantes, separadas por vírgula
    anexos = db.Column(db.String(1024), nullable=True) 

    veiculo = db.relationship('Veiculo', backref=db.backref('despesas_diversas', lazy=True, cascade="all, delete-orphan"))

    def to_dict(self):
        return {
            "id": self.id,
            "data": self.data.strftime('%d/%m/%Y'),
            "categoria": self.categoria,
            "descricao": self.descricao,
            "valor": self.valor,
            "anexos": self.anexos.split(',') if self.anexos else []
        }
    
class Localizacao(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    motorista_id = db.Column(db.Integer, db.ForeignKey('motorista.id'), nullable=False)
    viagem_id = db.Column(db.Integer, db.ForeignKey('viagem.id'), nullable=True)
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    endereco = db.Column(db.String(200), nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class Documento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    
    # Informações do Documento
    tipo_documento = db.Column(db.String(100), nullable=False, index=True) # Ex: CNH, CRLV, ANTT, Seguro
    descricao = db.Column(db.String(255), nullable=True) # Ex: Apólice de Seguro XYZ
    numero_documento = db.Column(db.String(100), nullable=True)
    data_emissao = db.Column(db.Date, nullable=True)
    data_validade = db.Column(db.Date, nullable=False, index=True) # Essencial para os alertas
    url_anexo = db.Column(db.String(500), nullable=True) # Link para o arquivo no Cloudflare R2
    
    # Chaves estrangeiras para ligar o documento a outras partes do sistema
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)
    
    # Relacionamento: Um documento pertence ou a um motorista OU a um veículo
    motorista_id = db.Column(db.Integer, db.ForeignKey('motorista.id'), nullable=True)
    veiculo_id = db.Column(db.Integer, db.ForeignKey('veiculo.id'), nullable=True)

    # Relacionamentos para facilitar o acesso aos objetos
    motorista = db.relationship('Motorista', backref=db.backref('documentos', lazy=True, cascade="all, delete-orphan"))
    veiculo = db.relationship('Veiculo', backref=db.backref('documentos', lazy=True, cascade="all, delete-orphan"))

    def __repr__(self):
        return f'<Documento {self.id} - {self.tipo_documento}>'    




def parse_nfe_xml(xml_file):
    """
    Extrai dados de um arquivo XML de NF-e, incluindo informações
    detalhadas do cliente e da viagem.
    """
    try:
        xml_file.seek(0)
        tree = ET.parse(xml_file)
        root = tree.getroot()
        ns = {'nfe': 'http://www.portalfiscal.inf.br/nfe'}

        # --- Dados do Destinatário (Cliente) ---
        dest_element = root.find('.//nfe:dest', ns)
        if dest_element is None: return None
        
        ender_dest = dest_element.find('nfe:enderDest', ns)
        
        # Determina se é CNPJ ou CPF
        cpf_cnpj_node = dest_element.find('nfe:CNPJ', ns)
        pessoa_tipo = 'juridica'
        if cpf_cnpj_node is None:
            cpf_cnpj_node = dest_element.find('nfe:CPF', ns)
            pessoa_tipo = 'fisica'
        
        cpf_cnpj = cpf_cnpj_node.text if cpf_cnpj_node is not None else None

        cpf_cnpj_limpo = re.sub(r'\D', '', cpf_cnpj) if cpf_cnpj else ''
        # Coleta todos os dados do cliente
        cliente_info = {
                'pessoa_tipo': pessoa_tipo,
                'cpf_cnpj': cpf_cnpj_limpo if cpf_cnpj_limpo else None,
                'nome_razao_social': dest_element.find('nfe:xNome', ns).text,
                'inscricao_estadual': getattr(dest_element.find('nfe:IE', ns), 'text', None),
                'logradouro': ender_dest.find('nfe:xLgr', ns).text,
                'numero': ender_dest.find('nfe:nro', ns).text,
                'complemento': getattr(ender_dest.find('nfe:xCpl', ns), 'text', None),
                'bairro': ender_dest.find('nfe:xBairro', ns).text,
                'cidade': ender_dest.find('nfe:xMun', ns).text,
                'estado': ender_dest.find('nfe:UF', ns).text,
                'cep': re.sub(r'\D', '', ender_dest.find('nfe:CEP', ns).text),
                'email': f"{cpf_cnpj_limpo}@email.xml", # <-- Agora esta linha funciona
                'telefone': '00000000000'
        }

        # --- Dados do Emissor (Endereço de Saída) ---
        emit_element = root.find('.//nfe:emit', ns)
        ender_emit = emit_element.find('nfe:enderEmit', ns)
        saida_rua = ender_emit.find('nfe:xLgr', ns).text
        saida_num = ender_emit.find('nfe:nro', ns).text
        saida_bairro = ender_emit.find('nfe:xBairro', ns).text
        saida_cidade = ender_emit.find('nfe:xMun', ns).text
        saida_uf = ender_emit.find('nfe:UF', ns).text
        endereco_saida_completo = f"{saida_rua}, {saida_num} - {saida_bairro}, {saida_cidade} - {saida_uf}"

        # --- Dados da Viagem ---
        viagem_info = {
            "cliente": cliente_info['nome_razao_social'],
            "endereco_saida": endereco_saida_completo,
            "endereco_destino": f"{cliente_info['logradouro']}, {cliente_info['numero']} - {cliente_info['bairro']}, {cliente_info['cidade']} - {cliente_info['estado']}",
            "nome_arquivo": getattr(xml_file, 'filename', 'N/A')
        }

        return {
            "viagem_info": viagem_info,
            "cliente_info": cliente_info
        }

    except Exception as e:
        logger.error(f"Erro ao processar XML: {e}", exc_info=True)
        return None



@app.route('/veiculo/<int:veiculo_id>/lancar_despesa_diversa', methods=['POST'])
@login_required
def lancar_despesa_diversa(veiculo_id):
    """
    Recebe os dados do formulário de lançamento rápido do dashboard do veículo.
    """
    veiculo = Veiculo.query.filter_by(id=veiculo_id, empresa_id=current_user.empresa_id).first_or_404()
    
    try:
        # Cria uma nova instância do modelo DespesaVeiculo com os dados do formulário
        nova_despesa = DespesaVeiculo(
            veiculo_id=veiculo.id,
            empresa_id=current_user.empresa_id,
            data=datetime.strptime(request.form['data'], '%Y-%m-%d').date(),
            categoria=request.form['categoria'],
            descricao=request.form.get('descricao', ''),
            valor=float(request.form['valor'])
        )

        # Lógica para Upload de Anexos
        urls_anexos = []
        files = request.files.getlist('anexos')
        if files and any(f and f.filename for f in files):
            s3_client = boto3.client(
                's3',
                endpoint_url=app.config['CLOUDFLARE_R2_ENDPOINT'],
                aws_access_key_id=app.config['CLOUDFLARE_R2_ACCESS_KEY'],
                aws_secret_access_key=app.config['CLOUDFLARE_R2_SECRET_KEY'],
                region_name='auto'
            )
            bucket_name = app.config['CLOUDFLARE_R2_BUCKET']
            public_url_base = app.config['CLOUDFLARE_R2_PUBLIC_URL']

            for file in files:
                if file and file.filename: # Checagem extra
                    filename = secure_filename(file.filename)
                    s3_path = f"despesas_veiculo/{veiculo.id}/{uuid.uuid4()}-{filename}"
                    
                    s3_client.upload_fileobj(
                        file, bucket_name, s3_path,
                        ExtraArgs={'ContentType': file.content_type or 'application/octet-stream'}
                    )
                    urls_anexos.append(f"{public_url_base}/{s3_path}")
        
        if urls_anexos:
            nova_despesa.anexos = ",".join(urls_anexos)

        # Adiciona e salva a nova despesa no banco de dados
        db.session.add(nova_despesa)
        db.session.commit()
        flash('Despesa registrada com sucesso!', 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao registrar despesa: {e}', 'error')
        logger.error(f"Erro ao adicionar despesa para veiculo {veiculo_id}: {e}", exc_info=True)

    # Redireciona de volta para o dashboard do veículo após o lançamento
    return redirect(url_for('veiculo_dashboard', veiculo_id=veiculo_id))


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

@app.route('/painel')
@login_required
def painel():
    """
    Esta rota agora carrega o dashboard principal da sua aplicação.
    Ela contém toda a lógica para buscar os dados das viagens e KPIs.
    """
    # Query base para buscar todas as viagens da empresa, já carregando dados relacionados
    viagens_query = Viagem.query.filter_by(empresa_id=current_user.empresa_id).options(
        db.joinedload(Viagem.veiculo),
        db.joinedload(Viagem.motorista_formal)
    ).order_by(Viagem.data_inicio.desc()).all()

    # --- KPIs Operacionais ---
    viagens_em_andamento_kpi = sum(1 for v in viagens_query if v.status == 'em_andamento')
    viagens_pendentes_kpi = sum(1 for v in viagens_query if v.status == 'pendente')
    veiculos_disponiveis_kpi = Veiculo.query.filter_by(empresa_id=current_user.empresa_id, status='Disponível').count()

    # --- KPIs Financeiros (para o mês atual) ---
    receita_mes = 0.0
    custo_mes = 0.0

    if current_user.role in ['Admin', 'Master']:
        hoje = date.today()
        viagens_do_mes = Viagem.query.filter(
            extract('year', Viagem.data_inicio) == hoje.year,
            extract('month', Viagem.data_inicio) == hoje.month,
            Viagem.empresa_id == current_user.empresa_id,
            Viagem.status == 'concluida'
        ).options(db.joinedload(Viagem.custo_viagem), db.joinedload(Viagem.abastecimentos)).all()

        for v in viagens_do_mes:
            receita_mes += v.valor_recebido or 0.0
            custo_despesas = 0
            if v.custo_viagem:
                custo_despesas = (v.custo_viagem.pedagios or 0) + (v.custo_viagem.alimentacao or 0) + (v.custo_viagem.hospedagem or 0) + (v.custo_viagem.outros or 0)
            custo_abastecimento = sum(a.custo_total for a in v.abastecimentos)
            custo_mes += custo_despesas + custo_abastecimento

    lucro_mes = receita_mes - custo_mes
    
    # --- Processa a lista de viagens para exibir na página principal ---
    viagens_para_template = []
    for viagem in viagens_query:
        motorista_nome = 'N/A'
        if viagem.motorista_formal:
            motorista_nome = viagem.motorista_formal.nome

        destinos_list = sorted(
            [{'endereco': d.endereco, 'ordem': d.ordem} for d in viagem.destinos],
            key=lambda d: d.get('ordem', 0)
        )

        viagens_para_template.append({
            'id': viagem.id,
            'cliente': viagem.cliente,
            'motorista_nome': motorista_nome,
            'endereco_saida': viagem.endereco_saida,
            'destinos': destinos_list,
            'status': viagem.status,
            'veiculo_placa': viagem.veiculo.placa if viagem.veiculo else 'N/A',
            'veiculo_modelo': viagem.veiculo.modelo if viagem.veiculo else 'N/A',
            'data_inicio': viagem.data_inicio,
            'data_fim': viagem.data_fim
        })

    # Renderiza o template do painel, que agora se chama 'painel.html'
    return render_template('painel.html',
                           viagens=viagens_para_template,
                           Maps_API_KEY=Maps_API_KEY,
                           viagens_em_andamento=viagens_em_andamento_kpi,
                           viagens_pendentes=viagens_pendentes_kpi,
                           veiculos_disponiveis=veiculos_disponiveis_kpi,
                           receita_mes=receita_mes,
                           custo_mes=custo_mes,
                           lucro_mes=lucro_mes)

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
        # --- CORREÇÃO APLICADA AQUI ---
        # A busca agora é feita pela coluna correta 'cpf' em vez da propriedade 'cpf_cnpj'
        motorista_formal = Motorista.query.filter_by(cpf=current_user.cpf_cnpj).first()
        
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

        # --- INÍCIO DA CORREÇÃO ---
        # Esta lógica agora só é executada se o convite for para um 'Motorista'.
        # Para Admins e outros papéis, ela é corretamente ignorada.
        if convite.role == 'Motorista':
            motorista = Motorista.query.filter_by(
                cpf=cpf_cnpj,  # Corrigido de 'cpf_cnpj' para 'cpf'
                empresa_id=convite.empresa_id
            ).first()
            if motorista:
                motorista.usuario_id = usuario.id
        # --- FIM DA CORREÇÃO ---

        # Finalmente commit de tudo em bloco único
        db.session.commit()

        flash('Conta criada com sucesso! Faça login.', 'success')
        return redirect(url_for('login'))

    return render_template('registrar_token.html', email=convite.email, role=convite.role)

def calcular_media_km_veiculo(veiculo_id):
    """Calcula a média de KM rodados por dia para um veículo (versão corrigida para SQLite)."""
    hoje = date.today()
    data_limite = hoje - timedelta(days=90)
    
    # O cálculo (odometro_final - odometro_inicial) é feito diretamente no SQL.
    resultado = db.session.query(
        func.sum(Viagem.odometro_final - Viagem.odometro_inicial).label('total_km'),
        func.count(func.distinct(func.date(Viagem.data_inicio))).label('dias_com_viagem')
    ).filter(
        Viagem.veiculo_id == veiculo_id,
        Viagem.status == 'concluida',
        Viagem.odometro_final.isnot(None),
        Viagem.odometro_inicial.isnot(None),
        Viagem.data_inicio >= data_limite
    ).first()

    if resultado and resultado.total_km and resultado.dias_com_viagem > 0:
        return resultado.total_km / resultado.dias_com_viagem
    
    return 100 # Retorna um valor padrão de 100 km/dia se não houver dados

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

    # LINHA A SER CORRIGIDA:
    veiculos_disponiveis = Veiculo.query.filter_by(status='Disponível', empresa_id=current_user.empresa_id).all() #
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
def index():

    return render_template('index.html')

# Em app.py, substitua a rota 'cadastrar_motorista' por esta versão completa

@app.route('/cadastrar_motorista', methods=['GET', 'POST'])
@login_required
def cadastrar_motorista():
    if request.method == 'POST':
        try:
            # Validações iniciais (CPF, CNH)
            cpf = re.sub(r'\D', '', request.form.get('cpf'))
            cnh_numero = re.sub(r'\D', '', request.form.get('cnh_numero'))

            if Motorista.query.filter_by(cpf=cpf, empresa_id=current_user.empresa_id).first():
                flash('Um motorista com este CPF já foi cadastrado.', 'error')
                return render_template('cadastrar_motorista.html', motorista=request.form)
            
            if Motorista.query.filter_by(cnh_numero=cnh_numero, empresa_id=current_user.empresa_id).first():
                flash('Um motorista com esta CNH já foi cadastrado.', 'error')
                return render_template('cadastrar_motorista.html', motorista=request.form)

            # --- INÍCIO DA CORREÇÃO 1: Lógica para múltiplos anexos e download ---
            
            anexos_urls = []
            anexos_files = request.files.getlist("anexos")

            if anexos_files and any(f.filename for f in anexos_files):
                # Validação das credenciais do Cloudflare R2
                bucket_name = app.config.get('CLOUDFLARE_R2_BUCKET')
                if not bucket_name:
                    flash('Erro de configuração: O nome do bucket do Cloudflare R2 não foi definido nas variáveis de ambiente.', 'error')
                    return render_template('cadastrar_motorista.html', motorista=request.form)

                s3_client = boto3.client(
                    's3',
                    endpoint_url=app.config['CLOUDFLARE_R2_ENDPOINT'],
                    aws_access_key_id=app.config['CLOUDFLARE_R2_ACCESS_KEY'],
                    aws_secret_access_key=app.config['CLOUDFLARE_R2_SECRET_KEY'],
                    region_name='auto'
                )
                public_url_base = app.config['CLOUDFLARE_R2_PUBLIC_URL']
                
                for anexo_file in anexos_files:
                    if anexo_file.filename:
                        filename = secure_filename(anexo_file.filename)
                        s3_path = f"motoristas/{cpf}/anexos/{uuid.uuid4()}-{filename}"
                        
                        s3_client.upload_fileobj(
                            anexo_file,
                            bucket_name,
                            s3_path,
                            ExtraArgs={
                                'ContentType': anexo_file.content_type or 'application/octet-stream',
                                'ContentDisposition': 'attachment'
                            }
                        )
                        anexos_urls.append(f"{public_url_base}/{s3_path}")

            # --- FIM DA CORREÇÃO 1 ---

            def to_date(date_string):
                return datetime.strptime(date_string, '%Y-%m-%d').date() if date_string else None

            usuario_correspondente = Usuario.query.filter_by(cpf_cnpj=cpf, empresa_id=current_user.empresa_id).first()

            novo_motorista = Motorista(
                empresa_id=current_user.empresa_id,
                usuario_id=usuario_correspondente.id if usuario_correspondente else None,
                nome=request.form.get('nome'),
                telefone=re.sub(r'\D', '', request.form.get('telefone')),
                cpf=cpf,
                data_nascimento=to_date(request.form.get('data_nascimento')),
                nacionalidade=request.form.get('nacionalidade'),
                naturalidade=request.form.get('naturalidade'),
                estado_civil=request.form.get('estado_civil'),
                sexo=request.form.get('sexo'),
                pai=request.form.get('pai'),
                mae=request.form.get('mae'),
                data_admissao=to_date(request.form.get('data_admissao')),
                situacao=request.form.get('situacao'),
                data_desativacao=to_date(request.form.get('data_desativacao')),
                classificacao=request.form.get('classificacao'),
                cod_departamento=request.form.get('cod_departamento'),
                numero_ficha=request.form.get('numero_ficha'),
                foto=None,
                anexos=','.join(anexos_urls) if anexos_urls else None,
                cep=re.sub(r'\D', '', request.form.get('cep')),
                tipo_logradouro=request.form.get('tipo_logradouro'),
                logradouro=request.form.get('logradouro'),
                numero=request.form.get('numero'),
                complemento=request.form.get('complemento'),
                bairro=request.form.get('bairro'),
                cidade=request.form.get('cidade'),
                uf=request.form.get('uf'),
                email=request.form.get('email'),
                tipo_imovel=request.form.get('tipo_imovel'),
                tempo_residencia=request.form.get('tempo_residencia'),
                cnh_numero=cnh_numero,
                cnh_data_primeira=to_date(request.form.get('cnh_data_primeira')),
                cnh_data_vencimento=to_date(request.form.get('cnh_data_vencimento')),
                cnh_categoria=request.form.get('cnh_categoria'),
                cnh_cod_seguranca=request.form.get('cnh_cod_seguranca'),
                rg=request.form.get('rg'),
                rg_uf=request.form.get('rg_uf'),
                pis=request.form.get('pis'),
                inss=request.form.get('inss'),
                titulo_eleitor=request.form.get('titulo_eleitor'),
                ctps=request.form.get('ctps'),
                funcao=request.form.get('funcao'),
                mopp_numero=request.form.get('mopp_numero'),
                mopp_vencimento=to_date(request.form.get('mopp_vencimento')),
                contato_nome=request.form.get('contato_nome'),
                contato_tipo_ref=request.form.get('contato_tipo_ref'),
                contato_tipo_fone=request.form.get('contato_tipo_fone'),
                contato_telefone=re.sub(r'\D', '', request.form.get('contato_telefone')),
                contato_operadora=request.form.get('contato_operadora'),
                contato_obs=request.form.get('contato_obs')
            )
            
            db.session.add(novo_motorista)
            db.session.commit()
            flash('Motorista cadastrado com sucesso!', 'success')
            return redirect(url_for('consultar_motoristas'))

        except Exception as e:
            db.session.rollback()
            logger.error(f"Erro ao cadastrar motorista: {e}", exc_info=True)
            flash(f'Ocorreu um erro ao salvar o motorista: {e}', 'error')
            return render_template('cadastrar_motorista.html', motorista=request.form)

    return render_template('cadastrar_motorista.html', motorista={}, active_page='cadastrar_motorista')

# Substitua sua função 'dateformat' por esta versão
@app.template_filter('dateformat')
def dateformat(value):
    # --- INÍCIO DA CORREÇÃO 2: Filtro de data inteligente ---
    if isinstance(value, (date, datetime)):
        return value.strftime('%Y-%m-%d')
    # Se o valor já for uma string (como ao recarregar o form após um erro),
    # apenas o retorna sem tentar formatar.
    if isinstance(value, str):
        return value
    return ''
    # --- FIM DA CORREÇÃO 2 ---

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
                Motorista.cpf.ilike(search_filter),
                Motorista.cnh_numero.ilike(search_filter)
            )
        )

    motoristas_list = query.order_by(Motorista.nome.asc()).all()

    # Adiciona o status e corrige a data da CNH para cada objeto motorista
    for motorista in motoristas_list:
        # Define o status de viagem do motorista
        viagem_ativa = Viagem.query.filter(
            Viagem.motorista_id == motorista.id,
            Viagem.status == 'em_andamento'
        ).first()
        motorista.status = 'Em Viagem' if viagem_ativa else 'Disponível'

        # --- INÍCIO DA CORREÇÃO ---
        # Se a data de vencimento da CNH não estiver definida (for None),
        # o template irá gerar um TypeError ao tentar comparar None < date.
        # Para evitar isso, atribuímos temporariamente uma data máxima ao campo.
        # Esta alteração não é persistida no banco de dados.
        if motorista.cnh_data_vencimento is None:
            motorista.cnh_data_vencimento = date.max
        # --- FIM DA CORREÇÃO ---

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
            return redirect(url_for('painel'))
            
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
    # Busca o motorista que pertence à empresa do usuário, ou retorna erro 404.
    motorista = Motorista.query.filter_by(id=motorista_id, empresa_id=current_user.empresa_id).first_or_404()

    if request.method == 'POST':
        try:
            # Coleta os dados do formulário
            cpf = re.sub(r'\D', '', request.form.get('cpf', ''))
            cnh_numero = re.sub(r'\D', '', request.form.get('cnh_numero', ''))

            # --- PRINCÍPIO DE VALIDAÇÃO PARA EDIÇÃO APLICADO ---

            # 1. Valida se o CPF informado já pertence a OUTRO motorista, ignorando o motorista atual.
            motorista_com_mesmo_cpf = Motorista.query.filter(
                Motorista.id != motorista_id,  # A CONDIÇÃO MAIS IMPORTANTE: Exclui o próprio registro da busca.
                Motorista.cpf == cpf,
                Motorista.empresa_id == current_user.empresa_id
            ).first()

            if motorista_com_mesmo_cpf:
                flash('Erro: Este CPF já está cadastrado para outro motorista.', 'error')
                return redirect(url_for('editar_motorista', motorista_id=motorista_id))
            
            # 2. Valida se a CNH informada já pertence a OUTRO motorista, aplicando o mesmo princípio.
            motorista_com_mesma_cnh = Motorista.query.filter(
                Motorista.id != motorista_id, # A CONDIÇÃO MAIS IMPORTANTE: Exclui o próprio registro da busca.
                Motorista.cnh_numero == cnh_numero,
                Motorista.empresa_id == current_user.empresa_id
            ).first()

            if motorista_com_mesma_cnh:
                flash('Erro: Esta CNH já está cadastrada para outro motorista.', 'error')
                return redirect(url_for('editar_motorista', motorista_id=motorista_id))
            
            # --- FIM DA APLICAÇÃO DO PRINCÍPIO ---

            def to_date(date_string):
                return datetime.strptime(date_string, '%Y-%m-%d').date() if date_string else None
            
            # Atualiza todos os campos do objeto motorista com os dados do formulário
            motorista.nome = request.form.get('nome')
            motorista.telefone = re.sub(r'\D', '', request.form.get('telefone', ''))
            motorista.cpf = cpf
            motorista.data_nascimento = to_date(request.form.get('data_nascimento'))
            motorista.nacionalidade = request.form.get('nacionalidade')
            motorista.naturalidade = request.form.get('naturalidade')
            motorista.estado_civil = request.form.get('estado_civil')
            motorista.sexo = request.form.get('sexo')
            motorista.pai = request.form.get('pai')
            motorista.mae = request.form.get('mae')
            motorista.data_admissao = to_date(request.form.get('data_admissao'))
            motorista.situacao = request.form.get('situacao')
            motorista.data_desativacao = to_date(request.form.get('data_desativacao'))
            motorista.classificacao = request.form.get('classificacao')
            motorista.cod_departamento = request.form.get('cod_departamento')
            motorista.numero_ficha = request.form.get('numero_ficha')
            motorista.cep = re.sub(r'\D', '', request.form.get('cep', ''))
            motorista.tipo_logradouro = request.form.get('tipo_logradouro')
            motorista.logradouro = request.form.get('logradouro')
            motorista.numero = request.form.get('numero')
            motorista.complemento = request.form.get('complemento')
            motorista.bairro = request.form.get('bairro')
            motorista.cidade = request.form.get('cidade')
            motorista.uf = request.form.get('uf')
            motorista.email = request.form.get('email')
            motorista.tipo_imovel = request.form.get('tipo_imovel')
            motorista.tempo_residencia = request.form.get('tempo_residencia')
            motorista.cnh_numero = cnh_numero
            motorista.cnh_data_primeira = to_date(request.form.get('cnh_data_primeira'))
            motorista.cnh_data_vencimento = to_date(request.form.get('cnh_data_vencimento'))
            motorista.cnh_categoria = request.form.get('cnh_categoria')
            motorista.cnh_cod_seguranca = request.form.get('cnh_cod_seguranca')
            motorista.rg = request.form.get('rg')
            motorista.rg_uf = request.form.get('rg_uf')
            motorista.pis = request.form.get('pis')
            motorista.inss = request.form.get('inss')
            motorista.titulo_eleitor = request.form.get('titulo_eleitor')
            motorista.ctps = request.form.get('ctps')
            motorista.funcao = request.form.get('funcao')

            # ▼▼▼ LINHA ADICIONADA AQUI ▼▼▼
            motorista.salario_base = request.form.get('salario_base', type=float, default=0.0)
            
            motorista.mopp_numero = request.form.get('mopp_numero')
            motorista.mopp_vencimento = to_date(request.form.get('mopp_vencimento'))
            motorista.contato_nome = request.form.get('contato_nome')
            motorista.contato_tipo_ref = request.form.get('contato_tipo_ref')
            motorista.contato_tipo_fone = request.form.get('contato_tipo_fone')
            motorista.contato_telefone = re.sub(r'\D', '', request.form.get('contato_telefone', ''))
            motorista.contato_operadora = request.form.get('contato_operadora')
            motorista.contato_obs = request.form.get('contato_obs')

            # Salva as alterações no banco de dados
            db.session.commit()
            flash('Dados do motorista atualizados com sucesso!', 'success')
            return redirect(url_for('editar_motorista', motorista_id=motorista_id))

        except Exception as e:
            db.session.rollback()
            logger.error(f"Erro ao editar motorista {motorista_id}: {e}", exc_info=True)
            flash(f'Ocorreu um erro ao salvar as alterações: {e}', 'error')
            return redirect(url_for('editar_motorista', motorista_id=motorista_id))

    # Método GET: apenas renderiza a página com os dados do motorista
    return render_template('editar_motorista.html', motorista=motorista)

@app.route('/relatorios/dre')
@login_required
def relatorio_financeiro_dre():
    """
    Rota para gerar um Demonstrativo de Resultado do Exercício (DRE) simplificado.
    Calcula receitas e custos fixos/variáveis dentro de um período.
    """
    try:
        # Pega as datas do filtro, com valores padrão para o mês atual
        hoje = date.today()
        primeiro_dia_mes_str = hoje.replace(day=1).strftime('%Y-%m-%d')
        data_inicio_str = request.args.get('data_inicio', primeiro_dia_mes_str)
        data_fim_str = request.args.get('data_fim', hoje.strftime('%Y-%m-%d'))
        
        data_inicio = datetime.strptime(data_inicio_str, '%Y-%m-%d')
        # Adiciona um dia ao fim para incluir o dia inteiro na consulta
        data_fim = datetime.strptime(data_fim_str, '%Y-%m-%d') + timedelta(days=1)

        empresa_id = current_user.empresa_id

        # 1. RECEITA BRUTA (Viagens concluídas no período)
        receita_bruta = db.session.query(func.sum(Viagem.valor_recebido)).filter(
            Viagem.empresa_id == empresa_id,
            Viagem.status == 'concluida',
            Viagem.data_fim >= data_inicio,
            Viagem.data_fim < data_fim
        ).scalar() or 0.0

        # 2. CUSTOS VARIÁVEIS (Diretamente ligados às viagens)
        custos_variaveis = defaultdict(float)
        viagens_no_periodo = Viagem.query.filter(
            Viagem.empresa_id == empresa_id,
            Viagem.data_inicio >= data_inicio,
            Viagem.data_inicio < data_fim
        ).options(db.joinedload(Viagem.custo_viagem), db.joinedload(Viagem.abastecimentos)).all()

        for viagem in viagens_no_periodo:
            if viagem.custo_viagem:
                custos_variaveis['Pedágios'] += viagem.custo_viagem.pedagios or 0
                custos_variaveis['Alimentação'] += viagem.custo_viagem.alimentacao or 0
                custos_variaveis['Hospedagem'] += viagem.custo_viagem.hospedagem or 0
                custos_variaveis['Outros (Viagem)'] += viagem.custo_viagem.outros or 0
            for abast in viagem.abastecimentos:
                custos_variaveis['Combustível'] += abast.custo_total or 0
        
        custos_variaveis['Total'] = sum(custos_variaveis.values())

        # 3. CUSTOS FIXOS (Despesas gerais dos veículos no período)
        custos_fixos = defaultdict(float)
        despesas_veiculares = DespesaVeiculo.query.filter(
            DespesaVeiculo.empresa_id == empresa_id,
            DespesaVeiculo.data >= data_inicio.date(),
            DespesaVeiculo.data < data_fim.date()
        ).all()
        for despesa in despesas_veiculares:
            custos_fixos[despesa.categoria] += despesa.valor
        
        custos_fixos_total = sum(custos_fixos.values())

        # 4. Montagem do DRE
        margem_contribuicao = receita_bruta - custos_variaveis['Total']
        lucro_operacional = margem_contribuicao - custos_fixos_total
        
        dre = {
            'receita_bruta': receita_bruta,
            'custos_variaveis': dict(custos_variaveis),
            'margem_contribuicao': margem_contribuicao,
            'custos_fixos': {'Total': custos_fixos_total, 'Detalhado': dict(custos_fixos)},
            'lucro_operacional': lucro_operacional
        }

        return render_template('relatorio_dre.html', dre=dre)
        
    except Exception as e:
        logger.error(f"Erro ao gerar relatório DRE: {e}", exc_info=True)
        flash("Ocorreu um erro ao gerar o relatório DRE.", "error")
        return redirect(url_for('relatorios'))
    
@app.route('/adicionar_anexo/motorista/<int:motorista_id>', methods=['POST'])
@login_required
def adicionar_anexo_motorista(motorista_id):
    motorista = Motorista.query.filter_by(id=motorista_id, empresa_id=current_user.empresa_id).first_or_404()
    
    novos_anexos = request.files.getlist("anexos")

    if not novos_anexos or not any(f.filename for f in novos_anexos):
        flash('Nenhum arquivo selecionado para upload.', 'warning')
        return redirect(url_for('editar_motorista', motorista_id=motorista_id))

    try:
        anexos_atuais = motorista.anexos.split(',') if motorista.anexos else []
        
        # Lógica de Upload (reutilizada da sua função original)
        bucket_name = app.config.get('CLOUDFLARE_R2_BUCKET')
        s3_client = boto3.client(
            's3',
            endpoint_url=app.config['CLOUDFLARE_R2_ENDPOINT'],
            aws_access_key_id=app.config['CLOUDFLARE_R2_ACCESS_KEY'],
            aws_secret_access_key=app.config['CLOUDFLARE_R2_SECRET_KEY'],
            region_name='auto'
        )
        public_url_base = app.config['CLOUDFLARE_R2_PUBLIC_URL']
        
        for anexo in novos_anexos:
            if anexo.filename:
                filename = secure_filename(anexo.filename)
                s3_path = f"motoristas/{motorista.cpf}/anexos/{uuid.uuid4()}-{filename}"
                s3_client.upload_fileobj(
                    anexo, bucket_name, s3_path,
                    ExtraArgs={'ContentType': anexo.content_type or 'application/octet-stream', 'ContentDisposition': 'attachment'}
                )
                anexos_atuais.append(f"{public_url_base}/{s3_path}")
        
        motorista.anexos = ','.join(filter(None, anexos_atuais))
        db.session.commit()
        flash('Anexo(s) adicionado(s) com sucesso!', 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao fazer upload do anexo: {e}', 'error')
        logger.error(f"Erro no upload de anexo para motorista {motorista_id}: {e}", exc_info=True)

    return redirect(url_for('editar_motorista', motorista_id=motorista_id))

@app.route('/relatorios/veiculos')
@login_required
def relatorio_desempenho_veiculos():
    """
    Rota para gerar um relatório detalhado do desempenho de cada veículo.
    """
    try:
        empresa_id = current_user.empresa_id
        veiculos = Veiculo.query.filter_by(empresa_id=empresa_id).all()
        stats_finais = []

        for veiculo in veiculos:
            viagens = Viagem.query.filter_by(veiculo_id=veiculo.id).all()
            abastecimentos = Abastecimento.query.filter_by(veiculo_id=veiculo.id).all()
            despesas_gerais = DespesaVeiculo.query.filter_by(veiculo_id=veiculo.id).all()
            manutencoes = Manutencao.query.filter_by(veiculo_id=veiculo.id).all()

            km_rodados = sum(v.distancia_percorrida for v in viagens)
            receita_total = sum(v.valor_recebido or 0 for v in viagens)
            
            custo_combustivel = sum(a.custo_total for a in abastecimentos)
            custo_despesas_viagens = sum(
                (v.custo_viagem.pedagios or 0) + (v.custo_viagem.alimentacao or 0) + 
                (v.custo_viagem.hospedagem or 0) + (v.custo_viagem.outros or 0)
                for v in viagens if v.custo_viagem
            )
            custo_despesas_fixas = sum(d.valor for d in despesas_gerais)
            custo_manutencoes = sum(m.custo_total or 0 for m in manutencoes)
            
            custo_total = custo_combustivel + custo_despesas_viagens + custo_despesas_fixas + custo_manutencoes
            lucro_total = receita_total - custo_total
            total_litros = sum(a.litros for a in abastecimentos)

            stats = {
                'info': veiculo,
                'km_rodados': km_rodados,
                'consumo_medio': (km_rodados / total_litros) if total_litros > 0 else 0,
                'receita_km': (receita_total / km_rodados) if km_rodados > 0 else 0,
                'custo_real_km': (custo_total / km_rodados) if km_rodados > 0 else 0,
                'lucro_km': (lucro_total / km_rodados) if km_rodados > 0 else 0,
                'custo_total': custo_total
            }
            stats_finais.append(stats)

        return render_template('relatorio_veiculos.html', veiculos_stats=stats_finais)

    except Exception as e:
        logger.error(f"Erro ao gerar relatório de veículos: {e}", exc_info=True)
        flash("Ocorreu um erro ao gerar o relatório de desempenho dos veículos.", "error")
        return redirect(url_for('relatorios'))

@app.route('/relatorios/contas_a_receber')
@login_required
def relatorio_contas_a_receber():
    """
    Rota para gerar um relatório de contas a receber, categorizado por data de vencimento.
    """
    try:
        empresa_id = current_user.empresa_id
        hoje = date.today()
        
        contas = Cobranca.query.filter(
            Cobranca.empresa_id == empresa_id,
            Cobranca.status.in_(['Pendente', 'Vencida'])
        ).order_by(Cobranca.data_vencimento.asc()).all()

        relatorio = {
            'vencidas': [], 'vence_hoje': [], 'vence_7_dias': [],
            'vence_30_dias': [], 'futuras': []
        }
        total_a_receber = 0.0

        for c in contas:
            total_a_receber += c.valor_total
            delta_dias = (c.data_vencimento - hoje).days
            
            if delta_dias < 0:
                relatorio['vencidas'].append(c)
            elif delta_dias == 0:
                relatorio['vence_hoje'].append(c)
            elif 1 <= delta_dias <= 7:
                relatorio['vence_7_dias'].append(c)
            elif 8 <= delta_dias <= 30:
                relatorio['vence_30_dias'].append(c)
            else:
                relatorio['futuras'].append(c)

        return render_template('relatorio_contas_receber.html', relatorio=relatorio, total_a_receber=total_a_receber)

    except Exception as e:
        logger.error(f"Erro ao gerar relatório de contas a receber: {e}", exc_info=True)
        flash("Ocorreu um erro ao gerar o relatório de contas a receber.", "error")
        return redirect(url_for('relatorios'))

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


@app.route('/excluir_anexo/motorista/<int:motorista_id>', methods=['POST'])
@login_required
def excluir_anexo_motorista(motorista_id):
    motorista = Motorista.query.filter_by(id=motorista_id, empresa_id=current_user.empresa_id).first_or_404()
    anexo_url_para_excluir = request.form.get('anexo_url')
    
    if not anexo_url_para_excluir:
        flash('Nenhum anexo especificado para exclusão.', 'error')
        return redirect(url_for('editar_motorista', motorista_id=motorista_id))

    anexos_atuais = motorista.anexos.split(',') if motorista.anexos else []
    
    if anexo_url_para_excluir in anexos_atuais:
        try:
            # Lógica para excluir do Cloudflare R2
            bucket_name = app.config.get('CLOUDFLARE_R2_BUCKET')
            if not bucket_name:
                flash('Erro de configuração: O nome do bucket do Cloudflare R2 não foi definido.', 'error')
                return redirect(url_for('editar_motorista', motorista_id=motorista_id))

            s3_client = boto3.client(
                's3',
                endpoint_url=app.config['CLOUDFLARE_R2_ENDPOINT'],
                aws_access_key_id=app.config['CLOUDFLARE_R2_ACCESS_KEY'],
                aws_secret_access_key=app.config['CLOUDFLARE_R2_SECRET_KEY'],
                region_name='auto'
            )
            key = anexo_url_para_excluir.replace(app.config['CLOUDFLARE_R2_PUBLIC_URL'] + '/', '')
            s3_client.delete_object(Bucket=bucket_name, Key=key)
            
            anexos_atuais.remove(anexo_url_para_excluir)
            motorista.anexos = ','.join(filter(None, anexos_atuais)) or None
            db.session.commit()
            flash('Anexo removido com sucesso!', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao remover anexo do Cloudflare: {e}', 'error')
    else:
        flash('Anexo não encontrado ou permissão negada.', 'error')
        
    return redirect(url_for('editar_motorista', motorista_id=motorista_id))

# E substitua sua função 'dateformat' por esta versão mais robusta:
@app.template_filter('dateformat')
def dateformat(value):
    if isinstance(value, (date, datetime)):
        return value.strftime('%Y-%m-%d')
    if isinstance(value, str):
        # Tenta converter a string para data antes de formatar
        try:
            return datetime.strptime(value, '%Y-%m-%d').strftime('%Y-%m-%d')
        except ValueError:
            return value
    return ''
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
        try:
            placa = request.form.get('placa', '').strip().upper()
            if not validate_placa(placa):
                flash('Placa inválida. Use o formato ABC1D23 ou ABC1234.', 'error')
                return render_template('cadastrar_veiculo.html', form_data=request.form)

            if Veiculo.query.filter_by(placa=placa, empresa_id=current_user.empresa_id).first():
                flash(f'Erro: Um veículo com a placa {placa} já foi cadastrado.', 'error')
                return render_template('cadastrar_veiculo.html', form_data=request.form)

            fotos_urls = []
            files = request.files.getlist('fotos[]')
            if files and any(f and f.filename for f in files):
                s3_client = boto3.client(
                    's3',
                    endpoint_url=app.config['CLOUDFLARE_R2_ENDPOINT'],
                    aws_access_key_id=app.config['CLOUDFLARE_R2_ACCESS_KEY'],
                    aws_secret_access_key=app.config['CLOUDFLARE_R2_SECRET_KEY'],
                    region_name='auto'
                )
                bucket_name = app.config['CLOUDFLARE_R2_BUCKET']
                public_url_base = app.config['CLOUDFLARE_R2_PUBLIC_URL']

                for file in files:
                    if file and file.filename:
                        filename = secure_filename(file.filename)
                        s3_path = f"veiculos/{placa}/fotos/{uuid.uuid4()}-{filename}"
                        
                        s3_client.upload_fileobj(
                            file, bucket_name, s3_path,
                            ExtraArgs={'ContentType': file.content_type or 'application/octet-stream'}
                        )
                        fotos_urls.append(f"{public_url_base}/{s3_path}")
            
            def to_date(date_string):
                return datetime.strptime(date_string, '%Y-%m-%d').date() if date_string else None
            
            def to_float(num_string):
                return float(num_string) if num_string else None

            def to_int(num_string):
                return int(num_string) if num_string else None

            novo_veiculo = Veiculo(
                empresa_id=current_user.empresa_id,
                
                placa=placa,
                categoria=request.form.get('categoria'),
                status=request.form.get('status'),
                modelo=request.form.get('modelo'),
                marca=request.form.get('marca'),
                ano_fabricacao=to_int(request.form.get('ano_fabricacao')),
                ano_modelo=to_int(request.form.get('ano_modelo')),
                cor=request.form.get('cor').strip() or None,
                combustivel=request.form.get('combustivel') or None,
                
                # --- CORREÇÃO APLICADA AQUI ---
                renavam=request.form.get('renavam').strip() or None,
                chassi=request.form.get('chassi').strip() or None,
                numero_motor=request.form.get('numero_motor').strip() or None,
                crlv_numero=request.form.get('crlv_numero').strip() or None,
                # ------------------------------

                crlv_vencimento=to_date(request.form.get('crlv_vencimento')),
                seguro_numero=request.form.get('seguro_numero').strip() or None,
                seguro_seguradora=request.form.get('seguro_seguradora').strip() or None,
                seguro_vencimento=to_date(request.form.get('seguro_vencimento')),
                
                capacidade_carga_kg=to_float(request.form.get('capacidade_carga')),
                peso_bruto_total_kg=to_float(request.form.get('peso_bruto')),
                eixos=to_int(request.form.get('eixos')),
                cilindrada=request.form.get('cilindrada').strip() or None,
                potencia_cv=to_int(request.form.get('potencia')),
                tanque_combustivel_litros=to_int(request.form.get('tanque_combustivel')),
                consumo_medio_km_l=to_float(request.form.get('consumo_medio')),
                
                valor_aquisicao=to_float(request.form.get('valor_aquisicao')),
                data_aquisicao=to_date(request.form.get('data_aquisicao')),
                km_atual=to_float(request.form.get('km_atual')),
                ultima_manutencao=to_date(request.form.get('ultima_manutencao')),
                km_ultima_manutencao=to_float(request.form.get('km_ultima_manutencao')),
                proxima_manutencao=to_date(request.form.get('proxima_manutencao')),
                motorista_padrao_id=to_int(request.form.get('motorista_padrao')),
                
                observacoes=request.form.get('observacoes').strip() or None,
                fotos_urls=','.join(fotos_urls) if fotos_urls else None
            )

            db.session.add(novo_veiculo)
            db.session.commit()
            flash('Veículo cadastrado com sucesso!', 'success')
            return redirect(url_for('consultar_veiculos'))

        except (ValueError, TypeError) as e:
            db.session.rollback()
            logger.error(f"Erro de tipo/valor ao cadastrar veículo: {e}", exc_info=True)
            flash('Erro de valor inválido. Verifique se os números e datas estão corretos.', 'error')
        except Exception as e:
            db.session.rollback()
            logger.error(f"Erro ao cadastrar veículo: {e}", exc_info=True)
            flash(f'Ocorreu um erro inesperado ao cadastrar o veículo: {e}', 'error')
        
        return render_template('cadastrar_veiculo.html', form_data=request.form, active_page='cadastrar_veiculo')

    return render_template('cadastrar_veiculo.html', active_page='cadastrar_veiculo')

@app.route('/consultar_veiculos', methods=['GET'])
@login_required
def consultar_veiculos():
    search_query = request.args.get('search', '').strip()
    query = Veiculo.query.filter_by(empresa_id=current_user.empresa_id)
    
    if search_query:
        search_filter = f"%{search_query}%"
        query = query.filter(
            or_(
                Veiculo.placa.ilike(search_filter),
                Veiculo.modelo.ilike(search_filter),
                Veiculo.categoria.ilike(search_filter)
            )
        )
    

    veiculos_obj = query.order_by(Veiculo.placa.asc()).all()
    
    veiculos_json = [v.to_dict() for v in veiculos_obj]

    # Passo 3: Envia a lista de dicionários (veiculos_json) para o template
    return render_template('consultar_veiculos.html', veiculos=veiculos_json, search_query=search_query, active_page='consultar_veiculos')

@app.route('/editar_veiculo/<int:veiculo_id>', methods=['GET', 'POST'])
@login_required
def editar_veiculo(veiculo_id):
    veiculo = Veiculo.query.filter_by(id=veiculo_id, empresa_id=current_user.empresa_id).first_or_404()
    motoristas = Motorista.query.filter_by(empresa_id=current_user.empresa_id).order_by(Motorista.nome).all()

    if request.method == 'POST':
        try:
            def to_int(val): return int(val) if val and val.strip() else None
            def to_float(val): return float(val) if val and val.strip() else None
            def to_date(val): return datetime.strptime(val, '%Y-%m-%d').date() if val and val.strip() else None

            veiculo.categoria = request.form.get('categoria')
            veiculo.status = request.form.get('status')
            veiculo.modelo = request.form.get('modelo')
            veiculo.marca = request.form.get('marca')
            veiculo.ano_fabricacao = to_int(request.form.get('ano_fabricacao'))
            veiculo.ano_modelo = to_int(request.form.get('ano_modelo'))
            veiculo.cor = request.form.get('cor').strip() or None
            veiculo.combustivel = request.form.get('combustivel') or None

            # --- CORREÇÃO APLICADA AQUI ---
            veiculo.renavam = request.form.get('renavam').strip() or None
            veiculo.chassi = request.form.get('chassi').strip() or None
            veiculo.crlv_numero = request.form.get('crlv_numero').strip() or None
            # ------------------------------
            
            veiculo.crlv_vencimento = to_date(request.form.get('crlv_vencimento'))
            
            veiculo.km_atual = to_float(request.form.get('km_atual'))
            veiculo.motorista_padrao_id = to_int(request.form.get('motorista_padrao_id'))
            veiculo.observacoes = request.form.get('observacoes').strip() or None

            db.session.commit()
            
            flash('Veículo atualizado com sucesso!', 'success')
            return redirect(url_for('consultar_veiculos'))

        except Exception as e:
            db.session.rollback()
            logger.error(f"Erro ao editar o veículo {veiculo_id}: {e}", exc_info=True)
            flash(f'Ocorreu um erro inesperado ao salvar: {str(e)}', 'error')
            return redirect(url_for('editar_veiculo', veiculo_id=veiculo_id))

    return render_template('editar_veiculo.html', 
                           veiculo=veiculo, 
                           motoristas=motoristas, 
                           active_page='consultar_veiculos')
                           
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

@app.route('/api/viagem/processar_nfe', methods=['POST'])
@login_required
def processar_nfe_api():
    if 'nfe_files' not in request.files:
        return jsonify({'success': False, 'message': 'Nenhum arquivo enviado.'}), 400
    
    files = request.files.getlist('nfe_files')
    viagens_processadas = []
    clientes_criados = 0

    for file in files:
        if file and file.filename.endswith('.xml'):
            parsed_data = parse_nfe_xml(file)
            if not parsed_data:
                continue

            cliente_info = parsed_data['cliente_info']
            viagem_info = parsed_data['viagem_info']
            
            try:
                if cliente_info and cliente_info['cpf_cnpj']:
                    cliente_existente = Cliente.query.filter_by(
                        cpf_cnpj=cliente_info['cpf_cnpj'],
                        empresa_id=current_user.empresa_id
                    ).first()

                    if not cliente_existente:
                        novo_cliente = Cliente(
                            empresa_id=current_user.empresa_id,
                            cadastrado_por_id=current_user.id,
                            **cliente_info
                        )
                        db.session.add(novo_cliente)
                        clientes_criados += 1
                
                viagens_processadas.append(viagem_info)

            except Exception as e:
                db.session.rollback()
                logger.error(f"Erro ao criar cliente a partir do XML {viagem_info['nome_arquivo']}: {e}", exc_info=True)
                continue

    if clientes_criados > 0:
        db.session.commit()

    if not viagens_processadas:
        return jsonify({'success': False, 'message': 'Nenhum dado de viagem pôde ser extraído dos arquivos XML fornecidos.'}), 400

    mensagem_sucesso = f"{len(viagens_processadas)} NF-e(s) processada(s). "
    if clientes_criados > 0:
        mensagem_sucesso += f"E {clientes_criados} novo(s) cliente(s) foram cadastrados automaticamente."

    return jsonify({'success': True, 'viagens': viagens_processadas, 'message': mensagem_sucesso})

@app.route('/viagem/importar_nfe')
@login_required
def importar_nfe_page():
    """Renderiza a nova página de importação."""
    motoristas = Motorista.query.filter_by(empresa_id=current_user.empresa_id, situacao='NORMAL / LIBERADO').order_by(Motorista.nome).all()
    veiculos = Veiculo.query.filter_by(status='Disponível', empresa_id=current_user.empresa_id).order_by(Veiculo.placa).all()
    return render_template('importar_nfe.html', motoristas=motoristas, veiculos=veiculos)

@app.route('/iniciar_viagem', methods=['GET'])
@login_required
def iniciar_viagem_page():
    """Apenas renderiza a página do formulário de iniciar viagem."""
    motoristas = Motorista.query.filter_by(empresa_id=current_user.empresa_id).order_by(Motorista.nome).all()
    veiculos = Veiculo.query.filter_by(status='Disponível', empresa_id=current_user.empresa_id).order_by(Veiculo.placa).all()
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
        valor_recebido = float(data.get('valor_recebido') or 0) # Captura o valor recebido
        
        if not all([motorista_id, veiculo_id, cliente, endereco_saida, enderecos_destino, data_inicio_str]):
            return jsonify({'success': False, 'message': 'Todos os campos são obrigatórios.'}), 400

        motorista = db.session.get(Motorista, int(motorista_id))
        veiculo = db.session.get(Veiculo, int(veiculo_id))
        if not motorista or not veiculo:
            return jsonify({'success': False, 'message': 'Motorista ou Veículo não encontrado.'}), 404
        if veiculo.status != 'Disponível':
            return jsonify({'success': False, 'message': f'Veículo {veiculo.placa} não está disponível (Status: {veiculo.status}).'}), 409
            
        todos_enderecos = [endereco_saida] + enderecos_destino
        
        rota_otimizada, distancia_km, duracao_segundos, geometria, erro = calcular_rota_otimizada_ors(todos_enderecos)

        if erro:
            return jsonify({'success': False, 'message': erro}), 400

        # --- CÁLCULO DA ESTIMATIVA DE CUSTO (Lógica reutilizada da API de estimativa) ---
        consumo_real = calcular_consumo_medio_real(veiculo.id)
        consumo_a_ser_usado = consumo_real or veiculo.consumo_medio_km_l or 1.0
        preco_combustivel_para_calculo = obter_preco_medio_combustivel_recente(current_user.empresa_id)
        litros_estimados = distancia_km / consumo_a_ser_usado
        custo_combustivel = litros_estimados * preco_combustivel_para_calculo

        salario_base = motorista.salario_base or 0.0
        custo_hora_motorista = salario_base / 220
        duracao_horas = duracao_segundos / 3600
        custo_motorista = duracao_horas * custo_hora_motorista

        custo_fixo_km = calcular_custo_fixo_por_km(veiculo.id)
        custo_manutencao_km = calcular_custo_manutencao_por_km(veiculo.id)
        custo_desgaste_veiculo = distancia_km * (custo_fixo_km + custo_manutencao_km)
        
        custo_total_estimado = custo_combustivel + custo_motorista + custo_desgaste_veiculo
        lucro_estimado = valor_recebido - custo_total_estimado
        # --- FIM DO CÁLCULO ---

        nova_viagem = Viagem(
            motorista_id=motorista_id,
            motorista_cpf_cnpj=motorista.cpf_cnpj,
            veiculo_id=veiculo_id,
            cliente=cliente,
            valor_recebido=valor_recebido,
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
        veiculo.status = 'Em Rota'
        db.session.add(nova_viagem)
        db.session.flush()

        for ordem, endereco in enumerate(rota_otimizada[1:], 1):
            destino = Destino(viagem_id=nova_viagem.id, endereco=endereco, ordem=ordem)
            db.session.add(destino)

        db.session.commit()

        # Adiciona os novos dados ao JSON de retorno
        return jsonify({
            'success': True,
            'message': 'Viagem criada com sucesso!',
            'viagem_id': nova_viagem.id,
            'roteiro': rota_otimizada,
            'distancia': f"{distancia_km:.2f}",
            'duracao_minutos': duracao_segundos // 60,
           
            'custo_estimado': f"{custo_total_estimado:.2f}",
            'lucro_estimado': f"{lucro_estimado:.2f}"
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
            # LINHA A SER CORRIGIDA:
            viagem.veiculo.status = 'Disponível' #
        db.session.delete(viagem)
        db.session.commit()
        flash('Viagem excluída com sucesso!', 'success')

    except Exception as e:
        db.session.rollback()
        logger.error(f"Erro ao excluir viagem: {str(e)}")
        flash(f'Erro ao excluir viagem: {str(e)}', 'error')

    return redirect(url_for('consultar_viagens'))

@app.route('/fiscal/configuracao', methods=['GET', 'POST'])
@login_required
@admin_required # Apenas administradores podem configurar
def configuracao_fiscal():
    # A query agora usa o modelo 'CertificadoDigital' importado de 'extensions.py'
    certificado = CertificadoDigital.query.filter_by(empresa_id=current_user.empresa_id).first()

    if request.method == 'POST':
        arquivo_cert = request.files.get('certificado')
        senha_cert = request.form.get('senha_certificado')
        validade_cert_str = request.form.get('validade_certificado')

        if not all([arquivo_cert, senha_cert, validade_cert_str]):
            flash('Todos os campos são obrigatórios.', 'error')
            return redirect(url_for('configuracao_fiscal'))

        try:
            validade_cert = datetime.strptime(validade_cert_str, '%Y-%m-%d').date()
            
            # Lógica de upload para o Cloudflare R2
            s3_client = boto3.client(
                's3',
                endpoint_url=app.config['CLOUDFLARE_R2_ENDPOINT'],
                aws_access_key_id=app.config['CLOUDFLARE_R2_ACCESS_KEY'],
                aws_secret_access_key=app.config['CLOUDFLARE_R2_SECRET_KEY'],
                region_name='auto'
            )
            bucket_name = app.config['CLOUDFLARE_R2_BUCKET']
            public_url_base = app.config['CLOUDFLARE_R2_PUBLIC_URL']
            
            filename = secure_filename(arquivo_cert.filename)
            s3_path = f"certificados/{current_user.empresa_id}/{uuid.uuid4()}-{filename}"
            
            s3_client.upload_fileobj(
                arquivo_cert, bucket_name, s3_path,
                ExtraArgs={'ContentType': 'application/x-pkcs12'}
            )
            
            if not certificado:
                certificado = CertificadoDigital(empresa_id=current_user.empresa_id)
                db.session.add(certificado)
            
            certificado.nome_arquivo = filename
            certificado.caminho_r2 = s3_path
            
            # --- CORREÇÃO APLICADA AQUI ---
            # Passando o objeto 'cipher_suite' para a função set_senha, como definido no modelo.
            certificado.set_senha(senha_cert, cipher_suite)
            
            certificado.data_validade = validade_cert
            
            db.session.commit()
            flash('Certificado digital salvo com sucesso!', 'success')
            
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao salvar certificado: {e}', 'error')
            logger.error(f"Erro ao salvar certificado: {e}", exc_info=True)

        return redirect(url_for('configuracao_fiscal'))

    return render_template('configuracao_fiscal.html', certificado=certificado, hoje=date.today())

@app.route('/fiscal/importar')
@login_required
@master_required
def importar_notas_fiscais():
    # A query agora usa o modelo 'NFeImportada' importado de 'extensions.py'
    notas_importadas = NFeImportada.query.filter_by(
        empresa_id=current_user.empresa_id
    ).order_by(NFeImportada.data_emissao.desc()).all()
    
    return render_template('importar_notas_fiscais.html', notas=notas_importadas)

# API PARA DISPARAR A CONSULTA NA SEFAZ
@app.route('/api/fiscal/consultar_sefaz', methods=['POST'])
@login_required
def api_consultar_sefaz():
    # Esta rota chama a função do sefaz_service, que foi corrigido para não causar o loop.
    resultado = consultar_notas_sefaz(current_user.empresa_id)
    return jsonify(resultado)


@app.route('/api/fiscal/buscar_dados_nota/<string:chave_acesso>')
@login_required
def api_buscar_dados_nota(chave_acesso):
    """
    Esta API apenas BUSCA os dados de uma nota para exibi-los no modal.
    Ela não salva nem altera nada.
    """
    nota = NFeImportada.query.filter_by(
        chave_acesso=chave_acesso,
        empresa_id=current_user.empresa_id
    ).first()

    if not nota:
        return jsonify({'success': False, 'message': 'Nota não encontrada na base local. Realize a consulta à SEFAZ primeiro.'}), 404

    if nota.status == 'PROCESSADA':
        return jsonify({'success': False, 'message': 'Esta nota já foi processada anteriormente.'}), 409
    
    # Prepara os dados da nota para serem retornados como JSON
    dados_da_nota = {
        "emitente_nome": nota.emitente_nome,
        "emitente_cnpj": nota.emitente_cnpj,
        "valor_total": nota.valor_total,
        "data_emissao": nota.data_emissao.isoformat() # Formato ISO para o JS ler fácil
    }
    
    return jsonify({'success': True, 'nota': dados_da_nota})


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

        viagem = Viagem.query.filter_by(id=viagem_id, empresa_id=current_user.empresa_id).first_or_404()

        status_antigo = viagem.status
        viagem.status = novo_status

        if novo_status in ['concluida', 'cancelada']:
            if not viagem.data_fim:
                viagem.data_fim = datetime.utcnow()
            if viagem.veiculo:
                viagem.veiculo.status = 'Disponível' #

        elif novo_status == 'em_andamento' and status_antigo in ['concluida', 'cancelada']:
            viagem.data_fim = None
            if viagem.veiculo:
                outra_viagem_ativa = Viagem.query.filter(
                    Viagem.veiculo_id == viagem.veiculo_id,
                    Viagem.veiculo.has(status='Em Rota'), # Certifique-se de que o filtro aqui também está correto.
                    Viagem.id != viagem.id
                ).first()

                if outra_viagem_ativa:
                    flash(f'Erro: O veículo {viagem.veiculo.placa} já está em uso na viagem #{outra_viagem_ativa.id}.', 'error')
                    db.session.rollback()
                    return jsonify({'success': False, 'message': f'Veículo já está em outra viagem.'}), 409

                # LINHA A SER CORRIGIDA:
                viagem.veiculo.status = 'Em Rota' #

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
    odometro_str = data.get('odometer')

    try:
        odometro_final = float(odometro_str)
        if viagem.odometro_inicial is not None and odometro_final < viagem.odometro_inicial:
            return jsonify({'success': False, 'message': 'Odômetro final não pode ser menor que o inicial.'}), 400
    except (ValueError, TypeError):
        return jsonify({'success': False, 'message': 'Odômetro final inválido. Por favor, insira um número válido.'}), 400

    viagem.status = 'concluida'
    viagem.data_fim = datetime.utcnow()
    viagem.odometro_final = odometro_final
    
    if viagem.veiculo:
        viagem.veiculo.status = 'Disponível'
        viagem.veiculo.km_rodados = odometro_final
        
    db.session.commit()
    
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

@app.route('/oficina')
@login_required
def oficina():
    """ Rota principal do dashboard da oficina. """
    todos_veiculos_obj = Veiculo.query.filter_by(empresa_id=current_user.empresa_id).order_by(Veiculo.modelo).all()
    
    alertas = []
    veiculos_data = []
    limite_km_alerta = 1000
    limite_dias_alerta = 30

    for veiculo in todos_veiculos_obj:
        planos_progresso = []
        progresso_maximo = -1
        next_maint_summary = {}

        for plano_assoc in veiculo.planos_associados:
            alerta_gerado = False
            mensagem = ""
            km_desde_ultima = 0
            progresso_km = 0
            
            if plano_assoc.intervalo_km and plano_assoc.intervalo_km > 0 and veiculo.km_rodados is not None:
                km_desde_ultima = (veiculo.km_rodados or 0) - (plano_assoc.km_ultima_manutencao or 0)
                if km_desde_ultima < 0: km_desde_ultima = 0
                
                progresso_km = (km_desde_ultima / plano_assoc.intervalo_km) * 100

                if km_desde_ultima >= plano_assoc.intervalo_km:
                    alerta_gerado = True
                    mensagem = "Vencido por KM"
                elif plano_assoc.intervalo_km - km_desde_ultima <= limite_km_alerta:
                    alerta_gerado = True
                    mensagem = "Próximo por KM"

            planos_progresso.append({
                "descricao": plano_assoc.plano.descricao,
                "progresso": int(progresso_km),
                "km_desde_ultima": km_desde_ultima,
                "intervalo_km": plano_assoc.intervalo_km,
            })

            if not alerta_gerado and plano_assoc.intervalo_meses and plano_assoc.data_ultima_manutencao:
                data_proxima = plano_assoc.data_ultima_manutencao + timedelta(days=plano_assoc.intervalo_meses * 30)
                dias_restantes = (data_proxima - date.today()).days
                if dias_restantes <= 0:
                    alerta_gerado = True
                    mensagem = "Vencido por tempo"
                elif dias_restantes <= limite_dias_alerta:
                    alerta_gerado = True
                    mensagem = "Próximo por tempo"

            if alerta_gerado:
                alertas.append({
                    "veiculo": veiculo,
                    "plano": plano_assoc.plano,
                    "km_ultima_manutencao": plano_assoc.km_ultima_manutencao,
                    "intervalo_km": plano_assoc.intervalo_km,
                    "mensagem": mensagem
                })

            if progresso_km > progresso_maximo:
                progresso_maximo = progresso_km
                next_maint_summary = {
                    "descricao": plano_assoc.plano.descricao,
                    "progresso": int(progresso_km),
                    "km_desde_ultima": km_desde_ultima,
                    "intervalo_km": plano_assoc.intervalo_km
                }

        manutencao_id_ativa = None
        if veiculo.status == 'Em Manutenção':
            manutencao_ativa = Manutencao.query.filter_by(veiculo_id=veiculo.id, status='Em Andamento').first()
            if manutencao_ativa:
                manutencao_id_ativa = manutencao_ativa.id
                
        veiculos_data.append({
            "id": veiculo.id,
            "modelo": veiculo.modelo,
            "placa": veiculo.placa,
            "km_rodados": veiculo.km_rodados or 0,
            "status": veiculo.status,
            "manutencao_id": manutencao_id_ativa,
            "proxima_manutencao": next_maint_summary if progresso_maximo > -1 else None,
            "planos_progresso": sorted(planos_progresso, key=lambda x: x['progresso'], reverse=True)
        })

    manutencoes_em_andamento = Manutencao.query.filter(
        Manutencao.veiculo.has(empresa_id=current_user.empresa_id),
        Manutencao.status.in_(['Em Andamento', 'Agendada'])
    ).all()
    
    hoje = date.today()
    primeiro_dia_mes = hoje.replace(day=1)
    q_kpis = db.session.query(
        func.count(Manutencao.id).label('concluidas_no_mes'),
        func.sum(Manutencao.custo_total).label('custo_mes_atual')
    ).join(Veiculo).filter(
        Veiculo.empresa_id == current_user.empresa_id,
        Manutencao.status == 'Concluída',
        Manutencao.data_saida >= primeiro_dia_mes
    ).first()
    kpis = {
        'concluidas_no_mes': q_kpis.concluidas_no_mes or 0,
        'custo_mes_atual': q_kpis.custo_mes_atual or 0.0,
    }

    alertas_de_estoque = Insumo.query.filter(
        Insumo.empresa_id == current_user.empresa_id,
        Insumo.ponto_ressuprimento.isnot(None),
        Insumo.quantidade_em_estoque <= Insumo.ponto_ressuprimento
    ).all()

    return render_template(
        'oficina.html',
        alertas=alertas,
        manutencoes_em_andamento=manutencoes_em_andamento,
        todos_veiculos=veiculos_data,
        kpis=kpis,
        alertas_de_estoque=alertas_de_estoque
    )

@app.route('/oficina/iniciar', methods=['POST'])
@login_required
def iniciar_manutencao_oficina():
    """ Inicia uma nova manutenção, com automação de kit de peças. """
    veiculo_id = request.form.get('veiculo_id', type=int)
    hodometro = request.form.get('hodometro', type=int)
    status = request.form.get('status')
    plano_id_str = request.form.get('plano_id')
    descricao = request.form.get('descricao')

    veiculo = Veiculo.query.get_or_404(veiculo_id)
    
    nova_manutencao = Manutencao(
        veiculo_id=veiculo.id,
        odometro=hodometro,
        status=status,
        empresa_id=current_user.empresa_id
    )

    if plano_id_str and plano_id_str.isdigit():
        plano_id = int(plano_id_str)
        nova_manutencao.tipo_manutencao = 'Preventiva'
        
        plano_assoc = VeiculoPlano.query.filter_by(veiculo_id=veiculo_id, plano_id=plano_id).first()
        if plano_assoc:
            nova_manutencao.veiculo_plano_veiculo_id = veiculo_id
            nova_manutencao.veiculo_plano_plano_id = plano_id
        
        kit_de_insumos = PlanoInsumo.query.filter_by(plano_id=plano_id).all()
        
        for item_do_kit in kit_de_insumos:
            item_manutencao = ManutencaoItem(
                manutencao=nova_manutencao, 
                data=date.today(),
                descricao=item_do_kit.insumo.descricao,
                quantidade=item_do_kit.quantidade,
                custo_unitario=item_do_kit.insumo.custo_unitario_medio or 0.0
            )
            db.session.add(item_manutencao)
            
    else:
        nova_manutencao.tipo_manutencao = 'Corretiva'
        nova_manutencao.descricao_problema = descricao

    if status == 'Em Andamento':
        veiculo.status = 'Em Manutenção'
    
    veiculo.km_rodados = max(veiculo.km_rodados or 0, hodometro)

    db.session.add(nova_manutencao)
    db.session.commit()

    flash(f'Manutenção registrada! Status do veículo {veiculo.placa} atualizado.', 'success')
    return redirect(url_for('oficina'))

@app.route('/oficina/finalizar', methods=['POST'])
@login_required
def finalizar_manutencao_oficina():
    """ Finaliza uma manutenção, com automação de baixa de estoque. """
    manutencao_id = request.form.get('manutencao_id', type=int)
    manutencao = Manutencao.query.get_or_404(manutencao_id)
    
    veiculo = manutencao.veiculo
    
    manutencao.status = 'Concluída'
    manutencao.data_saida = datetime.utcnow()
    manutencao.servicos_executados = request.form.get('servicos_executados')
    manutencao.custo_total = request.form.get('custo_total', type=float)
    
    veiculo.status = 'Disponível'
    veiculo.km_rodados = max(veiculo.km_rodados or 0, manutencao.odometro)

    if manutencao.tipo_manutencao == 'Preventiva' and manutencao.veiculo_plano_associado:
        assoc = manutencao.veiculo_plano_associado
        assoc.km_ultima_manutencao = manutencao.odometro
        assoc.data_ultima_manutencao = manutencao.data_saida.date()

    try:
        itens_utilizados = ManutencaoItem.query.filter_by(manutencao_id=manutencao.id).all()
        for item in itens_utilizados:
            insumo_correspondente = Insumo.query.filter_by(
                descricao=item.descricao,
                empresa_id=current_user.empresa_id
            ).first()
            
            if insumo_correspondente:
                insumo_correspondente.quantidade_em_estoque -= item.quantidade
        
        db.session.commit()
        flash(f'Manutenção finalizada e estoque atualizado! Veículo {veiculo.placa} agora está disponível.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Manutenção finalizada, mas ocorreu um erro ao atualizar o estoque: {e}', 'error')
        logger.error(f"Erro na baixa de estoque para manutenção {manutencao_id}: {e}", exc_info=True)

    return redirect(url_for('oficina'))

@app.route('/api/planos', methods=['GET'])
@login_required
def api_get_todos_planos():
    planos = PlanoDeManutencao.query.order_by(PlanoDeManutencao.descricao).all()
    return jsonify([{
        'id': plano.id,
        'descricao': plano.descricao,
        'intervalo_km_padrao': plano.intervalo_km_padrao,
        'intervalo_meses_padrao': plano.intervalo_meses_padrao
    } for plano in planos])

@app.route('/api/veiculo/<int:veiculo_id>/planos', methods=['GET', 'POST'])
@login_required
def api_gerenciar_planos_veiculo(veiculo_id):
    veiculo = Veiculo.query.get_or_404(veiculo_id)
    if veiculo.empresa_id != current_user.empresa_id:
        return jsonify({'message': 'Acesso negado'}), 403

    if request.method == 'GET':
        planos_atribuidos = [{
            'id': assoc.plano.id,
            'descricao': assoc.plano.descricao,
            'intervalo_km': assoc.intervalo_km,
            'intervalo_meses': assoc.intervalo_meses,
            'km_ultima_manutencao': assoc.km_ultima_manutencao,
            'data_ultima_manutencao': assoc.data_ultima_manutencao.strftime('%Y-%m-%d') if assoc.data_ultima_manutencao else None,
        } for assoc in veiculo.planos_associados]
        return jsonify(planos_atribuidos)

    if request.method == 'POST':
        try:
            data = request.json
            # Deleta as associações antigas para recriá-las com os novos dados
            VeiculoPlano.query.filter_by(veiculo_id=veiculo_id).delete()
            
            for plano_data in data:
                plano = PlanoDeManutencao.query.filter_by(descricao=plano_data['descricao']).first()
                if not plano: # Se o plano não existe, cria um novo
                    plano = PlanoDeManutencao(descricao=plano_data['descricao'])
                    db.session.add(plano)
                    db.session.flush() # Garante que o 'plano.id' esteja disponível

                data_ultima = None
                if data_str := plano_data.get('data_ultima_manutencao'):
                    data_ultima = datetime.strptime(data_str, '%Y-%m-%d').date()

                nova_assoc = VeiculoPlano(
                    veiculo_id=veiculo.id,
                    plano_id=plano.id,
                    intervalo_km=plano_data.get('intervalo_km'),
                    intervalo_meses=plano_data.get('intervalo_meses'),
                    km_ultima_manutencao=plano_data.get('km_ultima_manutencao'), # Salva o valor exato (número ou null)
                    data_ultima_manutencao=data_ultima
                )
                db.session.add(nova_assoc)
                if plano_data.get('atualizar_padrao'):
                    plano.intervalo_km_padrao = int(plano_data['intervalo_km']) if plano_data.get('intervalo_km') else None
                    plano.intervalo_meses_padrao = int(plano_data['intervalo_meses']) if plano_data.get('intervalo_meses') else None
            
            db.session.commit()
            return jsonify({'message': 'Planos atualizados com sucesso!'}), 200
        except Exception as e:
            db.session.rollback()
            logger.error(f"Erro ao salvar planos para veiculo {veiculo_id}: {e}", exc_info=True)
            return jsonify({'message': f'Erro interno do servidor: {e}'}), 500

@app.route('/api/manutencao/<int:manutencao_id>/itens', methods=['GET'])
@login_required
def get_manutencao_itens(manutencao_id):
    manutencao = Manutencao.query.join(Veiculo).filter(
        Manutencao.id == manutencao_id,
        Veiculo.empresa_id == current_user.empresa_id
    ).first_or_404()
    
    itens = [{
        'id': item.id,
        'data': item.data.strftime('%d/%m/%Y'),
        'descricao': item.descricao,
        'quantidade': item.quantidade,
        'custo_unitario': item.custo_unitario,
        'custo_total_item': item.custo_total_item
    } for item in manutencao.itens]
    
    return jsonify(itens)

@app.route('/api/manutencao/<int:manutencao_id>/adicionar_item', methods=['POST'])
@login_required
def add_manutencao_item(manutencao_id):
    manutencao = Manutencao.query.join(Veiculo).filter(
        Manutencao.id == manutencao_id,
        Veiculo.empresa_id == current_user.empresa_id
    ).first_or_404()
    
    data = request.json
    try:
        novo_item = ManutencaoItem(
            manutencao_id=manutencao.id,
            data=datetime.strptime(data['data'], '%Y-%m-%d').date(),
            descricao=data['descricao'],
            quantidade=float(data['quantidade']),
            custo_unitario=float(data['custo_unitario'])
        )
        db.session.add(novo_item)
        db.session.commit()

        custo_atualizado = db.session.query(func.sum(ManutencaoItem.quantidade * ManutencaoItem.custo_unitario)).filter_by(manutencao_id=manutencao.id).scalar()
        manutencao.custo_total = custo_atualizado or 0
        db.session.commit()

        return jsonify({'success': True, 'message': 'Item adicionado com sucesso!'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/veiculo/<int:veiculo_id>/historico')
@login_required
def api_historico_veiculo(veiculo_id):
    query = db.session.query(
        Manutencao,
        PlanoDeManutencao.descricao.label('plano_descricao')
    ).outerjoin(
        VeiculoPlano, and_(
            Manutencao.veiculo_plano_veiculo_id == VeiculoPlano.veiculo_id,
            Manutencao.veiculo_plano_plano_id == VeiculoPlano.plano_id
        )
    ).outerjoin(PlanoDeManutencao).filter(Manutencao.veiculo_id == veiculo_id).order_by(Manutencao.data_entrada.desc())

    historico = [{
        'data_saida': m.data_saida.isoformat() if m.data_saida else None,
        'data_entrada': m.data_entrada.isoformat() if m.data_entrada else None,
        'tipo_manutencao': m.tipo_manutencao,
        'plano_descricao': plano_desc,
        'odometro': m.odometro,
        'servicos_executados': m.servicos_executados,
        'descricao_problema': m.descricao_problema,
        'custo_total': m.custo_total,
        'status': m.status
    } for m, plano_desc in query.all()]
    return jsonify(historico)

@app.route('/api/manutencoes/historico', methods=['GET'])
@login_required
def get_historico_manutencoes():
    query = Manutencao.query.join(Veiculo).filter(
        Veiculo.empresa_id == current_user.empresa_id,
        Manutencao.status == 'Concluída'
    )
    if placa := request.args.get('placa'):
        query = query.filter(Veiculo.placa.ilike(f'%{placa}%'))
    if data_inicio_str := request.args.get('data_inicio'):
        query = query.filter(Manutencao.data_saida >= datetime.strptime(data_inicio_str, '%Y-%m-%d').date())
    if data_fim_str := request.args.get('data_fim'):
        query = query.filter(Manutencao.data_saida <= datetime.strptime(data_fim_str, '%Y-%m-%d').date())
    if tipo := request.args.get('tipo'):
        query = query.filter(Manutencao.tipo_manutencao == tipo)

    manutencoes = query.order_by(Manutencao.data_saida.desc()).all()
    resultado = [{
        'veiculo': f"{m.veiculo.modelo} ({m.veiculo.placa})",
        'data_saida': m.data_saida.strftime('%d/%m/%Y') if m.data_saida else 'N/A',
        'tipo': m.tipo_manutencao,
        'servicos': m.servicos_executados or 'N/A',
        'custo': m.custo_total or 0
    } for m in manutencoes]
    return jsonify(resultado)

@app.route('/oficina/insumos')
@login_required
def gerenciar_insumos_page():
    """ Renderiza a página para o CRUD de Insumos. """
    insumos = Insumo.query.filter_by(empresa_id=current_user.empresa_id).order_by(Insumo.descricao).all()
    return render_template('gerenciar_insumos.html', insumos=insumos)

@app.route('/oficina/api/insumos', methods=['POST'])
@login_required
def api_criar_insumo():
    """ API para criar um novo insumo (versão corrigida). """
    data = request.json
    try:
        custo = float(data.get('custo_unitario_medio')) if data.get('custo_unitario_medio') else 0.0
        qtd_estoque = float(data.get('quantidade_em_estoque')) if data.get('quantidade_em_estoque') else 0.0
        ponto_ressuprimento = float(data.get('ponto_ressuprimento')) if data.get('ponto_ressuprimento') else None

        novo_insumo = Insumo(
            empresa_id=current_user.empresa_id,
            descricao=data['descricao'],
            codigo_peca=data.get('codigo_peca'),
            custo_unitario_medio=custo,
            quantidade_em_estoque=qtd_estoque,
            ponto_ressuprimento=ponto_ressuprimento
        )
        db.session.add(novo_insumo)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Insumo criado com sucesso!'}), 201
    except IntegrityError:
        db.session.rollback()
        return jsonify({'success': False, 'message': 'Já existe um insumo com esta descrição.'}), 409
    except Exception as e:
        db.session.rollback()
        logger.error(f"Erro ao criar insumo: {e}", exc_info=True)
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/oficina/api/insumos/<int:insumo_id>/entrada', methods=['POST'])
@login_required
def api_entrada_estoque_insumo(insumo_id):
    """ API para registrar a entrada de novas unidades de um insumo no estoque (versão corrigida). """
    insumo = Insumo.query.filter_by(id=insumo_id, empresa_id=current_user.empresa_id).first_or_404()
    data = request.json
    try:
        quantidade_entrada = float(data.get('quantidade', 0))
        if quantidade_entrada <= 0:
            return jsonify({'success': False, 'message': 'A quantidade deve ser maior que zero.'}), 400

        insumo.quantidade_em_estoque += quantidade_entrada
        db.session.commit()
        
        return jsonify({
            'success': True, 
            'message': f'Entrada de {quantidade_entrada} unidade(s) registrada com sucesso!',
            'novo_estoque': insumo.quantidade_em_estoque 
        }), 200
        
    except (ValueError, TypeError):
        db.session.rollback()
        return jsonify({'success': False, 'message': 'Quantidade inválida.'}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/oficina/planos_de_manutencao')
@login_required
def gerenciar_planos_page():
    """ Renderiza a página para gerenciar os Planos de Manutenção e seus kits. """
    planos = PlanoDeManutencao.query.order_by(PlanoDeManutencao.descricao).all()
    insumos = Insumo.query.filter_by(empresa_id=current_user.empresa_id).order_by(Insumo.descricao).all()
    return render_template('gerenciar_planos.html', planos=planos, insumos=insumos)

@app.route('/api/planos/<int:plano_id>/insumos', methods=['GET'])
@login_required
def api_get_kit_do_plano(plano_id):
    """ Retorna a lista de insumos (kit) de um plano específico. """
    kit = PlanoInsumo.query.filter_by(plano_id=plano_id).all()
    kit_data = [{
        'insumo_id': item.insumo_id,
        'insumo_descricao': item.insumo.descricao,
        'quantidade': item.quantidade
    } for item in kit]
    return jsonify(kit_data)

@app.route('/api/planos/<int:plano_id>/insumos', methods=['POST'])
@login_required
def api_salvar_kit_do_plano(plano_id):
    """ Salva o kit de insumos E os detalhes de um plano específico (versão corrigida). """
    plano = PlanoDeManutencao.query.get_or_404(plano_id)
    data = request.json
    
    try:
        plano.intervalo_km_padrao = int(data['intervalo_km']) if data.get('intervalo_km') else None
        plano.intervalo_meses_padrao = int(data['intervalo_meses']) if data.get('intervalo_meses') else None

        PlanoInsumo.query.filter_by(plano_id=plano_id).delete()
        
        for item_raw in data.get('kit', []):
            item_data = json.loads(item_raw) if isinstance(item_raw, str) else item_raw
            novo_item_kit = PlanoInsumo(
                plano_id=plano.id,
                insumo_id=int(item_data['insumo_id']),
                quantidade=float(item_data['quantidade'])
            )
            db.session.add(novo_item_kit)
            
        db.session.commit()
        return jsonify({'success': True, 'message': 'Plano e Kit de peças salvos com sucesso!'})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Erro ao salvar kit para o plano {plano_id}: {e}", exc_info=True)
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/oficina/api/insumos/<int:insumo_id>', methods=['DELETE'])
@login_required
def api_deletar_insumo(insumo_id):
    """ API para deletar um insumo (versão corrigida). """
    insumo = Insumo.query.filter_by(id=insumo_id, empresa_id=current_user.empresa_id).first_or_404()
    try:
        db.session.delete(insumo)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Insumo deletado com sucesso!'})
    
    # ### CORREÇÃO APLICADA AQUI ###
    # Capturamos o erro específico de integridade do banco de dados.
    except IntegrityError:
        db.session.rollback()
        return jsonify({
            'success': False, 
            'message': 'Erro: Este insumo não pode ser excluído pois está em uso em um ou mais Kits de Planos.'
        }), 409 # HTTP 409 Conflict é um bom status para este caso.
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Erro ao deletar insumo {insumo_id}: {e}", exc_info=True)
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/oficina/previsao')
@login_required
def previsao_custos_manutencao():
    """ Rota para a página de previsão de custos e orçamento de frota. """
    previsoes_detalhadas = []
    custo_mensal = defaultdict(float)
    hoje = date.today()
    
    veiculos = Veiculo.query.filter_by(empresa_id=current_user.empresa_id).all()

    for veiculo in veiculos:
        media_km_dia = calcular_media_km_veiculo(veiculo.id)
        
        for plano_assoc in veiculo.planos_associados:
            if not plano_assoc.intervalo_km or media_km_dia <= 0:
                continue

            km_desde_ultima = (veiculo.km_rodados or 0) - (plano_assoc.km_ultima_manutencao or 0)
            km_restantes = plano_assoc.intervalo_km - km_desde_ultima

            if km_restantes > 0:
                dias_para_manutencao = km_restantes / media_km_dia
                data_prevista = hoje + timedelta(days=dias_para_manutencao)
                
                custo_estimado_plano = db.session.query(
                    func.sum(PlanoInsumo.quantidade * Insumo.custo_unitario_medio)
                ).join(Insumo).filter(PlanoInsumo.plano_id == plano_assoc.plano_id).scalar() or 0.0

                if custo_estimado_plano > 0:
                    previsao = {
                        'veiculo_modelo': veiculo.modelo,
                        'veiculo_placa': veiculo.placa,
                        'plano_descricao': plano_assoc.plano.descricao,
                        'km_restantes': km_restantes,
                        'data_prevista': data_prevista,
                        'custo_estimado': custo_estimado_plano
                    }
                    previsoes_detalhadas.append(previsao)
                    
                    mes_ano = data_prevista.strftime('%Y-%m')
                    custo_mensal[mes_ano] += custo_estimado_plano

    previsoes_detalhadas.sort(key=lambda x: x['data_prevista'])
    
    orcamento_formatado = {
        'labels': sorted(custo_mensal.keys()),
        'data': [custo_mensal[key] for key in sorted(custo_mensal.keys())]
    }
    
    return render_template(
        'previsao_custos.html',
        previsoes=previsoes_detalhadas,
        orcamento=orcamento_formatado,
        today=hoje # Passa a data de hoje para o template
    )

@app.route('/exportar_relatorio')
@login_required
def exportar_relatorio():
    """
    Gera e exporta um relatório detalhado de viagens em formato Excel (XLSX),
    com formatação aprimorada e quebras de custo.
    """
    try:
        # 1. Pega os mesmos filtros da página 'consultar_viagens'
        args = request.args
        query = Viagem.query.filter_by(empresa_id=current_user.empresa_id)

        if data_inicio_str := args.get('data_inicio'):
            query = query.filter(Viagem.data_inicio >= datetime.strptime(data_inicio_str, '%Y-%m-%d'))
        if data_fim_str := args.get('data_fim'):
            data_fim_obj = datetime.strptime(data_fim_str, '%Y-%m-%d') + timedelta(days=1)
            query = query.filter(Viagem.data_inicio < data_fim_obj)
        if motorista_id := args.get('motorista_id', type=int):
            query = query.filter(Viagem.motorista_id == motorista_id)
        if status := args.get('status'):
            query = query.filter(Viagem.status == status)
        if search_query := args.get('search'):
             search_term = f'%{search_query}%'
             query = query.outerjoin(Motorista, Viagem.motorista_id == Motorista.id).filter(
                or_(
                    Viagem.cliente.ilike(search_term),
                    Motorista.nome.ilike(search_term),
                    Viagem.veiculo.has(Veiculo.placa.ilike(search_term))
                )
            )

        # 2. Carrega eficientemente todos os dados relacionados para evitar múltiplas consultas
        viagens = query.options(
            db.joinedload(Viagem.motorista_formal),
            db.joinedload(Viagem.veiculo),
            db.joinedload(Viagem.custo_viagem),
            db.joinedload(Viagem.abastecimentos),
            db.joinedload(Viagem.destinos)
        ).order_by(Viagem.data_inicio.desc()).all()

        output = io.BytesIO()
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Relatório Detalhado de Viagens"

        # 3. Define os novos cabeçalhos detalhados
        headers = [
            "ID Viagem", "Status", "Data Início", "Hora Início", "Data Fim", "Hora Fim", "Duração (HH:MM:SS)",
            "Cliente", "Motorista", "Veículo (Placa)", "Veículo (Modelo)",
            "Endereço Saída", "Destinos Intermediários", "Endereço Destino Final",
            "Distância Estimada (km)", "Distância Real (km)", "Odômetro Inicial", "Odômetro Final",
            "Receita (R$)", "Custo Real Total (R$)", "Lucro Real (R$)",
            "Custo: Combustível (R$)", "Custo: Pedágios (R$)", "Custo: Alimentação (R$)", "Custo: Hospedagem (R$)", "Custo: Outros (R$)",
            "Forma Pagamento", "Observações"
        ]
        
        for col_num, header in enumerate(headers, 1):
            cell = sheet.cell(row=1, column=col_num, value=header)
            cell.font = Font(bold=True)
            cell.alignment = Alignment(horizontal='center', vertical='center')

        # 4. Preenche o Excel com os dados detalhados
        for row_num, viagem in enumerate(viagens, 2):
            # Extração de custos detalhados
            custo_combustivel = sum(a.custo_total for a in viagem.abastecimentos)
            custo_pedagios = viagem.custo_viagem.pedagios if viagem.custo_viagem else 0.0
            custo_alimentacao = viagem.custo_viagem.alimentacao if viagem.custo_viagem else 0.0
            custo_hospedagem = viagem.custo_viagem.hospedagem if viagem.custo_viagem else 0.0
            custo_outros = viagem.custo_viagem.outros if viagem.custo_viagem else 0.0
            
            # Formatação da duração
            duracao_fmt = "N/A"
            if viagem.duracao_segundos:
                duracao_fmt = str(timedelta(seconds=viagem.duracao_segundos))

            # Concatenação dos destinos intermediários
            destinos_ordenados = sorted(viagem.destinos, key=lambda d: d.ordem)
            # Exclui o último destino, que já tem sua própria coluna
            destinos_intermediarios = ", ".join([d.endereco for d in destinos_ordenados[:-1]])

            # Preenchimento das células
            sheet.cell(row=row_num, column=1, value=viagem.id)
            sheet.cell(row=row_num, column=2, value=viagem.status.replace('_', ' ').title())
            sheet.cell(row=row_num, column=3, value=viagem.data_inicio.strftime('%d/%m/%Y'))
            sheet.cell(row=row_num, column=4, value=viagem.data_inicio.strftime('%H:%M:%S'))
            sheet.cell(row=row_num, column=5, value=viagem.data_fim.strftime('%d/%m/%Y') if viagem.data_fim else 'N/A')
            sheet.cell(row=row_num, column=6, value=viagem.data_fim.strftime('%H:%M:%S') if viagem.data_fim else 'N/A')
            sheet.cell(row=row_num, column=7, value=duracao_fmt)
            sheet.cell(row=row_num, column=8, value=viagem.cliente)
            sheet.cell(row=row_num, column=9, value=viagem.motorista_formal.nome if viagem.motorista_formal else 'N/A')
            sheet.cell(row=row_num, column=10, value=viagem.veiculo.placa if viagem.veiculo else 'N/A')
            sheet.cell(row=row_num, column=11, value=viagem.veiculo.modelo if viagem.veiculo else 'N/A')
            sheet.cell(row=row_num, column=12, value=viagem.endereco_saida)
            sheet.cell(row=row_num, column=13, value=destinos_intermediarios)
            sheet.cell(row=row_num, column=14, value=viagem.endereco_destino)
            sheet.cell(row=row_num, column=15, value=viagem.distancia_km)
            sheet.cell(row=row_num, column=16, value=viagem.distancia_percorrida)
            sheet.cell(row=row_num, column=17, value=viagem.odometro_inicial)
            sheet.cell(row=row_num, column=18, value=viagem.odometro_final)
            sheet.cell(row=row_num, column=19, value=viagem.valor_recebido or 0).number_format = 'R$ #,##0.00'
            sheet.cell(row=row_num, column=20, value=viagem.custo_real_completo).number_format = 'R$ #,##0.00'
            sheet.cell(row=row_num, column=21, value=viagem.lucro_real).number_format = 'R$ #,##0.00'
            sheet.cell(row=row_num, column=22, value=custo_combustivel or 0).number_format = 'R$ #,##0.00'
            sheet.cell(row=row_num, column=23, value=custo_pedagios or 0).number_format = 'R$ #,##0.00'
            sheet.cell(row=row_num, column=24, value=custo_alimentacao or 0).number_format = 'R$ #,##0.00'
            sheet.cell(row=row_num, column=25, value=custo_hospedagem or 0).number_format = 'R$ #,##0.00'
            sheet.cell(row=row_num, column=26, value=custo_outros or 0).number_format = 'R$ #,##0.00'
            sheet.cell(row=row_num, column=27, value=viagem.forma_pagamento or 'N/A')
            sheet.cell(row=row_num, column=28, value=viagem.observacoes)

        # 5. Auto-ajuste da largura das colunas para melhor visualização
        for col in sheet.columns:
            max_length = 0
            column = col[0].column_letter # Pega a letra da coluna
            for cell in col:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = (max_length + 2)
            sheet.column_dimensions[column].width = adjusted_width

        workbook.save(output)
        output.seek(0)

        return send_file(
            output,
            as_attachment=True,
            download_name=f"relatorio_detalhado_viagens_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except Exception as e:
        logger.error(f"Erro ao exportar relatório detalhado: {str(e)}", exc_info=True)
        flash('Ocorreu um erro inesperado ao gerar o relatório em Excel.', 'error')
        # Redireciona de volta para a página de consulta, mantendo os filtros
        return redirect(url_for('consultar_viagens', **request.args))
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
            cpf=current_user.cpf_cnpj, 
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
                    nome="Carlos Silva",
                    data_nascimento=datetime(1985, 5, 15).date(),
                    # Campos de endereço atualizados:
                    logradouro="Rua das Flores",
                    numero="123",
                    cidade="São Paulo",
                    uf="SP",
                    # Campos de documento atualizados:
                    cpf="12345678901",
                    rg="123456789",
                    telefone="11987654323",
                    cnh_numero="98765432101",
                    cnh_data_vencimento=datetime(2026, 12, 31).date(),
                    cnh_categoria="AB", # Adicionado campo obrigatório
                    # IDs de relacionamento:
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

@app.route('/veiculo/<int:veiculo_id>/dashboard')
@login_required
def veiculo_dashboard(veiculo_id):
    veiculo = Veiculo.query.filter_by(id=veiculo_id, empresa_id=current_user.empresa_id).first_or_404()
    # PASSE A DATA DE HOJE PARA O TEMPLATE
    return render_template('veiculo_dashboard.html', veiculo=veiculo, hoje=date.today())


@app.template_filter('get_usuario')
def get_usuario(cpf_cnpj):
    return Usuario.query.filter_by(cpf_cnpj=cpf_cnpj).first()

@app.route('/financeiro/folha_pagamento/exportar')
@login_required
@master_required
def exportar_folha_excel():
    mes = request.args.get('mes', type=int)
    ano = request.args.get('ano', type=int)

    if not mes or not ano:
        flash('Mês e Ano são obrigatórios para exportar.', 'error')
        return redirect(url_for('folha_pagamento_dashboard'))

    folhas = FolhaPagamento.query.filter(
        FolhaPagamento.empresa_id == current_user.empresa_id,
        FolhaPagamento.mes_referencia == mes,
        FolhaPagamento.ano_referencia == ano
    ).join(Motorista).order_by(Motorista.nome).all()

    if not folhas:
        flash('Nenhuma folha de pagamento encontrada para este período.', 'warning')
        return redirect(url_for('folha_pagamento_dashboard', mes=mes, ano=ano))

    output = io.BytesIO()
    workbook = Workbook()
    
    # --- Aba 1: Resumo ---
    sheet_resumo = workbook.active
    sheet_resumo.title = "Resumo Folha"
    headers_resumo = ["Motorista", "Salário Base (R$)", "Total Proventos (R$)", "Total Descontos (R$)", "Salário Líquido (R$)"]
    sheet_resumo.append(headers_resumo)
    for cell in sheet_resumo[1]:
        cell.font = Font(bold=True)

    for folha in folhas:
        row = [
            folha.motorista.nome,
            folha.salario_base_registro,
            folha.total_proventos,
            folha.total_descontos,
            folha.salario_liquido
        ]
        sheet_resumo.append(row)

    # --- Aba 2: Detalhado ---
    sheet_detalhado = workbook.create_sheet("Detalhado")
    headers_detalhado = ["ID Folha", "Motorista", "Tipo", "Descrição", "Valor (R$)"]
    sheet_detalhado.append(headers_detalhado)
    for cell in sheet_detalhado[1]:
        cell.font = Font(bold=True)

    for folha in folhas:
        # Adiciona o salário base como um provento
        sheet_detalhado.append([folha.id, folha.motorista.nome, "Provento", "Salário Base", folha.salario_base_registro])
        
        itens_ordenados = sorted(folha.itens.all(), key=lambda item: item.tipo)
        for item in itens_ordenados:
            row = [
                folha.id,
                folha.motorista.nome,
                item.tipo,
                item.descricao,
                item.valor
            ]
            sheet_detalhado.append(row)
    
    # Autoajuste de colunas
    for sheet in workbook.worksheets:
        for col in sheet.columns:
            max_length = 0
            column = col[0].column_letter
            for cell in col:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = (max_length + 2)
            sheet.column_dimensions[column].width = adjusted_width

    workbook.save(output)
    output.seek(0)
    
    return send_file(
        output,
        as_attachment=True,
        download_name=f"folha_pagamento_{mes:02d}_{ano}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

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

@app.route('/api/veiculo/<int:veiculo_id>/despesas_consolidadas')
@login_required
def get_veiculo_despesas_consolidadas(veiculo_id):
    Veiculo.query.filter_by(id=veiculo_id, empresa_id=current_user.empresa_id).first_or_404()
    
    extrato_final = []

    # 1. Buscar Manutenções (Lógica atualizada)
    manutencoes = Manutencao.query.filter_by(veiculo_id=veiculo_id).all()
    for despesa in manutencoes:
        extrato_final.append({
            "data": despesa.data_entrada.isoformat(),
            "categoria": f"Manutenção ({despesa.tipo_manutencao})",
            "descricao": despesa.servicos_executados or despesa.descricao_problema or 'N/A',
            "valor": despesa.custo_total,
            "anexos": []
        })

    # 2. Buscar Abastecimentos (Lógica existente)
    abastecimentos = Abastecimento.query.filter_by(veiculo_id=veiculo_id).all()
    for despesa in abastecimentos:
        extrato_final.append({
            "data": despesa.data_abastecimento.isoformat(),
            "categoria": "Combustível",
            "descricao": f"{despesa.litros:.2f}L @ R$ {despesa.preco_por_litro:.2f}/L (Odôm: {despesa.odometro}km)",
            "valor": despesa.custo_total,
            "anexos": [despesa.anexo_comprovante] if despesa.anexo_comprovante else []
        })

    # 3. Buscar Despesas de Viagem (Lógica existente)
    viagens_do_veiculo = Viagem.query.filter_by(veiculo_id=veiculo_id).options(db.joinedload(Viagem.custo_viagem)).all()
    for viagem in viagens_do_veiculo:
        if viagem.custo_viagem:
            custo = viagem.custo_viagem
            anexos_custo = custo.anexos.split(',') if custo.anexos else []
            if custo.pedagios and custo.pedagios > 0:
                extrato_final.append({"data": viagem.data_inicio.isoformat(), "categoria": "Pedágio", "descricao": f"Ref. Viagem #{viagem.id}", "valor": custo.pedagios, "anexos": anexos_custo})
            if custo.alimentacao and custo.alimentacao > 0:
                extrato_final.append({"data": viagem.data_inicio.isoformat(), "categoria": "Alimentação", "descricao": f"Ref. Viagem #{viagem.id}", "valor": custo.alimentacao, "anexos": anexos_custo})
            if custo.hospedagem and custo.hospedagem > 0:
                extrato_final.append({"data": viagem.data_inicio.isoformat(), "categoria": "Hospedagem", "descricao": f"Ref. Viagem #{viagem.id}", "valor": custo.hospedagem, "anexos": anexos_custo})
            if custo.outros and custo.outros > 0:
                extrato_final.append({"data": viagem.data_inicio.isoformat(), "categoria": "Outras Desp. (Viagem)", "descricao": custo.descricao_outros, "valor": custo.outros, "anexos": anexos_custo})

    # 4. Buscar Despesas Diversas (Lógica existente)
    despesas_diversas = DespesaVeiculo.query.filter_by(veiculo_id=veiculo_id).all()
    for despesa in despesas_diversas:
         extrato_final.append({
            "data": despesa.data.isoformat(),
            "categoria": despesa.categoria,
            "descricao": despesa.descricao,
            "valor": despesa.valor,
            "anexos": despesa.anexos.split(',') if despesa.anexos else []
        })

    extrato_ordenado = sorted(extrato_final, key=lambda d: d['data'], reverse=True)
    return jsonify(success=True, extrato=extrato_ordenado)

@app.route('/api/veiculo/<int:veiculo_id>/details')
@login_required
def get_veiculo_details_api(veiculo_id):
    veiculo = Veiculo.query.filter_by(id=veiculo_id, empresa_id=current_user.empresa_id).first_or_404()
    
    viagens = Viagem.query.filter_by(veiculo_id=veiculo.id).options(
        db.joinedload(Viagem.custo_viagem),
        db.joinedload(Viagem.abastecimentos)
    ).all()
    
    manutencoes = Manutencao.query.filter_by(veiculo_id=veiculo.id).order_by(Manutencao.data_entrada.desc()).all()
    abastecimentos = Abastecimento.query.filter_by(veiculo_id=veiculo.id).order_by(Abastecimento.data_abastecimento.desc()).all()

    total_km = veiculo.km_rodados or 0.0
    
    
    total_custo_viagens = 0
    for v in viagens:
        custo_despesas = 0
        if v.custo_viagem:
            custo_despesas = (v.custo_viagem.pedagios or 0) + (v.custo_viagem.alimentacao or 0) + (v.custo_viagem.hospedagem or 0) + (v.custo_viagem.outros or 0)
        custo_abastecimento_viagem = sum(a.custo_total for a in v.abastecimentos)
        total_custo_viagens += custo_despesas + custo_abastecimento_viagem

    total_custo_manutencao = sum(m.custo_total or 0 for m in manutencoes)
    
    custo_geral = total_custo_viagens + total_custo_manutencao
    custo_por_km = (custo_geral / total_km) if total_km > 0 else 0
    
    kpis = {
        "total_viagens": len(viagens),
        "total_km": round(total_km, 2),
        "custo_geral": round(custo_geral, 2),
        "custo_por_km": round(custo_por_km, 2)
    }

    manutencoes_data = [{
        "id": m.id,
        "data": m.data_entrada.strftime('%d/%m/%Y'),
        "odometro": m.odometro,
        "tipo": m.tipo_manutencao,
        "descricao": m.servicos_executados or m.descricao_problema or 'N/A',
        "custo": m.custo_total
    } for m in manutencoes]

    return jsonify({
        "success": True,
        "kpis": kpis,
        "manutencoes": manutencoes_data,
        "abastecimentos": [a.to_dict() for a in abastecimentos]
    })

@app.route('/veiculo/<int:veiculo_id>/lancar_receita', methods=['POST'])
@login_required
def lancar_receita_veiculo(veiculo_id):
    veiculo = Veiculo.query.filter_by(id=veiculo_id, empresa_id=current_user.empresa_id).first_or_404()
    
    try:
        # Cada frete cria um registro de viagem, mesmo que simplificado
        nova_viagem_receita = Viagem(
            veiculo_id=veiculo.id,
            empresa_id=current_user.empresa_id,
            # Usamos o campo 'cliente' para a Fazenda/Empresa do frete
            cliente=request.form.get('cliente'), 
            valor_recebido=float(request.form.get('valor_frete')),
            data_inicio=datetime.strptime(request.form.get('data'), '%Y-%m-%d'),
            # Usamos 'observacoes' para guardar detalhes como material, peso, etc.
            observacoes=f"Material: {request.form.get('material', '')} | Peso: {request.form.get('peso', '')} TN",
            # Preenchemos campos obrigatórios com valores padrão
            endereco_saida='N/A',
            endereco_destino='N/A',
            status='concluida' # Marcamos como concluída, pois é apenas um registro de faturamento
        )
        
        db.session.add(nova_viagem_receita)
        db.session.commit()
        flash('Receita (frete) lançada com sucesso!', 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao lançar receita: {e}', 'error')
        logger.error(f"Erro ao lançar receita para o veículo {veiculo_id}: {e}", exc_info=True)

    return redirect(url_for('veiculo_dashboard', veiculo_id=veiculo_id))

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

@app.route('/financeiro/fluxo_caixa')
@login_required
@master_required
def fluxo_caixa():
    """Página principal do fluxo de caixa"""
    hoje = date.today()
    
    # Filtros da URL
    data_inicio = request.args.get('data_inicio', hoje.strftime('%Y-%m-%d'))
    data_fim = request.args.get('data_fim', (hoje + timedelta(days=30)).strftime('%Y-%m-%d'))
    visualizacao = request.args.get('visualizacao', 'periodo')  # 'dia', 'mes', 'periodo'
    categoria_filtro = request.args.get('categoria', '')
    status_filtro = request.args.get('status', '')
    
    try:
        data_inicio_obj = datetime.strptime(data_inicio, '%Y-%m-%d').date()
        data_fim_obj = datetime.strptime(data_fim, '%Y-%m-%d').date()
    except ValueError:
        data_inicio_obj = hoje
        data_fim_obj = hoje + timedelta(days=30)
    
    # Buscar lançamentos manuais
    query_manuais = LancamentoFluxoCaixa.query.filter(
        LancamentoFluxoCaixa.empresa_id == current_user.empresa_id,
        LancamentoFluxoCaixa.data_vencimento >= data_inicio_obj,
        LancamentoFluxoCaixa.data_vencimento <= data_fim_obj
    )
    
    # Buscar lançamentos de NFe (contas a pagar)
    query_nfe = LancamentoNotaFiscal.query.filter(
        LancamentoNotaFiscal.empresa_id == current_user.empresa_id,
        LancamentoNotaFiscal.data_vencimento >= data_inicio_obj,
        LancamentoNotaFiscal.data_vencimento <= data_fim_obj
    )
    
    # Aplicar filtros se especificados
    if categoria_filtro:
        query_manuais = query_manuais.filter(LancamentoFluxoCaixa.categoria.ilike(f'%{categoria_filtro}%'))
    
    if status_filtro:
        query_manuais = query_manuais.filter(LancamentoFluxoCaixa.status_pagamento == status_filtro)
        query_nfe = query_nfe.filter(LancamentoNotaFiscal.status_pagamento == status_filtro)
    
    lancamentos_manuais = query_manuais.order_by(LancamentoFluxoCaixa.data_vencimento).all()
    lancamentos_nfe = query_nfe.order_by(LancamentoNotaFiscal.data_vencimento).all()
    
    # Consolidar todos os lançamentos
    fluxo_consolidado = consolidar_fluxo_caixa(lancamentos_manuais, lancamentos_nfe, data_inicio_obj, data_fim_obj)
    
    # Calcular totais e saldos
    totais = calcular_totais_fluxo(fluxo_consolidado)
    
    # Buscar categorias para filtro
    categorias_manuais = db.session.query(LancamentoFluxoCaixa.categoria).filter(
        LancamentoFluxoCaixa.empresa_id == current_user.empresa_id,
        LancamentoFluxoCaixa.categoria.isnot(None)
    ).distinct().all()
    
    categorias = [cat[0] for cat in categorias_manuais if cat[0]] + ['Fornecedores (NFe)']
    
    return render_template('fluxo_caixa.html',
                           fluxo_consolidado=fluxo_consolidado,
                           totais=totais,
                           categorias=sorted(set(categorias)),
                           data_inicio=data_inicio,
                           data_fim=data_fim,
                           visualizacao=visualizacao,
                           categoria_filtro=categoria_filtro,
                           status_filtro=status_filtro,
                           hoje=hoje)

def consolidar_fluxo_caixa(lancamentos_manuais, lancamentos_nfe, data_inicio, data_fim):
    """Consolida todos os lançamentos em uma estrutura unificada"""
    fluxo = []
    
    # Processar lançamentos manuais
    for lancamento in lancamentos_manuais:
        # Correção para garantir que parcela_total não seja None
        parcela_total_manual = lancamento.parcela_total
        fluxo.append({
            'id': f"manual_{lancamento.id}",
            'tipo_origem': 'MANUAL',
            'tipo_movimento': lancamento.tipo,
            'data_vencimento': lancamento.data_vencimento,
            'data_pagamento': lancamento.data_pagamento,
            'descricao': lancamento.descricao,
            'categoria': lancamento.categoria or 'Sem categoria',
            'fornecedor_cliente': lancamento.fornecedor_cliente,
            'documento': lancamento.documento_numero,
            'valor': lancamento.valor_total,
            'status': lancamento.status_pagamento,
            'parcela': f"{lancamento.parcela_numero}/{parcela_total_manual}" if parcela_total_manual and parcela_total_manual > 1 else None,
            'observacoes': lancamento.observacoes,
            'is_vencido': lancamento.is_vencido,
            'objeto': lancamento
        })
    
    # Processar lançamentos de NFe
    for lancamento in lancamentos_nfe:
        # Correção para garantir que parcela_total não seja None
        parcela_total_nfe = getattr(lancamento, 'parcela_total', 1)
        fluxo.append({
            'id': f"nfe_{lancamento.id}",
            'tipo_origem': 'NFE',
            'tipo_movimento': 'DESPESA',
            'data_vencimento': lancamento.data_vencimento,
            'data_pagamento': lancamento.data_pagamento,
            'descricao': f"NFe - {lancamento.emitente_nome}",
            'categoria': 'Fornecedores (NFe)',
            'fornecedor_cliente': lancamento.emitente_nome,
            'documento': lancamento.chave_acesso[-8:],
            'valor': lancamento.valor_total,
            'status': lancamento.status_pagamento,
            'parcela': f"{getattr(lancamento, 'parcela_numero', 1)}/{parcela_total_nfe}" if parcela_total_nfe and parcela_total_nfe > 1 else None,
            'observacoes': getattr(lancamento, 'observacoes', None),
            'is_vencido': (lancamento.data_vencimento < date.today() if lancamento.data_vencimento else False) and lancamento.status_pagamento == 'A Pagar',
            'objeto': lancamento
        })
    
    fluxo.sort(key=lambda x: x.get('data_vencimento') or date.max)
    
    return fluxo

def calcular_totais_fluxo(fluxo_consolidado):
    """Calcula totais, saldos e estatísticas do fluxo"""
    hoje = date.today()
    
    total_receitas = 0
    total_despesas = 0
    total_receitas_pagas = 0
    total_despesas_pagas = 0
    total_vencido_receitas = 0
    total_vencido_despesas = 0
    
    # Totais por status
    pendente_hoje = 0
    vencido_total = 0
    
    for item in fluxo_consolidado:
        valor = item['valor']
        
        if item['tipo_movimento'] == 'RECEITA':
            total_receitas += valor
            if item['status'] in ['PAGO', 'Paga']:
                total_receitas_pagas += valor
            elif item['is_vencido']:
                total_vencido_receitas += valor
        else:  # DESPESA
            total_despesas += valor
            if item['status'] in ['PAGO', 'Paga']:
                total_despesas_pagas += valor
            elif item['is_vencido']:
                total_vencido_despesas += valor
        
        # Vencimentos de hoje
        if item['data_vencimento'] == hoje and item['status'] in ['PENDENTE', 'A Pagar']:
            if item['tipo_movimento'] == 'RECEITA':
                pendente_hoje += valor
            else:
                pendente_hoje -= valor
        
        # Total vencido
        if item['is_vencido']:
            if item['tipo_movimento'] == 'RECEITA':
                vencido_total += valor
            else:
                vencido_total -= valor
    
    saldo_previsto = total_receitas - total_despesas
    saldo_realizado = total_receitas_pagas - total_despesas_pagas
    saldo_pendente = (total_receitas - total_receitas_pagas) - (total_despesas - total_despesas_pagas)
    
    return {
        'total_receitas': total_receitas,
        'total_despesas': total_despesas,
        'total_receitas_pagas': total_receitas_pagas,
        'total_despesas_pagas': total_despesas_pagas,
        'saldo_previsto': saldo_previsto,
        'saldo_realizado': saldo_realizado,
        'saldo_pendente': saldo_pendente,
        'total_vencido_receitas': total_vencido_receitas,
        'total_vencido_despesas': total_vencido_despesas,
        'pendente_hoje': pendente_hoje,
        'vencido_total': vencido_total
    }



@app.route('/financeiro/fluxo_caixa/novo_lancamento', methods=['GET', 'POST'])
@login_required
@master_required
def novo_lancamento_fluxo():
    """Criar novo lançamento manual no fluxo de caixa"""
    if request.method == 'POST':
        try:
            # Dados básicos
            tipo = request.form.get('tipo')
            descricao = request.form.get('descricao')
            categoria = request.form.get('categoria')
            valor_total = float(request.form.get('valor_total', 0))
            data_vencimento = datetime.strptime(request.form.get('data_vencimento'), '%Y-%m-%d').date()
            
            # Dados opcionais
            fornecedor_cliente = request.form.get('fornecedor_cliente')
            documento_numero = request.form.get('documento_numero')
            observacoes = request.form.get('observacoes')
            
            # Controle de parcelas
            parcela_total = int(request.form.get('parcela_total', 1))
            
            if parcela_total == 1:
                # Lançamento único
                novo_lancamento = LancamentoFluxoCaixa(
                    empresa_id=current_user.empresa_id,
                    tipo=tipo,
                    descricao=descricao,
                    categoria=categoria,
                    valor_total=valor_total,
                    data_vencimento=data_vencimento,
                    fornecedor_cliente=fornecedor_cliente,
                    documento_numero=documento_numero,
                    observacoes=observacoes,
                    parcela_numero=1,
                    parcela_total=1
                )
                db.session.add(novo_lancamento)
                
            else:
                # Criar lançamento pai
                lancamento_pai = LancamentoFluxoCaixa(
                    empresa_id=current_user.empresa_id,
                    tipo=tipo,
                    descricao=f"{descricao} (Parcelado)",
                    categoria=categoria,
                    valor_total=valor_total,
                    data_vencimento=data_vencimento,
                    fornecedor_cliente=fornecedor_cliente,
                    documento_numero=documento_numero,
                    observacoes=observacoes,
                    parcela_numero=0,  # Lançamento pai não conta como parcela
                    parcela_total=parcela_total,
                    status_pagamento='PARCELADO'  # Status especial para o pai
                )
                db.session.add(lancamento_pai)
                db.session.flush()  # Para obter o ID
                
                # Criar parcelas
                valor_parcela = valor_total / parcela_total
                for i in range(1, parcela_total + 1):
                    data_parcela = data_vencimento + timedelta(days=(i-1)*30)  # Mensais por padrão
                    
                    parcela = LancamentoFluxoCaixa(
                        empresa_id=current_user.empresa_id,
                        tipo=tipo,
                        descricao=f"{descricao} - Parcela {i}/{parcela_total}",
                        categoria=categoria,
                        valor_total=valor_parcela,
                        data_vencimento=data_parcela,
                        fornecedor_cliente=fornecedor_cliente,
                        documento_numero=f"{documento_numero}-{i:02d}" if documento_numero else None,
                        observacoes=observacoes,
                        parcela_numero=i,
                        parcela_total=parcela_total,
                        lancamento_pai_id=lancamento_pai.id
                    )
                    db.session.add(parcela)
            
            db.session.commit()
            flash(f'Lançamento criado com sucesso! {parcela_total} parcela(s) gerada(s).', 'success')
            return redirect(url_for('fluxo_caixa'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao criar lançamento: {e}', 'error')
            logger.error(f"Erro ao criar lançamento manual: {e}", exc_info=True)
            
    return render_template('novo_lancamento_fluxo.html')

@app.route('/api/fluxo_caixa/marcar_pago', methods=['POST'])
@login_required
def api_marcar_pago_fluxo():
    """API para marcar lançamento como pago"""
    data = request.get_json()
    item_id = data.get('item_id')
    data_pagamento_str = data.get('data_pagamento')
    
    if not item_id:
        return jsonify({'success': False, 'message': 'ID do item é obrigatório'}), 400
    
    try:
        data_pagamento = datetime.strptime(data_pagamento_str, '%Y-%m-%d').date() if data_pagamento_str else date.today()
        
        # Identificar se é lançamento manual ou NFe
        if item_id.startswith('manual_'):
            lancamento_id = int(item_id.replace('manual_', ''))
            lancamento = LancamentoFluxoCaixa.query.filter_by(
                id=lancamento_id, 
                empresa_id=current_user.empresa_id
            ).first_or_404()
            
            lancamento.status_pagamento = 'PAGO'
            lancamento.data_pagamento = data_pagamento
            
        elif item_id.startswith('nfe_'):
            lancamento_id = int(item_id.replace('nfe_', ''))
            lancamento = LancamentoNotaFiscal.query.filter_by(
                id=lancamento_id, 
                empresa_id=current_user.empresa_id
            ).first_or_404()
            
            lancamento.status_pagamento = 'Pago'
            lancamento.data_pagamento = data_pagamento
            
        else:
            return jsonify({'success': False, 'message': 'Formato de ID inválido'}), 400
        
        db.session.commit()
        return jsonify({'success': True, 'message': 'Lançamento marcado como pago!'})
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Erro ao marcar como pago: {e}", exc_info=True)
        return jsonify({'success': False, 'message': str(e)}), 500

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
