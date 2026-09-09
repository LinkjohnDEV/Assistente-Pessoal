"""Cada frase tem que cair na ferramenta certa. Sem histórico entre elas —
cada uma é julgada sozinha, como quando alguém fala do nada no grupo.

Custa cota: são 14 chamadas de verdade na API. Rodar quando mexer nas
instruções, não a toda hora.
"""
import os, sqlite3, sys, tempfile
AQUI=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0,AQUI)
import ferramentas as f, cerebro

tmp=os.path.join(tempfile.mkdtemp(prefix="t-rot-"),"casa.db"); f.BANCO=tmp
with sqlite3.connect(tmp) as c: c.executescript(open(os.path.join(AQUI,"schema.sql")).read())

# o mundo como está no grupo de verdade
f.salvar_conta("aluguel", 23, 2300)
f.salvar_conta("luz", 10, 187.4)
f.banco_salvar("inter", saldo_inicial=4000)
f.banco_salvar("c6", saldo_inicial=0)
f.tarefa_add("Fazer a troca da conta de água e luz da casa nova", quando="2026-09-08")
f.tarefa_add("Arrumar a porta nova na casa antiga", quando="2026-09-08")
f.lista_add("sabão"); f.lista_add("chuveiro")
f.lembrar_fato("wifi", "senha123")

CASOS = [
    # (frase, ferramenta que TEM que ser chamada)
    ("Lauren, preciso arrumar o portão amanhã",              "tarefa_add"),
    ("Lauren, já arrumei a porta",                           "tarefa_feita"),
    ("Lauren, foi concluída a troca da conta de água e luz", "tarefa_feita"),
    ("Lauren, o que tenho pra fazer?",                       "tarefas_ver"),

    ("Lauren, minha internet é dia 15, 99 reais por mês",    "salvar_conta"),
    ("Lauren, paguei a luz",                                 "marcar_pago"),
    ("Lauren, a luz vence quando?",                          "consultar_contas"),
    ("Lauren, quanto eu devo esse mês?",                     "consultar_contas"),

    ("Lauren, gastei 50 no mercado pelo Inter",              "gasto_registrar"),
    ("Lauren, uber 27 pelo inter",                           "gasto_registrar"),
    ("Lauren, recebi 3200 de salário no inter",              "entrada_registrar"),
    ("Lauren, quanto tenho no inter?",                       "saldo_ver"),
    ("Lauren, quanto gastei esse mês?",                      "resumo"),

    ("Lauren, falta comprar detergente",                     "lista_add"),
    ("Lauren, já comprei o sabão",                           "lista_marcar_comprado"),
    ("Lauren, o que falta comprar?",                         "lista_ver"),
    ("Lauren, a senha do portão é 4321",                     "lembrar_fato"),
    ("Lauren, qual a senha do wifi?",                        "buscar_fato"),
]

cb = cerebro.Cerebro(cerebro.carregar_config(exigir=("lauren",)))
erros = []
for frase, esperada in CASOS:
    r = cb.responder(frase, quem="Johnata")          # sem histórico: cada uma sozinha
    usou = [x["nome"] for x in r["ferramentas"]]
    ok = esperada in usou
    print(f"  [{'ok' if ok else 'FALHOU'}] {frase}")
    print(f"          esperado {esperada} | chamou {usou or '(nenhuma)'}")
    if not ok:
        erros.append((frase, esperada, usou))

print(f"\n{len(CASOS)-len(erros)}/{len(CASOS)} caíram na ferramenta certa")
if erros:
    print("\nERRARAM:")
    for frase, esp, usou in erros:
        print(f"  {frase}\n    esperado {esp}, chamou {usou}")
    sys.exit(1)
