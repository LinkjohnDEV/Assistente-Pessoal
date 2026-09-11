"""Contexto e preço na lista (opcionais) e gasto por período livre."""
import datetime as dt, os, sqlite3, sys, tempfile
AQUI=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0,AQUI)
import ferramentas as f
falhas,n=[],0
def checa(d,c,det=""):
    global n; n+=1; ok=bool(c)
    print(f"  [{'ok' if ok else 'FALHOU'}] {d}"+(f"  → {det}" if det else ""))
    if not ok: falhas.append(d)
tmp=os.path.join(tempfile.mkdtemp(prefix="t-ld-"),"casa.db"); f.BANCO=tmp
with sqlite3.connect(tmp) as c: c.executescript(open(os.path.join(AQUI,"schema.sql")).read())
hoje=dt.date.today()

print("preço é OPCIONAL")
f.lista_add("sabão", onde="mercado")
f.lista_add("detergente", onde="mercado")
f.lista_add("guarda-roupa casal", onde="casa", valor=1200)
f.lista_add("armário cozinha", onde="casa", valor=1800)
f.lista_add("chuveiro", onde="casa")
r=f.lista_ver()
checa("item sem preço entra normal", r["quantidade"]==5)
checa("total soma só quem tem preço", r["total_estimado"]==3000.0, str(r.get("total_estimado")))
checa("diz quantos entraram na soma", r["itens_com_preco"]==2 and r["itens_sem_preco"]==3,
      f"{r['itens_com_preco']} com, {r['itens_sem_preco']} sem")
checa("lista toda sem preço não inventa total",
      "total_estimado" not in f.lista_ver(onde="mercado"))

print("\nsem contexto é vazio, não a palavra 'none' (bug de 11/09/2026)")
x = f.lista_add("parafuso")
checa("item sem onde grava NULL", x["onde"] is None, repr(x["onde"]))
with sqlite3.connect(tmp) as _c:
    checa("nada no banco com onde='none'",
          _c.execute("select count(*) from lista_compras where lower(onde)='none'").fetchone()[0] == 0)
checa("'none' não aparece como contexto", "none" not in (f.lista_ver().get("contextos") or []))
f.lista_marcar_comprado("parafuso")

print("\ncontexto separa mercado de casa")
checa("mercado traz 2", f.lista_ver(onde="mercado")["quantidade"]==2)
checa("casa traz 3", f.lista_ver(onde="casa")["quantidade"]==3)
checa("total da casa é 3000", f.lista_ver(onde="casa")["total_estimado"]==3000.0)
checa("sem filtro lista os contextos", f.lista_ver()["contextos"]==["casa","mercado"],
      str(f.lista_ver()["contextos"]))
checa("item sem contexto continua aparecendo",
      f.lista_add("pilha")["ok"] and f.lista_ver()["quantidade"]==6)
checa("e não entra em nenhum contexto", f.lista_ver(onde="casa")["quantidade"]==3)

print("\ncompletar depois")
c=f.lista_add("chuveiro", valor=250)
checa("repetir item com preço COMPLETA em vez de duplicar", c["acao"]=="completado", c["acao"])
checa("não criou linha nova", f.lista_ver()["quantidade"]==6)
checa("agora entra na soma", f.lista_ver(onde="casa")["total_estimado"]==3250.0)
checa("corrigir muda o preço", f.lista_corrigir("chuveiro", valor=300)["valor"]==300.0)
checa("corrigir muda o contexto", f.lista_corrigir("pilha", onde="mercado")["onde"]=="mercado")
checa("corrigir sem dizer o quê é recusado", f.lista_corrigir("pilha")["ok"] is False)
checa("marcar comprado ainda funciona", f.lista_marcar_comprado("sabão")["ok"])

print("\ngasto por período livre")
f.banco_salvar("inter", saldo_inicial=9000)
for meses, valor in ((0,300),(1,250),(2,400)):
    d=(hoje.replace(day=1)-dt.timedelta(days=31*meses)).isoformat()
    f.gasto_registrar(valor,"mercado",banco="inter",categoria="Mercado",quando=d)
f.gasto_registrar(80,"uber",banco="inter",categoria="Transporte")
desde=(hoje-dt.timedelta(days=120)).isoformat()
g=f.gastos_periodo(desde)
merc=[c for c in g["categorias"] if c["categoria"]=="Mercado"][0]
checa("soma mercado atravessando meses", merc["gasto"]==950.0, str(merc["gasto"]))
checa("conta os lançamentos", merc["lancamentos"]==3)
checa("traz média por mês", g["media_por_mes"]>0, str(g["media_por_mes"]))
checa("filtra por categoria",
      f.gastos_periodo(desde, categoria="Transporte")["total_gasto"]==80.0)
# só o uber é de hoje; o mercado deste mês foi lançado no dia 1º
checa("período de hoje exclui o que ficou em dias anteriores",
      f.gastos_periodo(hoje.isoformat())["total_gasto"]==80.0,
      str(f.gastos_periodo(hoje.isoformat())["total_gasto"]))
checa("data inválida é recusada", f.gastos_periodo("ontem")["ok"] is False)
checa("datas trocadas se ajeitam sozinhas",
      f.gastos_periodo(hoje.isoformat(), desde)["desde"]==desde)

print(f"\n{n-len(falhas)}/{n} verificações passaram")
if falhas:
    print("FALHAS:"); [print("  -",x) for x in falhas]; sys.exit(1)
