"""Mês sem conta: "a luz de setembro não vai ter". Sem IA, banco descartável."""
import datetime as dt, os, sqlite3, sys, tempfile
AQUI=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0,AQUI)
import ferramentas as f, lembretes
falhas,n=[],0
def checa(d,c,det=""):
    global n; n+=1; ok=bool(c)
    print(f"  [{'ok' if ok else 'FALHOU'}] {d}"+(f"  → {det}" if det else ""))
    if not ok: falhas.append(d)
tmp=os.path.join(tempfile.mkdtemp(prefix="t-pular-"),"casa.db"); f.BANCO=tmp
with sqlite3.connect(tmp) as c: c.executescript(open(os.path.join(AQUI,"schema.sql")).read())
hoje=dt.date.today(); mes=f.competencia_atual()
f.banco_salvar("inter", saldo_inicial=4000)
f.salvar_conta("luz", (hoje-dt.timedelta(days=1)).day, 0)
f.salvar_conta("água", (hoje-dt.timedelta(days=1)).day, 90)
f.salvar_conta("aluguel", 23, 2300)

print("antes de pular")
checa("luz vencida aparece no aviso", any(c["nome"]=="luz" for c in lembretes.a_vencer()))
checa("água entra no quanto sobra", any(c["nome"]=="água" for c in f.quanto_sobra()["contas_a_pagar"]))

print("\npular o mês")
r=f.conta_pular("luz", quem="Johnata")
checa("pula a luz", r["ok"] and r["acao"]=="pulado", str(r))
checa("acha 'agua' sem acento", f.conta_pular("agua")["ok"])
l=[c for c in f.consultar_contas()["contas"] if c["nome"]=="luz"][0]
checa("consultar diz pulado", l["pulado"] is True)
checa("e NÃO diz pago", l["pago"] is False)
checa("sai do aviso das 6h", not any(c["nome"] in ("luz","água") for c in lembretes.a_vencer()),
      str([c["nome"] for c in lembretes.a_vencer()]))
checa("sai do quanto sobra", not any(c["nome"] in ("luz","água") for c in f.quanto_sobra()["contas_a_pagar"]))
checa("aluguel continua no quanto sobra", any(c["nome"]=="aluguel" for c in f.quanto_sobra()["contas_a_pagar"]))
checa("não mexeu no saldo", f.saldo_ver("inter")["bancos"][0]["saldo"]==4000.0)
checa("pular de novo é recusado", f.conta_pular("luz")["ok"] is False)
checa("conta que não existe é recusada", f.conta_pular("gás")["ok"] is False)

print("\nmudou de ideia")
p=f.marcar_pago("luz", valor_pago=213.45, banco="inter")
checa("pagar um mês pulado vale o pagamento", p["ok"] and p["acao"]=="registrado", p.get("acao"))
l=[c for c in f.consultar_contas()["contas"] if c["nome"]=="luz"][0]
checa("agora está pago", l["pago"] is True and l["pulado"] is False)
checa("e o saldo caiu", f.saldo_ver("inter")["bancos"][0]["saldo"]==3786.55)
d=f.desmarcar_pago("água")
checa("desmarcar desfaz o pulo da água", d["ok"] and d["acao"]=="pulo_desfeito", d.get("acao"))
checa("a água volta pro aviso", any(c["nome"]=="água" for c in lembretes.a_vencer()))

print("\no mês seguinte não é afetado")
prox=(hoje.replace(day=1)+dt.timedelta(days=32)).strftime("%Y-%m")
with sqlite3.connect(tmp) as c:
    pulos=c.execute("select count(*) from pagamentos where competencia=? ",(prox,)).fetchone()[0]
checa("pular setembro não pula outubro", pulos==0)

print(f"\n{n-len(falhas)}/{n} verificações passaram")
if falhas:
    print("FALHAS:"); [print("  -",x) for x in falhas]; sys.exit(1)
