from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import requests
import logging

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///transport.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = 'sua_chave_secreta'
GOOGLE_MAPS_API_KEY = 'AIzaSyBPdSOZF2maHURmdRmVzLgVo5YO2wliylo'  # Replace with the new key from May 11, 2025

# Configurar logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

db = SQLAlchemy(app)

# Modelos
class Motorista(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    cpf = db.Column(db.String(11), unique=True, nullable=False)

class Veiculo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    placa = db.Column(db.String(7), unique=True, nullable=False)
    modelo = db.Column(db.String(50), nullable=False)
    disponivel = db.Column(db.Boolean, default=True)

class Viagem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    motorista_id = db.Column(db.Integer, db.ForeignKey('motorista.id'), nullable=False)
    veiculo_id = db.Column(db.Integer, db.ForeignKey('veiculo.id'), nullable=False)
    endereco_saida = db.Column(db.String(200), nullable=False)
    endereco_destino = db.Column(db.String(200), nullable=False)
    distancia_km = db.Column(db.Float, nullable=True)
    data_inicio = db.Column(db.DateTime, nullable=False)
    data_fim = db.Column(db.DateTime, nullable=True)
    motorista = db.relationship('Motorista', backref='viagens')
    veiculo = db.relationship('Veiculo', backref='viagens')

# Funções de API
def validar_endereco(endereco):
    url = 'https://maps.googleapis.com/maps/api/geocode/json'
    params = {'address': endereco, 'key': GOOGLE_MAPS_API_KEY}
    try:
        logger.debug(f"Validando endereço: {endereco}")
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        logger.debug(f"Resposta da Geocoding API: {data}")
        return data['status'] == 'OK' and len(data['results']) > 0
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro na Geocoding API: {str(e)}")
        return False

def calcular_distancia(origem, destino):
    url = 'https://maps.googleapis.com/maps/api/distancematrix/json'
    params = {'origins': origem, 'destinations': destino, 'key': GOOGLE_MAPS_API_KEY, 'units': 'metric'}
    try:
        logger.debug(f"Calculando distância entre {origem} e {destino}")
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        logger.debug(f"Resposta da Distance Matrix API: {data}")
        if data['status'] == 'OK' and data['rows'][0]['elements'][0]['status'] == 'OK':
            return data['rows'][0]['elements'][0]['distance']['value'] / 1000
        else:
            logger.warning(f"Erro na Distance Matrix API: {data.get('error_message', 'Erro desconhecido')}")
            flash(f'Erro ao calcular distância: {data.get('error_message', 'Erro desconhecido')}')
            return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro de conexão com a Distance Matrix API: {str(e)}")
        flash(f'Erro de conexão com a API do Google Maps: {str(e)}')
        return None

# Rotas
@app.route('/')
def index():
    motoristas = Motorista.query.all()
    veiculos = Veiculo.query.all()
    viagens = Viagem.query.all()
    return render_template('index.html', motoristas=motoristas, veiculos=veiculos, viagens=viagens)

@app.route('/cadastrar_motorista', methods=['GET', 'POST'])
def cadastrar_motorista():
    if request.method == 'POST':
        nome = request.form['nome']
        cpf = request.form['cpf']
        motorista = Motorista(nome=nome, cpf=cpf)
        try:
            db.session.add(motorista)
            db.session.commit()
            flash('Motorista cadastrado com sucesso!')
            return redirect(url_for('index'))
        except:
            db.session.rollback()
            flash('Erro: CPF já cadastrado.')
    return render_template('cadastrar_motorista.html')

@app.route('/cadastrar_veiculo', methods=['GET', 'POST'])
def cadastrar_veiculo():
    if request.method == 'POST':
        placa = request.form['placa']
        modelo = request.form['modelo']
        veiculo = Veiculo(placa=placa, modelo=modelo)
        try:
            db.session.add(veiculo)
            db.session.commit()
            flash('Veículo cadastrado com sucesso!')
            return redirect(url_for('index'))
        except:
            db.session.rollback()
            flash('Erro: Placa já cadastrada.')
    return render_template('cadastrar_veiculo.html')

@app.route('/iniciar_viagem', methods=['GET', 'POST'])
def iniciar_viagem():
    if request.method == 'POST':
        try:
            motorista_id = request.form['motorista_id']
            veiculo_id = request.form['veiculo_id']
            endereco_saida = request.form['endereco_saida']
            endereco_destino = request.form['endereco_destino']
            
            logger.debug(f"Dados recebidos: motorista_id={motorista_id}, veiculo_id={veiculo_id}, endereco_saida={endereco_saida}, endereco_destino={endereco_destino}")
            
            veiculo = Veiculo.query.get(veiculo_id)
            if not veiculo:
                flash('Erro: Veículo não encontrado.')
                return redirect(url_for('iniciar_viagem'))
            if not veiculo.disponivel:
                flash('Erro: Veículo já está em viagem.')
                return redirect(url_for('iniciar_viagem'))
            
            if not validar_endereco(endereco_saida):
                flash('Endereço de saída inválido. Por favor, insira um endereço válido.')
                return redirect(url_for('iniciar_viagem'))
            if not validar_endereco(endereco_destino):
                flash('Endereço de destino inválido. Por favor, insira um endereço válido.')
                return redirect(url_for('iniciar_viagem'))
            
            distancia = calcular_distancia(endereco_saida, endereco_destino)
            if distancia is None:
                flash('Não foi possível calcular a distância. Verifique os endereços ou a configuração da API.')
                return redirect(url_for('iniciar_viagem'))
            
            viagem = Viagem(motorista_id=motorista_id, veiculo_id=veiculo_id, endereco_saida=endereco_saida,
                          endereco_destino=endereco_destino, distancia_km=distancia, data_inicio=datetime.now())
            veiculo.disponivel = False
            db.session.add(viagem)
            db.session.commit()
            flash(f'Viagem iniciada com sucesso! Distância: {distancia:.2f} km')
            return redirect(url_for('index'))
        except Exception as e:
            logger.error(f"Erro ao iniciar viagem: {str(e)}")
            flash(f'Erro ao iniciar viagem: {str(e)}')
            return redirect(url_for('iniciar_viagem'))
    
    motoristas = Motorista.query.all()
    veiculos = Veiculo.query.filter_by(disponivel=True).all()
    return render_template('iniciar_viagem.html', motoristas=motoristas, veiculos=veiculos, GOOGLE_MAPS_API_KEY=GOOGLE_MAPS_API_KEY)

@app.route('/finalizar_viagem/<int:viagem_id>')
def finalizar_viagem(viagem_id):
    viagem = Viagem.query.get_or_404(viagem_id)
    if viagem.data_fim:
        flash('Erro: Viagem já finalizada.')
    else:
        viagem.data_fim = datetime.now()
        viagem.veiculo.disponivel = True
        db.session.commit()
        flash('Viagem finalizada com sucesso!')
    return redirect(url_for('index'))

with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True)