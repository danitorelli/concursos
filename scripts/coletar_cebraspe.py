"""Coletor da Cebraspe (inscrições abertas).
 
A página é montada por JavaScript, então abrimos um navegador de verdade,
capturamos as respostas JSON que ela usa e as convertemos para data/concursos.json.
Também salva material de diagnóstico em debug/ para ajustes na primeira execução.
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
 
HINTS = {
    "titulo": ("nome", "titulo", "título", "concurso", "descricao", "descrição", "orgao", "órgão"),
    "inicio": ("inicio", "início", "abertura", "dtinicio"),
    "fim": ("fim", "termino", "término", "encerramento", "dtfim"),
    "prova": ("prova", "aplicacao", "aplicação"),
    "vagas": ("vaga",),
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
    if re.search(r"pol[ií]cia|\bpm\b|\bpc\b|\bpf\b|\bprf\b|penal|penitenci|bombeiro|guarda", t):
        tags.append("Polícia")
    if re.search(r"tecnologia da informa|\bti\b|sistemas|desenvolv|infraestrutura de t|suporte t", t):
        tags.append("TI")
    if re.search(r"n[ií]vel m[eé]dio|t[eé]cnico|assistente|agente", t):
        tags.append("Nível médio")
    return tags
 
 
def normalizar(item):
    titulo = str(pick(item, "titulo") or "").strip()
    if not titulo:
        return None
    link = str(pick(item, "link") or "")
    if link and not link.startswith("http"):
        link = "https://www.cebraspe.org.br/concursos/" + link.lstrip("/")
    tags = classificar(titulo + " " + json.dumps(item, ensure_ascii=False))
    return {
        "id": re.sub(r"\W+", "-", f"cebraspe-{titulo}").strip("-").lower()[:80],
        "fonte": FONTE,
        "orgao": titulo,
        "cargo": "",
        "status": "aberto",
        "categorias": tags,
        "inicio": iso(pick(item, "inicio")),
        "fim": iso(pick(item, "fim")),
        "prova": iso(pick(item, "prova")),
        "vagas": int(num(pick(item, "vagas"))),
        "taxa": num(pick(item, "taxa")),
        "link": link or URL,
        "bruto": item,
    }
 
 
def main():
    OUT.mkdir(exist_ok=True)
    DBG.mkdir(exist_ok=True)
    respostas = []
 
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 1800})
 
        def captura(resp):
            if "json" in (resp.headers.get("content-type") or ""):
                try:
                    respostas.append({"url": resp.url, "data": resp.json()})
                except Exception:
                    pass
 
        page.on("response", captura)
        page.goto(URL, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(4000)
        (DBG / "pagina.txt").write_text(page.inner_text("body"), encoding="utf-8")
        (DBG / "pagina.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(DBG / "tela.png"), full_page=True)
        browser.close()
 
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
        print("AVISO: nada extraído. Mantendo o arquivo anterior. Veja a pasta debug/.")
        return
 
    agora = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    (OUT / "concursos.json").write_text(
        json.dumps({"atualizado": agora, "fonte": FONTE, "concursos": itens},
                   ensure_ascii=False, indent=1), encoding="utf-8")
 
 
if __name__ == "__main__":
    main()
