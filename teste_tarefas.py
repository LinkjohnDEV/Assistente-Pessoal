"""Prova as tarefas, sem IA. Banco descartável no /tmp."""
import datetime as dt, os, sqlite3, sys, tempfile
AQUI = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, AQUI)
import ferramentas as f

falhas, n = [], 0
def checa(d, cond, det=""):
    global n; n += 1
    ok = bool(cond)
    print(f"  [{'ok' if ok else 'FALHOU'}] {d}" + (f"  → {det}" if det else ""))
    if not ok: falhas.append(d)
def sql(q, a=()):
    with sqlite3.connect(f.BANCO) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute(q, a).fetchall()]

tmp = os.path.join(tempfile.mkdtemp(prefix="t-tarefas-"), "casa.db")
f.BANCO = tmp
with sqlite3.connect(tmp) as c: c.executescript(open(os.path.join(AQUI,"schema.sql")).read())
hoje = dt.date.today()
d3 = (hoje + dt.timedelta(days=3)).isoformat()
print(f"banco: {tmp} | hoje: {hoje}\n")

print("criar")
a = f.tarefa_add("revisar o carro", quando=d3, quem="Johnata")
l = sql("select * from tarefas")[0]
checa("grava a tarefa", l["tarefa"] == "revisar o carro")
checa("grava a data", l["quando"] == d3)
checa("nasce como não feita", l["feita"] == 0)
s = f.tarefa_add("comprar presente da vó")
checa("tarefa SEM data é aceita", s["ok"] and s["quando"] is None)
checa("data inválida é recusada",
      f.tarefa_add("x", quando="quinta")["ok"] is False)
checa("tarefa vazia é recusada", f.tarefa_add("   ")["ok"] is False)

print("\nver")
r = f.tarefas_ver()
checa("lista as duas pendentes", r["quantidade"] == 2)
checa("a com data vem primeiro", r["tarefas"][0]["tarefa"] == "revisar o carro")
checa("calcula quantos dias faltam", r["tarefas"][0]["faltam"] == 3,
      str(r["tarefas"][0]["faltam"]))
checa("sem data tem faltam = None", r["tarefas"][1]["faltam"] is None)
checa("filtro por data pega só a com prazo",
      f.tarefas_ver(ate=d3)["quantidade"] == 1)

print("\ncorrigir (nasceu junto com o criar)")
c1 = f.tarefa_corrigir("revisar carro", novo_texto="revisar o carro na oficina")
checa("acha por palavra (sem o 'o')", c1["ok"], c1.get("erro",""))
checa("trocou o texto", sql("select tarefa from tarefas where id=?", (a["id"],))[0]["tarefa"]
      == "revisar o carro na oficina")
checa("NÃO marcou como feita", sql("select feita from tarefas where id=?", (a["id"],))[0]["feita"] == 0)
checa("não criou linha nova", len(sql("select * from tarefas")) == 2)
novo = (hoje + dt.timedelta(days=10)).isoformat()
f.tarefa_corrigir("revisar o carro na oficina", novo_quando=novo)
checa("trocou só a data", sql("select quando,tarefa from tarefas where id=?", (a["id"],))[0]["quando"] == novo)
checa("corrigir sem dizer o quê é recusado", f.tarefa_corrigir("revisar")["ok"] is False)
checa("corrigir o que não existe é recusado",
      f.tarefa_corrigir("lavar o dinossauro", novo_texto="x")["ok"] is False)

print("\nmarcar feita")
ff = f.tarefa_feita("presente da vó", quem="Esposa")
checa("acha por pedaço", ff["ok"] and ff["tarefa"] == "comprar presente da vó", ff.get("erro",""))
l = sql("select * from tarefas where id=?", (s["id"],))[0]
checa("feita = 1", l["feita"] == 1)
checa("guarda quem e quando", l["feita_por"] == "Esposa" and l["feita_em"])
checa("a linha CONTINUA existindo", len(sql("select * from tarefas")) == 2)
checa("some das pendentes", f.tarefas_ver()["quantidade"] == 1)
checa("aparece com incluir_feitas", f.tarefas_ver(incluir_feitas=True)["quantidade"] == 2)
checa("marcar feita de novo é recusado", f.tarefa_feita("presente da vó")["ok"] is False)

print("\naviso diário")
import lembretes
f.tarefa_add("levar o cachorro no veterinário", quando=(hoje+dt.timedelta(days=2)).isoformat())
f.tarefa_add("trocar o pneu", quando=(hoje+dt.timedelta(days=30)).isoformat())
p = lembretes.tarefas_proximas()
checa("avisa a de 2 dias", any("cachorro" in t["tarefa"] for t in p), str([t["tarefa"] for t in p]))
checa("não avisa a de 30 dias", all("pneu" not in t["tarefa"] for t in p))
checa("não avisa a que já foi feita", all("presente" not in t["tarefa"] for t in p))
f.tarefa_add("pagar o IPVA", quando=(hoje-dt.timedelta(days=2)).isoformat())
p = lembretes.tarefas_proximas()
checa("avisa a ATRASADA", any("IPVA" in t["tarefa"] for t in p), str([t["tarefa"] for t in p]))
checa("texto marca o atraso", "atrasada" in lembretes.texto_tarefas(p).lower())

print(f"\n{n - len(falhas)}/{n} verificações passaram")
if falhas:
    print("FALHAS:"); [print("  -", x) for x in falhas]; sys.exit(1)
