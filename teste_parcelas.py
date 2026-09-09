"""Compra parcelada e leitura do histórico. Sem IA, banco descartável."""
import datetime as dt, json, os, sqlite3, sys, tempfile
AQUI=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0,AQUI)
import ferramentas as f

falhas,n=[],0
def checa(d,c,det=""):
    global n; n+=1; ok=bool(c)
    print(f"  [{'ok' if ok else 'FALHOU'}] {d}"+(f"  → {det}" if det else ""))
    if not ok: falhas.append(d)
def sql(q,a=()):
    with sqlite3.connect(f.BANCO) as c:
        c.row_factory=sqlite3.Row
        return [dict(r) for r in c.execute(q,a).fetchall()]

tmp=os.path.join(tempfile.mkdtemp(prefix="t-parc-"),"casa.db"); f.BANCO=tmp
with sqlite3.connect(tmp) as c: c.executescript(open(os.path.join(AQUI,"schema.sql")).read())
hoje=dt.date.today()
f.banco_salvar("inter", saldo_inicial=5000)
print(f"banco: {tmp}\n")

print("parcelamento")
p = f.compra_parcelada("geladeira", 10, valor_parcela=300, banco="inter",
                       categoria="Moradia", quem="Johnata")
linhas = sql("select * from movimentos where grupo is not null order by parcela")
checa("cria 10 lançamentos", len(linhas)==10)
checa("todos negativos", all(l["valor"]==-300.0 for l in linhas))
checa("numerados 1..10", [l["parcela"] for l in linhas]==list(range(1,11)))
checa("descrição diz a parcela", linhas[2]["descricao"]=="geladeira (3/10)", linhas[2]["descricao"])
checa("um mês entre cada uma",
      linhas[1]["quando"][:7] != linhas[0]["quando"][:7])
checa("valor total é 3000", p["valor_total"]==3000.0)

print("\nO QUE MAIS IMPORTA: o saldo hoje não cai 3000")
checa("saldo caiu só a 1ª parcela (5000-300)", f.saldo_ver("inter")["bancos"][0]["saldo"]==4700.0,
      str(f.saldo_ver("inter")["bancos"][0]["saldo"]))
checa("mas as 10 estão gravadas", len(sql("select * from movimentos where grupo is not null"))==10)
prox = (hoje.replace(day=1)+dt.timedelta(days=32)).strftime("%Y-%m")
checa("a parcela 2 está no mês que vem",
      any(l["competencia"]==prox for l in linhas), prox)
checa("o extrato do mês que vem já a mostra",
      f.extrato(competencia=prox)["quantidade"]==1)

print("\narredondamento não some com centavo")
q = f.compra_parcelada("sofá", 3, valor_total=1000, banco="inter")
tres = sql("select valor from movimentos where descricao like 'sofá%' order by parcela")
checa("3 parcelas", len(tres)==3)
checa("somam exatamente 1000", round(sum(abs(x['valor']) for x in tres),2)==1000.0,
      str([abs(x['valor']) for x in tres]))

print("\ncancelar o que ainda não venceu")
antes_de_cancelar = f.saldo_ver("inter")["bancos"][0]["saldo"]
c = f.parcelas_cancelar("geladeira")
checa("cancela as 9 futuras", c["canceladas"]==9, str(c["canceladas"]))
checa("a 1ª (já paga) continua valendo",
      sql("select estornado from movimentos where descricao='geladeira (1/10)'")[0]["estornado"]==0)
checa("cancelar futuras não mexe no saldo de hoje",
      f.saldo_ver("inter")["bancos"][0]["saldo"] == antes_de_cancelar,
      f"{f.saldo_ver('inter')['bancos'][0]['saldo']} vs {antes_de_cancelar}")
checa("NÃO levou junto as parcelas do sofá",
      all(x["estornado"]==0 for x in sql("select estornado from movimentos where descricao like 'sofá%'")))
checa("nada foi apagado", len(sql("select * from movimentos where descricao like 'geladeira%'"))==10)
checa("cancelar de novo é recusado", f.parcelas_cancelar("geladeira")["ok"] is False)
checa("cancelar o que não existe é recusado", f.parcelas_cancelar("jatinho")["ok"] is False)

print("\nvalidações")
checa("1 parcela é recusado", f.compra_parcelada("x",1,valor_parcela=10)["ok"] is False)
checa("sem valor é recusado", f.compra_parcelada("x",3)["ok"] is False)
checa("sem descrição é recusado", f.compra_parcelada("",3,valor_parcela=10)["ok"] is False)

print("\nler o histórico")
with sqlite3.connect(tmp) as c:
    c.execute("INSERT INTO historico (ts,quem,mensagem,resposta,ferramentas) VALUES"
              " (datetime('now','localtime'),'Johnata','Lauren, uber 27','ok',?)",
              (json.dumps(["gasto_registrar"]),))
    c.execute("INSERT INTO historico (ts,quem,mensagem,resposta,ferramentas) VALUES"
              " (datetime('now','localtime'),'Bia','Lauren, o que falta?','ok','[]')")
    c.execute("INSERT INTO historico (ts,quem,mensagem,resposta,ferramentas) VALUES"
              " (datetime('now','localtime','-10 days'),'Johnata','velha','ok',?)",
              (json.dumps(["lista_add"]),))
h = f.historico_ver(dias=1)
checa("traz o de hoje", h["quantidade"]==1, str(h["quantidade"]))
checa("ignora quem não gravou nada", all(r["ferramentas"] for r in h["registros"]))
checa("ferramentas vêm como lista", h["registros"][0]["ferramentas"]==["gasto_registrar"])
checa("com mais dias, acha a antiga", f.historico_ver(dias=30)["quantidade"]==2)

print(f"\n{n-len(falhas)}/{n} verificações passaram")
if falhas:
    print("FALHAS:"); [print("  -",x) for x in falhas]; sys.exit(1)
