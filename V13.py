import os
import re
import time
import socket
import shutil
import subprocess
import sys
import requests
from playwright.sync_api import sync_playwright
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta
from dotenv import load_dotenv
import gc
import joblib
import pandas as pd
from urllib.parse import urlparse

# Jogadores com aproveitamento abaixo de 55.1% na base de 17k
BLACKLIST_V12 = [
]

# =================================================================
# 1. CARREGAMENTO DO V12 ULTRA ENSEMBLE
# =================================================================
def carregar_v12():
    diretorio_script = os.path.dirname(os.path.abspath(__file__))
    caminho_pkl = os.path.join(diretorio_script, 'v12_sniper_final.pkl')
    try:
        cerebro = joblib.load(caminho_pkl)
        print(f"✅ [V12 SNIPER] Cérebro carregado com sucesso!")
        return cerebro
    except Exception as e:
        print(f"⚠️ Modelo V12 indisponível: {e}")
        return None

CEREBRO_V12 = carregar_v12()

# =================================================================
# 2. CONEXÕES (PLANILHA E TELEGRAM)
# =================================================================
def conectar_planilha():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    diretorio_atual = os.path.dirname(os.path.abspath(__file__))
    caminho_json = os.path.join(diretorio_atual, 'credentials.json')
    creds = ServiceAccountCredentials.from_json_keyfile_name(caminho_json, scope)
    client = gspread.authorize(creds)
    return client.open_by_key("1df2CADBLfvEn3mY_A6bqPnBDMt5YJmDtE0bWyFeD6Zc")

load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def enviar_telegram(mensagem):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": mensagem, "parse_mode": "Markdown"}
    try:
        response = requests.post(url, data=payload)
        if response.status_code == 200:
            return response.json().get("result", {}).get("message_id")
    except Exception:
        pass
    return None

def editar_mensagem_telegram(message_id, novo_texto):
    url = f"https://api.telegram.org/bot{TOKEN}/editMessageText"
    payload = {"chat_id": CHAT_ID, "message_id": message_id, "text": novo_texto, "parse_mode": "Markdown"}
    try:
        requests.post(url, data=payload)
    except Exception:
        pass

# =================================================================
# 3. EXTRAÇÃO DE DADOS (BET365)
# =================================================================
def extrair_todos_jogos_bet365(page_bet):
    jogos = []
    try:
        bloco_liga = page_bet.locator(".ovm-Competition").filter(has_text="H2H GG League").first
        if bloco_liga.count() == 0:
            return jogos

        cards = bloco_liga.locator(".ovm-Fixture").all()

        for card in cards:
            try:
                times = card.locator(".ovm-FixtureDetailsTwoWay_TeamName").all_inner_texts()
                if len(times) < 2:
                    continue

                casa = (times[0] or "").strip()
                fora = (times[1] or "").strip()
                if not casa or not fora:
                    continue

                placares = card.locator(".ovm-ScorePill").all_inner_texts()
                if len(placares) < 2:
                    continue
                s_casa = int((placares[0] or "0").strip())
                s_fora = int((placares[1] or "0").strip())

                periodo_el = card.locator(".ovm-PeriodInfo")
                tempo_el   = card.locator(".ovm-InPlayTimer")

                if periodo_el.count() == 0:
                    continue

                periodo = (periodo_el.inner_text() or "").strip()

                tempo = "00:00"
                if tempo_el.count() > 0:
                    tempo = (tempo_el.inner_text() or "").strip()
                elif "Intervalo" in periodo or "Half" in periodo:
                    periodo = "Intervalo"

                if   "3º" in periodo or "Q3" in periodo: periodo = "Q3"
                elif "2º" in periodo or "Q2" in periodo: periodo = "Q2"
                elif "1º" in periodo or "Q1" in periodo: periodo = "Q1"

                linha_o, odd_o, linha_u, odd_u = None, None, None, None
                mercados = card.locator(".ovm-Market").all()

                if len(mercados) >= 2:
                    participantes = mercados[1].locator(".ovm-ParticipantHandicap").all()
                    if len(participantes) >= 2:
                        txt_o = (participantes[0].inner_text() or "").replace('\n', ' ')
                        mo = re.search(r'O\s*(\d+[\.,]\d+)\s*(\d+[\.,]\d+)', txt_o)
                        if mo:
                            linha_o = float(mo.group(1).replace(',', '.'))
                            odd_o   = float(mo.group(2).replace(',', '.'))

                        txt_u = (participantes[1].inner_text() or "").replace('\n', ' ')
                        mu = re.search(r'U\s*(\d+[\.,]\d+)\s*(\d+[\.,]\d+)', txt_u)
                        if mu:
                            linha_u = float(mu.group(1).replace(',', '.'))
                            odd_u   = float(mu.group(2).replace(',', '.'))

                jogos.append({
                    "id": f"{casa} vs {fora}",
                    "casa": casa, "fora": fora,
                    "periodo": periodo, "tempo": tempo,
                    "score_casa": s_casa, "score_fora": s_fora,
                    "linha_over": linha_o, "odd_over": odd_o,
                    "linha_under": linha_u, "odd_under": odd_u,
                })
            except Exception:
                continue
    except Exception:
        pass
    return jogos


def registrar_atualizacoes_bet365(jogos, estado_anterior):
    """Exibe somente mudanças observadas na página já aberta pelo usuário."""
    estado_atual = {
        jogo["id"]: (
            jogo["periodo"], jogo["tempo"], jogo["score_casa"], jogo["score_fora"],
            jogo["linha_over"], jogo["odd_over"], jogo["linha_under"], jogo["odd_under"],
        )
        for jogo in jogos
    }

    if not estado_anterior:
        print(f"📡 Bet365: {len(jogos)} jogo(s) encontrado(s). Aguardando alterações...")
    else:
        alterados = [
            jogo_id for jogo_id, dados in estado_atual.items()
            if estado_anterior.get(jogo_id) != dados
        ]
        removidos = set(estado_anterior) - set(estado_atual)
        if alterados or removidos:
            print(f"🔄 Bet365 atualizou: {len(alterados)} alterado(s), {len(removidos)} removido(s).")

    return estado_atual


def localizar_aba_bet365(context):
    """Localiza a aba pela URL, inclusive quando o título foi alterado por uma página de erro."""
    return next(
        (
            page for page in context.pages
            if "bet365" in page.url.lower() or "bet365" in page.title().lower()
        ),
        None,
    )


def pagina_bet365_bloqueada(page_bet):
    """Detecta páginas de bloqueio para evitar novas tentativas automáticas."""
    try:
        titulo = page_bet.title().lower()
        texto = page_bet.locator("body").inner_text(timeout=3_000).lower()
        return "cloudflare" in titulo or "you have been blocked" in texto
    except Exception:
        return False


CDP_HOST = "127.0.0.1"
CDP_PORT = int(os.getenv("BET365_CDP_PORT", "9222"))
CDP_URL = f"http://{CDP_HOST}:{CDP_PORT}"


def encontrar_executavel_chrome():
    """Retorna o executável do Chrome no Linux ou Windows."""
    if sys.platform.startswith("win"):
        candidatos = [
            os.path.join(os.environ.get("PROGRAMFILES", ""), "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
        ]
    else:
        candidatos = [
            shutil.which("google-chrome"),
            shutil.which("google-chrome-stable"),
            shutil.which("chromium"),
            shutil.which("chromium-browser"),
            "/usr/bin/google-chrome",
        ]

    return next((caminho for caminho in candidatos if caminho and os.path.exists(caminho)), None)


def porta_cdp_disponivel():
    try:
        with socket.create_connection((CDP_HOST, CDP_PORT), timeout=1):
            return True
    except OSError:
        return False


def encerrar_todas_instancias_chrome():
    """Encerra navegadores Chrome/Chromium antes de iniciar o ambiente dedicado do bot."""
    print("🛑 Encerrando todas as instâncias do Chrome/Chromium...")
    if sys.platform.startswith("win"):
        subprocess.run(
            ["taskkill", "/F", "/IM", "chrome.exe"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        for processo in ("chrome", "google-chrome", "chromium", "chromium-browser"):
            subprocess.run(
                ["pkill", "-x", processo],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )

    for _ in range(10):
        if not porta_cdp_disponivel():
            print("✅ Instâncias anteriores encerradas.")
            return
        time.sleep(1)

    raise RuntimeError(f"A porta {CDP_PORT} permaneceu ocupada após encerrar o Chrome.")


def conectar_ou_iniciar_chrome(playwright):
    """Conecta ao Chrome existente ou inicia uma instância local para o bot."""
    if porta_cdp_disponivel():
        print(f"🌐 Chrome existente encontrado na porta {CDP_PORT}.")
        return playwright.chromium.connect_over_cdp(CDP_URL)

    executavel = encontrar_executavel_chrome()
    if not executavel:
        raise RuntimeError("Google Chrome não foi encontrado. Instale-o ou informe o executável no sistema.")

    perfil_configurado = os.getenv("BET365_CHROME_PROFILE")
    perfil = perfil_configurado or os.path.join(os.path.expanduser("~"), ".chrome-bet365-debug")
    os.makedirs(perfil, exist_ok=True)
    headless = os.getenv("BET365_HEADLESS", "false").strip().lower() in {"1", "true", "yes", "sim"}
    comando = [
        executavel,
        f"--remote-debugging-port={CDP_PORT}",
        f"--remote-debugging-address={CDP_HOST}",
        f"--user-data-dir={perfil}",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--window-size=1920,1080",
    ]
    if headless:
        comando.append("--headless=new")

    print(f"🌐 Iniciando Chrome do bot ({'headless' if headless else 'visível'})...")
    subprocess.Popen(comando, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    for _ in range(15):
        if porta_cdp_disponivel():
            print(f"✅ Chrome do bot pronto na porta {CDP_PORT}.")
            return playwright.chromium.connect_over_cdp(CDP_URL)
        time.sleep(1)

    raise RuntimeError(f"Chrome não respondeu na porta {CDP_PORT} em 15 segundos.")


def limpar_abas_do_contexto(context):
    """Fecha abas antigas do contexto do bot e preserva uma aba para a nova navegação."""
    paginas = context.pages[:]
    pagina_principal = paginas[0] if paginas else context.new_page()

    for pagina in paginas[1:]:
        try:
            pagina.close()
        except Exception:
            pass

    return pagina_principal

# =================================================================
# 4. EXTRAÇÃO DE DADOS (TIPMANAGER)
# =================================================================
def acessar_confronto_tipmanager(browser_context, nome_casa_bet, nome_fora_bet, tentativas=2):
    def extrair_player(nome):
        match = re.search(r'\((.*?)\)', nome)
        return match.group(1).strip().title() if match else nome.strip().title()

    p1, p2 = extrair_player(nome_casa_bet), extrair_player(nome_fora_bet)
    print(f"   🌐 TipManager: Buscando {p1} vs {p2}...")

    for tentativa in range(1, tentativas + 1):
        page_tip = browser_context.new_page()
        try:
            page_tip.goto("https://tipmanager.net/pt/today", timeout=60000)
            gatilho = page_tip.locator("span:has-text('Buscar...')").first
            gatilho.wait_for(state="visible", timeout=10000)
            gatilho.click()
            campo_input = page_tip.get_by_placeholder("Busque jogadores, esportes ou ligas")
            campo_input.wait_for(state="visible", timeout=10000)
            campo_input.type(f"{p1} {p2}", delay=0)

            try:
                page_tip.wait_for_selector("div.cursor-pointer, span:has-text('vs')", timeout=4000)
                page_tip.wait_for_timeout(1000)
            except: page_tip.wait_for_timeout(1500)
                        
            card_encontrado = None
            for scroll_count in range(2):
                items = page_tip.locator("div.cursor-pointer, a[href*='game'], button").all()
                for item in items:
                    try:
                        texto_lower = item.inner_text().replace("\n", " ").strip().lower()
                        if "vs" in texto_lower and p1.lower() in texto_lower and p2.lower() in texto_lower and ("basket" in texto_lower or "h2h gg" in texto_lower):
                            card_encontrado = item
                            break 
                    except: continue
                if card_encontrado: break
                if scroll_count < 1:
                    page_tip.locator("input[cmdk-input]").press("PageDown")
                    page_tip.wait_for_timeout(800)
            
            if not card_encontrado:
                page_tip.close()
                continue
            
            try: card_encontrado.click(timeout=1000)
            except: card_encontrado.evaluate("el => el.click()")
            
            try:
                page_tip.wait_for_selector("p.font-exo.italic.font-black", timeout=15000)
                page_tip.wait_for_timeout(3000)
                return page_tip 
            except:
                page_tip.close()
                continue
        except Exception:
            page_tip.close()
    return None

def extrair_e_analisar(page_tip):
    linhas_over_vip, linhas_under_vip = {}, {}
    try:
        page_tip.set_viewport_size({"width": 1920, "height": 1080})
        page_tip.evaluate("document.body.style.zoom = '0.8'")
        page_tip.wait_for_timeout(1000)

        try:
            h2h_element = page_tip.locator("p.font-exo.italic.font-black").first
            h2h_element.wait_for(state="visible", timeout=6000)
            if h2h_element.count() > 0:
                numero_cru = (h2h_element.inner_text() or "").strip()
                if numero_cru.isdigit() and int(numero_cru) < 1:
                    return {"status": "DESCARTADO_H2H"}
                h2h_count = f"{numero_cru} Partidas" if numero_cru.isdigit() else numero_cru
        except Exception:
            h2h_count = "N/A"

        media_q4_scanner, media_l10_total, media_hist_total = 0.0, 0.0, 0.0

        try:
            candidatos = page_tip.locator("div").filter(has_text=re.compile(r"^\s*4Q\s*$")).all()
            for elemento in candidatos:
                if not elemento.is_visible():
                    continue
                mc = float((elemento.locator("xpath=preceding-sibling::div[1]").inner_text() or "0").strip())
                mf = float((elemento.locator("xpath=following-sibling::div[1]").inner_text() or "0").strip())
                if mc > 0 or mf > 0:
                    media_q4_scanner = mc + mf
                    break
        except Exception:
            pass

        try:
            lbl_l10 = page_tip.locator("div").filter(has_text=re.compile(r"^10 últimas$")).last
            media_l10_total = float(
                (lbl_l10.locator("xpath=..").locator("div").last.locator("span").first.inner_text() or "0").strip()
            )
            lbl_geral = page_tip.locator("div").filter(has_text=re.compile(r"^Todas as Partidas$")).last
            media_hist_total = float(
                (lbl_geral.locator("xpath=..").locator("div").last.locator("span").first.inner_text() or "0").strip()
            )
        except Exception:
            pass

        soma_ponderada   = (media_l10_total * 0.77) + (media_hist_total * 0.23)
        media_estimada   = soma_ponderada / 4
        media_final_ajustada = (
            (media_estimada * 0.89) + (media_q4_scanner * 0.11)
            if media_q4_scanner > 0 else media_estimada
        )

        quadro_partida = page_tip.locator("div.rounded-xl").filter(
            has=page_tip.locator("h3", has_text="Over/Under (Partida)")
        ).first

        if quadro_partida.count() > 0:
            for linha in quadro_partida.locator("div.h-14.border-b").all():
                dados = linha.inner_text().split("\n")
                if len(dados) >= 2:
                    try:
                        pct       = float(dados[1].replace("%", "").strip().replace(',', '.'))
                        linha_val = float(dados[0].replace(',', '.'))
                        linhas_over_vip[linha_val] = pct
                    except Exception:
                        pass

        return {
            "status": "SUCESSO",
            "h2h": h2h_count,
            "media_q4_scanner": media_q4_scanner,
            "media_l10_total": media_l10_total,
            "media_hist_total": media_hist_total,
            "media_estimada": media_estimada,
            "media_final": media_final_ajustada,
            "over_vip": linhas_over_vip,
            "under_vip": linhas_under_vip,
        }
    except Exception:
        return {"status": "ERRO"}

# =================================================================
# 5. AUDITORIA DE PLACAR
# =================================================================
from datetime import datetime, timedelta
fila_espera_auditoria = []

def auditar_resultado_recente(page_gg, dados_tarefa):
    try:
        from datetime import datetime, timedelta

        id_completo = dados_tarefa.get('id')
        partes = id_completo.split(" vs ")
        
        def limpar_nome(texto):
            match = re.search(r'\((.*?)\)', texto)
            return match.group(1).strip().upper() if match else texto.strip().upper()

        j_casa = limpar_nome(partes[0])
        j_fora = limpar_nome(partes[1])

        # 1. LOCALIZAÇÃO DOS CARDS
        cards_locator = page_gg.locator("a.match-list__item").filter(has_text=j_casa).filter(has_text=j_fora)
        count = cards_locator.count()

        if count == 0: return None, None

        # 2. FILTRO POR JANELA DE TEMPO (MÁX 30 MIN)
        card_valido = None
        hora_tip = datetime.strptime(dados_tarefa['horario_envio_robo'], "%H:%M")

        for i in range(count):
            card_temp = cards_locator.nth(i)
            # No seu HTML, o horário fica na classe 'match-list__year'
            horario_site_str = card_temp.locator(".match-list__year").inner_text().strip()
            
            try:
                hora_site = datetime.strptime(horario_site_str, "%H:%M")
                
                # Cálculo da diferença (em minutos)
                diff = abs((hora_site - hora_tip).total_seconds() / 60)

                # Se a diferença for maior que 30 min, é outro jogo (outra rodada)
                if diff <= 40:
                    card_valido = card_temp
                    break
            except:
                continue

        if not card_valido:
            print(f" ⏳ Jogo {id_completo} encontrado, mas fora da janela de 30min. Ignorando.")
            return None, None

        # 3. VERIFICAÇÃO DE STATUS (Se já encerrou)
        status_texto = card_valido.inner_text().lower()
        if "live" in status_texto or "suspended" in status_texto:
            return None, None

        # 4. CAPTURA DO PLACAR
        scores_elements = card_valido.locator(".widget__match-score").all_inner_texts()
        if len(scores_elements) < 2: return None, None

        scores = [int(re.sub(r'\D', '', x)) for x in scores_elements[:2]]
        total_final = sum(scores)
        linha_bet = float(dados_tarefa['linha_bet'])
        mercado = dados_tarefa['mercado'].upper()

        # 5. RESULTADO
        if total_final == linha_bet:
            resultado = "VOID"
        elif mercado == "OVER":
            resultado = "GREEN" if total_final > linha_bet else "RED"
        else:
            resultado = "GREEN" if total_final < linha_bet else "RED"

        placar_str = f"{scores[0]}:{scores[1]}"

        # 6. ATUALIZAÇÃO DA PLANILHA (Coluna X)
        try:
            plan = conectar_planilha()
            aba = plan.worksheet({1:"JAN", 2:"FEV", 3:"MAR", 4:"ABR", 5:"MAI", 6:"JUN", 7:"JUL", 8:"AGO", 9:"SET", 10:"OUT", 11:"NOV", 12:"DEZ"}[datetime.now().month])
            aba.update_acell(f"X{dados_tarefa['linha_planilha']}", resultado)
        except: pass

        return resultado, placar_str

    except Exception as e:
        print(f"❌ Erro Auditoria: {e}")
        return None, None

# =================================================================
# 6. MOTOR PRINCIPAL (V13)
# =================================================================
# ... (imports e funções anteriores) ...

def iniciar_monitoramento_final():
    modo_coleta = CEREBRO_V12 is None
    if modo_coleta:
        print("🧪 MODO COLETA BET365: modelo V12 ausente; sinais, Telegram e planilha estão desativados.")
    else:
        print("🧠 Modelo V12 carregado; monitoramento completo habilitado.")

    with sync_playwright() as p:
        encerrar_chrome = os.getenv("BET365_CLOSE_ALL_CHROME", "true").strip().lower() in {"1", "true", "yes", "sim"}
        if encerrar_chrome:
            encerrar_todas_instancias_chrome()
        browser = conectar_ou_iniciar_chrome(p)
        context = browser.contexts[0]

        limpar_abas = os.getenv("BET365_RESET_TABS", "true").strip().lower() in {"1", "true", "yes", "sim"}
        page_bet = limpar_abas_do_contexto(context) if limpar_abas else localizar_aba_bet365(context)
        if limpar_abas:
            print("🧹 Abas anteriores do Chrome do bot foram fechadas.")

        if not page_bet or "bet365" not in page_bet.url.lower():
            print("🌐 Abrindo a Bet365 na aba limpa.")
            page_bet = page_bet or context.new_page()
            page_bet.goto("https://www.bet365.bet.br/#/IP/B18", timeout=60000)
            page_bet.wait_for_timeout(3000)
            print("✅ Aba da Bet365 aberta!")

        if pagina_bet365_bloqueada(page_bet):
            print("❌ A Bet365 retornou uma página de bloqueio (Cloudflare). Nenhuma coleta será feita.")
            print("   Resolva o bloqueio diretamente no navegador e execute o script novamente.")
            return

        page_gg = next((pg for pg in context.pages if "h2hggl.com" in pg.url), None)
        if not modo_coleta and not page_gg:
            print("🌐 Abrindo página H2HGGL para auditoria...")
            page_gg = context.new_page()
            page_gg.goto("https://h2hggl.com/", timeout=60000)
            page_gg.wait_for_timeout(3000)
            print("✅ Página de Auditoria (H2HGGL) aberta!")

        memoria_pre_live      = {}
        jogos_para_auditar    = []
        jogos_descartados     = set()
        lockdown_jogos        = {}
        ultimo_status_lockdown = False
        ultimo_qtd_mochila    = -1
        ultimo_qtd_auditoria  = -1
        estado_bet365_anterior = {}

        # NOVO: Dicionário para controlar as tentativas no TipManager
        # { "ID_JOGO": { "tentativas": N, "timestamp_ultima_tentativa": T } }
        tentativas_tipmanager_por_jogo = {}
        MAX_TENTATIVAS_TIPMANAGER = 3 # Limite de tentativas para o TipManager

        print("\n" + "="*50)
        print("🚀 MOTOR V13 INICIADO (Movido a Inteligência Artificial)")
        print("="*50 + "\n")

        while True:
            try:
                # Limpeza de tentativas antigas (ex: após 1 hora)
                agora = time.time()
                tentativas_tipmanager_por_jogo = {
                    k: v for k, v in tentativas_tipmanager_por_jogo.items()
                    if (agora - v.get('timestamp_ultima_tentativa', 0)) < 3600 # Limpa após 1 hora
                }

                qtd_mochila   = len(memoria_pre_live)
                qtd_auditoria = len(jogos_para_auditar)

                # Limpa jogos com mais de 1h na mochila
                memoria_pre_live = {
                    k: v for k, v in memoria_pre_live.items()
                    if (time.time() - v.get('timestamp_adicao', time.time())) < 3600
                }

                if qtd_mochila != ultimo_qtd_mochila or qtd_auditoria != ultimo_qtd_auditoria:
                    print(f"🎒 Jogos na mochila: {qtd_mochila} | Auditando: {qtd_auditoria}")
                    ultimo_qtd_mochila   = qtd_mochila
                    ultimo_qtd_auditoria = qtd_auditoria

                # Adiciona um atraso aleatório antes de extrair da Bet365
                time.sleep(3)
                jogos_na_tela = extrair_todos_jogos_bet365(page_bet)

                if modo_coleta:
                    estado_bet365_anterior = registrar_atualizacoes_bet365(
                        jogos_na_tela, estado_bet365_anterior
                    )
                    time.sleep(1)
                    continue

                # ----- PRIORIDADE E LOCKDOWN -----
                def urgencia(j):
                    peso = {"Q3": 3000, "Q2": 2000, "Q1": 1000}.get(j["periodo"], 0)
                    try:
                        m, s = map(int, j["tempo"].split(":"))
                        return peso - (m * 60 + s)
                    except Exception:
                        return peso

                jogos_na_tela = sorted(jogos_na_tela, key=urgencia, reverse=True)

                em_lockdown = any(
                    (j["periodo"] == "Q3" and (j["tempo"] == "01:00" or j["tempo"].startswith("00:")))
                    or j["periodo"] == "Intervalo"
                    for j in jogos_na_tela if j["id"] in memoria_pre_live
                )

                if em_lockdown != ultimo_status_lockdown:
                    if em_lockdown:
                        print("🛡️ LOCKDOWN ATIVO: Foco total no jogo crítico! (Novos jogos ignorados)")
                    else:
                        print("🔓 LOCKDOWN ENCERRADO: Voltando a buscar novos jogos.")
                    ultimo_status_lockdown = em_lockdown

                # ----- LOOP DE JOGOS -----
                for jogo in jogos_na_tela:
                    id_j = jogo['id']

                    # 🛑 BARREIRA: Ignora jogos que já estão no Q4
                    if "4" in jogo['periodo'] or "Q4" in jogo['periodo']:
                        if id_j not in jogos_descartados:
                            jogos_descartados.add(id_j)
                            print(f" ⏭️ Ignorando jogo que já está no Q4: {id_j}")
                        continue

                    if id_j in lockdown_jogos:
                        if (time.time() - lockdown_jogos[id_j]) < 30:
                            continue

                    # ---------------------------------------------------------
                    # FASE PRÉ-LIVE
                    # ---------------------------------------------------------
                    # NOVO: Verifica se o jogo já excedeu o limite de tentativas no TipManager
                    if id_j in tentativas_tipmanager_por_jogo and \
                       tentativas_tipmanager_por_jogo[id_j]['tentativas'] >= MAX_TENTATIVAS_TIPMANAGER:
                        if id_j not in jogos_descartados:
                            jogos_descartados.add(id_j)
                            print(f"   🚫 Jogo {id_j} descartado: Excedeu {MAX_TENTATIVAS_TIPMANAGER} tentativas no TipManager.")
                        continue # Pula para o próximo jogo

                    if not em_lockdown and id_j not in memoria_pre_live and id_j not in jogos_descartados:
                        lockdown_jogos[id_j] = time.time()

                        # Atualiza o contador de tentativas para este jogo
                        tentativas_tipmanager_por_jogo.setdefault(id_j, {"tentativas": 0, "timestamp_ultima_tentativa": agora})
                        tentativas_tipmanager_por_jogo[id_j]["tentativas"] += 1
                        tentativas_tipmanager_por_jogo[id_j]["timestamp_ultima_tentativa"] = agora

                        print(f"   🌐 Tentativa {tentativas_tipmanager_por_jogo[id_j]['tentativas']}/{MAX_TENTATIVAS_TIPMANAGER} para {id_j} no TipManager...")

                        page_tip = acessar_confronto_tipmanager(context, jogo['casa'], jogo['fora'])

                        if page_tip:
                            dados_tip = None
                            try:
                                dados_tip = extrair_e_analisar(page_tip)
                            except Exception as e:
                                print(f"⚠️ Erro ao ler TipManager: {e}")
                            finally:
                                try:
                                    page_tip.close()
                                except Exception:
                                    pass

                            if dados_tip and dados_tip["status"] == "SUCESSO":
                                dados_tip['linha_inicial_bet']  = jogo['linha_over'] if jogo['linha_over'] else 0
                                dados_tip['timestamp_adicao']   = time.time()

                                memoria_pre_live[id_j] = dados_tip
                                print(f"✅ Dados Salvos na Mochila: {id_j}")
                                # Se teve sucesso, remove das tentativas para que possa ser reavaliado se sair da mochila
                                if id_j in tentativas_tipmanager_por_jogo:
                                    del tentativas_tipmanager_por_jogo[id_j]

                            elif dados_tip and dados_tip["status"] == "DESCARTADO_H2H":
                                jogos_descartados.add(id_j)
                                print(f"   🚫 Descartado H2H Baixo: {id_j}")
                                # Também remove das tentativas, pois já foi descartado por outro motivo
                                if id_j in tentativas_tipmanager_por_jogo:
                                    del tentativas_tipmanager_por_jogo[id_j]
                            else:
                                # Se a extração do TipManager falhou (dados_tip é None ou status != SUCESSO)
                                # O contador já foi incrementado no início do bloco
                                print(f"   ⚠️ Falha na extração do TipManager para {id_j}. Tentativas restantes: {MAX_TENTATIVAS_TIPMANAGER - tentativas_tipmanager_por_jogo[id_j]['tentativas']}")
                                if tentativas_tipmanager_por_jogo[id_j]['tentativas'] >= MAX_TENTATIVAS_TIPMANAGER:
                                    jogos_descartados.add(id_j)
                                    print(f"   🚫 Jogo {id_j} descartado: Excedeu {MAX_TENTATIVAS_TIPMANAGER} tentativas no TipManager.")

                        else: # page_tip é None, ou seja, acessar_confronto_tipmanager falhou
                            print(f"   ⚠️ Falha ao acessar TipManager para {id_j}. Tentativas restantes: {MAX_TENTATIVAS_TIPMANAGER - tentativas_tipmanager_por_jogo[id_j]['tentativas']}")
                            if tentativas_tipmanager_por_jogo[id_j]['tentativas'] >= MAX_TENTATIVAS_TIPMANAGER:
                                jogos_descartados.add(id_j)
                                print(f"   🚫 Jogo {id_j} descartado: Excedeu {MAX_TENTATIVAS_TIPMANAGER} tentativas no TipManager.")

                    # ---------------------------------------------------------
                    # FASE DE DECISÃO: GATILHO V12 SNIPER
                    # ---------------------------------------------------------
                    is_gatilho = (
                        (jogo['periodo'] == 'Q3' and jogo['tempo'] == '00:00')
                        or jogo['periodo'] == 'Intervalo'
                    )
                    time.sleep(1.5) # Mantém este sleep para simular pausa humana

                    if is_gatilho and id_j in memoria_pre_live and id_j not in jogos_descartados:
                        mem = memoria_pre_live[id_j]

                        s_casa, s_fora   = jogo['score_casa'], jogo['score_fora']
                        diferenca        = abs(s_casa - s_fora)
                        total_3q         = s_casa + s_fora
                        linha_ativa      = jogo['linha_over'] if jogo['linha_over'] else jogo['linha_under']
                        odd_ativa        = jogo['odd_over']   if jogo['linha_over'] else jogo['odd_under']
                        mercado_analisado = "OVER" if jogo['linha_over'] else "UNDER"

                        if not linha_ativa:
                            continue

                        try:
                            h2h_val = float(str(mem['h2h']).split()[0])
                        except Exception:
                            h2h_val = 0.0

                        def obter_porcentagem_vip(l_bet, d_vip):
                            if not d_vip:
                                return 0.0
                            l_bet = float(l_bet)
                            for l_vip, pct in d_vip.items():
                                if abs(l_bet - float(l_vip)) <= 1.5:
                                    return float(pct)
                            return 0.0

                        pct_val = obter_porcentagem_vip(
                            linha_ativa,
                            mem['over_vip'] if mercado_analisado == "OVER" else mem['under_vip']
                        )
                        alvo_calculado        = round(mem['media_final'] + total_3q)
                        movimento_linha_atual = float(linha_ativa - mem['linha_inicial_bet'])

                        # --- MÉTRICA RECUPERADA DO V13 ---
                        pace_linha = linha_ativa - total_3q
                        media_final_x_pace_linha = mem['media_final'] - pace_linha
                        # -----------------------------

                        # FILTROS MECÂNICOS (Agora igual ao V13)
                        q3_linha = total_3q / linha_ativa
                        if diferenca < 8 or alvo_calculado < linha_ativa + 1 or q3_linha < 0.72 or q3_linha > 0.83 or media_final_x_pace_linha < 1.5:
                            print(f"    🚫 Filtro: {id_j} (G:{diferenca} | A:{alvo_calculado} vs L:{linha_ativa} | Pace:{q3_linha:.2f} | Média x Pace: {media_final_x_pace_linha:.2f})")
                            if id_j in memoria_pre_live:
                                del memoria_pre_live[id_j]
                            jogos_descartados.add(id_j)
                            continue

                        # PREDIÇÃO V12
                        try:
                            if 0.1 <= pct_val < 40:
                                print(f" ✋ VETADO PELO SITE: {id_j} | Tipmanager: {pct_val}%")
                                jogos_descartados.add(id_j)
                            else:
                                if pct_val == 0:
                                    print(f" ℹ️ Tipmanager sem dados. IA assumindo.")

                                dados_v12 = {
                                    'Média L10':      float(mem['media_l10_total']),
                                    'Média Hist':     float(mem['media_hist_total']),
                                    'Pace Jogo':      float(total_3q / 3),
                                    'Garbage':        float(diferenca),
                                    'Q3/Linha':       float(total_3q / linha_ativa),
                                    'Mov Linha':      float(movimento_linha_atual),
                                    'Diff L10 Hist':  float(mem['media_l10_total'] - mem['media_hist_total']),
                                }

                                df_jogo     = pd.DataFrame([dados_v12])
                                modelo_ia   = CEREBRO_V12['modelo'] if isinstance(CEREBRO_V12, dict) else CEREBRO_V12
                                chance_green = modelo_ia.predict_proba(df_jogo)[0][1]

                                if chance_green >= 0.58:
                                    if id_j in jogos_descartados:
                                        continue
                                    jogos_descartados.add(id_j)
                                    if id_j in memoria_pre_live:
                                        del memoria_pre_live[id_j]

                                    if   chance_green >= 0.69: label_vip = "🏆 SINAL ELITE (2.5%) 🏆"
                                    elif chance_green >= 0.65: label_vip = "💎 SINAL DIAMANTE (1.5%) 💎"
                                    else:                      label_vip = "⚡ SINAL NORMAL (0.5%) ⚡"

                                    # Limpeza de nomes blindada contra NoneType
                                    casa_str = str(jogo.get('casa', ''))
                                    fora_str = str(jogo.get('fora', ''))

                                    t_casa_limpo = (
                                        casa_str.split('(')[1].split(')')[0].strip().title()
                                        if '(' in casa_str and ')' in casa_str else casa_str.strip().title()
                                    )
                                    t_fora_limpo = (
                                        fora_str.split('(')[1].split(')')[0].strip().title()
                                        if '(' in fora_str and ')' in fora_str else fora_str.strip().title()
                                    )

                                    if t_casa_limpo in BLACKLIST_V12 or t_fora_limpo in BLACKLIST_V12:
                                        print(f" 🚫 Blacklist: {t_casa_limpo} vs {t_fora_limpo} ignorado.")
                                    else:

                                        msg = (
                                            f"{label_vip}\n"
                                            f"🏀 *{id_j}*\n"
                                            f"👉 Entrada: *{mercado_analisado} {linha_ativa}* (Odd: {odd_ativa})\n"
                                            f"🤖 Confiança V12: {chance_green:.2%}\n"
                                            f"📊 Tipmanager: {pct_val}%\n"
                                            f"🎯 Alvo: {alvo_calculado} | Score 3Q: {total_3q}\n"
                                        )

                                        msg_id = enviar_telegram(msg)
                                        print(f" ✅ V13 ENVIADO: {id_j} | Confiança: {chance_green:.2%}")

                                        try:
                                            plan = conectar_planilha()
                                            aba = plan.worksheet({1:"JAN", 2:"FEV", 3:"MAR", 4:"ABR", 5:"MAI", 6:"JUN", 7:"JUL", 8:"AGO", 9:"SET", 10:"OUT", 11:"NOV", 12:"DEZ"}[datetime.now().month])

                                            nova_linha = [
                                                datetime.now().strftime("%d/%m/%Y"), # B: Data
                                                "GG League",                         # C: Competição
                                                t_casa_limpo,                        # D: Time Casa
                                                t_fora_limpo,                        # E: Time Fora
                                                int(total_3q),                       # F: Pontos Q3
                                                float(mem['media_l10_total']),       # G: Média L10
                                                float(mem['media_hist_total']),      # H: Média Hist
                                                float(mem['media_estimada']),        # I: Média Est
                                                float(total_3q / 3),                 # J: Pace Jogo
                                                float(mem['media_q4_scanner']),      # K: Média 4Q
                                                float(mem.get('media_final', 0)),    # L: Média Final
                                                float(linha_ativa - total_3q),       # M: Pace Linha (Ajustado)
                                                1,                                   # N: Fator
                                                float(alvo_calculado),               # O: Alvo
                                                float(linha_ativa),                  # P: Linha
                                                mercado_analisado.title(),           # Q: Mercado
                                                int(diferenca),                      # R: Diferença
                                                float(round(chance_green*100, 2)),   # S: % (IA)
                                                float(h2h_val),                      # T: H2H
                                                "",                                  # U: Final (DEIXAR VAZIO PARA AUDITORIA)
                                                20,                                  # V: Stake
                                                str(odd_ativa).replace('.', ','),    # W: Odds
                                                ""                                   # X: Resultado
                                            ]

                                            linha_alvo = max(len(aba.col_values(2)) + 1, 3)
                                            aba.update(
                                                values=[nova_linha],
                                                range_name=f"B{linha_alvo}:X{linha_alvo}",
                                                value_input_option="USER_ENTERED",
                                            )

                                            jogos_para_auditar.append({
                                                'id':                  id_j,
                                                'linha_bet':           linha_ativa,
                                                'mercado':             mercado_analisado,
                                                'linha_planilha':      linha_alvo,
                                                'texto_base':          msg,
                                                'msg_id':              msg_id,
                                                'horario_conferencia': time.time() + 900,
                                                'horario_envio_robo':  datetime.now().strftime("%H:%M"),
                                                'primeira_leitura':    None,
                                            })
                                            print(f" ✅ [V13] Agendado para auditoria!")

                                        except Exception as e:
                                            print(f" ❌ Erro Planilha: {e}")
                                else:
                                    print(f" ⚠️ V13 VETOU: {id_j} | Confiança: {chance_green:.2%}")
                                    jogos_descartados.add(id_j)

                        except Exception as e:
                            print(f" ❌ Erro Crítico na Predição V13: {e}")

                # -----------------------------------------------------------------
                # FASE DE AUDITORIA (V12 BLINDADA)
                # -----------------------------------------------------------------
                page_gg = next((pg for pg in context.pages if "h2hggl.com" in pg.url), None)

                if page_gg:
                    for tarefa in jogos_para_auditar[:]:
                        if "horario_conferencia" not in tarefa:
                            tarefa["horario_conferencia"] = time.time() + 900

                        if time.time() >= tarefa["horario_conferencia"]:
                            res, placar = auditar_resultado_recente(page_gg, tarefa)
                            if res:
                                leitura_atual = f"{res}-{placar}"
                                if tarefa.get("primeira_leitura") is None:
                                    tarefa["primeira_leitura"] = leitura_atual
                                    tarefa["horario_conferencia"] = time.time() + 120
                                    continue

                                if leitura_atual == tarefa["primeira_leitura"]:
                                    try:
                                        g_casa, g_fora = map(int, placar.split(':'))
                                        soma_final = g_casa + g_fora
                                        plan = conectar_planilha()
                                        aba_aud = plan.worksheet({1:"JAN", 2:"FEV", 3:"MAR", 4:"ABR", 5:"MAI", 6:"JUN", 7:"JUL", 8:"AGO", 9:"SET", 10:"OUT", 11:"NOV", 12:"DEZ"}[datetime.now().month])
                                        aba_aud.update_acell(f"U{tarefa['linha_planilha']}", soma_final)
                                        aba_aud.update_acell(f"X{tarefa['linha_planilha']}", res)

                                    except Exception as e:
                                        tarefa["horario_conferencia"] = time.time() + 60
                            else:
                                # Se o placar não saiu, espera mais 60s para não travar o loop
                                tarefa["horario_conferencia"] = time.time() + 60

                # Faxina
                if len(lockdown_jogos) > 20:
                    lockdown_jogos.clear()
                for extra_page in context.pages[:]:
                    try:
                        url_aba = extra_page.url.lower()
                        if "bet365" not in url_aba and "h2hggl" not in url_aba:
                            extra_page.close()
                    except Exception:
                        pass

                time.sleep(1) # Aumenta e randomiza o sleep principal
                gc.collect()

            except Exception as e:
                print(f"❌ Erro Crítico no Loop Principal: {e}")
                time.sleep(10)

if __name__ == "__main__":
    iniciar_monitoramento_final()
