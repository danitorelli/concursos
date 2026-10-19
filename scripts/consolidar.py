"""Junta todos os arquivos de data/fontes/*.json em data/concursos.json."""
import json
import datetime as dt
from pathlib import Path
 
todos, fontes = [], []
for f in sorted(Path("data/fontes").glob("*.json")):
    d = json.loads(f.read_text(encoding="utf-8"))
    fontes.append({"fonte": d.get("fonte"), "atualizado": d.get("atualizado"), "total": len(d.get("concursos", []))})
    todos += d.get("concursos", [])
 
todos.sort(key=lambda c: (c.get("fim") or "9999-12-31"))
agora = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
Path("data").mkdir(exist_ok=True)
Path("data/concursos.json").write_text(
    json.dumps({"atualizado": agora, "fontes": fontes, "concursos": todos}, ensure_ascii=False, indent=1),
    encoding="utf-8")
print(f"Consolidado: {len(todos)} concursos de {len(fontes)} fonte(s)")
 
