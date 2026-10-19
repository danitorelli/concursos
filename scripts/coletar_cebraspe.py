"""Coletor da Cebraspe - versão 3.
 
Abre o site uma vez e consulta a mesma API interna que a página usa
(lista de concursos + detalhe de cada um). Salva em data/fontes/cebraspe.json.
Campos que só existem dentro do edital em PDF (taxa, escolaridade, local e data
da prova) ficam como null por enquanto.
"""
import json
import re
import time
import datetime as dt
from pathlib import Path
from playwright.sync_api import sync_playwright
 
SITE = "https://www.cebraspe.org.br/concursos/inscricoes-abertas"
API = "https://apis.cebraspe.org.br/cebraspe/"
FASES = ["inscricoes-abertas", "novos"]   # "novos" = editais recém-publicados
FONTE = "Cebraspe"
OUT = Path("data/fontes")
MAX_DETALHES = 150
 
UFS = set("AC AL AM AP BA CE DF ES GO MA MG MS MT PA PB PE PI PR RJ RN RO RR RS SC SE SP TO".split())
JS_FETCH = """async (u) => {
  try { const r = await fetch(u); if (!r.ok) return {__erro: r.status}; return await r.json(); }
  catch (e) { return {__erro: String(e)}; }
}"""
 
 
def hoje_brt():
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=3)).date()
 
 
def periodo(txt):
    datas = re.findall(r"(\d{2})/(\d{2})/(\d{4})", txt or "")
    iso = [f"{a}-{m}-{d}" for d, m, a in datas]
    return (iso[0] if iso else ""), (iso[1] if len(iso) > 1 else "")
 
 
def uf_de(texto):
    achadas = [t for t in re.findall(r"\b[A-Z]{2}\b", texto) if t in UFS]
    return achadas[-1] if achadas else "BR"
 
 
def classificar(texto):
    t = texto.lower()
    tags = []
    if re.search(r"pol[ií]cia|\bpm\b|\bpc\b|\bpf\b|\bprf\b|\bpp\b|\bppf\b|penal|penitenci|\bgcm\b|guarda", t):
        tags.append("Polícia")
    if re.search(r"bombeiro|\bcbm\b", t):
        tags.append("Bombeiro")
    if re.search(r"tecnologia da informa|\bti\b|sistemas|desenvolv|infraestrutura de t|suporte t|ci[eê]ncia de dados", t):
        tags.append("TI")
    return tags
 
 
def inteiro(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0
 
 
def montar(item, det):
    d = det or {}
    sigla = (item.get("eventoNomeAbreviado") or d.get("eventoNomeAbreviado") or "").strip()
    nome = (d.get("eventoNomeCompleto") or "").strip()
    orgao = nome if nome and nome.upper() != sigla.upper() and "_" not in nome else sigla
    cargos = [c.get("area", "").strip() for c in (d.get("eventoCargos") or []) if isinstance(c, dict)]
    inicio, fim = periodo(d.get("periodoInscricao"))
    hoje = hoje_brt().isoformat()
    if inicio and fim:
        status = "encerrado" if hoje > fim else ("aberto" if hoje >= inicio else "edital")
    else:
        status = "edital"
    editais = [
        {"descricao": (a.get("descricaoArquivo") or "").strip(),
         "data": a.get("dataArquivo") or "",
         "arquivo": a.get("nomeArquivo") or ""}
        for a in (d.get("arquivosEdital") or [])
        if "pdf" in (a.get("tipoExtensaoArquivo") or "").lower()
    ][:8]
    url = item.get("eventoURL") or d.get("eventoURL") or ""
    salario = item.get("eventoSalarioMaximo") or d.get("eventoSalarioMaximo") or 0
    return {
        "id": re.sub(r"\W+", "-", f"cebraspe-{url or sigla}").strip("-").lower(),
        "fonte": FONTE,
        "sigla": sigla,
        "orgao": orgao,
        "status": status,
        "uf": uf_de(sigla + " " + nome),
        "categorias": classificar(sigla + " " + nome + " " + " ".join(cargos)),
        "cargos": cargos,
        "vagas": inteiro(item.get("eventoTotalVagas") or d.get("eventoTotalVagas")),
        "salario_max": float(salario or 0),
        "inicio": inicio,
        "fim": fim,
        "periodo_texto": d.get("periodoInscricao") or "",
        "prova": None,
        "taxa": None,
        "escolaridade": None,
        "locais": None,
        "link": "https://www.cebraspe.org.br/concursos/" + url,
        "editais": editais,
    }
 
 
def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(SITE, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(2000)
 
        def api(caminho):
            return page.evaluate(JS_FETCH, f"{API}{caminho}?_={int(time.time() * 1000)}")
 
        itens = {}
        for fase in FASES:
            r = api(f"eventos/tipo/concursos/fase/{fase}")
            if isinstance(r, list):
                eventos = []
                for grupo in r:
                    if isinstance(grupo, dict) and isinstance(grupo.get("eventos"), list):
                        eventos += grupo["eventos"]
                    elif isinstance(grupo, dict):
                        eventos.append(grupo)
                eventos = [e for e in eventos if isinstance(e, dict) and e.get("eventoURL")]
                print(f"Fase {fase}: {len(eventos)} concursos")
                for it in eventos:
                    itens.setdefault(it["eventoURL"], it)
            else:
                print(f"Fase {fase}: sem dados ({r})")
 
        if not itens:
            print("AVISO: nada coletado. Mantendo o arquivo anterior.")
            browser.close()
            return
 
        concursos = []
        for url, it in list(itens.items())[:MAX_DETALHES]:
            det = api(f"eventos/{url}")
            if not isinstance(det, dict) or "__erro" in det:
                print(f"  {url}: detalhe indisponível ({det})")
                det = {}
            concursos.append(montar(it, det))
 
        # Diagnóstico: descobre como os links dos editais aparecem na página.
        debug_links = []
        try:
            pg = browser.new_page()
            pg.goto(concursos[0]["link"], wait_until="networkidle", timeout=60000)
            pg.wait_for_timeout(2000)
            todos = pg.eval_on_selector_all(
                "a", "els => els.map(e => ({t: (e.innerText||'').trim().slice(0,120), h: e.href}))")
            debug_links = [l for l in todos if "edital" in l["t"].lower() or l["h"].lower().endswith(".pdf")][:10]
        except Exception as e:
            debug_links = [{"erro": str(e)}]
        browser.close()
 
    agora = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    (OUT / "cebraspe.json").write_text(
        json.dumps({"atualizado": agora, "fonte": FONTE, "debug_links": debug_links,
                    "concursos": concursos}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print(f"Salvo: {len(concursos)} concursos")
 
 
if __name__ == "__main__":
    main()
