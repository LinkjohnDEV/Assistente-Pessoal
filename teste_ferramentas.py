"""Passo 6: prova as 8 ferramentas sem IA e sem WhatsApp.

Cada verificação lê o BANCO direto por SQL — não confia no que a função
devolveu. Roda num banco descartável em /tmp: casa.db não é sujada.

    python3 teste_ferramentas.py
"""

import os
import sqlite3
import sys
import tempfile

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)

import ferramentas  # noqa: E402

falhas = []
n = 0


def checa(descricao, condicao, detalhe=""):
    global n
    n += 1
    ok = bool(condicao)
    print(f"  [{'ok' if ok else 'FALHOU'}] {descricao}" + (f"  → {detalhe}" if detalhe else ""))
    if not ok:
        falhas.append(descricao)


def sql(q, args=()):
    with sqlite3.connect(ferramentas.BANCO) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute(q, args).fetchall()]


# ------------------------------------------------ banco descartável
banco = os.path.join(tempfile.mkdtemp(prefix="teste-lauren-"), "casa.db")
ferramentas.BANCO = banco
with sqlite3.connect(banco) as c:
    c.executescript(open(os.path.join(AQUI, "schema.sql")).read())
mes = ferramentas.competencia_atual()
print(f"banco de teste: {banco}\nmês corrente: {mes}\n")

# ------------------------------------------------ contas e pagamentos
print("contas e pagamentos")
ferramentas.salvar_conta("aluguel", 23, 2300, quem="Johnata")
linhas = sql("SELECT * FROM contas WHERE nome='aluguel'")
checa("salvar_conta cria a linha em contas", len(linhas) == 1)
checa("valor gravado é 2300.0 e não 23.0", linhas and linhas[0]["valor"] == 2300.0,
      f"valor={linhas[0]['valor'] if linhas else '?'}")
checa("dia_vencimento gravado é 23", linhas and linhas[0]["dia_vencimento"] == 23)

r = ferramentas.consultar_contas("aluguel")
checa("consultar_contas diz que NÃO foi pago este mês", r["contas"][0]["pago"] is False)

ferramentas.marcar_pago("aluguel", quem="Johnata")
pags = sql("SELECT * FROM pagamentos")
checa("marcar_pago cria 1 linha em pagamentos", len(pags) == 1)
checa("competencia é o mês atual", pags and pags[0]["competencia"] == mes,
      f"competencia={pags[0]['competencia'] if pags else '?'}")

r = ferramentas.consultar_contas("aluguel")
checa("consultar_contas agora diz que FOI pago", r["contas"][0]["pago"] is True)

r2 = ferramentas.marcar_pago("aluguel")
checa("marcar_pago repetido NÃO duplica a linha", len(sql("SELECT * FROM pagamentos")) == 1,
      f"acao={r2['acao']}")

try:
    with sqlite3.connect(banco) as c:
        c.execute("INSERT INTO pagamentos (conta_id, competencia) VALUES (?,?)",
                  (pags[0]["conta_id"], mes))
    checa("UNIQUE(conta_id,competencia) barra insert duplicado no banco", False)
except sqlite3.IntegrityError:
    checa("UNIQUE(conta_id,competencia) barra insert duplicado no banco", True)

ferramentas.salvar_conta("Aluguel ", 23, 2350)
checa("salvar_conta de novo atualiza, não cria segunda conta",
      len(sql("SELECT * FROM contas")) == 1 and sql("SELECT * FROM contas")[0]["valor"] == 2350.0)
ferramentas.salvar_conta("aluguel", 23, 2300)

# ------------------------------------------------ lista de compras
print("\nlista de compras")
ferramentas.lista_add("sabão", quem="Esposa")
ferramentas.lista_add("cabeça de chuveiro", quem="Johnata")
r = ferramentas.lista_ver()
checa("lista_ver devolve os dois itens", r["quantidade"] == 2,
      ", ".join(i["item"] for i in r["itens"]))

ferramentas.lista_marcar_comprado("sabão", quem="Johnata")
linhas = sql("SELECT * FROM lista_compras WHERE item='sabão'")
checa("sabão fica comprado = 1", linhas and linhas[0]["comprado"] == 1)
checa("a linha do sabão CONTINUA existindo (não deletou)", len(linhas) == 1)
checa("total na tabela continua 2 linhas", len(sql("SELECT * FROM lista_compras")) == 2)

r = ferramentas.lista_ver()
checa("lista_ver devolve só a cabeça de chuveiro",
      r["quantidade"] == 1 and r["itens"][0]["item"] == "cabeça de chuveiro")

r = ferramentas.lista_marcar_comprado("chuveiro")
checa("marcar por pedaço do nome acha 'cabeça de chuveiro'",
      r["ok"] and r["item"] == "cabeça de chuveiro")
checa("lista_ver fica vazia", ferramentas.lista_ver()["quantidade"] == 0)

ferramentas.lista_add("fechadura porta")
r = ferramentas.lista_marcar_comprado("fechadura da porta")
checa("acha item mesmo com palavra a mais ('da')", r["ok"] and r["item"] == "fechadura porta",
      r.get("erro", ""))
ferramentas.lista_add("caixa de papelão")
r = ferramentas.lista_marcar_comprado("caixa papelao")
checa("acha ignorando 'de'", r["ok"] and r["item"] == "caixa de papelão", r.get("erro", ""))
ferramentas.lista_add("pilha")
checa("palavra que não existe não casa por engano",
      ferramentas.lista_marcar_comprado("parafuso")["ok"] is False)
checa("uma letra solta NÃO casa com nada",
      ferramentas.lista_marcar_comprado("x")["ok"] is False)
checa("pedaço curto de palavra não casa ('pá' em 'papelão')",
      ferramentas.lista_marcar_comprado("pá")["ok"] is False)
checa("palavra vazia de sentido não casa",
      ferramentas.lista_marcar_comprado("de")["ok"] is False)
checa("a pilha continua pendente", ferramentas.lista_ver()["quantidade"] == 1)
ferramentas.lista_marcar_comprado("pilha")

r = ferramentas.lista_marcar_comprado("arroz")
checa("marcar item que não existe devolve erro, não finge", r["ok"] is False)

# ------------------------------------------------ fatos
print("\nfatos")
ferramentas.lembrar_fato("wifi", "senha123", quem="Johnata")
linhas = sql("SELECT * FROM fatos WHERE assunto='wifi'")
checa("lembrar_fato grava a linha", len(linhas) == 1 and linhas[0]["valor"] == "senha123")

r = ferramentas.buscar_fato("wifi")
checa("buscar_fato acha o fato", r["quantidade"] == 1 and r["fatos"][0]["valor"] == "senha123")

ferramentas.lembrar_fato("wifi", "senha456")
linhas = sql("SELECT * FROM fatos WHERE assunto='wifi'")
checa("mesmo assunto atualiza em vez de empilhar",
      len(linhas) == 1 and linhas[0]["valor"] == "senha456")
checa("buscar_fato de termo inexistente devolve vazio",
      ferramentas.buscar_fato("placa do carro")["quantidade"] == 0)

# ------------------------------------------------ A VIRADA DO MÊS
print("\nvirada do mês (seção 12) — o teste que mais importa")
anterior = sql("SELECT strftime('%Y-%m','now','localtime','-1 month') AS m")[0]["m"]
with sqlite3.connect(banco) as c:
    c.execute("UPDATE pagamentos SET competencia = ? WHERE conta_id = 1", (anterior,))
r = ferramentas.consultar_contas("aluguel")
checa(f"pagamento de {anterior} NÃO conta como pago em {mes}", r["contas"][0]["pago"] is False,
      "se isso falhar, o modelo de dados está errado e você perde o aluguel")

ferramentas.marcar_pago("aluguel")
checa("dá pra pagar o mês novo mesmo com o anterior registrado",
      len(sql("SELECT * FROM pagamentos")) == 2)
checa("consultar_contas volta a dizer pago", ferramentas.consultar_contas("aluguel")["contas"][0]["pago"] is True)

# ------------------------------------------------
print(f"\n{n - len(falhas)}/{n} verificações passaram")
if falhas:
    print("FALHAS:")
    for f in falhas:
        print(f"  - {f}")
    sys.exit(1)
print("passo 6 aprovado — as ferramentas funcionam sem IA nenhuma")
