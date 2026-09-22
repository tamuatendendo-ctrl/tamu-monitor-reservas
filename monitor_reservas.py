import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
from requests.auth import HTTPBasicAuth


# ============================================================
# TAMU - MONITOR DE RESERVAS STAYS
# Versao para execucao continua no Windows / servidor
#
# CREDENCIAIS NAO FICAM NO CODIGO.
# Configure estas variaveis de ambiente no Windows:
#   STAYS_LOGIN
#   STAYS_SENHA
#   TELEGRAM_BOT_TOKEN
#
# Opcional:
#   TELEGRAM_CHAT_ID
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

URL_API = "https://mdc.stays.com.br"
INTERVALO_MINUTOS = 10
TIMEOUT = 30
MAX_RESERVAS_HISTORICO = 20

ARQUIVO_PROCESSADAS = BASE_DIR / "reservas_processadas.json"
ARQUIVO_MONITOR = BASE_DIR / "reservas_monitor.json"

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    "-1004471247181"
)

PRIMEIRA_EXECUCAO_SILENCIOSA = True


# ============================================================
# LOG
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%d/%m/%Y %H:%M:%S",
)
logger = logging.getLogger("tamu_monitor")


# ============================================================
# CONFIGURACAO / CREDENCIAIS
# ============================================================

def carregar_credenciais():
    login = os.getenv("STAYS_LOGIN")
    senha = os.getenv("STAYS_SENHA")
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")

    faltando = []

    if not login:
        faltando.append("STAYS_LOGIN")
    if not senha:
        faltando.append("STAYS_SENHA")
    if not bot_token:
        faltando.append("TELEGRAM_BOT_TOKEN")

    if faltando:
        raise RuntimeError(
            "Variaveis de ambiente ausentes: " + ", ".join(faltando)
        )

    return login, senha, bot_token


LOGIN, SENHA, BOT_TOKEN = carregar_credenciais()


# ============================================================
# SESSAO STAYS
# ============================================================

session = requests.Session()
session.auth = HTTPBasicAuth(LOGIN, SENHA)
session.headers.update({"Accept": "application/json"})


# ============================================================
# ARQUIVOS DE CONTROLE
# ============================================================

def carregar_processadas():
    if not ARQUIVO_PROCESSADAS.exists():
        return set()

    try:
        with ARQUIVO_PROCESSADAS.open("r", encoding="utf-8") as arquivo:
            dados = json.load(arquivo)
        return set(str(item) for item in dados) if isinstance(dados, list) else set()
    except Exception as erro:
        logger.warning("Falha lendo %s: %s", ARQUIVO_PROCESSADAS.name, erro)
        return set()


def salvar_processadas(processadas):
    temporario = ARQUIVO_PROCESSADAS.with_suffix(".tmp")
    with temporario.open("w", encoding="utf-8") as arquivo:
        json.dump(sorted(processadas), arquivo, indent=4, ensure_ascii=False)
    temporario.replace(ARQUIVO_PROCESSADAS)


def monitor_padrao():
    return {
        "ultima_verificacao": None,
        "total_reservas_processadas": 0,
        "ultima_reserva": None,
        "reservas_novas": [],
        "status": "iniciando",
        "ultimo_erro": None,
        "telegram": "nao testado",
    }


def carregar_monitor():
    if not ARQUIVO_MONITOR.exists():
        return monitor_padrao()

    try:
        with ARQUIVO_MONITOR.open("r", encoding="utf-8") as arquivo:
            dados = json.load(arquivo)
        if not isinstance(dados, dict):
            return monitor_padrao()
        base = monitor_padrao()
        base.update(dados)
        return base
    except Exception as erro:
        logger.warning("Falha lendo %s: %s", ARQUIVO_MONITOR.name, erro)
        return monitor_padrao()


def salvar_monitor(dados):
    temporario = ARQUIVO_MONITOR.with_suffix(".tmp")
    with temporario.open("w", encoding="utf-8") as arquivo:
        json.dump(dados, arquivo, indent=4, ensure_ascii=False)
    temporario.replace(ARQUIVO_MONITOR)


def atualizar_status_monitor(
    processadas,
    ultima_reserva=None,
    status=None,
    ultimo_erro=None,
    telegram=None,
):
    monitor = carregar_monitor()
    monitor["ultima_verificacao"] = datetime.now().isoformat()
    monitor["total_reservas_processadas"] = len(processadas)

    if ultima_reserva:
        monitor["ultima_reserva"] = ultima_reserva
        historico = monitor.get("reservas_novas", [])
        if not isinstance(historico, list):
            historico = []
        historico.insert(0, ultima_reserva)
        monitor["reservas_novas"] = historico[:MAX_RESERVAS_HISTORICO]

    if status is not None:
        monitor["status"] = status
    if ultimo_erro is not None:
        monitor["ultimo_erro"] = ultimo_erro
    if telegram is not None:
        monitor["telegram"] = telegram

    salvar_monitor(monitor)


# ============================================================
# STAYS API
# ============================================================

def consultar_reservas():
    hoje = datetime.now().date()
    data_inicio = hoje - timedelta(days=1)
    data_fim = hoje + timedelta(days=1)

    url = URL_API.rstrip("/") + "/external/v1/booking/reservations"
    params = {
        "from": data_inicio.strftime("%Y-%m-%d"),
        "to": data_fim.strftime("%Y-%m-%d"),
        "dateType": "creation",
    }

    response = session.get(url, params=params, timeout=TIMEOUT)
    response.raise_for_status()
    dados = response.json()

    if isinstance(dados, list):
        return dados
    if isinstance(dados, dict):
        return dados.get("list") or dados.get("reservations") or dados.get("results") or []
    return []


def consultar_imovel(listing_id):
    if not listing_id:
        return {}
    url = URL_API.rstrip("/") + "/external/v1/content/listings/" + str(listing_id)
    response = session.get(url, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def consultar_cliente(client_id):
    if not client_id:
        return {}
    url = URL_API.rstrip("/") + "/external/v1/booking/clients/" + str(client_id)
    response = session.get(url, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


# ============================================================
# TELEGRAM
# ============================================================

def enviar_telegram(mensagem):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    dados = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mensagem,
    }

    response = requests.post(url, data=dados, timeout=TIMEOUT)
    response.raise_for_status()
    retorno = response.json()

    if not retorno.get("ok"):
        raise RuntimeError("Telegram retornou erro: " + str(retorno))

    return True


def testar_telegram():
    enviar_telegram(
        "🤖 TAMU MONITOR\n\n"
        "Monitor iniciado com sucesso.\n"
        "Telegram conectado ao grupo TAMU."
    )


# ============================================================
# PREPARACAO DA RESERVA
# ============================================================

def formatar_data(data):
    if not data:
        return ""
    try:
        return datetime.strptime(data, "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        return str(data)


def identificar_canal(reserva):
    partner = reserva.get("partner", {})
    if isinstance(partner, dict):
        nome = str(partner.get("name", "")).strip()
        nome_lower = nome.lower()
        if "airbnb" in nome_lower:
            return "Airbnb"
        if "booking" in nome_lower:
            return "Booking.com"
        if nome:
            return nome
    return "Nao informado"


def preparar_reserva(reserva):
    listing_id = reserva.get("_idlisting")
    client_id = reserva.get("_idclient")

    logger.info("Consultando imovel da reserva %s...", reserva.get("id", "sem ID"))
    imovel = consultar_imovel(listing_id)

    logger.info("Consultando hospede da reserva %s...", reserva.get("id", "sem ID"))
    cliente = consultar_cliente(client_id)

    apartamento = (
        imovel.get("internalName")
        or imovel.get("id")
        or "Nao informado"
    )

    hospede = (
        cliente.get("name")
        or (str(cliente.get("fName", "")) + " " + str(cliente.get("lName", ""))).strip()
        or "Nao informado"
    )

    telefone = ""
    telefones = cliente.get("phones", [])
    if isinstance(telefones, list):
        for item in telefones:
            if not isinstance(item, dict):
                continue
            telefone = item.get("iso") or item.get("phone") or item.get("number") or ""
            if telefone:
                break
    if not telefone:
        telefone = "Nao informado"

    return {
        "id_interno": reserva.get("_id"),
        "reserva": reserva.get("id", "Nao informado"),
        "apartamento": apartamento,
        "hospede": hospede,
        "telefone": telefone,
        "checkin": formatar_data(reserva.get("checkInDate")),
        "checkin_hora": reserva.get("checkInTime", ""),
        "checkout": formatar_data(reserva.get("checkOutDate")),
        "checkout_hora": reserva.get("checkOutTime", ""),
        "hospedes": reserva.get("guests", 0),
        "canal": identificar_canal(reserva),
        "criada_em": reserva.get("creationDateTime", ""),
        "tipo": reserva.get("type", ""),
    }


def montar_mensagem(dados):
    return (
        "🚨 NOVA RESERVA TAMU\n\n"
        f"📋 Reserva: {dados['reserva']}\n"
        f"🏠 Apartamento: {dados['apartamento']}\n"
        f"👤 Hóspede: {dados['hospede']}\n"
        f"📱 Telefone: {dados['telefone']}\n\n"
        f"📅 Check-in: {dados['checkin']} às {dados['checkin_hora']}\n"
        f"📅 Check-out: {dados['checkout']} às {dados['checkout_hora']}\n"
        f"👥 Hóspedes: {dados['hospedes']}\n"
        f"🌐 Canal: {dados['canal']}\n"
        f"🕐 Criada: {dados['criada_em']}"
    )


# ============================================================
# CICLO DE MONITORAMENTO
# ============================================================

def executar_ciclo(processadas, primeira_execucao):
    logger.info("🔎 VERIFICANDO NOVAS RESERVAS")

    reservas = consultar_reservas()
    logger.info("Reservas encontradas na API: %s", len(reservas))

    if primeira_execucao:
        logger.info("🟡 PRIMEIRA EXECUCAO: registrando reservas sem alertar.")
        adicionadas = 0

        for reserva in reservas:
            reserva_id = reserva.get("_id")
            if reserva_id and str(reserva_id) not in processadas:
                processadas.add(str(reserva_id))
                adicionadas += 1

        salvar_processadas(processadas)
        atualizar_status_monitor(processadas, status="ativo", ultimo_erro=None)
        logger.info("✅ %s reservas registradas sem Telegram.", adicionadas)
        return False

    encontrou_nova = False

    for reserva in reservas:
        reserva_id = reserva.get("_id")
        if not reserva_id:
            logger.warning("Reserva sem _id ignorada.")
            continue

        reserva_id = str(reserva_id)
        if reserva_id in processadas:
            continue

        encontrou_nova = True
        logger.info("🆕 NOVA RESERVA: %s", reserva.get("id", reserva_id))

        try:
            dados = preparar_reserva(reserva)
            logger.info(
                "Reserva: %s | Apartamento: %s | Hospede: %s",
                dados["reserva"], dados["apartamento"], dados["hospede"]
            )

            logger.info("📱 Enviando alerta para Telegram...")
            enviar_telegram(montar_mensagem(dados))
            logger.info("✅ Telegram enviado com sucesso.")

            # Importante: so marca como processada depois do Telegram OK.
            processadas.add(reserva_id)
            salvar_processadas(processadas)

            atualizar_status_monitor(
                processadas,
                ultima_reserva=dados,
                status="ativo",
                ultimo_erro=None,
                telegram="enviado",
            )

            logger.info("💾 Reserva %s registrada como processada.", dados["reserva"])

        except Exception as erro:
            logger.exception(
                "❌ ERRO AO PROCESSAR RESERVA %s",
                reserva.get("id", reserva_id),
            )
            atualizar_status_monitor(
                processadas,
                status="ativo com erro",
                ultimo_erro=str(erro),
            )

    if not encontrou_nova:
        atualizar_status_monitor(
            processadas,
            status="ativo",
            ultimo_erro=None,
        )
        logger.info("✅ Nenhuma reserva nova.")

    return encontrou_nova


# ============================================================
# ============================================================
# MAIN - GITHUB ACTIONS
# Cada execução faz UM ciclo e encerra.
# O GitHub Actions será responsável por executar novamente
# a cada 10 minutos.
# ============================================================

def main():
    logger.info("=" * 60)
    logger.info("TAMU - MONITOR DE RESERVAS STAYS")
    logger.info("EXECUÇÃO GITHUB ACTIONS - CICLO ÚNICO")
    logger.info("=" * 60)
    logger.info("Intervalo agendado: %s minutos", INTERVALO_MINUTOS)
    logger.info("Grupo Telegram: %s", TELEGRAM_CHAT_ID)
    logger.info("Controle: %s", ARQUIVO_PROCESSADAS.name)
    logger.info("Status: %s", ARQUIVO_MONITOR.name)

    processadas = carregar_processadas()
    logger.info("Reservas já processadas: %s", len(processadas))

    primeira_execucao = PRIMEIRA_EXECUCAO_SILENCIOSA and not processadas

    try:
        executar_ciclo(processadas, primeira_execucao)
        logger.info("✅ Ciclo concluído com sucesso.")

    except Exception as erro:
        logger.exception("❌ ERRO NO CICLO: %s", erro)

        try:
            atualizar_status_monitor(
                processadas,
                status="erro no ciclo",
                ultimo_erro=str(erro),
            )
        except Exception:
            logger.exception("❌ Não foi possível salvar o status do erro.")

        # Retorna código de erro para o GitHub Actions marcar a execução
        # como falha e facilitar a identificação do problema.
        raise


if __name__ == "__main__":
    main()
if __name__ == "__main__":
    main()
