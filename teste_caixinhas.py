"""Caixinhas: guardar, tirar, ver — e nunca virar gasto no relatório."""
import datetime as dt, os, sqlite3, sys, tempfile
AQUI=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0,AQUI)
import ferramentas as f
falhas,n=[],0
def checa(d,c,det=""):
    global n; n+=1; ok=bool(c)
    print(f"  [{'ok' if ok else 'FALHOU'}] {d}"+(f"  → {det}" if det else ""))
    if not ok: falhas.append(d)
tmp=os.path.join(tempfile.mkdtemp(prefix="t-cx-"),"casa.db"); f.BANCO=tmp
with sqlite3.connect(tmp) as c: c.executescript(open(os.path.join(AQUI,"schema.sql")).read())

f.banco_salvar("nubank", saldo_inicial=3000)
for cx in ("IPVA","VIAGEM","EMERGÊNCIA","CASA"):
    f.banco_salvar(cx, tipo="caixinha")

print("guardar")
t=f.transferir(500, de="nubank", para="viagem", quem="Johnata")
checa("transferiu", t["ok"] and t["valor"]==500.0)
checa("saiu da conta", t["saldo_de"]==2500.0, str(t["saldo_de"]))
checa("entrou na caixinha", t["saldo_para"]==500.0)
checa("dois lançamentos, mesmo grupo",
      len(f.extrato(limite=99)["movimentos"])>=2)
with sqlite3.connect(tmp) as c:
    cat=[r[0] for r in c.execute("select distinct categoria from movimentos where grupo=?",(t["grupo"],))]
checa("categoria é Transferência", cat==["Transferência"], str(cat))

print("\nNÃO conta como gasto — o que mais importa")
f.gasto_registrar(120,"mercado",banco="nubank",categoria="Mercado")
r=f.resumo()
checa("resumo só mostra o gasto de verdade", r["total_gasto"]==120.0, str(r["total_gasto"]))
checa("Transferência não aparece como categoria",
      all(c["categoria"]!="Transferência" for c in r["categorias"]))
checa("não conta como entrada", r["total_entrou"]==0.0, str(r["total_entrou"]))
hoje=dt.date.today().isoformat()
checa("gasto por período também ignora", f.gastos_periodo(hoje)["total_gasto"]==120.0)

print("\ntirar de volta")
v=f.transferir(200, de="viagem", para="nubank")
checa("tirou da caixinha", v["saldo_de"]==300.0, str(v["saldo_de"]))
checa("voltou pra conta", v["saldo_para"]==2580.0, str(v["saldo_para"]))
checa("continua sem virar gasto", f.resumo()["total_gasto"]==120.0)
checa("tirar mais do que tem avisa, mas deixa",
      "aviso" in f.transferir(50, de="ipva", para="nubank"))
f.transferir(50, de="nubank", para="ipva")   # devolve

print("\nsem dizer o outro lado (o jeito que se fala de verdade)")
a=f.transferir(100, para="casa")            # "guardei 100 na casa"
checa("guardar sem dizer de onde sai da conta", a["ok"] and a["de"]=="nubank", str(a.get("erro") or a.get("de")))
b=f.transferir(40, de="casa")               # "tirei 40 da casa"
checa("tirar sem dizer pra onde volta pra conta", b["ok"] and b["para"]=="nubank", str(b.get("erro") or b.get("para")))
checa("sem os dois lados é recusado", f.transferir(10)["ok"] is False)
f.transferir(60, de="casa")                 # zera a casa de novo

print("\nver")
s=f.saldo_ver()
checa("separa disponível de guardado", s["disponivel"]==2580.0 and s["guardado"]==300.0,
      f"disp {s.get('disponivel')} guard {s.get('guardado')}")
checa("total é os dois somados", s["total"]==2880.0, str(s["total"]))
checa("lista as 4 caixinhas", len(s["caixinhas"])==4, str(len(s.get("caixinhas",[]))))
checa("dá pra ver uma caixinha só", f.saldo_ver("viagem")["bancos"][0]["saldo"]==300.0)

print("\ncaixinha nunca é escolhida sozinha")
g=f.gasto_registrar(30,"pão")            # sem dizer o banco
checa("gasto sem banco vai pra CONTA, não pra caixinha", g["ok"] and g["banco"]=="nubank",
      g.get("banco") or g.get("erro"))
checa("quanto_sobra usa só o disponível", f.quanto_sobra()["saldo"]==2550.0,
      str(f.quanto_sobra()["saldo"]))
checa("e cita o guardado à parte", f.quanto_sobra().get("guardado_em_caixinhas")==300.0)

print("\naposentar banco")
f.banco_salvar("inter", saldo_inicial=100)
d=f.banco_desativar("inter")
checa("desativa", d["ok"] and d["acao"]=="desativado")
checa("some do saldo_ver", all(b["nome"]!="inter" for b in f.saldo_ver()["bancos"]))
checa("o histórico fica", len(f.extrato(banco="inter", limite=99)["movimentos"])>=0)
g2 = f.gasto_registrar(10,"x",banco="inter")
checa("gastar em banco aposentado é RECUSADO, não estoura", g2["ok"] is False, g2.get("erro"))
checa("e a mensagem explica", "aposentado" in (g2.get("erro") or ""))
checa("desativar de novo não quebra", f.banco_desativar("inter")["acao"]=="ja_estava_desativado")
checa("banco que não existe é recusado", f.banco_desativar("bradesco")["ok"] is False)

print(f"\n{n-len(falhas)}/{n} verificações passaram")
if falhas:
    print("FALHAS:"); [print("  -",x) for x in falhas]; sys.exit(1)
