# ---- Importações ----
# Bibliotecas padrão e de terceiros necessárias para o aplicativo
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from datetime import datetime, timedelta
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

# ---- Configurações Iniciais ----
# Carrega variáveis de ambiente do arquivo .env
load_dotenv()

# Inicializa o aplicativo Flask
app = Flask(__name__)

# Configurações do Flask
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///transport.db')  # Banco de dados (SQLite local ou PostgreSQL no Render)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False  # Desativa rastreamento de modificações para economizar recursos
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'w9z$kL2mNpQvR7tYxJ3hF8gWcPqB5vM2nZ4rT6yU')
GOOGLE_MAPS_API_KEY = os.getenv('GOOGLE_MAPS_API_KEY', 'AIzaSyBPdSOZF2maHURmdRmVzLgVo5YO2wliylo')  # Chave da API do Google Maps

# Configuração do Cloudflare R2 (usado para upload de anexos)
app.config['CLOUDFLARE_R2_ENDPOINT'] = os.getenv('CLOUDFLARE_R2_ENDPOINT')
app.config['CLOUDFLARE_R2_ACCESS_KEY'] = os.getenv('CLOUDFLARE_R2_ACCESS_KEY')
app.config['CLOUDFLARE_R2_SECRET_KEY'] = os.getenv('CLOUDFLARE_R2_SECRET_KEY')
app.config['CLOUDFLARE_R2_BUCKET'] = os.getenv('CLOUDFLARE_R2_BUCKET')
app.config['CLOUDFLARE_R2_PUBLIC_URL'] = os.getenv('CLOUDFLARE_R2_PUBLIC_URL')

# Configura o logging para depuração
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ---- Inicialização de Extensões ----
# Inicializa o SQLAlchemy para gerenciar o banco de dados
db = SQLAlchemy(app)
# Inicializa o Flask-Migrate para gerenciar migrações do banco
migrate = Migrate(app, db)

# Inicializa o Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    """Carrega o usuário pelo ID."""
    return Usuario.query.get(int(user_id))

# ---- Modelos do Banco de Dados ----
# Modelo Motorista: armazena informações dos motoristas
class Motorista(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    data_nascimento = db.Column(db.Date, nullable=False)
    endereco = db.Column(db.String(200), nullable=False)
    pessoa_tipo = db.Column(db.String(10), nullable=False)  # 'fisica' ou 'juridica'
    cpf_cnpj = db.Column(db.String(14), unique=True, nullable=False, index=True)
    rg = db.Column(db.String(9), nullable=True)
    telefone = db.Column(db.String(11), nullable=False)
    cnh = db.Column(db.String(11), unique=True, nullable=False, index=True)
    validade_cnh = db.Column(db.Date, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    anexos = db.Column(db.String(500), nullable=True)  # URLs dos arquivos no Cloudflare R2, separadas por vírgula
    viagens = db.relationship('Viagem', backref='motorista')  # Relacionamento com Viagem

# Modelo Veiculo: armazena informações dos veículos
class Veiculo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    placa = db.Column(db.String(7), unique=True, nullable=False, index=True)
    categoria = db.Column(db.String(50), nullable=True)  # Novo campo
    modelo = db.Column(db.String(50), nullable=False)
    ano = db.Column(db.Integer, nullable=True)  # Novo campo
    valor = db.Column(db.Float, nullable=True)  # Novo campo
    km_rodados = db.Column(db.Float, nullable=True)  # Novo campo
    ultima_manutencao = db.Column(db.Date, nullable=True)  # Novo campo
    disponivel = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    viagens = db.relationship('Viagem', backref='veiculo')
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin

# Modelo Usuario: armazena informações dos usuários
class Usuario(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    sobrenome = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    senha_hash = db.Column(db.String(128), nullable=False)
    telefone = db.Column(db.String(11), nullable=True)
    idioma = db.Column(db.String(20), default='Português')
    two_factor_enabled = db.Column(db.Boolean, default=False)
    two_factor_phone = db.Column(db.String(11), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        """Define a senha com hash."""
        self.senha_hash = generate_password_hash(password)

    def check_password(self, password):
        """Verifica a senha."""
        return check_password_hash(self.senha_hash, password)

# Modelo Destino: armazena os destinos de uma viagem
class Destino(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    viagem_id = db.Column(db.Integer, db.ForeignKey('viagem.id'), nullable=False)
    endereco = db.Column(db.String(200), nullable=False)
    ordem = db.Column(db.Integer, nullable=False)  # Ordem do destino (1, 2, 3, ...)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# Modelo CustoViagem: armazena os custos associados a uma viagem
class CustoViagem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    viagem_id = db.Column(db.Integer, db.ForeignKey('viagem.id'), nullable=False, unique=True)
    combustivel = db.Column(db.Float, nullable=True)
    pedagios = db.Column(db.Float, nullable=True)
    alimentacao = db.Column(db.Float, nullable=True)
    hospedagem = db.Column(db.Float, nullable=True)
    outros = db.Column(db.Float, nullable=True)
    descricao_outros = db.Column(db.String(300), nullable=True)
    anexos = db.Column(db.String(500), nullable=True)  # links de arquivos
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    viagem = db.relationship('Viagem', backref=db.backref('custo_viagem', uselist=False))

# Modelo Viagem: armazena informações das viagens
class Viagem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    motorista_id = db.Column(db.Integer, db.ForeignKey('motorista.id'), nullable=False)
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
    destinos = db.relationship('Destino', backref='viagem', lazy='dynamic', cascade='all, delete-orphan')

# ---- Funções Utilitárias ----
# Valida CPF (11 dígitos) ou CNPJ (14 dígitos)
def validate_cpf_cnpj(cpf_cnpj, pessoa_tipo):
    """Valida se o CPF/CNPJ tem o número correto de dígitos."""
    if pessoa_tipo == 'fisica':
        return bool(re.match(r'^\d{11}$', cpf_cnpj))
    return bool(re.match(r'^\d{14}$', cpf_cnpj))

# Valida telefone (10 ou 11 dígitos)
def validate_telefone(telefone):
    """Valida se o telefone tem 10 ou 11 dígitos."""
    return bool(re.match(r'^\d{10,11}$', telefone))

# Valida CNH (11 dígitos)
def validate_cnh(cnh):
    """Valida se a CNH tem 11 dígitos."""
    return bool(re.match(r'^\d{11}$', cnh))

# Valida placa (7 caracteres alfanuméricos)
def validate_placa(placa):
    """Valida se a placa tem 7 caracteres alfanuméricos."""
    return bool(re.match(r'^[A-Z0-9]{7}$', placa.upper()))

# Calcula distância em linha reta usando a fórmula de Haversine
def haversine_distance(lat1, lon1, lat2, lon2):
    """Calcula distância em linha reta usando a fórmula de Haversine."""
    R = 6371  # Raio da Terra em km
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

# Obtém coordenadas de um endereço usando Google Maps Geocoding API
def get_coordinates(endereco):
    """Obtém coordenadas de um endereço usando Google Maps Geocoding API."""
    url = 'https://maps.googleapis.com/maps/api/geocode/json'
    params = {'address': endereco, 'key': GOOGLE_MAPS_API_KEY}
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

# Valida se o endereço é reconhecido pela Google Maps API
def validar_endereco(endereco):
    """Valida se o endereço é reconhecido pela Google Maps API."""
    url = 'https://maps.googleapis.com/maps/api/geocode/json'
    params = {'address': endereco, 'key': GOOGLE_MAPS_API_KEY}
    try:
        logger.debug(f"Validando endereço: {endereco}")
        response = requests.get(url, params=params, timeout=5)
        response.raise_for_status()
        data = response.json()
        return data['status'] == 'OK' and len(data['results']) > 0
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro na validação de endereço: {str(e)}")
        return False

# Calcula distância e duração da viagem usando Google Maps Directions API
def calcular_distancia_e_duracao(enderecos):
    """Calcula distância e duração da viagem com múltiplos destinos usando Google Maps Directions API."""
    if len(enderecos) < 2:
        logger.error("Pelo menos dois endereços são necessários (origem e pelo menos um destino).")
        return None, None

    url = 'https://maps.googleapis.com/maps/api/directions/json'
    origem = enderecos[0]
    destinos = enderecos[1:]
    total_distancia_km = 0
    total_duracao_segundos = 0

    try:
        logger.debug(f"Calculando rota para endereços: {enderecos}")
        for i in range(len(destinos)):
            params = {
                'origin': origem,
                'destination': destinos[i],
                'key': GOOGLE_MAPS_API_KEY,
                'units': 'metric',
                'departure_time': 'now'
            }
            if i < len(destinos) - 1:
                params['waypoints'] = destinos[i]
                origem = destinos[i]  # O destino atual se torna a origem para o próximo trecho

            response = requests.get(url, params=params, timeout=5)
            response.raise_for_status()
            data = response.json()
            if data['status'] == 'OK' and data['routes']:
                route = data['routes'][0]['legs'][0]
                total_distancia_km += route['distance']['value'] / 1000
                total_duracao_segundos += route['duration']['value']
            else:
                logger.warning(f"Erro na Directions API para trecho {origem} -> {destinos[i]}: {data.get('error_message', 'Erro desconhecido')}")
                # Fallback para Haversine se a API falhar
                lat1, lon1 = get_coordinates(origem)
                lat2, lon2 = get_coordinates(destinos[i])
                if lat1 is None or lat2 is None:
                    flash(f'Não foi possível calcular a distância para o trecho {origem} -> {destinos[i]}.', 'error')
                    return None, None
                distancia_km = haversine_distance(lat1, lon1, lat2, lon2)
                velocidade_media_kmh = 60
                duracao_segundos = int((distancia_km / velocidade_media_kmh) * 3600)
                total_distancia_km += distancia_km
                total_duracao_segundos += duracao_segundos
                flash(f'Distância calculada em linha reta para o trecho {origem} -> {destinos[i]}.', 'warning')
                origem = destinos[i]

        return total_distancia_km, total_duracao_segundos
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro na Directions API: {str(e)}")
        flash(f'Erro de conexão com a API do Google Maps: {str(e)}', 'error')
        return None, None

# ---- Rotas do Aplicativo ----
# Rota principal: exibe o dashboard com motoristas, veículos e viagens
@app.route('/')
def index():
    """Página inicial com dashboard."""
    motoristas = Motorista.query.all()
    veiculos = Veiculo.query.all()
    viagens = Viagem.query.all()
    return render_template(
        'index.html',
        motoristas=motoristas,
        veiculos=veiculos,
        viagens=viagens,
        GOOGLE_MAPS_API_KEY=GOOGLE_MAPS_API_KEY
    )

from flask import render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user

# Rota para a página de configurações
@app.route('/configuracoes', methods=['GET', 'POST'])
@login_required
def configuracoes():
    """Rota para gerenciar configurações do usuário."""
    if request.method == 'POST':
        # Atualizar informações gerais
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
            flash('Configurações gerais atualizadas com sucesso!', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao atualizar configurações: {str(e)}', 'error')

        return redirect(url_for('configuracoes'))

    return render_template('configuracoes.html', usuario=current_user)

# Rota para cadastrar motoristas
@app.route('/cadastrar_motorista', methods=['GET', 'POST'])
def cadastrar_motorista():
    """Rota para cadastrar motoristas com validação e upload de anexos."""
    if request.method == 'POST':
        # Coleta de dados do formulário
        nome = request.form.get('nome', '').strip()
        data_nascimento = request.form.get('data_nascimento', '').strip()
        endereco = request.form.get('endereco', '').strip()
        pessoa_tipo = request.form.get('pessoa_tipo', '').strip()
        cpf_cnpj = request.form.get('cpf_cnpj', '').strip()
        rg = request.form.get('rg', '').strip() or None
        telefone = request.form.get('telefone', '').strip()
        cnh = request.form.get('cnh', '').strip()
        validade_cnh = request.form.get('validade_cnh', '').strip()
        files = request.files.getlist('anexos')

        # Validação dos campos obrigatórios
        if not all([nome, data_nascimento, endereco, pessoa_tipo, cpf_cnpj, telefone, cnh, validade_cnh]):
            flash('Todos os campos obrigatórios devem ser preenchidos.', 'error')
            return redirect(url_for('cadastrar_motorista'))

        # Validação das datas
        try:
            data_nascimento = datetime.strptime(data_nascimento, '%Y-%m-%d').date()
            validade_cnh = datetime.strptime(validade_cnh, '%Y-%m-%d').date()
        except ValueError:
            flash('Formato de data inválido.', 'error')
            return redirect(url_for('cadastrar_motorista'))

        # Validação de CPF/CNPJ
        if not validate_cpf_cnpj(cpf_cnpj, pessoa_tipo):
            flash(f"{'CPF' if pessoa_tipo == 'fisica' else 'CNPJ'} inválido. Deve conter {'11' if pessoa_tipo == 'fisica' else '14'} dígitos numéricos.", 'error')
            return redirect(url_for('cadastrar_motorista'))

        # Validação de telefone
        if not validate_telefone(telefone):
            flash('Telefone inválido. Deve conter 10 ou 11 dígitos numéricos.', 'error')
            return redirect(url_for('cadastrar_motorista'))

        # Validação de CNH
        if not validate_cnh(cnh):
            flash('CNH inválida. Deve conter 11 dígitos numéricos.', 'error')
            return redirect(url_for('cadastrar_motorista'))

        # Verificar unicidade de CPF/CNPJ e CNH
        if Motorista.query.filter_by(cpf_cnpj=cpf_cnpj).first():
            flash('CPF/CNPJ já cadastrado.', 'error')
            return redirect(url_for('cadastrar_motorista'))
        if Motorista.query.filter_by(cnh=cnh).first():
            flash('CNH já cadastrada.', 'error')
            return redirect(url_for('cadastrar_motorista'))

        # Upload de anexos para Cloudflare R2
        anexos_urls = []
        allowed_extensions = {'.pdf', '.jpg', '.jpeg', '.png'}  # Extensões permitidas
        if files and any(f.filename for f in files):  # Verifica se há arquivos válidos
            try:
                # Inicializa o cliente S3 para Cloudflare R2
                s3_client = boto3.client(
                    's3',
                    endpoint_url=app.config['CLOUDFLARE_R2_ENDPOINT'],
                    aws_access_key_id=app.config['CLOUDFLARE_R2_ACCESS_KEY'],
                    aws_secret_access_key=app.config['CLOUDFLARE_R2_SECRET_KEY']
                )
                bucket_name = app.config['CLOUDFLARE_R2_BUCKET']

                # Processa cada arquivo
                for file in files:
                    if file and file.filename:
                        # Verifica a extensão do arquivo
                        extension = os.path.splitext(file.filename)[1].lower()
                        if extension not in allowed_extensions:
                            flash(f'Arquivo {file.filename} não permitido. Use PDF, JPG ou PNG.', 'error')
                            continue

                        # Gera um nome seguro para o arquivo
                        filename = secure_filename(file.filename)
                        # Organiza arquivos em uma pasta por CPF/CNPJ
                        s3_path = f"motoristas/{cpf_cnpj}/{filename}"

                        # Faz o upload do arquivo
                        s3_client.upload_fileobj(
                            file,
                            bucket_name,
                            s3_path,
                            ExtraArgs={'ContentType': file.content_type or 'application/octet-stream'}
                        )
                        # Gera a URL pública do arquivo
                        public_url = f"{app.config['CLOUDFLARE_R2_PUBLIC_URL']}/{s3_path}"
                        anexos_urls.append(public_url)
            except Exception as e:
                flash(f'Erro ao fazer upload dos arquivos: {str(e)}', 'error')
                return redirect(url_for('cadastrar_motorista'))

        # Cria o objeto Motorista
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
            anexos=','.join(anexos_urls) if anexos_urls else None
        )

        # Salva no banco de dados
        try:
            db.session.add(motorista)
            db.session.commit()
            flash('Motorista cadastrado com sucesso!', 'success')
            return redirect(url_for('index'))
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao cadastrar motorista: {str(e)}', 'error')
            return redirect(url_for('cadastrar_motorista'))

    # Renderiza o formulário para GET
    return render_template('cadastrar_motorista.html')
def create_test_user():
    with app.app_context():
        if not Usuario.query.filter_by(email='admin@example.com').first():
            try:
                admin = Usuario(
                    nome='Admin',
                    sobrenome='Sistema',
                    email='admin@example.com',
                    telefone='11999999999'
                )
                admin.set_password('admin123')
                db.session.add(admin)
                db.session.commit()
                print("✅ Usuário teste criado: admin@example.com / admin123")
            except Exception as e:
                print(f"❌ Erro ao criar usuário teste: {str(e)}")


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Você foi desconectado.', 'success')
    return redirect(url_for('index'))

# Rota para consultar motoristas
@app.route('/consultar_motoristas', methods=['GET'])
def consultar_motoristas():
    """Rota para consultar motoristas com suporte a busca."""
    search_query = request.args.get('search', '').strip()
    query = Motorista.query
    if search_query:
        query = query.filter(
            (Motorista.nome.ilike(f'%{search_query}%')) |
            (Motorista.cpf_cnpj.ilike(f'%{search_query}%'))
        )
    motoristas = query.order_by(Motorista.nome.asc()).all()
    return render_template('consultar_motoristas.html', motoristas=motoristas, search_query=search_query)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        senha = request.form.get('senha')
        
        if not email or not senha:
            flash('Preencha todos os campos', 'error')
            return redirect(url_for('login'))
            
        usuario = Usuario.query.filter_by(email=email).first()
        
        if not usuario:
            flash('Usuário não encontrado', 'error')
            return redirect(url_for('login'))
            
        if not usuario.check_password(senha):
            flash('Senha incorreta', 'error')
            return redirect(url_for('login'))
            
        login_user(usuario)
        flash('Login realizado com sucesso!', 'success')
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

# Rota para editar motoristas
@app.route('/editar_motorista/<int:motorista_id>', methods=['GET', 'POST'])
def editar_motorista(motorista_id):
    """Rota para editar motoristas com validação e upload de anexos."""
    motorista = Motorista.query.get_or_404(motorista_id)

    if request.method == 'POST':
        # Coleta de dados do formulário
        motorista.nome = request.form.get('nome', '').strip()
        data_nascimento = request.form.get('data_nascimento', '').strip()
        motorista.endereco = request.form.get('endereco', '').strip()
        motorista.pessoa_tipo = request.form.get('pessoa_tipo', '').strip()
        motorista.cpf_cnpj = request.form.get('cpf_cnpj', '').strip()
        motorista.rg = request.form.get('rg', '').strip() or None
        motorista.telefone = request.form.get('telefone', '').strip()
        motorista.cnh = request.form.get('cnh', '').strip()
        validade_cnh = request.form.get('validade_cnh', '').strip()
        files = request.files.getlist('anexos')

        # Validação dos campos obrigatórios
        if not all([motorista.nome, data_nascimento, motorista.endereco, motorista.pessoa_tipo, motorista.cpf_cnpj, motorista.telefone, motorista.cnh, validade_cnh]):
            flash('Todos os campos obrigatórios devem ser preenchidos.', 'error')
            return redirect(url_for('editar_motorista', motorista_id=motorista_id))

        # Validação das datas
        try:
            motorista.data_nascimento = datetime.strptime(data_nascimento, '%Y-%m-%d').date()
            motorista.validade_cnh = datetime.strptime(validade_cnh, '%Y-%m-%d').date()
        except ValueError:
            flash('Formato de data inválido.', 'error')
            return redirect(url_for('editar_motorista', motorista_id=motorista_id))

        # Validação de CPF/CNPJ
        if not validate_cpf_cnpj(motorista.cpf_cnpj, motorista.pessoa_tipo):
            flash(f"{'CPF' if motorista.pessoa_tipo == 'fisica' else 'CNPJ'} inválido. Deve conter {'11' if motorista.pessoa_tipo == 'fisica' else '14'} dígitos numéricos.", 'error')
            return redirect(url_for('editar_motorista', motorista_id=motorista_id))

        # Validação de telefone
        if not validate_telefone(motorista.telefone):
            flash('Telefone inválido. Deve conter 10 ou 11 dígitos numéricos.', 'error')
            return redirect(url_for('editar_motorista', motorista_id=motorista_id))

        # Validação de CNH
        if not validate_cnh(motorista.cnh):
            flash('CNH inválida. Deve conter 11 dígitos numéricos.', 'error')
            return redirect(url_for('editar_motorista', motorista_id=motorista_id))

        # Verificar unicidade
        existing_cpf_cnpj = Motorista.query.filter(Motorista.cpf_cnpj == motorista.cpf_cnpj, Motorista.id != motorista_id).first()
        if existing_cpf_cnpj:
            flash('CPF/CNPJ já cadastrado.', 'error')
            return redirect(url_for('editar_motorista', motorista_id=motorista_id))
        existing_cnh = Motorista.query.filter(Motorista.cnh == motorista.cnh, Motorista.id != motorista_id).first()
        if existing_cnh:
            flash('CNH já cadastrada.', 'error')
            return redirect(url_for('editar_motorista', motorista_id=motorista_id))

        # Upload de novos anexos para Cloudflare R2
        anexos_urls = motorista.anexos.split(',') if motorista.anexos else []
        allowed_extensions = {'.pdf', '.jpg', '.jpeg', '.png'}  # Extensões permitidas
        if files and any(f.filename for f in files):
            try:
                # Inicializa o cliente S3
                s3_client = boto3.client(
                    's3',
                    endpoint_url=app.config['CLOUDFLARE_R2_ENDPOINT'],
                    aws_access_key_id=app.config['CLOUDFLARE_R2_ACCESS_KEY'],
                    aws_secret_access_key=app.config['CLOUDFLARE_R2_SECRET_KEY']
                )
                bucket_name = app.config['CLOUDFLARE_R2_BUCKET']

                # Processa cada arquivo
                for file in files:
                    if file and file.filename:
                        # Verifica a extensão
                        extension = os.path.splitext(file.filename)[1].lower()
                        if extension not in allowed_extensions:
                            flash(f'Arquivo {file.filename} não permitido. Use PDF, JPG ou PNG.', 'error')
                            continue

                        # Gera nome seguro
                        filename = secure_filename(file.filename)
                        s3_path = f"motoristas/{motorista.cpf_cnpj}/{filename}"

                        # Faz upload
                        s3_client.upload_fileobj(
                            file,
                            bucket_name,
                            s3_path,
                            ExtraArgs={'ContentType': file.content_type or 'application/octet-stream'}
                        )
                        # Adiciona URL
                        public_url = f"{app.config['CLOUDFLARE_R2_PUBLIC_URL']}/{s3_path}"
                        anexos_urls.append(public_url)
            except Exception as e:
                flash(f'Erro ao fazer upload dos arquivos: {str(e)}', 'error')
                return redirect(url_for('editar_motorista', motorista_id=motorista_id))

        # Atualiza os anexos
        motorista.anexos = ','.join(anexos_urls) if anexos_urls else None

        # Salva as alterações
        try:
            db.session.commit()
            flash('Motorista atualizado com sucesso!', 'success')
            return redirect(url_for('consultar_motoristas'))
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao atualizar motorista: {str(e)}', 'error')
            return redirect(url_for('editar_motorista', motorista_id=motorista_id))

    return render_template('editar_motorista.html', motorista=motorista)

# Rota para excluir anexo de motorista
@app.route('/excluir_anexo/<int:motorista_id>/<path:anexo>', methods=['GET'])
def excluir_anexo(motorista_id, anexo):
    """Rota para excluir um anexo do motorista do Cloudflare R2."""
    motorista = Motorista.query.get_or_404(motorista_id)
    anexos_urls = motorista.anexos.split(',') if motorista.anexos else []
    if anexo in anexos_urls:
        try:
            # Inicializa o cliente S3
            s3_client = boto3.client(
                's3',
                endpoint_url=app.config['CLOUDFLARE_R2_ENDPOINT'],
                aws_access_key_id=app.config['CLOUDFLARE_R2_ACCESS_KEY'],
                aws_secret_access_key=app.config['CLOUDFLARE_R2_SECRET_KEY']
            )
            bucket_name = app.config['CLOUDFLARE_R2_BUCKET']
            # Extrai o caminho do arquivo
            filename = anexo.replace(app.config['CLOUDFLARE_R2_PUBLIC_URL'] + '/', '')
            # Exclui o arquivo
            s3_client.delete_object(Bucket=bucket_name, Key=filename)
            # Remove a URL da lista
            anexos_urls.remove(anexo)
            motorista.anexos = ','.join(anexos_urls) if anexos_urls else None
            db.session.commit()
            flash('Anexo excluído com sucesso!', 'success')
        except Exception as e:
            flash(f'Erro ao excluir o anexo: {str(e)}', 'error')
    else:
        flash('Anexo não encontrado.', 'error')
    return redirect(url_for('editar_motorista', motorista_id=motorista_id))

# Rota para excluir motorista
@app.route('/excluir_motorista/<int:motorista_id>')
def excluir_motorista(motorista_id):
    """Rota para excluir motoristas e seus anexos."""
    motorista = Motorista.query.get_or_404(motorista_id)
    # Verifica se o motorista tem viagens associadas
    if Viagem.query.filter_by(motorista_id=motorista_id).first():
        flash('Erro: Motorista possui viagens associadas.', 'error')
        return redirect(url_for('consultar_motoristas'))
    try:
        # Exclui anexos do Cloudflare R2
        if motorista.anexos:
            s3_client = boto3.client(
                's3',
                endpoint_url=app.config['CLOUDFLARE_R2_ENDPOINT'],
                aws_access_key_id=app.config['CLOUDFLARE_R2_ACCESS_KEY'],
                aws_secret_access_key=app.config['CLOUDFLARE_R2_SECRET_KEY']
            )
            bucket_name = app.config['CLOUDFLARE_R2_BUCKET']
            for anexo in motorista.anexos.split(','):
                filename = anexo.replace(app.config['CLOUDFLARE_R2_PUBLIC_URL'] + '/', '')
                try:
                    s3_client.delete_object(Bucket=bucket_name, Key=filename)
                except Exception as e:
                    logger.error(f"Erro ao excluir anexo {filename}: {str(e)}")
        # Exclui o motorista do banco
        db.session.delete(motorista)
        db.session.commit()
        flash('Motorista excluído com sucesso!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao excluir motorista: {str(e)}', 'error')
    return redirect(url_for('consultar_motoristas'))

# Rota para cadastrar veículos
@app.route('/cadastrar_veiculo', methods=['GET', 'POST'])
def cadastrar_veiculo():
    if request.method == 'POST':
        placa = request.form.get('placa', '').strip().upper()
        categoria = request.form.get('categoria', '').strip()
        modelo = request.form.get('modelo', '').strip()
        ano = request.form.get('ano', '').strip()
        valor = request.form.get('valor', '').strip()
        km_rodados = request.form.get('km_rodados', '').strip()
        ultima_manutencao = request.form.get('ultima_manutencao', '').strip()

        # Validações
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
            ultima_manutencao=ultima_manutencao if ultima_manutencao else None
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
    return render_template('cadastrar_veiculo.html')

# Rota para consultar veículos
@app.route('/consultar_veiculos', methods=['GET'])
def consultar_veiculos():
    search_query = request.args.get('search', '').strip()
    if search_query:
        veiculos = Veiculo.query.filter(
            or_(
                Veiculo.placa.ilike(f'%{search_query}%'),
                Veiculo.modelo.ilike(f'%{search_query}%')
            )
        ).all()
    else:
        veiculos = Veiculo.query.all()
    return render_template('consultar_veiculos.html', veiculos=veiculos, search_query=search_query)

# Rota para editar veículos
@app.route('/editar_veiculo/<int:veiculo_id>', methods=['GET', 'POST'])
def editar_veiculo(veiculo_id):
    veiculo = Veiculo.query.get_or_404(veiculo_id)
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
            return redirect(url_for('editar_veiculo', veiculo_id=veiculo_id))
        if not validate_placa(placa):
            flash('Placa inválida. Deve conter 7 caracteres alfanuméricos.', 'error')
            return redirect(url_for('editar_veiculo', veiculo_id=veiculo_id))
        if Veiculo.query.filter(Veiculo.placa == placa, Veiculo.id != veiculo_id).first():
            flash('Erro: Placa já cadastrada para outro veículo.', 'error')
            return redirect(url_for('editar_veiculo', veiculo_id=veiculo_id))
        if ano:
            try:
                ano = int(ano)
                if ano < 1900 or ano > datetime.now().year:
                    flash('Ano inválido.', 'error')
                    return redirect(url_for('editar_veiculo', veiculo_id=veiculo_id))
            except ValueError:
                flash('Ano deve ser um número válido.', 'error')
                return redirect(url_for('editar_veiculo', veiculo_id=veiculo_id))
        if valor:
            try:
                valor = float(valor)
                if valor < 0:
                    flash('Valor deve ser positivo.', 'error')
                    return redirect(url_for('editar_veiculo', veiculo_id=veiculo_id))
            except ValueError:
                flash('Valor deve ser um número válido.', 'error')
                return redirect(url_for('editar_veiculo', veiculo_id=veiculo_id))
        if km_rodados:
            try:
                km_rodados = float(km_rodados)
                if km_rodados < 0:
                    flash('Km rodados deve ser positivo.', 'error')
                    return redirect(url_for('editar_veiculo', veiculo_id=veiculo_id))
            except ValueError:
                flash('Km rodados deve ser um número válido.', 'error')
                return redirect(url_for('editar_veiculo', veiculo_id=veiculo_id))
        if ultima_manutencao:
            try:
                ultima_manutencao = datetime.strptime(ultima_manutencao, '%Y-%m-%d').date()
                if ultima_manutencao > datetime.now().date():
                    flash('Data de última manutenção não pode ser no futuro.', 'error')
                    return redirect(url_for('editar_veiculo', veiculo_id=veiculo_id))
            except ValueError:
                flash('Formato de data inválido para última manutenção.', 'error')
                return redirect(url_for('editar_veiculo', veiculo_id=veiculo_id))

        veiculo.placa = placa
        veiculo.categoria = categoria or None
        veiculo.modelo = modelo
        veiculo.ano = ano
        veiculo.valor = valor
        veiculo.km_rodados = km_rodados
        veiculo.ultima_manutencao = ultima_manutencao
        try:
            db.session.commit()
            flash('Veículo atualizado com sucesso!', 'success')
            return redirect(url_for('consultar_veiculos'))
        except:
            db.session.rollback()
            flash('Erro ao atualizar veículo.', 'error')
    return render_template('editar_veiculo.html', veiculo=veiculo)

# Rota para excluir veículos
@app.route('/excluir_veiculo/<int:veiculo_id>')
def excluir_veiculo(veiculo_id):
    """Rota para excluir veículos."""
    veiculo = Veiculo.query.get_or_404(veiculo_id)
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

# Rota para iniciar uma nova viagem
@app.route('/iniciar_viagem', methods=['GET', 'POST'])
def iniciar_viagem():
    """Rota para iniciar uma nova viagem."""
    if request.method == 'POST':
        try:
            motorista_id = request.form.get('motorista_id', '').strip()
            veiculo_id = request.form.get('veiculo_id', '').strip()
            cliente = request.form.get('cliente', '').strip()
            endereco_saida = request.form.get('endereco_saida', '').strip()
            enderecos_destino = request.form.getlist('enderecos_destino[]')
            data_inicio_str = request.form.get('data_inicio', '')
            forma_pagamento = request.form.get('forma_pagamento', '')
            status = request.form.get('status', '')
            observacoes = request.form.get('observacoes', '').strip()

            # Validações
            if not all([motorista_id, veiculo_id, cliente, endereco_saida, enderecos_destino, data_inicio_str, forma_pagamento, status]):
                flash('Todos os campos obrigatórios devem ser preenchidos.', 'error')
                return redirect(url_for('iniciar_viagem'))

            if not enderecos_destino:
                flash('Pelo menos um endereço de destino é necessário.', 'error')
                return redirect(url_for('iniciar_viagem'))

            try:
                data_inicio = datetime.strptime(data_inicio_str, '%Y-%m-%dT%H:%M')
            except ValueError:
                flash('Formato de data/hora inválido.', 'error')
                return redirect(url_for('iniciar_viagem'))

            veiculo = Veiculo.query.get(veiculo_id)
            if not veiculo:
                flash('Erro: Veículo não encontrado.', 'error')
                return redirect(url_for('iniciar_viagem'))
            if not veiculo.disponivel:
                flash('Erro: Veículo já está em viagem.', 'error')
                return redirect(url_for('iniciar_viagem'))

            # Validar endereços
            enderecos = [endereco_saida] + enderecos_destino
            for endereco in enderecos:
                if not validar_endereco(endereco):
                    flash(f'Endereço inválido: {endereco}. Por favor, insira endereços válidos.', 'error')
                    return redirect(url_for('iniciar_viagem'))

            # Calcular distância e duração total
            distancia_km, duracao_segundos = calcular_distancia_e_duracao(enderecos)
            if distancia_km is None or duracao_segundos is None:
                flash('Não foi possível calcular a distância ou duração.', 'error')
                return redirect(url_for('iniciar_viagem'))

            # Criar viagem
            viagem = Viagem(
                motorista_id=motorista_id,
                veiculo_id=veiculo_id,
                cliente=cliente,
                endereco_saida=endereco_saida,
                endereco_destino=enderecos_destino[-1],  # Último destino como destino principal
                distancia_km=distancia_km,
                data_inicio=data_inicio,
                duracao_segundos=duracao_segundos,
                forma_pagamento=forma_pagamento,
                status=status,
                observacoes=observacoes or None
            )
            veiculo.disponivel = False
            db.session.add(viagem)
            db.session.flush()  # Obtém o ID da viagem antes de commit

            # Adicionar destinos
            for ordem, endereco in enumerate(enderecos_destino, 1):
                destino = Destino(
                    viagem_id=viagem.id,
                    endereco=endereco,
                    ordem=ordem
                )
                db.session.add(destino)

            db.session.commit()
            flash(f'Viagem iniciada com sucesso! Distância: {distancia_km:.2f} km, Duração estimada: {duracao_segundos//60} minutos', 'success')
            return redirect(url_for('iniciar_viagem'))
        except Exception as e:
            logger.error(f"Erro ao iniciar viagem: {str(e)}")
            db.session.rollback()
            flash(f'Erro ao iniciar viagem: {str(e)}', 'error')
            return redirect(url_for('iniciar_viagem'))

    motoristas = Motorista.query.all()
    veiculos = Veiculo.query.filter_by(disponivel=True).all()
    viagens = Viagem.query.filter_by(data_fim=None).order_by(Viagem.data_inicio.desc()).all()
    viagens_data = []
    for viagem in viagens:
        data_inicio_formatada = viagem.data_inicio.strftime('%Y-%m-%d %H:%M:%S')[:-3]
        horario_chegada = (viagem.data_inicio + timedelta(seconds=viagem.duracao_segundos)).strftime('%d/%m/%Y %H:%M') if viagem.duracao_segundos else 'Não calculado'
        viagem_dict = {
            'valor_recebido': viagem.valor_recebido or 0,
            'id': viagem.id,
            'motorista_id': viagem.motorista_id,
            'veiculo_id': viagem.veiculo_id,
            'cliente': viagem.cliente,
            'endereco_saida': viagem.endereco_saida,
            'endereco_destino': viagem.endereco_destino,
            'data_inicio': data_inicio_formatada,
            'duracao_segundos': viagem.duracao_segundos,
            'custo': viagem.custo,
            'forma_pagamento': viagem.forma_pagamento,
            'status': viagem.status,
            'observacoes': viagem.observacoes,
            'motorista_nome': viagem.motorista.nome,
            'veiculo_placa': viagem.veiculo.placa,
            'veiculo_modelo': viagem.veiculo.modelo,
            'distancia_km': viagem.distancia_km,
            'horario_chegada': horario_chegada,
            'destinos': [{'endereco': destino.endereco, 'ordem': destino.ordem} for destino in viagem.destinos]
        }
        viagens_data.append(viagem_dict)
    return render_template('iniciar_viagem.html', motoristas=motoristas, veiculos=veiculos, viagens=viagens_data, GOOGLE_MAPS_API_KEY=GOOGLE_MAPS_API_KEY)

# Rota para editar viagens
@app.route('/editar_viagem/<int:viagem_id>', methods=['GET', 'POST'])
def editar_viagem(viagem_id):
    """Rota para editar uma viagem existente."""
    viagem = Viagem.query.get_or_404(viagem_id)
    if request.method == 'POST':
        try:
            motorista_id = request.form.get('motorista_id', '').strip()
            veiculo_id = request.form.get('veiculo_id', '').strip()
            cliente = request.form.get('cliente', '').strip()
            endereco_saida = request.form.get('endereco_saida', '').strip()
            enderecos_destino = request.form.getlist('enderecos_destino[]')
            data_inicio_str = request.form.get('data_inicio', '')
            forma_pagamento = request.form.get('forma_pagamento', '')
            status = request.form.get('status', '')
            observacoes = request.form.get('observacoes', '').strip()

            if not all([motorista_id, veiculo_id, cliente, endereco_saida, enderecos_destino, data_inicio_str, forma_pagamento, status]):
                flash('Todos os campos obrigatórios devem ser preenchidos.', 'error')
                return redirect(url_for('iniciar_viagem'))

            if not enderecos_destino:
                flash('Pelo menos um endereço de destino é necessário.', 'error')
                return redirect(url_for('iniciar_viagem'))

            try:
                data_inicio = datetime.strptime(data_inicio_str, '%Y-%m-%dT%H:%M')
            except ValueError:
                flash('Formato de data/hora inválido.', 'error')
                return redirect(url_for('iniciar_viagem'))

            # Validar endereços
            enderecos = [endereco_saida] + enderecos_destino
            for endereco in enderecos:
                if not validar_endereco(endereco):
                    flash(f'Endereço inválido: {endereco}. Por favor, insira endereços válidos.', 'error')
                    return redirect(url_for('iniciar_viagem'))

            # Calcular distância e duração
            distancia_km, duracao_segundos = calcular_distancia_e_duracao(enderecos)
            if distancia_km is None or duracao_segundos is None:
                flash('Não foi possível recalcular a distância ou duração.', 'error')
                return redirect(url_for('iniciar_viagem'))

            # Atualizar viagem
            viagem.motorista_id = motorista_id
            viagem.veiculo_id = veiculo_id
            viagem.cliente = cliente
            viagem.endereco_saida = endereco_saida
            viagem.endereco_destino = enderecos_destino[-1]
            viagem.data_inicio = data_inicio
            viagem.distancia_km = distancia_km
            viagem.duracao_segundos = duracao_segundos
            viagem.forma_pagamento = forma_pagamento
            viagem.status = status
            viagem.observacoes = observacoes or None

            # Atualizar destinos
            Destino.query.filter_by(viagem_id=viagem.id).delete()
            for ordem, endereco in enumerate(enderecos_destino, 1):
                destino = Destino(
                    viagem_id=viagem.id,
                    endereco=endereco,
                    ordem=ordem
                )
                db.session.add(destino)

            db.session.commit()
            flash('Viagem atualizada com sucesso!', 'success')
            return redirect(url_for('iniciar_viagem'))
        except Exception as e:
            logger.error(f"Erro ao editar viagem: {str(e)}")
            db.session.rollback()
            flash(f'Erro ao editar viagem: {str(e)}', 'error')
            return redirect(url_for('iniciar_viagem'))

    motoristas = Motorista.query.all()
    veiculos = Veiculo.query.filter_by(disponivel=True).all()
    if viagem.veiculo.disponivel == False:
        veiculos.append(viagem.veiculo)
    viagens = Viagem.query.filter_by(data_fim=None).order_by(Viagem.data_inicio.desc()).all()
    viagens_data = []
    for v in viagens:
        horario_chegada = (v.data_inicio + timedelta(seconds=v.duracao_segundos)).strftime('%d/%m/%Y %H:%M') if v.duracao_segundos else 'Não calculado'
        viagem_dict = {
            'id': v.id,
            'motorista_id': v.motorista_id,
            'veiculo_id': v.veiculo_id,
            'cliente': v.cliente,
            'endereco_saida': v.endereco_saida,
            'endereco_destino': v.endereco_destino,
            'data_inicio': v.data_inicio.strftime('%Y-%m-%dT%H:%M:%S'),
            'duracao_segundos': v.duracao_segundos,
            'custo': v.custo,
            'forma_pagamento': v.forma_pagamento,
            'status': v.status,
            'observacoes': v.observacoes,
            'motorista_nome': v.motorista.nome,
            'veiculo_placa': v.veiculo.placa,
            'veiculo_modelo': v.veiculo.modelo,
            'distancia_km': v.distancia_km,
            'horario_chegada': horario_chegada,
            'destinos': [{'endereco': destino.endereco, 'ordem': destino.ordem} for destino in v.destinos]
        }
        viagens_data.append(viagem_dict)
    return render_template('iniciar_viagem.html', motoristas=motoristas, veiculos=veiculos, viagens=viagens_data, viagem_edit=viagem, GOOGLE_MAPS_API_KEY=GOOGLE_MAPS_API_KEY)

# Rota para excluir viagens
@app.route('/excluir_viagem/<int:viagem_id>')
def excluir_viagem(viagem_id):
    """Rota para excluir uma viagem."""
    viagem = Viagem.query.get_or_404(viagem_id)
    try:
        if not viagem.data_fim:
            viagem.veiculo.disponivel = True
        db.session.delete(viagem)
        db.session.commit()
        flash('Viagem excluída com sucesso!', 'success')
    except Exception as e:
        logger.error(f"Erro ao excluir viagem: {str(e)}")
        flash(f'Erro ao excluir viagem: {str(e)}', 'error')
    return redirect(url_for('index'))

# Rota para salvar custos de viagem
@app.route('/salvar_custo_viagem', methods=['POST'])
def salvar_custo_viagem():
    try:
        viagem_id = request.form.get('viagem_id')
        viagem = Viagem.query.get_or_404(viagem_id)

        # Verifica se já existe um registro de custo
        custo = CustoViagem.query.filter_by(viagem_id=viagem_id).first()
        if custo:
            # Atualiza o registro existente
            custo.combustivel = float(request.form.get('combustivel') or custo.combustivel)
            custo.pedagios = float(request.form.get('pedagios') or custo.pedagios)
            custo.alimentacao = float(request.form.get('alimentacao') or custo.alimentacao)
            custo.hospedagem = float(request.form.get('hospedagem') or custo.hospedagem)
            custo.outros = float(request.form.get('outros') or custo.outros)
            custo.descricao_outros = request.form.get('descricao_outros') or custo.descricao_outros
        else:
            # Cria um novo registro
            custo = CustoViagem(
                viagem_id=viagem_id,
                combustivel=float(request.form.get('combustivel') or 0),
                pedagios=float(request.form.get('pedagios') or 0),
                alimentacao=float(request.form.get('alimentacao') or 0),
                hospedagem=float(request.form.get('hospedagem') or 0),
                outros=float(request.form.get('outros') or 0),
                descricao_outros=request.form.get('descricao_outros')
            )
            db.session.add(custo)

        # Calcular o custo total e atualizar a tabela viagem
        custo_total = (custo.combustivel or 0) + (custo.pedagios or 0) + (custo.alimentacao or 0) + (custo.hospedagem or 0) + (custo.outros or 0)
        viagem.custo = custo_total

        # Upload de anexos (se implementado)
        files = request.files.getlist('anexos')
        anexos_urls = []
        allowed_extensions = {'.pdf', '.jpg', '.jpeg', '.png'}
        if files and any(f.filename for f in files):
            s3_client = boto3.client(
                's3',
                endpoint_url=app.config['CLOUDFLARE_R2_ENDPOINT'],
                aws_access_key_id=app.config['CLOUDFLARE_R2_ACCESS_KEY'],
                aws_secret_access_key=app.config['CLOUDFLARE_R2_SECRET_KEY']
            )
            bucket_name = app.config['CLOUDFLARE_R2_BUCKET']
            for file in files:
                if file and file.filename:
                    extension = os.path.splitext(file.filename)[1].lower()
                    if extension not in allowed_extensions:
                        flash(f'Arquivo {file.filename} não permitido. Use PDF, JPG ou PNG.', 'error')
                        continue
                    filename = secure_filename(file.filename)
                    s3_path = f"custos_viagem/{viagem_id}/{filename}"
                    s3_client.upload_fileobj(file, bucket_name, s3_path)
                    public_url = f"{app.config['CLOUDFLARE_R2_PUBLIC_URL']}/{s3_path}"
                    anexos_urls.append(public_url)
            custo.anexos = ','.join(anexos_urls) if anexos_urls else None

        db.session.commit()
        flash('Custo da viagem salvo com sucesso!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao salvar custo da viagem: {str(e)}', 'error')
    return redirect(url_for('consultar_viagens'))

# Rota para consultar despesas de uma viagem
@app.route('/consultar_despesas/<int:viagem_id>', methods=['GET'])
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

# Rota para atualizar o status de uma viagem
@app.route('/atualizar_status_viagem/<int:viagem_id>', methods=['POST'])
def atualizar_status_viagem(viagem_id):
    try:
        data = request.get_json()
        novo_status = data.get('status')

        if novo_status not in ['pendente', 'em_andamento', 'concluida', 'cancelada']:
            return jsonify({'success': False, 'message': 'Status inválido'}), 400

        viagem = Viagem.query.get_or_404(viagem_id)
        viagem.status = novo_status

        if novo_status == 'concluida' and not viagem.data_fim:
            viagem.data_fim = datetime.now()

        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Erro ao atualizar status da viagem {viagem_id}: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500

# Rota para consultar viagens
@app.route('/consultar_viagens')
def consultar_viagens():
    """Rota para consultar viagens com filtros."""
    status_filter = request.args.get('status', '')
    search_query = request.args.get('search', '')
    data_inicio = request.args.get('data_inicio', '')
    data_fim = request.args.get('data_fim', '')
    query = Viagem.query
    if status_filter:
        query = query.filter_by(status=status_filter)
    if search_query:
        query = query.join(Motorista).filter(
            (Viagem.cliente.ilike(f'%{search_query}%')) |
            (Motorista.nome.ilike(f'%{search_query}%')) |
            (Viagem.endereco_saida.ilike(f'%{search_query}%')) |
            (Viagem.endereco_destino.ilike(f'%{search_query}%'))
        )
    if data_inicio:
        try:
            query = query.filter(Viagem.data_inicio >= datetime.strptime(data_inicio, '%Y-%m-%d'))
        except ValueError:
            flash('Data de início inválida.', 'error')
    if data_fim:
        try:
            query = query.filter(Viagem.data_inicio <= datetime.strptime(data_fim, '%Y-%m-%d'))
        except ValueError:
            flash('Data de fim inválida.', 'error')
    viagens = query.order_by(Viagem.data_inicio.desc()).all()
    viagens_data = []
    for v in viagens:
        horario_chegada = (v.data_inicio + timedelta(seconds=v.duracao_segundos)).strftime('%d/%m/%Y %H:%M') if v.duracao_segundos and not v.data_fim else 'Concluída'
        viagem_dict = {
            'id': v.id,
            'motorista_nome': v.motorista.nome,
            'veiculo_placa': v.veiculo.placa,
            'veiculo_modelo': v.veiculo.modelo,
            'cliente': v.cliente,
            'endereco_saida': v.endereco_saida,
            'endereco_destino': v.endereco_destino,
            'data_inicio': v.data_inicio.strftime('%d/%m/%Y %H:%M'),
            'data_fim': v.data_fim.strftime('%d/%m/%Y %H:%M') if v.data_fim else 'Em andamento',
            'duracao_segundos': v.duracao_segundos,
            'custo': v.custo,
            'forma_pagamento': v.forma_pagamento,
            'status': v.status,
            'observacoes': v.observacoes,
            'horario_chegada': horario_chegada
        }
        viagens_data.append(viagem_dict)
    return render_template(
        'consultar_viagens.html',
        viagens=viagens_data,
        status_filter=status_filter,
        search_query=search_query,
        data_inicio=data_inicio,
        data_fim=data_fim
    )

# Rota para finalizar viagens
@app.route('/finalizar_viagem/<int:viagem_id>', methods=['GET', 'POST'])
def finalizar_viagem(viagem_id):
    viagem = Viagem.query.get_or_404(viagem_id)
    
    if request.method == 'POST':
        try:
            valor_recebido = float(request.form.get('valor_recebido', 0))
            
            viagem.data_fim = datetime.now()
            viagem.veiculo.disponivel = True
            viagem.status = 'concluida'
            viagem.valor_recebido = valor_recebido
            
            db.session.commit()
            flash('Viagem finalizada com sucesso! Valor recebido registrado.', 'success')
            return redirect(url_for('consultar_viagens'))
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao finalizar viagem: {str(e)}', 'error')
    
    # Se for GET, mostra o formulário de finalização
    return render_template('finalizar_viagem.html', viagem=viagem)

# Rota para relatórios
@app.route('/relatorios')
def relatorios():
    # Filtros
    data_inicio = request.args.get('data_inicio', '')
    data_fim = request.args.get('data_fim', '')
    status_filter = request.args.get('status', '')
    motorista_id = request.args.get('motorista_id', '')

    # Query base
    query = Viagem.query

    # Aplicar filtros
    if data_inicio:
        try:
            query = query.filter(Viagem.data_inicio >= datetime.strptime(data_inicio, '%Y-%m-%d'))
        except ValueError:
            flash('Data de início inválida.', 'error')
    if data_fim:
        try:
            query = query.filter(Viagem.data_inicio <= datetime.strptime(data_fim, '%Y-%m-%d'))
        except ValueError:
            flash('Data de fim inválida.', 'error')
    if status_filter:
        query = query.filter_by(status=status_filter)
    if motorista_id:
        query = query.filter_by(motorista_id=motorista_id)

    # Estatísticas
    total_viagens = query.count()
    total_distancia = db.session.query(db.func.sum(Viagem.distancia_km)).filter(
        Viagem.distancia_km.isnot(None)
    ).scalar() or 0
    total_receita = db.session.query(db.func.sum(Viagem.valor_recebido)).filter(
        Viagem.valor_recebido.isnot(None)
    ).scalar() or 0
    total_custo = db.session.query(db.func.sum(Viagem.custo)).filter(
        Viagem.custo.isnot(None)
    ).scalar() or 0

    # Agregações
    viagens_por_status = db.session.query(
        Viagem.status,
        db.func.count(Viagem.id).label('total'),
        db.func.sum(Viagem.valor_recebido).label('receita'),
        db.func.sum(Viagem.custo).label('custo')
    ).group_by(Viagem.status).all()

    motoristas = {}
    for v in query.all():
        nome = v.motorista.nome
        if nome not in motoristas:
            motoristas[nome] = {'viagens': 0, 'custo': 0, 'receita': 0}
        motoristas[nome]['viagens'] += 1
        motoristas[nome]['custo'] += v.custo or 0
        motoristas[nome]['receita'] += v.valor_recebido or 0

    veiculos = {}
    for v in query.all():
        veiculo = f"{v.veiculo.placa} - {v.veiculo.modelo}"
        if veiculo not in veiculos:
            veiculos[veiculo] = {'km': 0, 'custo': 0}
        veiculos[veiculo]['km'] += v.distancia_km or 0
        veiculos[veiculo]['custo'] += v.custo or 0

    # Lucros por Viagem
    viagens = []
    for v in query.all():
        receita = v.valor_recebido if v.valor_recebido is not None else 0
        custo = v.custo or 0
        lucro = receita - custo
        viagens.append({
            'id': v.id,
            'cliente': v.cliente or 'N/A',
            'data': v.data_inicio.strftime('%d/%m/%Y') if v.data_inicio else '',
            'receita': receita,
            'custo': custo,
            'lucro': lucro
        })

    # Custos por Categoria
    categorias = {
        'Combustível': 0,
        'Pedágios': 0,
        'Alimentação': 0,
        'Hospedagem': 0,
        'Outros': 0
    }
    for v in query.all():
        if v.custo_viagem:
            categorias['Combustível'] += v.custo_viagem.combustivel or 0
            categorias['Pedágios'] += v.custo_viagem.pedagios or 0
            categorias['Alimentação'] += v.custo_viagem.alimentacao or 0
            categorias['Hospedagem'] += v.custo_viagem.hospedagem or 0
            categorias['Outros'] += v.custo_viagem.outros or 0

    # Receita vs Custo por Mês
    mensal = {}
    for v in query.all():
        if v.data_inicio:
            mes = v.data_inicio.strftime('%Y-%m')
            if mes not in mensal:
                mensal[mes] = {'receita': 0, 'custo': 0}
            mensal[mes]['receita'] += v.valor_recebido or 0
            mensal[mes]['custo'] += v.custo or 0

    # Obter lista de motoristas para o filtro
    motoristas_filtro = Motorista.query.all()

    return render_template(
        'relatorios.html',
        total_viagens=total_viagens,
        total_distancia=total_distancia,
        total_receita=total_receita,
        total_custo=total_custo,
        viagens_por_status=viagens_por_status,
        motoristas=motoristas,
        veiculos=veiculos,
        viagens=viagens,
        categorias=categorias,
        mensal=mensal,
        data_inicio=data_inicio,
        data_fim=data_fim,
        status_filter=status_filter,
        motorista_id=motorista_id,
        motoristas_filtro=motoristas_filtro
    )

# Rota para exportar relatório
@app.route('/exportar_relatorio')
def exportar_relatorio():
    try:
        # Filtros
        data_inicio = request.args.get('data_inicio', '')
        data_fim = request.args.get('data_fim', '')
        motorista_id = request.args.get('motorista_id', '')
        status_filter = request.args.get('status', '')

        # Query base
        query = Viagem.query

        # Aplicar filtros
        if data_inicio:
            query = query.filter(Viagem.data_inicio >= datetime.strptime(data_inicio, '%Y-%m-%d'))
        if data_fim:
            query = query.filter(Viagem.data_inicio <= datetime.strptime(data_fim, '%Y-%m-%d'))
        if motorista_id:
            query = query.filter_by(motorista_id=motorista_id)
        if status_filter:
            query = query.filter_by(status=status_filter)

        viagens = query.join(Motorista).join(Veiculo).all()

        # Criar arquivo Excel
        output = io.BytesIO()
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Relatório Financeiro"

        # Cabeçalhos
        headers = [
            "ID", "Data", "Cliente", "Motorista", "Veículo",
            "Distância (km)", "Receita (R$)", "Custo (R$)", "Lucro (R$)",
            "Forma Pagamento", "Status"
        ]
        
        for col_num, header in enumerate(headers, 1):
            sheet.cell(row=1, column=col_num, value=header).font = Font(bold=True)

        # Dados
        for row_num, viagem in enumerate(viagens, 2):
            receita = viagem.valor_recebido or 0
            custo = viagem.custo or 0
            lucro = receita - custo
            
            sheet.cell(row=row_num, column=1, value=viagem.id)
            sheet.cell(row=row_num, column=2, value=viagem.data_inicio.strftime('%d/%m/%Y'))
            sheet.cell(row=row_num, column=3, value=viagem.cliente)
            sheet.cell(row=row_num, column=4, value=viagem.motorista.nome)
            sheet.cell(row=row_num, column=5, value=f"{viagem.veiculo.placa} - {viagem.veiculo.modelo}")
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
        logger.error(f"Erro ao exportar relatório: {str(e)}")
        flash('Erro ao gerar relatório em Excel', 'error')
        return redirect(url_for('relatorios'))

# Rota para obter viagem ativa
@app.route('/get_active_trip')
def get_active_trip():
    """Rota para obter a viagem ativa atual."""
    viagem = Viagem.query.filter_by(data_fim=None, status='em_andamento').first()
    if viagem:
        horario_chegada = (viagem.data_inicio + timedelta(seconds=viagem.duracao_segundos)).strftime('%d/%m/%Y %H:%M') if viagem.duracao_segundos else 'Não calculado'
        trip_data = {
            'trip': {
                'motorista_nome': viagem.motorista.nome,
                'veiculo_placa': viagem.veiculo.placa,
                'veiculo_modelo': viagem.veiculo.modelo,
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
            telefone='11999999999'
        )
        admin.set_password('admin123')
        db.session.add(admin)
        db.session.commit()
        return 'Usuário admin criado!'
    return 'Usuário já existe'

# ---- Inicialização do Banco de Dados ----
# Cria as tabelas no banco de dados, se não existirem
with app.app_context():
    db.create_all()

# ---- Execução do Aplicativo ----
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        create_test_user()
    app.run(debug=True)