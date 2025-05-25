# ---- Importações ----
# Bibliotecas padrão e de terceiros necessárias para o aplicativo
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
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

# ---- Configurações Iniciais ----
# Carrega variáveis de ambiente do arquivo .env
load_dotenv()

# Inicializa o aplicativo Flask
app = Flask(__name__)

# Configurações do Flask
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///transport.db')  # Banco de dados (SQLite local ou PostgreSQL no Render)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False  # Desativa rastreamento de modificações para economizar recursos
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'sua_chave_secreta_forte')  # Chave secreta para sessões
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

class Destino(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    viagem_id = db.Column(db.Integer, db.ForeignKey('viagem.id'), nullable=False)
    endereco = db.Column(db.String(200), nullable=False)
    ordem = db.Column(db.Integer, nullable=False)  # Ordem do destino (1, 2, 3, ...)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# Modelo Viagem: armazena informações das viagens
class Viagem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    motorista_id = db.Column(db.Integer, db.ForeignKey('motorista.id'), nullable=False)
    veiculo_id = db.Column(db.Integer, db.ForeignKey('veiculo.id'), nullable=False)
    cliente = db.Column(db.String(100), nullable=False)
    endereco_saida = db.Column(db.String(200), nullable=False)
    distancia_km = db.Column(db.Float, nullable=True)
    data_inicio = db.Column(db.DateTime, nullable=False)
    data_fim = db.Column(db.DateTime, nullable=True)
    duracao_segundos = db.Column(db.Integer, nullable=True)
    custo = db.Column(db.Float, nullable=True)
    forma_pagamento = db.Column(db.String(50), nullable=True)
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
def calcular_distancia_e_duracao(origem, destino):
    """Calcula distância e duração da viagem usando Google Maps Directions API."""
    url = 'https://maps.googleapis.com/maps/api/directions/json'
    params = {
        'origin': origem,
        'destination': destino,
        'key': GOOGLE_MAPS_API_KEY,
        'units': 'metric',
        'departure_time': 'now'
    }
    try:
        logger.debug(f"Calculando distância entre {origem} e {destino}")
        response = requests.get(url, params=params, timeout=5)
        response.raise_for_status()
        data = response.json()
        if data['status'] == 'OK' and data['routes']:
            route = data['routes'][0]['legs'][0]
            distancia_km = route['distance']['value'] / 1000
            duracao_segundos = route['duration']['value']
            return distancia_km, duracao_segundos
        else:
            logger.warning(f"Erro na Directions API: {data.get('error_message', 'Erro desconhecido')}")
            lat1, lon1 = get_coordinates(origem)
            lat2, lon2 = get_coordinates(destino)
            if lat1 is None or lat2 is None:
                flash('Não foi possível calcular a distância entre os endereços.', 'error')
                return None, None
            distancia_km = haversine_distance(lat1, lon1, lat2, lon2)
            velocidade_media_kmh = 60
            duracao_segundos = int((distancia_km / velocidade_media_kmh) * 3600)
            flash('Distância calculada em linha reta. Duração estimada.', 'warning')
            return distancia_km, duracao_segundos
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
    viagens_ativas = Viagem.query.filter(Viagem.status.in_(['pendente', 'em_andamento'])).order_by(Viagem.data_inicio.desc()).all()
    return render_template(
        'index.html',
        motoristas=motoristas,
        veiculos=veiculos,
        viagens=viagens,
        viagens_ativas=viagens_ativas,
        GOOGLE_MAPS_API_KEY=GOOGLE_MAPS_API_KEY
    )

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
            flash('Todos os campos obrigatórios devem be preenchidos.', 'error')
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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
            # Opcional: Adicionar log para depuração
            print(f"Erro ao cadastrar veículo: {str(e)}")
    return render_template('cadastrar_veiculo.html')

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
            endereco_destino = request.form.get('endereco_destino', '').strip()
            data_inicio_str = request.form.get('data_inicio', '')
            custo = request.form.get('custo', '')
            forma_pagamento = request.form.get('forma_pagamento', '')
            status = request.form.get('status', '')
            observacoes = request.form.get('observacoes', '').strip()

            # Validações
            if not all([motorista_id, veiculo_id, cliente, endereco_saida, endereco_destino, data_inicio_str, forma_pagamento, status]):
                flash('Todos os campos obrigatórios devem ser preenchidos.', 'error')
                return redirect(url_for('iniciar_viagem'))

            try:
                data_inicio = datetime.strptime(data_inicio_str, '%Y-%m-%dT%H:%M')
            except ValueError:
                flash('Formato de data/hora inválido.', 'error')
                return redirect(url_for('iniciar_viagem'))

            custo = float(custo) if custo else None

            veiculo = Veiculo.query.get(veiculo_id)
            if not veiculo:
                flash('Erro: Veículo não encontrado.', 'error')
                return redirect(url_for('iniciar_viagem'))
            if not veiculo.disponivel:
                flash('Erro: Veículo já está em viagem.', 'error')
                return redirect(url_for('iniciar_viagem'))

            if data_inicio < datetime.now():
                flash('A data e hora da viagem não podem ser no passado.', 'error')
                return redirect(url_for('iniciar_viagem'))

            if not validar_endereco(endereco_saida) or not validar_endereco(endereco_destino):
                flash('Endereço inválido. Por favor, insira endereços válidos.', 'error')
                return redirect(url_for('iniciar_viagem'))

            distancia_km, duracao_segundos = calcular_distancia_e_duracao(endereco_saida, endereco_destino)
            if distancia_km is None or duracao_segundos is None:
                flash('Não foi possível calcular a distância ou duração.', 'error')
                return redirect(url_for('iniciar_viagem'))

            viagem = Viagem(
                motorista_id=motorista_id,
                veiculo_id=veiculo_id,
                cliente=cliente,
                endereco_saida=endereco_saida,
                endereco_destino=endereco_destino,
                distancia_km=distancia_km,
                data_inicio=data_inicio,
                duracao_segundos=duracao_segundos,
                custo=custo,
                forma_pagamento=forma_pagamento,
                status=status,
                observacoes=observacoes or None
            )
            veiculo.disponivel = False
            db.session.add(viagem)
            db.session.commit()
            flash(f'Viagem iniciada com sucesso! Distância: {distancia_km:.2f} km, Duração estimada: {duracao_segundos//60} minutos', 'success')
            return redirect(url_for('iniciar_viagem'))
        except Exception as e:
            logger.error(f"Erro ao iniciar viagem: {str(e)}")
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
            'horario_chegada': horario_chegada
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
            endereco_destino = request.form.get('endereco_destino', '').strip()
            data_inicio_str = request.form.get('data_inicio', '')
            custo = request.form.get('custo', '')
            forma_pagamento = request.form.get('forma_pagamento', '')
            status = request.form.get('status', '')
            observacoes = request.form.get('observacoes', '').strip()

            if not all([motorista_id, veiculo_id, cliente, endereco_saida, endereco_destino, data_inicio_str, forma_pagamento, status]):
                flash('Todos os campos obrigatórios devem ser preenchidos.', 'error')
                return redirect(url_for('iniciar_viagem'))

            try:
                data_inicio = datetime.strptime(data_inicio_str, '%Y-%m-%dT%H:%M')
            except ValueError:
                flash('Formato de data/hora inválido.', 'error')
                return redirect(url_for('iniciar_viagem'))

            custo = float(custo) if custo else None

            if not validar_endereco(endereco_saida) or not validar_endereco(endereco_destino):
                flash('Endereço inválido. Por favor, insira endereços válidos.', 'error')
                return redirect(url_for('iniciar_viagem'))

            distancia_km, duracao_segundos = calcular_distancia_e_duracao(endereco_saida, endereco_destino)
            if distancia_km is None or duracao_segundos is None:
                flash('Não foi possível recalcular a distância ou duração.', 'error')
                return redirect(url_for('iniciar_viagem'))

            viagem.motorista_id = motorista_id
            viagem.veiculo_id = veiculo_id
            viagem.cliente = cliente
            viagem.endereco_saida = endereco_saida
            viagem.endereco_destino = endereco_destino
            viagem.data_inicio = data_inicio
            viagem.distancia_km = distancia_km
            viagem.duracao_segundos = duracao_segundos
            viagem.custo = custo
            viagem.forma_pagamento = forma_pagamento
            viagem.status = status
            viagem.observacoes = observacoes or None
            db.session.commit()
            flash('Viagem atualizada com sucesso!', 'success')
            return redirect(url_for('iniciar_viagem'))
        except Exception as e:
            logger.error(f"Erro ao editar viagem: {str(e)}")
            flash(f'Erro ao editar viagem: {str(e)}', 'error')
            return redirect(url_for('iniciar_viagem'))

    motoristas = Motorista.query.all()
    veiculos = Veiculo.query.filter_by(disponivel=True).all()
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
            'horario_chegada': horario_chegada
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

# Rota para finalizar viagens
@app.route('/finalizar_viagem/<int:viagem_id>')
def finalizar_viagem(viagem_id):
    """Rota para finalizar uma viagem."""
    viagem = Viagem.query.get_or_404(viagem_id)
    if viagem.data_fim:
        flash('Erro: Viagem já finalizada.', 'error')
    else:
        viagem.data_fim = datetime.now()
        viagem.veiculo.disponivel = True
        viagem.status = 'concluida'
        db.session.commit()
        flash('Viagem finalizada com sucesso!', 'success')
    return redirect(url_for('index'))

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

# Rota para relatórios
@app.route('/relatorios')
def relatorios():
    """Rota para relatórios e análises."""
    data_inicio = request.args.get('data_inicio', '')
    data_fim = request.args.get('data_fim', '')
    query = Viagem.query
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

    total_viagens = query.count()
    total_distancia = db.session.query(db.func.sum(Viagem.distancia_km)).filter(query.is_(None) == False).scalar() or 0
    total_custo = db.session.query(db.func.sum(Viagem.custo)).filter(query.is_(None) == False).scalar() or 0
    viagens_por_status = db.session.query(Viagem.status, db.func.count(Viagem.id)).filter(query.is_(None) == False).group_by(Viagem.status).all()
    viagens_por_motorista = db.session.query(
        Motorista.nome,
        db.func.count(Viagem.id).label('total_viagens'),
        db.func.sum(Viagem.distancia_km).label('total_distancia'),
        db.func.sum(Viagem.custo).label('total_custo')
    ).join(Viagem).filter(query.is_(None) == False).group_by(Motorista.id).all()

    return render_template(
        'relatorios.html',
        total_viagens=total_viagens,
        total_distancia=total_distancia,
        total_custo=total_custo,
        viagens_por_status=viagens_por_status,
        viagens_por_motorista=viagens_por_motorista,
        data_inicio=data_inicio,
        data_fim=data_fim
    )

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

# ---- Inicialização do Banco de Dados ----
# Cria as tabelas no banco de dados, se não existirem
with app.app_context():
    db.create_all()

# ---- Execução do Aplicativo ----
if __name__ == '__main__':
    app.run(debug=True)