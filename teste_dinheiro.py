"""Prova as ferramentas de dinheiro e o lista_corrigir, sem IA nenhuma.

Cada verificação lê o BANCO por SQL. Roda em banco descartável no /tmp.
"""
import os, sqlite3, sys, tempfile
AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import ferramentas

falhas, n = [], 0
def checa(d, cond, det=""):
    global n; n += 1
    ok = bool(cond)
    print(f"  [{'ok' if ok else 'FALHOU'}] {d}" + (f"  → {det}" if det else ""))
    if not ok: falhas.append(d)

def sql(q, a=()):
    with sqlite3.connect(ferramentas.BANCO) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute(q, a).fetchall()]

banco = os.path.join(tempfile.mkdtemp(prefix="teste-dinheiro-"), "casa.db")
ferramentas.BANCO = banco
with sqlite3.connect(banco) as c:
    c.executescript(open(os.path.join(AQUI, "schema.sql")).read())
mes = ferramentas.competencia_atual()
print(f"banco: {banco} | mês: {mes}\n")

print("banco e saldo inicial")
r = ferramentas.banco_salvar("Inter", saldo_inicial=1200, quem="Johnata")
checa("cria o banco", len(sql("select * from bancos where nome='inter'")) == 1)
checa("nome normalizado pra minúscula", sql("select nome from bancos")[0]["nome"] == "inter")
checa("saldo inicial vira MOVIMENTO, não coluna", len(sql("select * from movimentos")) == 1)
checa("não existe coluna saldo em bancos",
      "saldo" not in [d["name"] for d in sql("pragma table_info(bancos)")])
checa("saldo devolvido é 1200", r["saldo"] == 1200.0, str(r["saldo"]))

print("\nbanco com saldo zero (bug de 07/09/2026)")
z = ferramentas.banco_salvar("C6", saldo_inicial=0, quem="Johnata")
checa("saldo zero NÃO é erro", z["ok"] is True, z.get("erro", ""))
checa("o banco existe mesmo assim", len(sql("select * from bancos where nome='c6'")) == 1)
checa("zero não vira movimento de abertura",
      len(sql("select * from movimentos where banco_id=?", (z["id"],))) == 0)
checa("saldo devolvido é 0", z["saldo"] == 0)
mau = ferramentas.banco_salvar("Banco Ruim", saldo_inicial="muito dinheiro")
checa("saldo ilegível é recusado", mau["ok"] is False)
checa("e o banco NÃO fica criado pela metade",
      len(sql("select * from bancos where nome='banco ruim'")) == 0)
with sqlite3.connect(banco) as _c:
    _c.execute("delete from bancos where nome='c6'")

print("\ngasto")
g = ferramentas.gasto_registrar(50, "chuveiro", banco="Inter", categoria="Moradia", quem="Johnata")
m = sql("select * from movimentos where id=?", (g["id"],))[0]
checa("gasto grava valor NEGATIVO", m["valor"] == -50.0, str(m["valor"]))
checa("categoria gravada", m["categoria"] == "Moradia")
checa("competencia derivada de quando", m["competencia"] == m["quando"][:7])
checa("saldo caiu pra 1150", g["saldo"] == 1150.0, str(g["saldo"]))
checa("saldo_ver concorda", ferramentas.saldo_ver("inter")["bancos"][0]["saldo"] == 1150.0)

ferramentas.gasto_registrar(27, "uber", banco="Inter", categoria="Transporte")
ferramentas.gasto_registrar(19, "misto quente", banco="Inter", categoria="Alimentação")
checa("categoria inventada cai em Outros",
      ferramentas.gasto_registrar(10, "x", banco="Inter", categoria="Locomoção")["categoria"] == "Outros")

print("\nentrada")
e = ferramentas.entrada_registrar(3200, "salário", banco="Inter", quem="Johnata")
checa("entrada grava valor POSITIVO", sql("select valor from movimentos where id=?",
      (e["id"],))[0]["valor"] == 3200.0)
checa("saldo somou", e["saldo"] == 1200 - 50 - 27 - 19 - 10 + 3200, str(e["saldo"]))

print("\ndata do gasto ≠ data do registro")
o = ferramentas.gasto_registrar(80, "feira", banco="Inter", categoria="Mercado",
                                quando="2026-08-15")
mo = sql("select * from movimentos where id=?", (o["id"],))[0]
checa("quando é a data informada", mo["quando"] == "2026-08-15")
checa("competencia vai pro mês do GASTO", mo["competencia"] == "2026-08")
checa("registrado_em é hoje, outra coisa", mo["registrado_em"][:7] == mes)
checa("não entra no resumo deste mês",
      all(c["categoria"] != "Mercado" for c in ferramentas.resumo()["categorias"]))
checa("entra no resumo de agosto",
      any(c["categoria"] == "Mercado" for c in ferramentas.resumo("2026-08")["categorias"]))
checa("data inválida é recusada",
      ferramentas.gasto_registrar(10, "x", banco="Inter", quando="ontem")["ok"] is False)

print("\nestorno")
antes = ferramentas.saldo_ver("inter")["bancos"][0]["saldo"]
es = ferramentas.estornar(g["id"])
checa("estorno devolve o valor ao saldo",
      ferramentas.saldo_ver("inter")["bancos"][0]["saldo"] == antes + 50)
checa("a linha NÃO foi apagada", len(sql("select * from movimentos where id=?", (g["id"],))) == 1)
checa("marcou estornado=1", sql("select estornado from movimentos where id=?",
      (g["id"],))[0]["estornado"] == 1)
checa("estornar de novo não dobra", ferramentas.estornar(g["id"])["acao"] == "ja_estava_estornado")
checa("estornado some do extrato",
      all(x["id"] != g["id"] for x in ferramentas.extrato(competencia=mes)["movimentos"]))
checa("estornar id que não existe devolve erro", ferramentas.estornar(9999)["ok"] is False)

print("\nresumo e limites")
res = ferramentas.resumo()
checa("resumo soma por categoria", any(c["categoria"] == "Transporte" and c["gasto"] == 27
                                       for c in res["categorias"]), str(res["categorias"]))
checa("resumo traz o que entrou", res["total_entrou"] == 3200.0, str(res["total_entrou"]))
checa("saldo inicial NÃO conta como entrada do mês",
      res["total_entrou"] == 3200.0 and ferramentas.saldo_ver("inter")["bancos"][0]["saldo"] > 3200)
checa("saldo inicial continua no extrato",
      any(x["categoria"] == "Saldo inicial" for x in ferramentas.extrato(competencia=mes, limite=50)["movimentos"]))
lim = ferramentas.limite_definir("Transporte", 300)
checa("limite gravado", sql("select * from limites")[0]["valor_mes"] == 300.0)
checa("limite já sabe o gasto do mês", lim["gasto_no_mes"] == 27.0)
tr = [c for c in ferramentas.resumo()["categorias"] if c["categoria"] == "Transporte"][0]
checa("resumo mostra limite e percentual", tr["limite"] == 300 and tr["percentual"] == 9)
checa("não estourou ainda", tr["estourou"] is False)
ferramentas.gasto_registrar(400, "táxi caro", banco="Inter", categoria="Transporte")
tr = [c for c in ferramentas.resumo()["categorias"] if c["categoria"] == "Transporte"][0]
checa("resumo acusa estouro", tr["estourou"] is True, f"{tr['gasto']} de {tr['limite']}")
checa("categoria inexistente no limite é recusada",
      ferramentas.limite_definir("Foguete", 100)["ok"] is False)

print("\nvários bancos")
ferramentas.banco_salvar("Nubank", saldo_inicial=500)
amb = ferramentas.gasto_registrar(30, "sem dizer o banco")
checa("com 2 bancos e sem dizer qual, RECUSA em vez de chutar", amb["ok"] is False, amb.get("erro"))
tot = ferramentas.saldo_ver()
checa("saldo_ver sem nome lista os dois", len(tot["bancos"]) == 2)
checa("total é a soma dos dois",
      tot["total"] == round(sum(b["saldo"] for b in tot["bancos"]), 2))
checa("banco que não existe devolve erro", ferramentas.saldo_ver("Bradesco")["ok"] is False)

print("\nlista_corrigir (o buraco de hoje)")
ferramentas.lista_add("cabeça de chuveiro")
cor = ferramentas.lista_corrigir("cabeça de chuveiro", "chuveiro")
linhas = sql("select * from lista_compras")
checa("renomeou no lugar", len(linhas) == 1 and linhas[0]["item"] == "chuveiro")
checa("NÃO marcou como comprado", linhas[0]["comprado"] == 0)
checa("não criou linha nova", len(linhas) == 1)
checa("corrigir o que não está na lista devolve erro",
      ferramentas.lista_corrigir("foguete", "nave")["ok"] is False)

print(f"\n{n - len(falhas)}/{n} verificações passaram")
if falhas:
    print("FALHAS:"); [print("  -", f) for f in falhas]; sys.exit(1)
