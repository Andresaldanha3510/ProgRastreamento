from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
import psycopg2
from psycopg2.extras import DictCursor
import os
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

# ============ Config. Cloudflare R2 ============
import boto3
from botocore.client import Config

# Ajuste se necessário
R2_ACCESS_KEY = "97060093e2382cb9b485900551b6e470"
R2_SECRET_KEY = "f82c29e70532b18b1705ffc94aea2f62fe4c2a85a8c99ad30b6894f068582970"
R2_ENDPOINT   = "https://e5dfe58dd78702917f5bb5852970c6c2.r2.cloudflarestorage.com"
R2_BUCKET_NAME = "meu-bucket-r2"
R2_PUBLIC_URL  = "https://pub-1e6f8559bc2b413c889fbf4860462599.r2.dev"

def get_r2_public_url(object_name):
    return f"{R2_PUBLIC_URL}/{object_name}"

def upload_file_to_r2(file_obj, object_name):
    s3 = boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=R2_ACCESS_KEY,
        aws_secret_access_key=R2_SECRET_KEY,
        config=Config(signature_version="s3v4")
    )
    file_obj.seek(0)
    s3.upload_fileobj(file_obj, R2_BUCKET_NAME, object_name)

def delete_file_from_r2(object_name):
    s3 = boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=R2_ACCESS_KEY,
        aws_secret_access_key=R2_SECRET_KEY,
        config=Config(signature_version="s3v4")
    )
    s3.delete_object(Bucket=R2_BUCKET_NAME, Key=object_name)

# ============ Config. Excel ============
import io
import xlsxwriter
import logging

logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)
secret_key = os.getenv("SECRET_KEY", "secret123")
app.secret_key = secret_key
logging.debug("SECRET_KEY carregado corretamente.")

# ============ Config. BD ============
PG_HOST = os.getenv("PG_HOST", "dpg-ctjqnsdds78s73erdqi0-a.oregon-postgres.render.com")
PG_PORT = os.getenv("PG_PORT", "5432")
PG_DB   = os.getenv("PG_DB", "programard_db")
PG_USER = os.getenv("PG_USER", "programard_db_user")
PG_PASSWORD = os.getenv("PG_PASSWORD", "hU9wJmIfgiyCg02KFQ3a4AropKSMopXr")

# Adição do filtro personalizado para validar formato de data
import re

@app.template_filter('is_date_format')
def is_date_format(value):
    if value is None:
        return False
    if isinstance(value, str):
        pattern = r'^\d{4}-\d{2}-\d{2}$'
        return bool(re.match(pattern, value))
    return False

def get_pg_connection():
    try:
        conn = psycopg2.connect(
            host=PG_HOST,
            port=PG_PORT,
            dbname=PG_DB,
            user=PG_USER,
            password=PG_PASSWORD,
            cursor_factory=DictCursor
        )
        return conn
    except psycopg2.Error as e:
        logging.error(f"Erro ao conectar ao PostgreSQL: {e}")
        import sys
        sys.exit(1)

def init_db():
    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)

    # Tabela RD
    create_rd_table = """
    CREATE TABLE IF NOT EXISTS rd (
        id TEXT PRIMARY KEY,
        solicitante TEXT NOT NULL,
        funcionario TEXT NOT NULL,
        data DATE NOT NULL,
        centro_custo TEXT NOT NULL,
        valor NUMERIC(15,2) NOT NULL,
        status TEXT DEFAULT 'Pendente',
        valor_adicional NUMERIC(15,2) DEFAULT 0,
        adicional_data DATE,
        valor_despesa NUMERIC(15,2),
        saldo_devolver NUMERIC(15,2),
        data_fechamento DATE,
        arquivos TEXT,
        aprovado_data DATE,
        liberado_data DATE,
        valor_liberado NUMERIC(15,2) DEFAULT 0,
        observacao TEXT,
        tipo TEXT DEFAULT 'credito alelo',
        unidade_negocio TEXT,
        motivo_recusa TEXT,
        adicionais_individuais TEXT,
        data_saldo_devolvido DATE,
        data_credito_solicitado DATE,
        data_credito_liberado DATE,
        data_debito_despesa DATE
    );
    """
    cursor.execute(create_rd_table)

    # Tabela historico_acoes (NOVA)
    create_historico_acoes_table = """
    CREATE TABLE IF NOT EXISTS historico_acoes (
        id SERIAL PRIMARY KEY,
        rd_id TEXT NOT NULL,
        data_acao TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        usuario TEXT NOT NULL,
        acao TEXT NOT NULL,
        detalhes TEXT
    );
    """
    cursor.execute(create_historico_acoes_table)

    # Verificar e adicionar colunas novas na tabela RD, se necessário
    for col in ['data_credito_solicitado', 'data_credito_liberado', 'data_debito_despesa']:
        cursor.execute(f"""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name='rd' AND column_name='{col}'
        """)
        if not cursor.fetchone():
            cursor.execute(f"ALTER TABLE rd ADD COLUMN {col} DATE")

    cursor.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name='rd' AND column_name='pronto_fechamento'
    """)
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE rd ADD COLUMN pronto_fechamento BOOLEAN DEFAULT FALSE")

    # anexo_divergente
    cursor.execute("""
    SELECT column_name 
    FROM information_schema.columns
    WHERE table_name='rd' AND column_name='anexo_divergente'
    """)
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE rd ADD COLUMN anexo_divergente BOOLEAN DEFAULT FALSE")

    # motivo_divergente
    cursor.execute("""
    SELECT column_name
    FROM information_schema.columns
    WHERE table_name='rd' AND column_name='motivo_divergente'
    """)
    if not cursor.fetchone():
        cursor.execute("ALTER TABLE rd ADD COLUMN motivo_divergente TEXT")

    # Tabela saldo_global
    create_saldo_global_table = """
    CREATE TABLE IF NOT EXISTS saldo_global (
        id SERIAL PRIMARY KEY,
        saldo NUMERIC(15,2) DEFAULT 30000
    );
    """
    cursor.execute(create_saldo_global_table)
    cursor.execute("SELECT COUNT(*) FROM saldo_global")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO saldo_global (saldo) VALUES (30000)")

    # Tabela funcionarios
    create_funcionarios_table = """
    CREATE TABLE IF NOT EXISTS funcionarios (
        id SERIAL PRIMARY KEY,
        nome TEXT NOT NULL,
        centro_custo TEXT NOT NULL,
        unidade_negocio TEXT NOT NULL
    );
    """
    cursor.execute(create_funcionarios_table)

    # Tabela historico_exclusao
    create_historico_table = """
    CREATE TABLE IF NOT EXISTS historico_exclusao (
        id SERIAL PRIMARY KEY,
        rd_id TEXT NOT NULL,
        solicitante TEXT NOT NULL,
        valor NUMERIC(15,2) NOT NULL,
        data_exclusao DATE NOT NULL,
        usuario_excluiu TEXT NOT NULL
    );
    """
    cursor.execute(create_historico_table)

    # Commit e fechamento devem ser no final de tudo
    conn.commit()
    cursor.close()
    conn.close()

# ====== Funções de lógica ======
def generate_custom_id():
    current_year = datetime.now().year % 100
    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("""
        SELECT id FROM rd
        WHERE split_part(id, '.', 2)::INTEGER=%s
        ORDER BY (split_part(id, '.',1))::INTEGER DESC LIMIT 1
    """, (current_year,))
    last_id = cursor.fetchone()
    conn.close()
    if not last_id:
        return f"400.{current_year}"
    last_str = last_id[0]
    last_num_str, _ = last_str.split('.')
    last_num = int(last_num_str)
    return f"{last_num+1}.{current_year}"

def user_role():
    return session.get('user_role')

def is_solicitante():
    return user_role() == "solicitante"

def is_gestor():
    return user_role() == "gestor"

def is_financeiro():
    return user_role() == "financeiro"

def can_add():
    return user_role() in ["solicitante", "gestor", "financeiro"]

def can_edit(status):
    if status == "Fechado":
        return False
    if is_solicitante():
        return status in ["Pendente", "Fechamento Recusado"]
    if is_gestor() or is_financeiro() or user_role() == "supervisor":
        return True
    return False

def can_delete(status, solicitante):
    if status == "Fechado":
        return False
    if status == "Pendente" and is_solicitante():
        return True
    if (is_gestor() or is_financeiro()) and status in ["Pendente", "Aprovado", "Liberado"]:
        return True
    return False

def can_approve(status):
    if status == "Pendente" and is_gestor():
        return True
    if status == "Fechamento Solicitado" and is_gestor():
        return True
    if status == "Aprovado" and is_financeiro():
        return True
    return False

def can_request_additional(status):
    return (is_solicitante() and status == "Liberado")

def can_close(status):
    return (is_solicitante() and status == "Liberado")

def get_saldo_global():
    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT saldo FROM saldo_global LIMIT 1")
    saldo = cursor.fetchone()[0]
    conn.close()
    return saldo

def set_saldo_global(novo_saldo):
    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("UPDATE saldo_global SET saldo=%s WHERE id=1", (novo_saldo,))
    conn.commit()
    conn.close()

def registrar_historico(conn, rd_id, acao, detalhes=""):
    """Registra uma nova ação no histórico de uma RD."""
    try:
        usuario = session.get('user_role', 'Sistema')
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO historico_acoes (rd_id, usuario, acao, detalhes)
            VALUES (%s, %s, %s, %s)
            """,
            (rd_id, usuario, acao, detalhes)
        )
    except psycopg2.Error as e:
        # Logar o erro é importante, mas não queremos que uma falha no histórico
        # impeça a operação principal.
        logging.error(f"Falha ao registrar histórico para RD {rd_id}: {e}")

def format_currency(value):
    if value is None:
        return "0,00"
    s = f"{value:,.2f}"
    parts = s.split(".")
    left = parts[0].replace(",", ".")
    right = parts[1]
    return f"{left},{right}"

# Registrando no Jinja
app.jinja_env.globals.update(
    get_r2_public_url=get_r2_public_url,
    is_gestor=is_gestor,
    is_solicitante=is_solicitante,
    is_financeiro=is_financeiro,
    format_currency=format_currency
)

# ============ ROTAS ============

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        if username == "gestor" and password == "115289":
            session["user_role"] = "gestor"
            flash("Login como gestor bem-sucedido.")
        elif username == "financeiro" and password == "351073":
            session["user_role"] = "financeiro"
            flash("Login como financeiro bem-sucedido.")
        elif username == "solicitante" and password == "102030":
            session["user_role"] = "solicitante"
            flash("Login como solicitante bem-sucedido.")
        elif username == "supervisor" and password == "223344":
            session["user_role"] = "supervisor"
            flash("Login como supervisor bem-sucedido.")
        else:
            flash("Credenciais inválidas.")
            return render_template("index.html", error="Credenciais inválidas", format_currency=format_currency)

        return redirect(url_for("index"))

    if "user_role" not in session:
        return render_template("index.html", error=None, format_currency=format_currency)

    # NOVO: Captura o parâmetro da aba ativa da URL. O padrão é 'tab1'.
    active_tab = request.args.get('active_tab', 'tab1')

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)

    if user_role() == "supervisor":
        cursor.execute("SELECT * FROM rd WHERE status='Liberado'")
        liberados = cursor.fetchall()
        pendentes = []
        aprovados = []
        fechamento_solicitado = []
        fechamento_recusado = []
        saldos_a_devolver = []
        fechados = []
        cursor.execute("SELECT COUNT(*) FROM rd WHERE anexo_divergente=TRUE")
        divergentes_count = cursor.fetchone()[0]
    else:
        cursor.execute("SELECT * FROM rd WHERE status='Pendente'")
        pendentes = cursor.fetchall()
        cursor.execute("SELECT * FROM rd WHERE status='Aprovado'")
        aprovados = cursor.fetchall()
        cursor.execute("SELECT * FROM rd WHERE status='Liberado'")
        liberados = cursor.fetchall()
        cursor.execute("SELECT * FROM rd WHERE status='Fechamento Solicitado'")
        fechamento_solicitado = cursor.fetchall()
        cursor.execute("SELECT * FROM rd WHERE status='Fechamento Recusado'")
        fechamento_recusado = cursor.fetchall()
        cursor.execute("SELECT * FROM rd WHERE status='Saldos a Devolver'")
        saldos_a_devolver = cursor.fetchall()
        cursor.execute("SELECT * FROM rd WHERE status='Fechado'")
        fechados = cursor.fetchall()
        divergentes_count = 0

    saldo_global = get_saldo_global()
    adicional_id = request.args.get("adicional")
    fechamento_id = request.args.get("fechamento")
    conn.close()

    return render_template(
        "index.html",
        error=None,
        format_currency=format_currency,
        user_role=user_role(),
        saldo_global=saldo_global if is_financeiro() else None,
        pendentes=pendentes,
        aprovados=aprovados,
        liberados=liberados,
        fechamento_solicitado=fechamento_solicitado,
        fechamento_recusado=fechamento_recusado,
        saldos_a_devolver=saldos_a_devolver,
        fechados=fechados,
        divergentes_count=divergentes_count,
        can_add=can_add(),
        can_delete_func=can_delete,
        can_edit_func=can_edit,
        can_approve_func=can_approve,
        can_request_additional=can_request_additional,
        can_close=can_close,
        adicional_id=adicional_id,
        fechamento_id=fechamento_id,
        active_tab=active_tab  # NOVO: Passa a variável para o template
    )

def can_mark_pronto_fechamento(status):
    return user_role() == "supervisor" and status == "Liberado"



@app.route("/add", methods=["POST"])
def add_rd():
    if not can_add():
        flash("Acesso negado.")
        return "Acesso negado", 403

    solicitante     = request.form["solicitante"].strip()
    funcionario     = request.form["funcionario"].strip()
    data_str        = request.form["data"].strip()
    centro_custo    = request.form["centro_custo"].strip()
    observacao      = request.form.get("observacao", "").strip()
    rd_tipo         = request.form.get("tipo", "credito alelo").strip()
    unidade_negocio = request.form.get("unidade_negocio", "").strip()

    try:
        valor = float(request.form["valor"].replace(",", "."))
    except (ValueError, TypeError):
        flash("Valor inválido.")
        return redirect(url_for("index"))

    custom_id = generate_custom_id()
    data_atual = datetime.now().strftime("%Y-%m-%d")
    arquivos = []
    if "arquivo" in request.files:
        for f in request.files.getlist("arquivo"):
            if f.filename:
                fname = f"{custom_id}_{f.filename}"
                upload_file_to_r2(f, fname)
                arquivos.append(fname)
    arquivos_str = ",".join(arquivos) if arquivos else None

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("""
    INSERT INTO rd (
      id, solicitante, funcionario, data, centro_custo,
      valor, status, arquivos, valor_liberado, observacao,
      tipo, unidade_negocio, data_credito_solicitado
    )
    VALUES (%s,%s,%s,%s,%s,
            %s,%s,%s,0,%s,
            %s,%s,%s)
    """, (custom_id, solicitante, funcionario, data_str, centro_custo,
          valor, "Pendente", arquivos_str, observacao, rd_tipo, unidade_negocio, data_atual))
    
    detalhe_valor = f"Valor solicitado: R$ {format_currency(valor)}"
    registrar_historico(conn, custom_id, "RD Criada", detalhe_valor)

    conn.commit()
    cursor.close()
    conn.close()
    flash("RD adicionada com sucesso.")
    
    # MODIFICADO: Captura a aba do formulário.
    active_tab = request.form.get('active_tab', 'tab1')
    return redirect(url_for("index", active_tab=active_tab))

@app.route("/historico/<rd_id>")
def ver_historico(rd_id):
    if "user_role" not in session:
        return redirect(url_for("index"))

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)

    cursor.execute("SELECT * FROM rd WHERE id = %s", (rd_id,))
    rd = cursor.fetchone()

    cursor.execute(
        "SELECT * FROM historico_acoes WHERE rd_id = %s ORDER BY data_acao DESC",
        (rd_id,)
    )
    historico = cursor.fetchall()
    
    conn.close()

    if not rd:
        flash("RD não encontrada.")
        return redirect(url_for("index"))

    return render_template("historico_rd.html", rd=rd, historico=historico, format_currency=format_currency)

def can_edit_status(id):
    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT status FROM rd WHERE id=%s", (id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return False
    return can_edit(row[0])

@app.route("/edit_form/<id>", methods=["GET"])
def edit_form(id):
    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT * FROM rd WHERE id=%s", (id,))
    rd = cursor.fetchone()
    conn.close()

    if not rd:
        flash("RD não encontrada.")
        return "RD não encontrada", 404

    if not can_edit(rd[6]):
        flash("Acesso negado.")
        return "Acesso negado", 403

    return render_template("edit_form.html", rd=rd, user_role=session.get("user_role"))

@app.route("/edit_submit/<id>", methods=["POST"])
def edit_submit(id):
    logging.debug(f"Iniciando edição da RD {id}")
    logging.debug(f"Dados do formulário: {request.form}")

    if not can_edit_status(id):
        logging.warning(f"Acesso negado para RD {id}")
        flash("Acesso negado.")
        return "Acesso negado", 403

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)

    cursor.execute("SELECT status, arquivos, valor_adicional, valor_liberado, valor_despesa, observacao FROM rd WHERE id=%s", (id,))
    row = cursor.fetchone()
    if not row:
        logging.error(f"RD {id} não encontrada")
        conn.close()
        return redirect(url_for("index"))
    
    original_status, arquivos_str, valor_adicional_antigo, valor_liberado, valor_despesa_antigo, observacao_antiga = row
    logging.debug(f"Status original: {original_status}")

    arqs_list = arquivos_str.split(",") if arquivos_str else []
    if "arquivo" in request.files:
        uploaded_files = request.files.getlist("arquivo")
        for f in uploaded_files:
            if f and f.filename:
                fname = f"{id}_{f.filename}"
                upload_file_to_r2(f, fname)
                arqs_list.append(fname)
                logging.debug(f"Anexo adicionado: {fname}")
    new_arqs = ",".join(arqs_list) if arqs_list else None

    if user_role() == "supervisor":
        observacao = request.form.get("observacao", "").strip()
        try:
            cursor.execute("""
            UPDATE rd
            SET arquivos=%s, observacao=%s
            WHERE id=%s
            """, (new_arqs, observacao, id))
            logging.debug(f"Supervisor atualizou arquivos: {new_arqs} e observação: {observacao}")
            
            registrar_historico(conn, id, "RD Editada pelo Supervisor", "Anexos e/ou observação foram atualizados.")
            
            conn.commit()
        except psycopg2.Error as e:
            logging.error(f"Erro no banco de dados: {e}")
            conn.rollback()
            flash("Erro ao salvar no banco de dados.")
            conn.close()
            return redirect(url_for("index"))
    else:
        solicitante = request.form.get("solicitante", "").strip()
        funcionario = request.form.get("funcionario", "").strip()
        data_str = request.form.get("data", "").strip()
        centro_custo = request.form.get("centro_custo", "").strip()
        observacao = request.form.get("observacao", "").strip()
        unidade_negocio = request.form.get("unidade_negocio", "").strip()

        if not all([solicitante, funcionario, data_str, centro_custo]):
            logging.error(f"Campos obrigatórios ausentes: solicitante={solicitante}, funcionario={funcionario}, data={data_str}, centro_custo={centro_custo}")
            flash("Preencha todos os campos obrigatórios.")
            conn.close()
            return redirect(url_for("index"))

        valor_raw = request.form.get("valor", "").strip()
        valor_adicional_raw = request.form.get("valor_adicional", "").strip()
        valor_despesa_raw = request.form.get("valor_despesa", "").strip()
        logging.debug(f"Valor bruto: {valor_raw}, Valor Adicional bruto: {valor_adicional_raw}, Valor Despesa bruto: {valor_despesa_raw}")

        try:
            valor_novo = float(valor_raw.replace(",", "."))
            valor_adicional_novo = float(valor_adicional_raw.replace(",", ".")) if valor_adicional_raw else 0.0
            valor_despesa_novo = float(valor_despesa_raw.replace(",", ".")) if valor_despesa_raw else valor_despesa_antigo
        except ValueError as e:
            logging.error(f"Erro ao converter valores: {e}")
            flash("Valor, Valor Adicional ou Valor Despesa inválido.")
            conn.close()
            return redirect(url_for("index"))

        total_cred = valor_novo + valor_adicional_novo
        saldo_devolver_novo = total_cred - valor_despesa_novo if valor_despesa_novo else None

        try:
            cursor.execute("""
            UPDATE rd
            SET solicitante=%s, funcionario=%s, data=%s, centro_custo=%s, valor=%s, valor_adicional=%s,
                valor_despesa=%s, saldo_devolver=%s, arquivos=%s, observacao=%s, unidade_negocio=%s
            WHERE id=%s
            """, (solicitante, funcionario, data_str, centro_custo, valor_novo, valor_adicional_novo,
                  valor_despesa_novo, saldo_devolver_novo, new_arqs, observacao, unidade_negocio, id))
            logging.debug(f"Executou UPDATE principal para RD {id}")

            registrar_historico(conn, id, "RD Editada")

            if is_solicitante() and original_status == "Fechamento Recusado":
                cursor.execute("UPDATE rd SET status='Fechamento Solicitado', motivo_recusa=NULL WHERE id=%s", (id,))
                logging.debug(f"Status alterado para 'Fechamento Solicitado'")
                
                registrar_historico(conn, id, "Reenviada para Fechamento", "RD corrigida após recusa.")

            conn.commit()
            logging.debug(f"Commit realizado com sucesso para RD {id}")
        except psycopg2.Error as e:
            logging.error(f"Erro no banco de dados: {e}")
            conn.rollback()
            flash("Erro ao salvar no banco de dados.")
            conn.close()
            return redirect(url_for("index"))

    conn.close()
    flash("RD atualizada com sucesso.")
    
    # MODIFICADO: Captura a aba do formulário.
    active_tab = request.form.get('active_tab', 'tab1')
    return redirect(url_for("index", active_tab=active_tab))

@app.route("/approve/<id>", methods=["POST"])
def approve(id):
    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT status, valor, valor_adicional, tipo, valor_liberado FROM rd WHERE id=%s", (id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for("index"))
    st_atual, val, val_adic, rd_tipo, valor_liberado_anterior = row

    if not can_approve(st_atual):
        conn.close()
        flash("Ação não permitida.")
        return redirect(url_for("index"))

    now = datetime.now().strftime("%Y-%m-%d")

    if st_atual == "Pendente" and is_gestor():
        new_st = "Aprovado"
        cursor.execute("""
        UPDATE rd SET status=%s, aprovado_data=%s
        WHERE id=%s
        """, (new_st, now, id))
        registrar_historico(conn, id, "Aprovada pelo Gestor")

    elif st_atual == "Aprovado" and is_financeiro():
        if rd_tipo.lower() == "reembolso":
            new_st = "Fechado"
            # MODIFICADO: Adiciona valor_despesa e saldo_devolver para consistência dos dados
            cursor.execute("""
            UPDATE rd SET status=%s, data_fechamento=%s, valor_despesa=valor, saldo_devolver=0
            WHERE id=%s
            """, (new_st, now, id))
            registrar_historico(conn, id, "Reembolso Aprovado e Fechado")
        else:
            new_st = "Liberado"
            total_credit = val + (val_adic or 0)
            novo_credito = total_credit - (valor_liberado_anterior or 0)
            saldo_atual = get_saldo_global()
            novo_saldo = saldo_atual - novo_credito
            set_saldo_global(novo_saldo)
            cursor.execute("""
            UPDATE rd SET status=%s, liberado_data=%s, valor_liberado=%s, data_credito_liberado=%s
            WHERE id=%s
            """, (new_st, now, total_credit, now, id))
            detalhe_liberado = f"Valor liberado: R$ {format_currency(total_credit)}"
            registrar_historico(conn, id, "Crédito Liberado pelo Financeiro", detalhe_liberado)

    elif st_atual == "Fechamento Solicitado" and is_gestor():
        new_st = "Saldos a Devolver"
        cursor.execute("""
        UPDATE rd SET status=%s, data_fechamento=%s
        WHERE id=%s
        """, (new_st, now, id))
        registrar_historico(conn, id, "Fechamento Aprovado pelo Gestor")
    else:
        conn.close()
        flash("Não é possível aprovar/liberar esta RD.")
        return redirect(url_for("index"))

    conn.commit()
    cursor.close()
    conn.close()
    flash("Operação realizada com sucesso.")
    
    # MODIFICADO: Captura a aba do formulário.
    active_tab = request.form.get('active_tab', 'tab1')
    return redirect(url_for("index", active_tab=active_tab))

@app.route("/delete/<id>", methods=["POST"])
def delete_rd(id):
    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT solicitante, status, valor_liberado, valor FROM rd WHERE id=%s", (id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for("index"))
    rd_solic, rd_status, rd_liber, rd_valor = row

    if not can_delete(rd_status, rd_solic):
        conn.close()
        flash("Ação não permitida.")
        return redirect(url_for("index"))

    registrar_historico(conn, id, "RD Excluída")

    usuario_excluiu = session.get("user_role", "desconhecido")
    data_exclusao = datetime.now().strftime("%Y-%m-%d")
    try:
        cursor.execute("""
        INSERT INTO historico_exclusao (rd_id, solicitante, valor, data_exclusao, usuario_excluiu)
        VALUES (%s, %s, %s, %s, %s)
        """, (id, rd_solic, rd_valor, data_exclusao, usuario_excluiu))
    except psycopg2.Error as e:
        conn.close()
        flash("Erro ao acessar banco de dados ao registrar histórico.")
        logging.error(f"Erro ao registrar histórico: {e}")
        return redirect(url_for("index"))

    if rd_status == "Liberado" and rd_liber and rd_liber > 0:
        saldo = get_saldo_global()
        set_saldo_global(saldo + rd_liber)

    cursor.execute("SELECT arquivos FROM rd WHERE id=%s", (id,))
    arq_str = cursor.fetchone()[0]
    if arq_str:
        for a in arq_str.split(","):
            delete_file_from_r2(a)

    cursor.execute("DELETE FROM rd WHERE id=%s", (id,))
    conn.commit()
    cursor.close()
    conn.close()
    flash("RD excluída com sucesso.")
    
    # MODIFICADO: Captura a aba do formulário.
    active_tab = request.form.get('active_tab', 'tab1')
    return redirect(url_for("index", active_tab=active_tab))

@app.route("/adicional_submit/<id>", methods=["POST"])
def adicional_submit(id):
    if "arquivo" in request.files:
        conn = get_pg_connection()
        cursor = conn.cursor(cursor_factory=DictCursor)
        cursor.execute("SELECT arquivos FROM rd WHERE id=%s", (id,))
        row = cursor.fetchone()
        arqs_atual = row[0].split(",") if row and row[0] else []
        for f in request.files.getlist("arquivo"):
            if f.filename:
                fname = f"{id}_{f.filename}"
                upload_file_to_r2(f, fname)
                arqs_atual.append(fname)
        new_arqs_str = ",".join(arqs_atual) if arqs_atual else None
        cursor.execute("UPDATE rd SET arquivos=%s WHERE id=%s", (new_arqs_str, id))
        conn.commit()
        cursor.close()
        conn.close()

    try:
        val_adi = float(request.form["valor_adicional"].replace(",", "."))
    except (ValueError, TypeError):
        flash("Valor adicional inválido.")
        return redirect(url_for("index"))

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT status, valor_adicional, adicionais_individuais, valor, valor_despesa FROM rd WHERE id=%s", (id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for("index"))
    st_atual, val_adic_atual, add_ind, val_sol, val_desp = row

    if not can_request_additional(st_atual):
        conn.close()
        flash("Não é possível solicitar adicional agora.")
        return redirect(url_for("index"))

    novo_total = (val_adic_atual or 0) + val_adi
    if add_ind:
        partes = [x.strip() for x in add_ind.split(",")]
        idx = len(partes) + 1
        add_ind = add_ind + f", Adicional {idx}:{val_adi:.2f}"
    else:
        add_ind = f"Adicional 1:{val_adi:.2f}"

    total_cred = val_sol + novo_total
    saldo_dev = total_cred - (val_desp or 0)

    data_add = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("""
    UPDATE rd
    SET valor_adicional=%s, adicional_data=%s, status='Pendente', adicionais_individuais=%s, saldo_devolver=%s
    WHERE id=%s
    """, (novo_total, data_add, add_ind, saldo_dev, id))
    
    detalhe_adicional = f"Valor adicional solicitado: R$ {format_currency(val_adi)}"
    registrar_historico(conn, id, "Solicitação de Crédito Adicional", detalhe_adicional)

    conn.commit()
    cursor.close()
    conn.close()
    flash("Crédito adicional solicitado. A RD voltou para 'Pendente'.")
    
    # MODIFICADO: Captura a aba do formulário com um padrão inteligente.
    active_tab = request.form.get('active_tab', 'tab3')
    return redirect(url_for("index", active_tab=active_tab))

@app.route("/fechamento_submit/<id>", methods=["POST"])
def fechamento_submit(id):
    if "arquivo" in request.files:
        conn = get_pg_connection()
        cursor = conn.cursor(cursor_factory=DictCursor)
        cursor.execute("SELECT arquivos FROM rd WHERE id=%s", (id,))
        row = cursor.fetchone()
        a_list = row[0].split(",") if row and row[0] else []
        for f in request.files.getlist("arquivo"):
            if f.filename:
                fname = f"{id}_{f.filename}"
                upload_file_to_r2(f, fname)
                a_list.append(fname)
        new_str = ",".join(a_list) if a_list else None
        cursor.execute("UPDATE rd SET arquivos=%s WHERE id=%s", (new_str, id))
        conn.commit()
        cursor.close()
        conn.close()

    try:
        val_desp = float(request.form["valor_despesa"].replace(",", "."))
    except (ValueError, TypeError):
        flash("Valor da despesa inválido.")
        return redirect(url_for("index"))

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT valor, valor_adicional, status FROM rd WHERE id=%s", (id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for("index"))
    val_sol, val_adic, st_atual = row

    if not can_close(st_atual):
        conn.close()
        flash("Não é possível fechar esta RD agora.")
        return redirect(url_for("index"))

    total_cred = val_sol + (val_adic or 0)
    if total_cred < val_desp:
        conn.close()
        flash("Valor da despesa maior que o total de créditos solicitados.")
        return redirect(url_for("index"))

    saldo_dev = total_cred - val_desp
    data_fech = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("""
    UPDATE rd
    SET valor_despesa=%s, saldo_devolver=%s, data_fechamento=%s,
        status='Fechamento Solicitado', data_debito_despesa=%s
    WHERE id=%s
    """, (val_desp, saldo_dev, data_fech, data_fech, id))

    detalhe_gasto = f"Valor gasto informado: R$ {format_currency(val_desp)}"
    registrar_historico(conn, id, "Solicitação de Fechamento", detalhe_gasto)
    
    conn.commit()
    cursor.close()
    conn.close()
    flash("Fechamento solicitado. Aguarde aprovação do gestor.")
    
    # MODIFICADO: Captura a aba do formulário com um padrão inteligente.
    active_tab = request.form.get('active_tab', 'tab3')
    return redirect(url_for("index", active_tab=active_tab))

@app.route("/reject_fechamento/<id>", methods=["POST"])
def reject_fechamento(id):
    if not is_gestor():
        flash("Acesso negado.")
        return redirect(url_for("index"))
    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT status FROM rd WHERE id=%s", (id,))
    row = cursor.fetchone()
    if not row or row[0] != "Fechamento Solicitado":
        conn.close()
        flash("Ação não permitida.")
        return redirect(url_for("index"))
    
    motivo = request.form.get("motivo", "").strip()
    if not motivo:
        flash("Informe um motivo para a recusa.")
        return redirect(url_for("index"))
    
    cursor.execute("""
    UPDATE rd
    SET status='Fechamento Recusado', motivo_recusa=%s
    WHERE id=%s
    """, (motivo, id))

    detalhe_motivo = f"Motivo: {motivo}"
    registrar_historico(conn, id, "Fechamento Recusado pelo Gestor", detalhe_motivo)

    conn.commit()
    cursor.close()
    conn.close()
    flash("Fechamento recusado com sucesso.")
    
    # MODIFICADO: Captura a aba do formulário com um padrão inteligente.
    active_tab = request.form.get('active_tab', 'tab4')
    return redirect(url_for("index", active_tab=active_tab))

@app.route("/reenviar_fechamento/<id>", methods=["POST"])
def reenviar_fechamento(id):
    flash("Utilize o botão 'Corrigir e reenviar' para editar a RD.")
    return redirect(url_for("index"))

@app.route("/edit_saldo", methods=["POST"])
def edit_saldo():
    if not is_financeiro():
        flash("Acesso negado.")
        return redirect(url_for("index"))

    try:
        novo_saldo = float(request.form["saldo_global"].replace(",", "."))
    except:
        flash("Saldo inválido.")
        return redirect(url_for("index"))

    set_saldo_global(novo_saldo)
    flash("Saldo Global atualizado com sucesso.")
    
    # MODIFICADO: Captura a aba do formulário.
    active_tab = request.form.get('active_tab', 'tab1')
    return redirect(url_for("index", active_tab=active_tab))

@app.route("/delete_file/<id>", methods=["POST"])
def delete_file(id):
    filename = request.form.get("filename")
    if not filename:
        flash("Nenhum arquivo para excluir.")
        return redirect(url_for("index"))

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT arquivos, status, solicitante FROM rd WHERE id=%s", (id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for("index"))

    arquivos_str, rd_status, rd_solic = row
    if not arquivos_str:
        conn.close()
        flash("Nenhum arquivo na RD.")
        return redirect(url_for("index"))

    if not (can_edit(rd_status) or can_delete(rd_status, rd_solic)):
        conn.close()
        flash("Você não pode excluir arquivos desta RD.")
        return redirect(url_for("index"))

    arq_list = arquivos_str.split(",")
    if filename not in arq_list:
        conn.close()
        flash("Arquivo não pertence a esta RD.")
        return redirect(url_for("index"))

    delete_file_from_r2(filename)
    arq_list.remove(filename)
    new_str = ",".join(arq_list) if arq_list else None
    cursor.execute("UPDATE rd SET arquivos=%s WHERE id=%s", (new_str, id))
    conn.commit()
    cursor.close()
    conn.close()
    flash("Arquivo excluído com sucesso.")
    
    # MODIFICADO: Usa a lógica da aba ativa em vez de request.referrer
    active_tab = request.form.get('active_tab', 'tab1')
    return redirect(url_for("index", active_tab=active_tab))

@app.route("/registrar_saldo_devolvido/<id>", methods=["POST"])
def registrar_saldo_devolvido(id):
    if not is_financeiro():
        flash("Acesso negado.")
        return redirect(url_for("index"))

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT valor, valor_adicional, valor_despesa, data_saldo_devolvido, status FROM rd WHERE id=%s", (id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        flash("RD não encontrada.")
        return redirect(url_for("index"))
    val_sol, val_adic, val_desp, data_sal_dev, status = row
    if data_sal_dev:
        conn.close()
        flash("Saldo já registrado antes.")
        return redirect(url_for("index"))
    if status != "Saldos a Devolver":
        conn.close()
        flash("Ação permitida apenas para RDs em 'Saldos a Devolver'.")
        return redirect(url_for("index"))
    total_cred = val_sol + (val_adic or 0)
    if total_cred < (val_desp or 0):
        conn.close()
        flash("Despesa maior que o total de créditos.")
        return redirect(url_for("index"))
    saldo_dev = total_cred - (val_desp or 0)
    saldo = get_saldo_global()
    set_saldo_global(saldo + saldo_dev)
    now = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("""
    UPDATE rd SET data_saldo_devolvido=%s, status='Fechado'
    WHERE id=%s
    """, (now, id))

    detalhe_devolvido = f"Valor devolvido ao saldo global: R$ {format_currency(saldo_dev)}"
    registrar_historico(conn, id, "Devolução de Saldo Registrada", detalhe_devolvido)

    conn.commit()
    cursor.close()
    conn.close()
    flash(f"Saldo devolvido com sucesso. Valor= R${format_currency(saldo_dev)}")
    
    # MODIFICADO: Captura a aba do formulário com um padrão inteligente.
    active_tab = request.form.get('active_tab', 'tab7')
    return redirect(url_for("index", active_tab=active_tab))

@app.route("/export_excel", methods=["GET"])
def export_excel():
    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT * FROM rd ORDER BY id ASC")
    rd_list = cursor.fetchall()
    saldo_global = get_saldo_global()
    output = io.BytesIO()
    wb = xlsxwriter.Workbook(output, {"in_memory": True})
    ws = wb.add_worksheet("Relatorio")

    header = [
        "Número RD", "Data Solicitação", "Solicitante", "Funcionário", "Valor Solicitado",
        "Valor Adicional", "Data do Adicional", "Centro de Custo", "Unidade de Negócio",
        "Valor Gasto", "Saldo a Devolver", "Data de Fechamento", "Status", "Data Crédito Solicitado",
        "Data Crédito Liberado", "Data Débito Despesa", "Pronto Para Fechamento", "Saldo Global"
    ]
    for col, h in enumerate(header):
        ws.write(0, col, h)

    rowi = 1
    for rd_row in rd_list:
        rd_id = rd_row[0]
        rd_data = rd_row[3]
        rd_solic = rd_row[1]
        rd_func = rd_row[2]
        rd_valor = rd_row[5]
        rd_val_adic = rd_row[7]
        rd_adic_data = rd_row[8]
        rd_ccusto = rd_row[4]
        rd_unidade_negocio = rd_row[18]
        rd_desp = rd_row[9]
        rd_saldo_dev = rd_row[10]
        rd_data_fech = rd_row[11]
        rd_status = rd_row[6]
        rd_data_cred_solic = rd_row[22]
        rd_data_cred_liber = rd_row[23]
        rd_data_deb_desp = rd_row[24]
        rd_pronto_fechamento = rd_row[35] if len(rd_row) > 35 else False

        ws.write(rowi, 0, rd_id)
        ws.write(rowi, 1, str(rd_data) if rd_data else "")
        ws.write(rowi, 2, rd_solic)
        ws.write(rowi, 3, rd_func)
        ws.write(rowi, 4, float(rd_valor or 0))
        ws.write(rowi, 5, float(rd_val_adic or 0))
        ws.write(rowi, 6, str(rd_adic_data) if rd_adic_data else "")
        ws.write(rowi, 7, rd_ccusto)
        ws.write(rowi, 8, rd_unidade_negocio if rd_unidade_negocio else "")
        ws.write(rowi, 9, float(rd_desp or 0))
        ws.write(rowi, 10, float(rd_saldo_dev or 0))
        ws.write(rowi, 11, str(rd_data_fech) if rd_data_fech else "")
        ws.write(rowi, 12, rd_status)
        ws.write(rowi, 13, str(rd_data_cred_solic) if rd_data_cred_solic else "")
        ws.write(rowi, 14, str(rd_data_cred_liber) if rd_data_cred_liber else "")
        ws.write(rowi, 15, str(rd_data_deb_desp) if rd_data_deb_desp else "")
        ws.write(rowi, 16, "Sim" if rd_pronto_fechamento else "Não")
        ws.write(rowi, 17, float(saldo_global))
        rowi += 1

    wb.close()
    output.seek(0)
    conn.close()

    return send_file(
        output,
        as_attachment=True,
        download_name=f"Relatorio_RD_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

@app.route("/export_historico", methods=["GET"])
def export_historico():
    if not is_financeiro():
        flash("Acesso negado.")
        return redirect(url_for("index"))

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    try:
        cursor.execute("SELECT rd_id, solicitante, valor, data_exclusao, usuario_excluiu FROM historico_exclusao ORDER BY data_exclusao DESC")
        historico = cursor.fetchall()
    except psycopg2.Error as e:
        conn.close()
        flash("Erro ao acessar banco de dados.")
        logging.error(f"Erro ao consultar histórico: {e}")
        return redirect(url_for("index"))

    if not historico:
        conn.close()
        flash("Nenhum registro de exclusão encontrado.")
        return redirect(url_for("index"))

    output = io.StringIO()
    output.write("Histórico de Exclusões de RDs\n")
    output.write("=" * 50 + "\n")
    for reg in historico:
        rd_id, solic, valor, data_exc, usuario = reg
        linha = f"Data: {data_exc} | RD: {rd_id} | Solicitante: {solic} | Valor: R${format_currency(valor)} | Excluído por: {usuario}\n"
        output.write(linha)
    output.write("=" * 50 + "\n")
    output.write(f"Total de exclusões: {len(historico)}\n")

    buffer = io.BytesIO(output.getvalue().encode('utf-8'))
    buffer.seek(0)
    conn.close()

    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"Historico_Exclusoes_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
        mimetype="text/plain"
    )


@app.route("/historico_geral")
def historico_geral():
    """
    Exibe uma visão resumida do histórico por RD.
    Mostra apenas a última ação de cada RD.
    """
    if "user_role" not in session:
        flash("Acesso negado.")
        return redirect(url_for("index"))

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)

    # ============ QUERY OTIMIZADA: ÚLTIMA AÇÃO + CONTAGEM EM UMA SÓ QUERY ============
    query = """
    WITH ultima_acao_por_rd AS (
        SELECT DISTINCT ON (rd_id)
            rd_id,
            acao as ultima_acao,
            data_acao as data_ultima_acao,
            usuario as usuario_ultima_acao,
            detalhes as detalhes_ultima_acao
        FROM historico_acoes
        WHERE rd_id IS NOT NULL
        ORDER BY rd_id, data_acao DESC
    ),
    contagem_por_rd AS (
        SELECT rd_id, COUNT(*) as total_movimentacoes
        FROM historico_acoes
        WHERE rd_id IS NOT NULL
        GROUP BY rd_id
    )
    SELECT 
        u.rd_id,
        u.ultima_acao,
        u.data_ultima_acao,
        u.usuario_ultima_acao,
        u.detalhes_ultima_acao,
        COALESCE(c.total_movimentacoes, 0) as total_movimentacoes
    FROM ultima_acao_por_rd u
    LEFT JOIN contagem_por_rd c ON u.rd_id = c.rd_id
    ORDER BY 
        CAST(split_part(u.rd_id, '.', 1) AS BIGINT) DESC,
        CAST(split_part(u.rd_id, '.', 2) AS BIGINT) DESC
    """
    
    try:
        cursor.execute(query)
        resumo_rds = cursor.fetchall()
    except psycopg2.Error as e:
        logging.error(f"Erro ao consultar histórico resumido: {e}")
        conn.rollback()
        resumo_rds = []

    # ============ ESTATÍSTICAS (queries separadas, sem rollback desnecessário) ============
    total_rds = len(resumo_rds)
    
    # Total geral de ações
    total_acoes = 0
    try:
        cursor.execute("SELECT COUNT(*) as total FROM historico_acoes WHERE rd_id IS NOT NULL")
        total_acoes_row = cursor.fetchone()
        if total_acoes_row:
            total_acoes = total_acoes_row['total']
    except psycopg2.Error as e:
        logging.error(f"Erro ao contar ações: {e}")
        total_acoes = 0
    
    # Última ação do sistema
    ultima_acao = "N/A"
    try:
        cursor.execute(
            "SELECT MAX(data_acao) as data_acao FROM historico_acoes WHERE rd_id IS NOT NULL"
        )
        ultima_acao_row = cursor.fetchone()
        if ultima_acao_row and ultima_acao_row['data_acao']:
            ultima_acao = ultima_acao_row['data_acao'].strftime('%d/%m/%Y %H:%M')
    except psycopg2.Error as e:
        logging.error(f"Erro ao buscar última ação: {e}")
        ultima_acao = "N/A"

    conn.close()

    return render_template(
        "historico_geral.html",
        resumo_rds=resumo_rds,
        total_rds=total_rds,
        total_acoes=total_acoes,
        ultima_acao=ultima_acao
    )


@app.route("/historico_geral_completo")
def historico_geral_completo():
    """
    Exibe o histórico completo com todos os eventos de todas as RDs.
    Com filtros avançados.
    """
    if "user_role" not in session:
        flash("Acesso negado.")
        return redirect(url_for("index"))

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)

    # ============ CAPTURA DE FILTROS ============
    filtro_rd_id = request.args.get('rd_id', '').strip()
    filtro_usuario = request.args.get('usuario', '').strip()
    filtro_acao = request.args.get('acao', '').strip()
    filtro_data_inicio = request.args.get('data_inicio', '').strip()
    filtro_data_fim = request.args.get('data_fim', '').strip()
    filtro_periodo = request.args.get('periodo', '').strip()

    # ============ MONTAGEM DA QUERY DINÂMICA ============
    query = "SELECT * FROM historico_acoes WHERE 1=1"
    params = []

    # Filtro por RD
    if filtro_rd_id:
        query += " AND rd_id = %s"
        params.append(filtro_rd_id)

    # Filtro por Usuário
    if filtro_usuario:
        query += " AND usuario = %s"
        params.append(filtro_usuario)

    # Filtro por Ação
    if filtro_acao:
        query += " AND acao = %s"
        params.append(filtro_acao)

    # Filtro por Período Rápido
    if filtro_periodo:
        hoje = datetime.now().date()
        if filtro_periodo == 'hoje':
            data_inicio = hoje
            data_fim = hoje
        elif filtro_periodo == '7dias':
            data_inicio = hoje - timedelta(days=7)
            data_fim = hoje
        elif filtro_periodo == '30dias':
            data_inicio = hoje - timedelta(days=30)
            data_fim = hoje
        elif filtro_periodo == '90dias':
            data_inicio = hoje - timedelta(days=90)
            data_fim = hoje
        else:
            data_inicio = None
            data_fim = None
        
        if data_inicio and data_fim:
            query += " AND DATE(data_acao) >= %s AND DATE(data_acao) <= %s"
            params.extend([data_inicio, data_fim])
    else:
        # Filtro por Datas Específicas
        if filtro_data_inicio:
            query += " AND DATE(data_acao) >= %s"
            params.append(filtro_data_inicio)
        
        if filtro_data_fim:
            query += " AND DATE(data_acao) <= %s"
            params.append(filtro_data_fim)

    # Ordenação padrão
    query += " ORDER BY data_acao DESC"

    # ============ EXECUÇÃO DA QUERY ============
    try:
        cursor.execute(query, params)
        historico_completo = cursor.fetchall()
    except psycopg2.Error as e:
        logging.error(f"Erro ao consultar histórico completo: {e}")
        conn.rollback()
        historico_completo = []

    # ============ ESTATÍSTICAS ============
    total_acoes = len(historico_completo)
    
    # Usuários únicos
    usuarios_unicos = set(evt['usuario'] for evt in historico_completo if evt['usuario'])
    usuarios_unicos_count = len(usuarios_unicos)
    
    # RDs afetadas
    rds_afetadas = set(evt['rd_id'] for evt in historico_completo if evt['rd_id'])
    rds_afetadas_count = len(rds_afetadas)

    # Período exibido
    if historico_completo:
        data_mais_recente = historico_completo[0]['data_acao'].strftime('%d/%m/%Y')
        data_mais_antiga = historico_completo[-1]['data_acao'].strftime('%d/%m/%Y')
        periodo = f"{data_mais_antiga} a {data_mais_recente}"
    else:
        periodo = "Sem dados"

    # ============ LISTAS PARA DROPDOWNS ============
    # Usuários disponíveis
    try:
        cursor.execute(
            "SELECT DISTINCT usuario FROM historico_acoes WHERE usuario IS NOT NULL ORDER BY usuario"
        )
        usuarios_disponiveis = [row['usuario'] for row in cursor.fetchall()]
    except psycopg2.Error as e:
        logging.error(f"Erro ao buscar usuários: {e}")
        conn.rollback()
        usuarios_disponiveis = []

    # Ações disponíveis
    try:
        cursor.execute(
            "SELECT DISTINCT acao FROM historico_acoes WHERE acao IS NOT NULL ORDER BY acao"
        )
        acoes_disponiveis = [row['acao'] for row in cursor.fetchall()]
    except psycopg2.Error as e:
        logging.error(f"Erro ao buscar ações: {e}")
        conn.rollback()
        acoes_disponiveis = []

    conn.close()

    return render_template(
        "historico_geral_completo.html",
        historico=historico_completo,
        total_acoes=total_acoes,
        usuarios_unicos=usuarios_unicos_count,
        rds_afetadas=rds_afetadas_count,
        periodo=periodo,
        # Filtros aplicados
        filtro_rd_id=filtro_rd_id,
        filtro_usuario=filtro_usuario,
        filtro_acao=filtro_acao,
        filtro_data_inicio=filtro_data_inicio,
        filtro_data_fim=filtro_data_fim,
        filtro_periodo=filtro_periodo,
        # Opções dos dropdowns
        usuarios_disponiveis=usuarios_disponiveis,
        acoes_disponiveis=acoes_disponiveis
    )

@app.route("/logout")
def logout():
    session.clear()
    flash("Logout realizado com sucesso.")
    return redirect(url_for("index"))

@app.route("/cadastro_funcionario", methods=["GET"])
def cadastro_funcionario():
    return render_template("cadastro_funcionario.html")

@app.route("/cadastrar_funcionario", methods=["POST"])
def cadastrar_funcionario():
    nome = request.form["nome"].strip()
    centro_custo = request.form["centroCusto"].strip()
    unidade_negocio = request.form["unidadeNegocio"].strip()

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("""
    INSERT INTO funcionarios (nome, centro_custo, unidade_negocio)
    VALUES (%s, %s, %s)
    """, (nome, centro_custo, unidade_negocio))
    conn.commit()
    cursor.close()
    conn.close()
    flash("Funcionário cadastrado com sucesso.")
    return redirect(url_for("cadastro_funcionario"))

@app.route("/consulta_funcionario", methods=["GET"])
def consulta_funcionario():
    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT * FROM funcionarios ORDER BY nome ASC")
    funcionarios = cursor.fetchall()
    conn.close()
    return render_template("consulta_funcionario.html", funcionarios=funcionarios)

@app.route("/marcar_divergente/<id>", methods=["GET", "POST"])
def marcar_divergente(id):
    if "user_role" not in session or session["user_role"] not in ["gestor", "solicitante"]:
        flash("Ação não permitida.")
        return redirect(url_for("index"))

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)

    cursor.execute("SELECT status FROM rd WHERE id = %s", (id,))
    rd = cursor.fetchone()
    if not rd:
        flash("RD não encontrada.")
        cursor.close()
        conn.close()
        return redirect(url_for("index"))

    if rd['status'] == 'Fechado':
        flash("Não é possível marcar uma RD já fechada como divergente.")
        cursor.close()
        conn.close()
        return redirect(url_for("index"))
    
    if request.method == "GET":
        cursor.close()
        conn.close()
        return render_template("motivo_divergente.html", rd_id=id)
    else: # POST
        motivo_div = request.form.get("motivo_divergente", "").strip()
        cursor.execute("""
        UPDATE rd
        SET anexo_divergente = TRUE,
            motivo_divergente = %s
        WHERE id = %s
        """, (motivo_div, id))
        
        detalhe_motivo = f"Motivo: {motivo_div}" if motivo_div else "Nenhum motivo informado."
        registrar_historico(conn, id, "Marcada como Divergente", detalhe_motivo)
        
        conn.commit()
        cursor.close()
        conn.close()
        flash("RD marcada como divergente.")
        
        # MODIFICADO: Captura a aba do formulário.
        active_tab = request.form.get('active_tab', 'tab3')
        return redirect(url_for("index", active_tab=active_tab))
    
@app.route("/anexos_divergentes", methods=["GET"])
def anexos_divergentes():
    if "user_role" not in session:
        flash("Acesso negado.")
        return redirect(url_for("index"))

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT * FROM rd WHERE anexo_divergente = TRUE ORDER BY id")
    divergentes = cursor.fetchall()
    cursor.close()
    conn.close()

    return render_template("divergentes.html", divergentes=divergentes, user_role=session.get("user_role"))

@app.route("/corrigir_divergente/<id>", methods=["GET", "POST"])
def corrigir_divergente(id):
    if "user_role" not in session or session["user_role"] != "supervisor":
        flash("Acesso negado.")
        return redirect(url_for("index"))

    if request.method == "GET":
        conn = get_pg_connection()
        cursor = conn.cursor(cursor_factory=DictCursor)
        cursor.execute("SELECT * FROM rd WHERE id = %s", (id,))
        rd = cursor.fetchone()
        cursor.close()
        conn.close()
        if not rd:
            flash("RD não encontrada.")
            return redirect(url_for("anexos_divergentes"))
        return render_template("corrigir_divergente.html", rd=rd)
    else: # POST
        conn = get_pg_connection()
        cursor = conn.cursor(cursor_factory=DictCursor)

        cursor.execute("SELECT arquivos FROM rd WHERE id = %s", (id,))
        row = cursor.fetchone()
        a_list = row[0].split(",") if (row and row[0]) else []

        if "arquivo" in request.files:
            for f in request.files.getlist("arquivo"):
                if f.filename:
                    fname = f"{id}_{f.filename}"
                    upload_file_to_r2(f, fname)
                    a_list.append(fname)
        new_arq_str = ",".join(a_list) if a_list else None

        cursor.execute("UPDATE rd SET arquivos = %s WHERE id = %s", (new_arq_str, id))
        conn.commit()

        cursor.execute("""
        UPDATE rd
        SET anexo_divergente = FALSE,
            motivo_divergente = NULL
        WHERE id = %s
        """, (id,))
        
        registrar_historico(conn, id, "Divergência Corrigida")
        
        conn.commit()

        cursor.close()
        conn.close()
        flash("Correção da divergência realizada com sucesso.")
        return redirect(url_for("anexos_divergentes"))
    
@app.route("/marcar_pronto_fechamento/<id>", methods=["POST"])
def marcar_pronto_fechamento(id):
    if user_role() != "supervisor":
        flash("Acesso negado.")
        return redirect(url_for("index"))

    conn = get_pg_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT pronto_fechamento FROM rd WHERE id=%s", (id,))
    row = cursor.fetchone()
    if not row:
        flash("RD não encontrada.")
        conn.close()
        return redirect(url_for("index"))

    novo_valor = not row["pronto_fechamento"]
    cursor.execute("UPDATE rd SET pronto_fechamento=%s WHERE id=%s", (novo_valor, id))
    conn.commit()
    cursor.close()
    conn.close()

    if novo_valor:
        flash("RD marcada como pronta para fechamento.")
    else:
        flash("RD desmarcada como pronta para fechamento.")
        
    # MODIFICADO: Captura a aba do formulário com um padrão inteligente.
    active_tab = request.form.get('active_tab', 'tab3')
    return redirect(url_for("index", active_tab=active_tab))


if __name__ == "__main__":
    init_db()
    app.run(debug=True)