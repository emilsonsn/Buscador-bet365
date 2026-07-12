import os
import re
import time
import random
import requests
from DrissionPage import ChromiumPage, ChromiumOptions
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta
from dotenv import load_dotenv
import gc
import joblib
import pandas as pd
from urllib.parse import urlparse
from PIL import Image
import pytesseract
import numpy as np
import cv2 # OpenCV

# Jogadores com aproveitamento abaixo de 55.1% na base de 17k
BLACKLIST_V12 = []

# =================================================================
# 0. FUNÇÕES DE CAPTURA DE LINK (websocket.py integrado)
# =================================================================
EV_RE = re.compile(r"EV\d{6,}C\d+", re.IGNORECASE)
GG_RE = re.compile(r"GG\s*League", re.IGNORECASE)

def normalize_ev(text):
    if not text:
        return None
    m = EV_RE.search(text)
    return m.group(0).upper() if m else None

def origin_of(url):
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"

def ev_url(origin, ev):
    return f"{origin}/#/IP/{ev}"

def detect_frame_with_gg(page):
    try:
        # Correção do erro: DrissionPage usa get_frames()
        for fr in page.get_frames():
            if "GG League" in fr.html:
                return fr
    except:
        pass
    return page

def list_gg_fixtures(frame):
    out = []
    competitions = frame.eles(".ovm-Competition")
    for comp in competitions:
        try:
            header = comp.ele(".ovm-CompetitionHeader_NameText")
            header_text = (header.text or "").strip() if header else ""
        except Exception:
            continue
        if not GG_RE.search(header_text):
            continue
        fixtures = comp.eles(".ovm-Fixture")
        for fx in fixtures:
            try:
                teams = fx.eles(".ovm-FixtureDetailsTwoWay_TeamName")
                if len(teams) < 2:
                    continue
                t1 = (teams[0].text or "").strip()
                t2 = (teams[1].text or "").strip()
                if not t1 or not t2:
                    continue
                period = ""
                try:
                    period_el = fx.ele(".ovm-FixtureFooter_Period")
                    period = (period_el.text or "").strip() if period_el else ""
                except Exception:
                    pass
                sig = f"{t1.lower()}__{t2.lower()}__{period.lower()}"
                out.append({"sig": sig, "team1": t1, "team2": t2, "period": period})
            except Exception:
                continue
    seen = set()
    dedup = []
    for x in out:
        if x["sig"] in seen:
            continue
        seen.add(x["sig"])
        dedup.append(x)
    return dedup

def refind_fixture(frame, item):
    t1 = item["team1"]
    t2 = item["team2"]

    comp = None
    # Usando .eles para encontrar todas as competições e depois filtrar pelo texto
    for c in frame.eles(".ovm-Competition"):
        if "GG League" in c.text:
            comp = c
            break

    if comp:
        for fx in comp.eles(".ovm-Fixture"):
            if t1 in fx.text and t2 in fx.text:
                return fx
    return None

def bring_fixture_into_view(frame, item, max_steps=10):
    for _ in range(max_steps):
        fx = refind_fixture(frame, item)
        if fx and fx.tag:
            try:
                fx.scroll_to_center()
            except Exception:
                pass
            frame.wait(0.15)
            try:
                if fx.is_displayed:
                    return fx
            except Exception:
                pass
        try:
            frame.scroll.down(500)
        except Exception:
            pass
        frame.wait(0.18)
    return None

def click_fixture_robusto(frame, item):
    fx = bring_fixture_into_view(frame, item, max_steps=12)
    if not fx:
        return False
    try:
        # 1. Tenta clicar no card inteiro via JavaScript (O mais garantido na Bet365)
        fx.click(by_js=True)
        return True
    except Exception:
        try:
            # 2. Fallback: tenta clicar no nome do time via JS
            team_name_el = fx.ele(".ovm-FixtureDetailsTwoWay_TeamName")
            if team_name_el:
                team_name_el.click(by_js=True)
                return True
        except Exception:
            pass

    return False

def ev_from_page_and_frame(page, frame):
    candidates = set()
    for src in [
        page.url,
        page.run_js("return location.href"),
        page.run_js("return location.hash"),
    ]:
        ev = normalize_ev(src)
        if ev:
            candidates.add(ev)
    try:
        for src in [
            frame.url,
            frame.run_js("return location.href"),
            frame.run_js("return location.hash"),
        ]:
            ev = normalize_ev(src)
            if ev:
                candidates.add(ev)
    except Exception:
        pass
    return candidates

def extract_evs_from_html(page, frame):
    out = set()
    for get_html in [
        lambda: page.html,
        lambda: frame.html,
    ]:
        try:
            html = get_html() or ""
            for m in EV_RE.finditer(html):
                out.add(m.group(0).upper())
        except Exception:
            pass
    return out


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
        print(f"❌ ERRO: {e}")
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
    js_code = """
    function extrair() {
        let jogos = [];
        let competicoes = document.querySelectorAll('.ovm-Competition');

        for (let comp of competicoes) {
            if (comp.innerText.includes('GG League')) {
                let cards = comp.querySelectorAll('.ovm-Fixture');
                for (let card of cards) {
                    try {
                        let times = card.querySelectorAll('.ovm-FixtureDetailsTwoWay_TeamName');
                        if (times.length < 2) continue;

                        let casa = times[0].innerText ? times[0].innerText.trim() : "";
                        let fora = times[1].innerText ? times[1].innerText.trim() : "";

                        if (!casa || !fora) continue;

                        let placares = card.querySelectorAll('.ovm-ScorePill');
                        let s_casa = placares.length > 0 ? parseInt(placares[0].innerText) || 0 : 0;
                        let s_fora = placares.length > 1 ? parseInt(placares[1].innerText) || 0 : 0;

                        let p_el = card.querySelector('.ovm-PeriodInfo');
                        let t_el = card.querySelector('.ovm-InPlayTimer');

                        let periodo = p_el ? p_el.innerText.trim() : "";
                        let tempo = t_el ? t_el.innerText.trim() : "00:00";

                        if (periodo.includes('Intervalo') || periodo.includes('Half')) periodo = 'Intervalo';
                        else if (periodo.includes('3º') || periodo.includes('Q3')) periodo = 'Q3';
                        else if (periodo.includes('2º') || periodo.includes('Q2')) periodo = 'Q2';
                        else if (periodo.includes('1º') || periodo.includes('Q1')) periodo = 'Q1';

                        let linha_o = null, odd_o = null, linha_u = null, odd_u = null;
                        let mercados = card.querySelectorAll('.ovm-Market');

                        if (mercados.length >= 2) {
                            let participantes = mercados[1].querySelectorAll('.ovm-ParticipantHandicap');
                            if (participantes.length >= 2) {
                                let h_o = participantes[0].querySelector('.ovm-ParticipantHandicap_Handicap');
                                let o_o = participantes[0].querySelector('.ovm-ParticipantHandicap_Odds');
                                if (h_o && o_o) {
                                    let match_o = h_o.innerText.replace(',', '.').match(/[\\d\\.]+/);
                                    if (match_o) linha_o = parseFloat(match_o[0]);
                                    odd_o = parseFloat(o_o.innerText.replace(',', '.'));
                                }

                                let h_u = participantes[1].querySelector('.ovm-ParticipantHandicap_Handicap');
                                let o_u = participantes[1].querySelector('.ovm-ParticipantHandicap_Odds');
                                if (h_u && o_u) {
                                    let match_u = h_u.innerText.replace(',', '.').match(/[\\d\\.]+/);
                                    if (match_u) linha_u = parseFloat(match_u[0]);
                                    odd_u = parseFloat(o_u.innerText.replace(',', '.'));
                                }
                            }
                        }

                        jogos.push({
                            "id": casa + " vs " + fora,
                            "casa": casa, "fora": fora,
                            "periodo": periodo, "tempo": tempo,
                            "score_casa": s_casa, "score_fora": s_fora,
                            "linha_over": linha_o, "odd_over": odd_o,
                            "linha_under": linha_u, "odd_under": odd_u
                        });
                    } catch(e) {}
                }
            }
        }
        return jogos;
    }
    return extrair();
    """

    try:
        jogos = page_bet.run_js(js_code)
        if not jogos:
            for frame in page_bet.get_frames():
                try:
                    jogos_frame = frame.run_js(js_code)
                    if jogos_frame:
                        jogos = jogos_frame
                        break
                except:
                    continue

        # BLINDAGEM CONTRA O ERRO 'NoneType'
        jogos_validos = []
        if jogos:
            for j in jogos:
                if j and isinstance(j, dict):
                    casa = j.get('casa')
                    fora = j.get('fora')
                    # Só aceita se o nome existir e for uma string válida
                    if casa and fora and isinstance(casa, str) and isinstance(fora, str):
                        jogos_validos.append(j)

        return jogos_validos

    except Exception as e:
        print(f"DEBUG: Erro na injeção JS: {e}")
        return []

# =================================================================
# 4. EXTRAÇÃO DE DADOS (TIPMANAGER)
# =================================================================
def acessar_confronto_tipmanager(browser_page, nome_casa_bet, nome_fora_bet, tentativas=2):
    def extrair_player(nome):
        # 1. Trava de segurança: se vier vazio, retorna vazio sem dar erro
        if not nome: 
            return ""

        nome_str = str(nome)

        # 2. Se o nome tiver parênteses ex: "CHA Hornets (OREZ)"
        if "(" in nome_str and ")" in nome_str:
            # Corta no '(' e depois corta no ')' para pegar só o que está no meio
            jogador = nome_str.split("(")[1].split(")")[0]
            return jogador.strip().title()

        # 3. Se não tiver parênteses, devolve o texto normal
        return nome_str.strip().title()

    p1, p2 = extrair_player(nome_casa_bet), extrair_player(nome_fora_bet)
    print(f"   🌐 TipManager: Buscando {p1} vs {p2}...")

    for tentativa in range(1, tentativas + 1):
        page_tip = browser_page.new_tab()
        try:
            page_tip.get("https://tipmanager.net/pt/today", timeout=60)

            # 1. Clicar no gatilho de busca
            gatilho = page_tip.ele("xpath://span[text()='Buscar...']", timeout=10)
            if gatilho:
                gatilho.click()
            else:
                page_tip.close()
                continue

            # 2. Digitar no campo de busca
            campo_input = page_tip.ele('@placeholder=Busque jogadores, esportes ou ligas', timeout=10)
            if not campo_input:
                page_tip.close()
                continue

            # Digita com "vs" no meio para forçar o card do jogo a ser o primeiro
            campo_input.input(f"{p1} vs {p2}")
            page_tip.wait(1.5) # Tempo para o site processar a busca

            # 3. Buscar os cards de resultado
            cards = page_tip.eles("css:div.cursor-pointer")

            card_alvo = None
            for card in cards:
                texto_card = card.text.lower()
                # Valida se o card tem os dois jogadores E a palavra "vs"
                if p1.lower() in texto_card and p2.lower() in texto_card and "vs" in texto_card:
                    card_alvo = card
                    break

            if not card_alvo:
                print(f"   ⚠️ Card não encontrado para {p1} vs {p2}.")
                page_tip.close()
                continue

            print("   🎯 Card do jogo encontrado! Clicando...")

            # 4. Clica no card (com fallback para JS caso o clique normal falhe)
            try:
                card_alvo.click()
            except:
                card_alvo.click(by_js=True)

            # 5. Apenas aguarda o carregamento e continua (Sem validação de URL)
            page_tip.wait(3)
            print("   ✅ Clique realizado! Repassando página para leitura de dados...")
            return page_tip

        except Exception as e:
            print(f"   ❌ Erro ao acessar TipManager na tentativa {tentativa}: {e}")
            page_tip.close()

    return None

def extrair_e_analisar(page_tip):
    linhas_over_vip, linhas_under_vip = {}, {}
    try:
        # 1. Ajuste de tela
        page_tip.wait(1)

        # 2. Extração do H2H
        try:
            h2h_element = page_tip.ele("css:p.font-exo.italic.font-black", timeout=5)
            if h2h_element:
                numero_cru = (h2h_element.text or "").strip()
                if numero_cru.isdigit() and int(numero_cru) < 1:
                    return {"status": "DESCARTADO_H2H"}
                h2h_count = f"{numero_cru} Partidas" if numero_cru.isdigit() else numero_cru
            else:
                h2h_count = "N/A"
        except Exception:
            h2h_count = "N/A"

        media_q4_scanner, media_l10_total, media_hist_total = 0.0, 0.0, 0.0

        # 3. Extração das Médias (Q4, L10, Histórica) - CORRIGIDO
        try:
            # Q4 Scanner
            # Busca o div que contém "4Q" e depois busca os valores de pontuação dentro do mesmo container
            q4_container = page_tip.ele("xpath://div[./div[contains(text(), '4Q')]]", timeout=3)
            if q4_container:
                # Pega os divs com os valores numéricos (12.40 e 12.80)
                scores = q4_container.eles("css:div.font-bold.font-exo")
                if len(scores) >= 2:
                    mc = float(scores[0].text.strip().replace(',', '.') or 0)
                    mf = float(scores[1].text.strip().replace(',', '.') or 0)
                    media_q4_scanner = mc + mf
        except Exception as e:
            print(f"   ⚠️ Erro ao extrair Média Q4: {e}")
            pass

        try:
            # Média 10 Últimas
            # Busca a linha que contém "10 últimas" e depois o span com o total (text-amber-500)
            l10_row = page_tip.ele("xpath://div[./div[contains(text(), '10 últimas')]]", timeout=3)
            if l10_row:
                # O valor total está no 4º div da linha, dentro de um span com text-amber-500
                total_span = l10_row.ele("css:div:nth-child(4) > span.text-amber-500", timeout=1)
                if total_span:
                    media_l10_total = float(total_span.text.strip().replace(',', '.') or 0)
        except Exception as e:
            print(f"   ⚠️ Erro ao extrair Média 10 Últimas: {e}")
            pass

        try:
            # Média Histórica (Todas as Partidas)
            # Busca a linha que contém "Todas as Partidas" e depois o span com o total (text-amber-500)
            hist_row = page_tip.ele("xpath://div[./div[contains(text(), 'Todas as Partidas')]]", timeout=3)
            if hist_row:
                # O valor total está no 4º div da linha, dentro de um span com text-amber-500
                total_span = hist_row.ele("css:div:nth-child(4) > span.text-amber-500", timeout=1)
                if total_span:
                    media_hist_total = float(total_span.text.strip().replace(',', '.') or 0)
        except Exception as e:
            print(f"   ⚠️ Erro ao extrair Média Histórica: {e}")
            pass

        # 4. Extração das Tabelas VIP (Over/Under) - CORRIGIDO
        try:
            # Procura o quadro principal de Over/Under pelo texto do título
            quadro_partida_title = page_tip.ele("xpath://h3[text()='Over/Under (Partida)']", timeout=5)

            if quadro_partida_title:
                # O container que contém as linhas de dados é o pai do título ou um irmão próximo.
                # Vamos tentar encontrar o div que contém todas as linhas de handicaps.
                # Baseado no HTML fornecido, o container é um div com 'dir="ltr"' e 'relative overflow-hidden'
                # e que contém as linhas de dados.
                # Podemos subir a árvore DOM a partir do título ou buscar um elemento irmão.
                # A forma mais robusta é buscar um ancestral comum que contenha as linhas.
                # No seu HTML, o div que contém as linhas é um irmão do h3, dentro de um div maior.
                # Vamos tentar encontrar o div que tem 'dir="ltr"' e contém as linhas.

                # Primeiro, vamos para o pai do h3 (que é um div)
                parent_of_h3 = quadro_partida_title.parent()
                if parent_of_h3:
                    # Agora, dentro desse pai, procuramos o div que contém as linhas de dados.
                    # O seletor CSS `div[dir='ltr'][class*='overflow-hidden']` é mais robusto.
                    container_scroll_area = parent_of_h3.ele("css:div[dir='ltr'][class*='overflow-hidden']", timeout=5)

                    if container_scroll_area:
                        # As linhas de dados são divs com a classe específica
                        linhas = container_scroll_area.eles("css:div.h-14.px-4.py-3.border-b.border-border.flex.items-center.gap-8")

                        for linha_element in linhas:
                            try:
                                # O valor do handicap (ex: 111.5)
                                linha_val_ele = linha_element.ele("css:div > div.text-sm.font-semibold.font-exo")
                                # A porcentagem (ex: 83.33%)
                                pct_val_ele = linha_element.ele("css:div > div.text-xs.text-muted-foreground.text-nowrap")

                                if linha_val_ele and pct_val_ele:
                                    linha_val_str = linha_val_ele.text.strip().replace(',', '.')
                                    pct_val_str = pct_val_ele.text.strip().replace('%', '').replace(',', '.')

                                    linha_val = float(linha_val_str)
                                    pct_val = float(pct_val_str)

                                    # Como a tabela do TipManager mistura, salvamos em ambas e o bot decide depois
                                    linhas_over_vip[linha_val] = pct_val
                                    linhas_under_vip[linha_val] = pct_val
                            except Exception as e:
                                print(f"   ⚠️ Erro ao parsear linha VIP: {e}")
                                continue
        except Exception as e:
            print(f"   ⚠️ Erro ao extrair Tabelas VIP: {e}")
            pass

        # 5. Cálculos Finais
        soma_ponderada = (media_l10_total * 0.77) + (media_hist_total * 0.23)
        media_estimada = soma_ponderada / 4

        if media_q4_scanner > 0:
            media_final = (media_estimada * 0.89) + (media_q4_scanner * 0.11)
        else:
            media_final = media_estimada

        # --- PRINTS PARA DEBUG DO TIPMANAGER ---
        print(f"   📊 [TipManager] H2H: {h2h_count}")
        print(f"   📊 [TipManager] Média Q4 Scanner: {media_q4_scanner:.2f}")
        print(f"   📊 [TipManager] Média L10 Total: {media_l10_total:.2f}")
        print(f"   📊 [TipManager] Média Histórica Total: {media_hist_total:.2f}")
        print(f"   📊 [TipManager] Média Estimada: {media_estimada:.2f}")
        print(f"   📊 [TipManager] Média Final: {media_final:.2f}")
        print(f"   📊 [TipManager] Linhas Over VIP: {linhas_over_vip}")
        print(f"   📊 [TipManager] Linhas Under VIP: {linhas_under_vip}")
        # ---------------------------------------

        # 6. Retorno Estruturado
        return {
            "status": "SUCESSO",
            "h2h": h2h_count,
            "media_q4_scanner": media_q4_scanner,
            "media_l10_total": media_l10_total,
            "media_hist_total": media_hist_total,
            "media_final": media_final,
            "media_estimada": media_estimada,
            "over_vip": linhas_over_vip,
            "under_vip": linhas_under_vip
        }

    except Exception as e:
        print(f"   ❌ Erro geral na extração do TipManager: {e}")
        return {"status": "ERRO"}

# =================================================================
# 5. AUDITORIA DE PLACAR
# =================================================================
fila_espera_auditoria = []

# ... (código anterior da função auditar_resultado_recente) ...

def auditar_resultado_recente(page_gg, dados_tarefa):
    try:
        id_completo = dados_tarefa.get('id')
        partes = id_completo.split(" vs ")

        def limpar_nome(texto):
            if not texto:
                return ""
            texto_str = str(texto)
            if "(" in texto_str and ")" in texto_str:
                jogador = texto_str.split("(")[1].split(")")[0]
                return jogador.strip().upper()
            return texto_str.strip().upper()

        j_casa = limpar_nome(partes[0])
        j_fora = limpar_nome(partes[1])

        # 1. LOCALIZAÇÃO DOS CARDS - CORRIGIDO
        # Primeiro, pegamos todos os cards sem filtro de texto direto no seletor
        all_cards = page_gg.eles("css:a.match-list__item")

        # Depois, filtramos em Python, verificando o texto de cada card
        filtered_cards = []
        for card in all_cards:
            try:
                card_text = card.text.upper() # Pega o texto do card e converte para maiúsculas para comparação
                if j_casa in card_text and j_fora in card_text:
                    filtered_cards.append(card)
            except Exception:
                # Ignora cards que não têm texto ou causam erro ao acessar .text
                continue

        if not filtered_cards:
            return None, None

        # ... (restante do código da função auditar_resultado_recente) ...

        # 2. FILTRO POR JANELA DE TEMPO (MÁX 30 MIN)
        card_valido = None
        hora_tip = datetime.strptime(dados_tarefa['horario_envio_robo'], "%H:%M")

        for card_temp in filtered_cards:
            # No seu HTML, o horário fica na classe 'match-list__year'
            horario_site_el = card_temp.ele(".match-list__year")
            horario_site_str = horario_site_el.text.strip() if horario_site_el else ""

            try:
                hora_site = datetime.strptime(horario_site_str, "%H:%M")

                # Cálculo da diferença (em minutos)
                diff = abs((hora_site - hora_tip).total_seconds() / 60)

                # Se a diferença for maior que 30 min, é outro jogo (outra rodada)
                if diff <= 30:
                    card_valido = card_temp
                    break
            except:
                continue

        if not card_valido:
            print(f" ⏳ Jogo {id_completo} encontrado, mas fora da janela de 30min. Ignorando.")
            return None, None

        # 3. VERIFICAÇÃO DE STATUS (Se já encerrou)
        status_texto = card_valido.text.lower() # DrissionPage .text
        if "live" in status_texto or "suspended" in status_texto:
            return None, None

        # 4. CAPTURA DO PLACAR
        scores_elements = [el.text for el in card_valido.eles(".widget__match-score")] # DrissionPage .eles() e .text
        if len(scores_elements) < 2:
            return None, None

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
        except Exception as e:
            print(f"   ❌ Erro ao atualizar planilha na auditoria: {e}")

        return resultado, placar_str

    except Exception as e:
        print(f"❌ Erro Auditoria: {e}")
        return None, None

# =================================================================
# 6. MOTOR PRINCIPAL (V13)
# =================================================================
# ... (imports e funções anteriores) ...

# Adicione esta nova variável global ou ajuste conforme sua necessidade
# Dicionário para armazenar o número de tentativas de acesso ao TipManager por jogo
# { "ID_JOGO": { "tentativas_tip": N, "timestamp_ultima_tentativa": T } }
tentativas_tipmanager_por_jogo = {}
MAX_TENTATIVAS_TIPMANAGER = 3 # Defina o limite de tentativas aqui

# ... (restante do código) ...

def iniciar_monitoramento_final():
    print("🧠 Carregando o Cérebro do V12 Sniper...")
    if not CEREBRO_V12:
        print("❌ Falha crítica: Cérebro V12 não carregado.")
        return

    co = ChromiumOptions()
    co.set_paths(browser_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    co.set_local_port(9222) # Conecta a uma instância do Chrome já aberta na porta 9222

    browser = None
    page_bet = None
    page_gg = None

    try:
        browser = ChromiumPage(co) # Inicia o navegador com DrissionPage

        # Procura por abas existentes
        for tab_id in browser.get_tabs():
            try:
                tab = browser.get_tab(tab_id)
                if tab.title and "bet365" in tab.title.lower():
                    page_bet = tab
                if tab.url and "h2hggl.com" in tab.url.lower():
                    page_gg = tab
            except Exception:
                pass

        if not page_bet:
            print("❌ Erro: Aba da Bet365 não encontrada. Abrindo nova aba.")
            page_bet = browser.new_tab()
            page_bet.get("https://www.bet365.bet.br/#/IP/B18", timeout=60)
            page_bet.wait(3)
            print("✅ Aba da Bet365 aberta!")

        if not page_gg:
            print("🌐 Abrindo página H2HGGL para auditoria...")
            page_gg = browser.new_tab()
            page_gg.get("https://h2hggl.com/", timeout=60)
            page_gg.wait(3)
            print("✅ Página de Auditoria (H2HGGL) aberta!")

        memoria_pre_live      = {}
        jogos_para_auditar    = []
        jogos_descartados     = set()
        lockdown_jogos        = {}
        ultimo_status_lockdown = False
        ultimo_qtd_mochila    = -1
        ultimo_qtd_auditoria  = -1

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

                jogos_na_tela = extrair_todos_jogos_bet365(page_bet)

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

                        page_tip = acessar_confronto_tipmanager(browser, jogo['casa'], jogo['fora'])

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
                    time.sleep(1.5)

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
                # DrissionPage: verifica se a página ainda está ativa
                gg_viva = False
                if page_gg:
                    try:
                        _ = page_gg.url # Tenta acessar uma propriedade para verificar se a página está viva
                        gg_viva = True
                    except Exception:
                        gg_viva = False

                if not gg_viva:
                    print("🌐 Página H2HGGL não encontrada ou fechada. Tentando reabrir...")
                    page_gg = browser.new_tab()
                    page_gg.get("https://h2hggl.com/", timeout=60)
                    page_gg.wait(3)
                    print("✅ Página de Auditoria (H2HGGL) reaberta!")

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
                # DrissionPage: itera sobre as abas abertas no navegador
                for tab_id in browser.get_tabs():
                    try:
                        extra_page = browser.get_tab(tab_id)
                        url_aba = extra_page.url.lower() if extra_page.url else ""
                        if "bet365" not in url_aba and "h2hggl" not in url_aba:
                            extra_page.close()
                    except Exception:
                        pass

                time.sleep(2)
                gc.collect()

            except Exception as e:
                print(f"❌ Erro Crítico no Loop Principal: {e}")
                time.sleep(10)
    finally:
        if browser:
            browser.quit() # Fecha o navegador DrissionPage

if __name__ == "__main__":
    iniciar_monitoramento_final()