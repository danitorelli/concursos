"""Coletor da Cebraspe - versão 3.
 
Abre o site uma vez e consulta a mesma API interna que a página usa
(lista de concursos + detalhe de cada um). Salva em data/fontes/cebraspe.json.
Campos que só existem dentro do edital em PDF (taxa, escolaridade, local e data
da prova) ficam como null por enquanto.
"""
import io
import json
import re
import time
import urllib.request
import urllib.parse
import datetime as dt
from pathlib import Path
from playwright.sync_api import sync_playwright
 
SITE = "https://www.cebraspe.org.br/concursos/inscricoes-abertas"
API = "https://apis.cebraspe.org.br/cebraspe/"
FASES = ["inscricoes-abertas", "novos"]   # "novos" = editais recém-publicados
FONTE = "Cebraspe"
OUT = Path("data/fontes")
MAX_DETALHES = 150
MAX_PDFS = 40                      # editais baixados por execução
CDN = "https://cdn.cebraspe.org.br/concursos/"
CACHE = Path("data/cache/cebraspe_editais.json")
UF_FIXO = {"ADEAL": "AL"}
 
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
    for chave, uf in UF_FIXO.items():
        if chave in texto:
            return uf
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
 
 
def situacao(editais):
    """Olha os editais do mais novo para o mais antigo e vê se o concurso foi suspenso/cancelado."""
    for a in editais:
        d = (a.get("descricaoArquivo") or "").lower()
        if re.search(r"(cancelamento|revoga[cç][aã]o|anula[cç][aã]o)\s+do\s+(concurso|edital|certame)", d):
            return "cancelado"
        if re.search(r"reabertura|retomada|fim da suspens|revoga[cç][aã]o da suspens", d):
            return "ativo"
        if "suspens" in d:
            return "suspenso"
    return "ativo"
 
 
def escolher_abertura(editais):
    """Escolhe o PDF do edital de abertura (de preferência o atualizado conforme retificações)."""
    cands = [a for a in editais
             if "pdf" in (a.get("tipoExtensaoArquivo") or "").lower()
             and re.search(r"(?<!re)abertura", (a.get("descricaoArquivo") or "").lower())]
    if not cands:
        return None
    atual = [a for a in cands if "atualizado" in a["descricaoArquivo"].lower()]
    return (atual or cands)[-1] if not atual else atual[0]
 
 
def limpar(t):
    return re.sub(r"\s+", " ", t.replace("\u00ad", "")).strip()
 
 
PROX = r"(?=\s\d+(?:\.\d+)+\s|\sANEXO\s|$)"
 
 
def segmentos(t, rotulo, maxlen):
    return [m.group(1).strip() for m in
            re.finditer(rotulo + r"\s*:\s*(.{1,%d}?)%s" % (maxlen, PROX), t, re.I)]
 
 
def valores(seg):
    return [float(v.replace(".", "").replace(",", ".")) for v in re.findall(r"R\$\s*([\d.]+,\d{2})", seg)]
 
 
def nivel_de(seg):
    s = seg.lower()
    n = set()
    if re.search(r"gradua[cç][aã]o|n[ií]vel superior|bacharel|licenciatura|tecn[oó]logo", s):
        n.add("Superior")
    if re.search(r"ensino m[eé]dio|n[ií]vel m[eé]dio|2[ºo°] grau|segundo grau", s):
        n.add("Médio")
    if re.search(r"curso t[eé]cnico|n[ií]vel t[eé]cnico|ensino t[eé]cnico|t[eé]cnico em", s):
        n.add("Técnico")
    return n
 
 
def extrair_edital(texto):
    """Tira taxa, remuneração, escolaridade, local e data de prova do texto do edital."""
    t = limpar(texto)
    out = {"taxa": None, "taxas": [], "taxa_texto": None, "remuneracoes": [],
           "escolaridade": [], "escolaridade_texto": None,
           "locais": [], "locais_texto": None, "prova": None, "prova_trecho": None}
 
    for seg in segmentos(t, r"\bTAXA", 400):
        v = valores(seg)
        if v:
            out["taxas"] = sorted(set(v))
            out["taxa"] = min(v)
            out["taxa_texto"] = seg[:300]
            break
    if out["taxa"] is None:
        m = re.search(r"taxa de inscri[cç][aã]o[^.]{0,80}?R\$\s*([\d.]+,\d{2})", t, re.I)
        if m:
            out["taxa"] = float(m.group(1).replace(".", "").replace(",", "."))
            out["taxas"] = [out["taxa"]]
 
    rem = []
    for seg in segmentos(t, r"REMUNERA[CÇ][AÃ]O", 300):
        rem += valores(seg)
    out["remuneracoes"] = sorted(set(rem))
 
    niveis, primeiro = set(), None
    for seg in segmentos(t, r"REQUISITOS?", 700):
        n = nivel_de(seg)
        if n:
            niveis |= n
            primeiro = primeiro or seg[:300]
    out["escolaridade"] = sorted(niveis)
    out["escolaridade_texto"] = primeiro
 
    m = re.search(r"provas?[^.]{0,600}?(?:realizad[ao]s?|aplicad[ao]s?)\s+(?:na|nas|no|nos|em)\s+"
                  r"(?:cidades?|munic[ií]pios?)\s+(?:de\s+)?([^.]{3,300}?)(?:\.\s|;)", t, re.I)
    if m:
        out["locais_texto"] = m.group(1).strip()
        out["locais"] = [x.strip() for x in re.findall(r"[A-ZÀ-Ú][A-Za-zÀ-ú' .-]*?/[A-Z]{2}", m.group(1))]
 
    marcas = list(re.finditer(r"ANEXO I\s*[–-]\s*CRONOGRAMA", t))
    base = t[marcas[-1].start():] if marcas else t[-15000:]
    m = re.search(r"aplica[cç][aã]o\s+d[ao]s?\s+provas?[^0-9]{0,160}?(\d{1,2})/(\d{1,2})/(\d{4})", base, re.I)
    if m:
        out["prova"] = f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
        out["prova_trecho"] = base[max(0, m.start()):m.end() + 40]
    return out
 
 
def ler_edital(url):
    """Baixa o PDF e devolve os dados extraídos (ou None se falhar)."""
    try:
        from pypdf import PdfReader
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (radar-concursos)"})
        dados = urllib.request.urlopen(req, timeout=90).read(30_000_000)
        leitor = PdfReader(io.BytesIO(dados))
        texto = "\n".join((pg.extract_text() or "") for pg in leitor.pages[:250])
        if len(texto) < 1000:
            return None
        return extrair_edital(texto)
    except Exception as e:
        print(f"    falha ao ler PDF: {e}")
        return None
 
 
def aplicar(c, info, ed, url):
    for k in ("taxa", "taxas", "taxa_texto", "escolaridade", "escolaridade_texto",
              "locais", "locais_texto", "prova", "prova_trecho"):
        if info.get(k) not in (None, [], ""):
            c[k] = info[k]
    rem = info.get("remuneracoes") or []
    if rem:
        c["remuneracoes"] = rem
        if not c["salario_max"]:
            c["salario_max"] = max(rem)
        c["salario_min"] = min(rem)
    if set(c.get("escolaridade") or []) & {"Médio", "Técnico"} and "Nível médio" not in c["categorias"]:
        c["categorias"].append("Nível médio")
    c["edital_url"] = url
    c["edital_descricao"] = ed.get("descricaoArquivo", "").strip()
 
 
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
    sit = situacao(d.get("arquivosEdital") or [])
    if sit != "ativo":
        status = sit
    abertura = escolher_abertura(d.get("arquivosEdital") or [])
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
        "escolaridade": [],
        "locais": [],
        "link": "https://www.cebraspe.org.br/concursos/" + url,
        "editais": editais,
        "_abertura": abertura,
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
 
    cache = {}
    if CACHE.exists():
        try:
            cache = json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    baixados = 0
    for c in concursos:
        ed = c.pop("_abertura", None)
        if not ed or c["status"] in ("encerrado", "suspenso", "cancelado"):
            continue
        slug = c["link"].rsplit("/", 1)[-1]
        chave = f"{slug}/{ed['nomeArquivo']}"
        url = CDN + f"{slug}/arquivos/" + urllib.parse.quote(ed["nomeArquivo"])
        info = cache.get(chave)
        if info is None and baixados < MAX_PDFS:
            baixados += 1
            print(f"  lendo edital de {c['sigla']}...")
            info = ler_edital(url)
            if info:
                cache[chave] = info
        if info:
            aplicar(c, info, {"descricaoArquivo": ed.get("descricaoArquivo")}, url)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
 
    agora = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    (OUT / "cebraspe.json").write_text(
        json.dumps({"atualizado": agora, "fonte": FONTE, "debug_links": debug_links,
                    "concursos": concursos}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print(f"Salvo: {len(concursos)} concursos")
 
 
if __name__ == "__main__":
    main()
