import sys
from app import app, db  # Importa o app e o db do seu arquivo principal
from sefaz_service import resetar_nsu_certificado

def executar_reset(cert_id):
    """
    Executa a função de reset dentro do contexto da aplicação Flask.
    """
    if not cert_id:
        print("ERRO: Você precisa fornecer o ID do certificado.")
        print("Uso: python reset_script.py <ID_DO_CERTIFICADO>")
        return

    with app.app_context():
        print(f"Iniciando reset do NSU para o certificado ID: {cert_id}...")
        resultado = resetar_nsu_certificado(cert_id)
        if resultado.get('success'):
            print(f"SUCESSO: {resultado.get('message')}")
        else:
            print(f"FALHA: {resultado.get('message')}")

if __name__ == '__main__':
    try:
        # Pega o ID do certificado do argumento da linha de comando
        certificado_id = int(sys.argv[1])
        executar_reset(certificado_id)
    except (IndexError, ValueError):
        print("ERRO: Argumento inválido.")
        print("Uso: python reset_script.py <ID_DO_CERTIFICADO>")
        # Exemplo: python reset_script.py 4