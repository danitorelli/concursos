"""Coletor da Cebraspe (inscrições abertas) - versão 2.
 
1) Abre a lista de inscrições abertas e captura os dados JSON da página.
2) Abre a página de cada concurso e guarda os dados e o texto visível dela
   dentro do próprio concursos.json (campos detalhe_bruto e detalhe_texto),
   para ajustarmos a extração de datas, cargos, taxa e locais de prova.
"""
import json
import re
import datetime as dt
from pathlib import Path
from playwright.sync_api import sync_playwright
 
URL = "https://www.cebraspe.org.br/concursos/inscricoes-abertas"
FONTE = "Cebraspe"
OUT = Path("data")
DBG = Path("debug")
MAX_DETALHES = 60
 
HINTS = {
    "titulo": ("nome", "titulo", "título", "concurso", "descricao", "descrição", "orgao", "órgão"),
    "inicio": ("inicio", "início", "abertura", "dtinicio"),
    "fim": ("fim", "termino", "término", "encerramento", "dtfim"),
    "prova": ("prova", "aplicacao", "aplicação"),
    "vagas": ("vaga",),
    "salario": ("salario", "salário"),
    "taxa": ("taxa", "valorinscricao"),
    "link": ("url", "link", "href", "slug"),
}
 
 
def walk(o):
    """Gera todas as listas de dicionários encontradas no JSON."""
    if isinstance(o, list):
        if o and all(isinstance(x, dict) for x in o):
            yield o
        for x in o:
            yield from walk(x)
    elif isinstance(o, dict):
        for v in o.values():
            yield from walk(v)
 
 
def textos_chave(o, hint):
    """Gera os textos de todas as chaves que contenham a palavra-chave."""
    if isinstance(o, dict):
        for k, v in o.items():
            if hint in k.lower() and isinstance(v, str):
                yield v
            yield from textos_chave(v, hint)
    elif isinstance(o, list):
        for x in o:
            yield from textos_chave(x, hint)
 
 
def score(lst):
    keys = " ".join(k.lower() for x in lst[:5] for k in x.keys())
    pts = sum(keys.count(h) for h in HINTS["titulo"] + HINTS["fim"] + HINTS["inicio"])
    return pts * min(len(lst), 50)
 
 
def pick(item, field):
    for k, v in item.items():
        if any(h in k.lower() for h in HINTS[field]) and v not in (None, "", [], {}):
            return v
    return None
 
 
def iso(v):
    if not isinstance(v, str):
        return ""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", v)
    if m:
        return m.group(0)
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", v)
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else ""
 
 
def num(v):
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        m = re.search(r"[\d.,]+", v)
        if m:
            try:
                return float(m.group(0).replace(".", "").replace(",", "."))
            except ValueError:
                pass
    return 0
 
 
def classificar(texto):
    t = texto.lower()
    tags = []
    if re.search(r"pol[ií]cia|\bpm\b|\bpc\b|\bpf\b|\bprf\b|\bpp\b|\bppf\b|penal|penitenci|\bgcm\b|guarda", t):
        tags.append("Polícia")
    if re.search(r"bombeiro|\bcbm\b", t):
        tags.append("Bombeiro")
    if re.search(r"tecnologia da informa|\bti\b|sistemas|desenvolv|infraestrutura de t|suporte t", t):
        tags.append("TI")
    if re.search(r"n[ií]vel m[eé]dio|t[eé]cnico|assistente|agente|soldado", t):
        tags.append("Nível médio")
    return tags
 
 
def normalizar(item):
    titulo = str(pick(item, "titulo") or "").strip()
    if not titulo:
        return None
    link = str(pick(item, "link") or "")
    if link and not link.startswith("http"):
        link = "https://www.cebraspe.org.br/concursos/" + link.lstrip("/")
    return {
        "id": re.sub(r"\W+", "-", f"cebraspe-{titulo}").strip("-").lower()[:80],
        "fonte": FONTE,
        "orgao": titulo,
        "cargo": "",
        "status": "aberto",
        "categorias": classificar(titulo),
        "inicio": iso(pick(item, "inicio")),
        "fim": iso(pick(item, "fim")),
        "prova": iso(pick(item, "prova")),
        "vagas": int(num(pick(item, "vagas"))),
        "salario": num(pick(item, "salario")),
        "taxa": num(pick(item, "taxa")),
        "link": link or URL,
        "bruto": item,
    }
 
 
def abrir(browser, url):
    """Abre a página e devolve (respostas JSON capturadas, texto visível)."""
    page = browser.new_page(viewport={"width": 1280, "height": 1800})
    cap = []
 
    def captura(resp):
        if "json" in (resp.headers.get("content-type") or ""):
            try:
                cap.append({"url": resp.url, "data": resp.json()})
            except Exception:
                pass
 
    page.on("response", captura)
    try:
        page.goto(url, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000)
        texto = page.inner_text("body")
    except Exception as e:
        texto = f"ERRO ao abrir: {e}"
    page.close()
    return cap, texto
 
 
def main():
    OUT.mkdir(exist_ok=True)
    DBG.mkdir(exist_ok=True)
 
    with sync_playwright() as p:
        browser = p.chromium.launch()
        respostas, texto = abrir(browser, URL)
        (DBG / "pagina.txt").write_text(texto, encoding="utf-8")
        (DBG / "respostas.json").write_text(
            json.dumps(respostas, ensure_ascii=False, indent=1), encoding="utf-8")
 
        listas = [l for r in respostas for l in walk(r["data"])]
        listas.sort(key=score, reverse=True)
        itens = []
        if listas and score(listas[0]) > 0:
            itens = [n for n in (normalizar(x) for x in listas[0]) if n]
 
        print(f"Respostas JSON capturadas: {len(respostas)}")
        print(f"Concursos extraídos: {len(itens)}")
        if not itens:
            print("AVISO: nada extraído. Mantendo o arquivo anterior.")
            browser.close()
            return
 
        for n in itens[:MAX_DETALHES]:
            resp, txt = abrir(browser, n["link"])
            n["detalhe_bruto"] = resp
            n["detalhe_texto"] = txt[:6000]
            cargos = " ".join(t for r in resp for t in textos_chave(r["data"], "cargo"))
            n["categorias"] = classificar(n["orgao"] + " " + cargos)
            print(f"  {n['orgao']}: {len(resp)} respostas JSON, {len(txt)} caracteres")
        browser.close()
 
    agora = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    (OUT / "concursos.json").write_text(
        json.dumps({"atualizado": agora, "fonte": FONTE, "concursos": itens},
                   ensure_ascii=False, indent=1), encoding="utf-8")
 
 
if __name__ == "__main__":
    main()
