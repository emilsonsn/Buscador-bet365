import re
from src.logging_service import get_logger

LOGGER = get_logger()


class TipManagerClient:
    def __init__(self, attempts=2):
        self.attempts = attempts

    def search(self, browser_context, home_name, away_name):
        return find_tipmanager_match(browser_context, home_name, away_name, self.attempts)

    def analyze(self, page_tip):
        return extract_and_analyze(page_tip)


def find_tipmanager_match(browser_context, home_name, away_name, attempts=2):
    def extract_player(name):
        match = re.search(r'\((.*?)\)', name)
        return match.group(1).strip().title() if match else name.strip().title()

    p1, p2 = extract_player(home_name), extract_player(away_name)
    print(f"   🌐 TipManager: Buscando {p1} vs {p2}...")
    for _ in range(attempts):
        page_tip = browser_context.new_page()
        try:
            page_tip.goto("https://tipmanager.net/pt/today", timeout=60000)
            page_tip.locator("span:has-text('Buscar...')").first.wait_for(state="visible", timeout=10000)
            page_tip.locator("span:has-text('Buscar...')").first.click()
            campo = page_tip.get_by_placeholder("Busque jogadores, esportes ou ligas")
            campo.wait_for(state="visible", timeout=10000)
            campo.type(f"{p1} {p2}", delay=0)
            try:
                page_tip.wait_for_selector("div.cursor-pointer, span:has-text('vs')", timeout=4000)
                page_tip.wait_for_timeout(1000)
            except Exception:
                LOGGER.warning("Resultados do TipManager não apareceram no tempo esperado.")
                page_tip.wait_for_timeout(1500)
            card = None
            for scroll_count in range(2):
                for item in page_tip.locator("div.cursor-pointer, a[href*='game'], button").all():
                    try:
                        texto = item.inner_text().replace("\n", " ").strip().lower()
                        if "vs" in texto and p1.lower() in texto and p2.lower() in texto and ("basket" in texto or "h2h gg" in texto):
                            card = item
                            break
                    except Exception:
                        LOGGER.debug("Não foi possível ler um card do TipManager.", exc_info=True)
                        continue
                if card:
                    break
                if scroll_count == 0:
                    page_tip.locator("input[cmdk-input]").press("PageDown")
                    page_tip.wait_for_timeout(800)
            if not card:
                page_tip.close()
                continue
            try:
                card.click(timeout=1000)
            except Exception:
                LOGGER.debug("Clique normal no TipManager falhou; usando fallback.", exc_info=True)
                card.evaluate("el => el.click()")
            try:
                page_tip.wait_for_selector("p.font-exo.italic.font-black", timeout=15000)
                page_tip.wait_for_timeout(3000)
                return page_tip
            except Exception:
                LOGGER.warning("Página de análise do TipManager não carregou para %s vs %s.", p1, p2)
                page_tip.close()
        except Exception:
            LOGGER.exception("Falha ao consultar TipManager para %s vs %s.", p1, p2)
            page_tip.close()
    return None


def extract_and_analyze(page_tip):
    linhas_over_vip, linhas_under_vip = {}, {}
    try:
        page_tip.set_viewport_size({"width": 1920, "height": 1080})
        page_tip.evaluate("document.body.style.zoom = '0.8'")
        page_tip.wait_for_timeout(1000)
        try:
            h2h_element = page_tip.locator("p.font-exo.italic.font-black").first
            h2h_element.wait_for(state="visible", timeout=6000)
            numero_cru = (h2h_element.inner_text() or "").strip()
            if numero_cru.isdigit() and int(numero_cru) < 1:
                return {"status": "DESCARTADO_H2H"}
            h2h_count = f"{numero_cru} Partidas" if numero_cru.isdigit() else numero_cru
        except Exception:
            h2h_count = "N/A"
        media_q4_scanner = media_l10_total = media_hist_total = 0.0
        try:
            for elemento in page_tip.locator("div").filter(has_text=re.compile(r"^\s*4Q\s*$")).all():
                if elemento.is_visible():
                    mc = float((elemento.locator("xpath=preceding-sibling::div[1]").inner_text() or "0").strip())
                    mf = float((elemento.locator("xpath=following-sibling::div[1]").inner_text() or "0").strip())
                    if mc > 0 or mf > 0:
                        media_q4_scanner = mc + mf
                        break
        except Exception:
            LOGGER.debug("Não foi possível obter média do quarto período.", exc_info=True)
        try:
            l10 = page_tip.locator("div").filter(has_text=re.compile(r"^10 últimas$")).last
            media_l10_total = float((l10.locator("xpath=..").locator("div").last.locator("span").first.inner_text() or "0").strip())
            geral = page_tip.locator("div").filter(has_text=re.compile(r"^Todas as Partidas$")).last
            media_hist_total = float((geral.locator("xpath=..").locator("div").last.locator("span").first.inner_text() or "0").strip())
        except Exception:
            LOGGER.debug("Não foi possível obter médias históricas.", exc_info=True)
        media_estimada = ((media_l10_total * .77) + (media_hist_total * .23)) / 4
        media_final = ((media_estimada * .89) + (media_q4_scanner * .11)) if media_q4_scanner > 0 else media_estimada
        quadro = page_tip.locator("div.rounded-xl").filter(has=page_tip.locator("h3", has_text="Over/Under (Partida)")).first
        if quadro.count() > 0:
            for linha in quadro.locator("div.h-14.border-b").all():
                dados = linha.inner_text().split("\n")
                if len(dados) >= 2:
                    try:
                        linhas_over_vip[float(dados[0].replace(',', '.'))] = float(dados[1].replace('%', '').strip().replace(',', '.'))
                    except Exception:
                        LOGGER.debug("Linha VIP inválida no TipManager.", exc_info=True)
        return {"status":"SUCESSO", "h2h":h2h_count, "media_q4_scanner":media_q4_scanner, "media_l10_total":media_l10_total, "media_hist_total":media_hist_total, "media_estimada":media_estimada, "media_final":media_final, "over_vip":linhas_over_vip, "under_vip":linhas_under_vip}
    except Exception:
        LOGGER.exception("Falha ao analisar dados do TipManager.")
        return {"status": "ERRO"}
