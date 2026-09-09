"""Prova desmarcar_pago, conta_desativar, esquecer_fato, o pagamento ligado ao
banco, a conta atrasada no aviso, o fechamento do mês e o arquivamento."""
import datetime as dt, json, os, sqlite3, sys, tempfile
AQUI=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0,AQUI)
import ferramentas as f, lembretes

falhas,n=[],0
def checa(d,c,det=""):
    global n; n+=1; ok=bool(c)
    print(f"  [{'ok' if ok else 'FALHOU'}] {d}"+(f"  → {det}" if det else ""))
    if not ok: falhas.append(d)
def sql(q,a=()):
    with sqlite3.connect(f.BANCO) as c:
        c.row_factory=sqlite3.Row
        return [dict(r) for r in c.execute(q,a).fetchall()]

tmp=os.path.join(tempfile.mkdtemp(prefix="t-desf-"),"casa.db"); f.BANCO=tmp
with sqlite3.connect(tmp) as c: c.executescript(open(os.path.join(AQUI,"schema.sql")).read())
hoje=dt.date.today(); mes=f.competencia_atual()
print(f"banco: {tmp}\n")

print("pagar conta tirando do banco")
f.banco_salvar("inter", saldo_inicial=5000)
f.salvar_conta("aluguel", 23, 2300)
p = f.marcar_pago("aluguel", banco="inter", quem="Johnata")
checa("pagamento registrado", len(sql("select * from pagamentos"))==1)
checa("gasto lançado junto", "gasto" in p, str(p.get("gasto") or p.get("gasto_nao_lancado")))
checa("saldo caiu 2300", f.saldo_ver("inter")["bancos"][0]["saldo"]==2700.0)
checa("gasto entrou como Contas e utilidades",
      sql("select categoria from movimentos where valor<0")[0]["categoria"]=="Contas e utilidades")
checa("sem banco, paga sem mexer no saldo",
      "gasto" not in f.marcar_pago("aluguel", competencia="2026-01"))

print("\ndesfazer pagamento")
d = f.desmarcar_pago("aluguel")
checa("some de pagamentos", len(sql("select * from pagamentos where competencia=?",(mes,)))==0)
checa("consultar_contas volta a dizer que NÃO pagou",
      f.consultar_contas("aluguel")["contas"][0]["pago"] is False)
checa("avisa que o gasto continua lá", "aviso" in d)
checa("o gasto realmente continua", f.saldo_ver("inter")["bancos"][0]["saldo"]==2700.0)
checa("dá pra pagar de novo depois de desmarcar", f.marcar_pago("aluguel")["ok"])
checa("desmarcar o que não estava pago é recusado",
      f.desmarcar_pago("aluguel", competencia="2020-01")["ok"] is False)

print("\naposentar conta")
f.salvar_conta("academia", 5, 120)
f.marcar_pago("academia", competencia="2026-08")
r = f.conta_desativar("academia")
checa("ativo virou 0", sql("select ativo from contas where nome='academia'")[0]["ativo"]==0)
checa("some de consultar_contas",
      all(c["nome"]!="academia" for c in f.consultar_contas()["contas"]))
checa("o histórico de pagamento fica", r["pagamentos_guardados"]==1)
checa("a linha da conta NÃO foi apagada", len(sql("select * from contas where nome='academia'"))==1)
checa("desativar de novo não quebra", f.conta_desativar("academia")["acao"]=="ja_estava_desativada")
checa("salvar_conta reativa", f.salvar_conta("academia",5,130)["ok"] and
      sql("select ativo from contas where nome='academia'")[0]["ativo"]==1)

print("\nesquecer fato")
f.lembrar_fato("wifi", "senha-velha-123")
e = f.esquecer_fato("wifi")
checa("apagou", len(sql("select * from fatos"))==0)
checa("devolveu o que estava lá", e["valor_que_estava"]=="senha-velha-123")
checa("esquecer o que não existe é recusado", f.esquecer_fato("foguete")["ok"] is False)
f.lembrar_fato("senha do wifi da sala", "abc")
checa("acha por palavra", f.esquecer_fato("wifi")["ok"])

print("\nconta ATRASADA volta a avisar")
f.salvar_conta("luz", (hoje-dt.timedelta(days=2)).day, 187.4)
p = lembretes.a_vencer()
luz = [c for c in p if c["nome"]=="luz"]
checa("a vencida aparece", len(luz)==1, str([c['nome'] for c in p]))
checa("faltam é negativo", luz and luz[0]["faltam"]<0, str(luz[0]["faltam"]) if luz else "")
checa("o texto diz VENCEU", "VENCEU" in lembretes.texto(luz))
checa("e usa o sino 🚨", "🚨" in lembretes.texto(luz))

print("\nfechamento do mês")
checa("em dia que não é 1º, não manda nada", lembretes.fechamento_do_mes(hoje.replace(day=15)) is None)
primeiro = hoje.replace(day=1)
f.gasto_registrar(250,"tenis",banco="inter",categoria="Vestuário",
                  quando=(primeiro-dt.timedelta(days=3)).isoformat())
t = lembretes.fechamento_do_mes(primeiro)
checa("no dia 1º manda o mês anterior", t and "Vestuário" in t, (t or "")[:40])
checa("mostra o que saiu e o que entrou", "Saiu:" in t and "Entrou:" in t)

print("\narquivar histórico")
with sqlite3.connect(tmp) as c:
    for i in range(3):
        c.execute("INSERT INTO historico (ts,quem,mensagem,resposta) VALUES"
                  " (datetime('now','localtime','-8 months'),'x','velha','r')")
    c.execute("INSERT INTO historico (ts,quem,mensagem,resposta) VALUES"
              " (datetime('now','localtime'),'x','nova','r')")
dest = tempfile.mkdtemp(prefix="arq-")
a = lembretes.arquivar_historico(destino=dest)
checa("arquivou as 3 velhas", a["arquivadas"]==3, str(a["arquivadas"]))
checa("a nova ficou", len(sql("select * from historico"))==1)
checa("o arquivo existe e é legível", os.path.exists(a["arquivo"]))
checa("e tem as 3 dentro", len(json.load(open(a["arquivo"])))==3)
checa("arquivo em chmod 600", oct(os.stat(a["arquivo"]).st_mode)[-3:]=="600")
checa("rodar de novo não arquiva nada", lembretes.arquivar_historico(destino=dest)["arquivadas"]==0)

print("\nlembretes.py carrega inteiro")
# Esta seção existe porque em 08/09/2026 um replace ganancioso apagou 4
# funções do lembretes.py e as 150 verificações passaram assim mesmo:
# nenhuma delas chamava o main(). Quebrou só na hora de rodar de verdade.
import ast, importlib
fonte = ast.parse(open(os.path.join(AQUI, "lembretes.py")).read())
definidas = {x.name for x in ast.walk(fonte) if isinstance(x, ast.FunctionDef)}
main = next(x for x in fonte.body if isinstance(x, ast.FunctionDef) and x.name == "main")
chamadas = {x.func.id for x in ast.walk(main)
            if isinstance(x, ast.Call) and isinstance(x.func, ast.Name)}
sumidas = {c for c in chamadas if c not in definidas and c not in dir(__builtins__)}
checa("toda função que o main() chama existe", not sumidas, str(sumidas))

for nome in ("a_vencer", "texto", "tarefas_proximas", "texto_tarefas",
             "limites_apertados", "texto_limites", "fechamento_do_mes",
             "arquivar_historico", "main"):
    checa(f"lembretes.{nome} existe", hasattr(lembretes, nome))

# e roda o main() de ponta a ponta, em modo seco, sem mandar nada
salvo = sys.argv[:]
sys.argv = ["lembretes.py", "--seco"]
try:
    saida = lembretes.main()
    checa("main() --seco roda sem estourar", saida == 0, f"saiu {saida}")
finally:
    sys.argv = salvo

print(f"\n{n-len(falhas)}/{n} verificações passaram")
if falhas:
    print("FALHAS:"); [print("  -",x) for x in falhas]; sys.exit(1)
