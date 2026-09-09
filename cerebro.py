# Passo 9 aprovado em 07/09/2026: roteiro da seção 12 rodado inteiro contra a
# API real (lauren-4), com o ciclo de tool calling fechando e a gravação
# conferida por SQL a cada passo. Este arquivo está verificado.

"""A camada da IA. É o ÚNICO arquivo que sabe qual modelo está atrás disso.

Fala o padrão chat completions (https://ialauren.com/docs/api) usando só a
biblioteca padrão — sem venv, sem pip, sem o pacote openai. Trocar a Lauren por
outro provedor do mesmo padrão é mudar base_url e api_key no config.json.

O que este arquivo NÃO faz: lembrar das coisas. Quem lembra é o banco, através
de ferramentas.py. Aqui só se traduz português em chamada de função.
"""

import json
import os
import time
import urllib.error
import urllib.request

import ferramentas

AQUI = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(AQUI, "config.json")

TETO_VOLTAS = 20      # agente sem teto é conta aberta (seção 4 da API)
TIMEOUT = 120
PLACEHOLDERS = ("COLAR-AQUI", "DEFINIR", "SEU-ENDPOINT-DA-LAUREN",
                "GERAR-COM-openssl-rand-base64-32", "CHAVE-DO-CANAL",
                "TOKEN-DO-CANAL", "1203634XXXXXXXXX@g.us")


class ErroLauren(Exception):
    """Erro vindo da API, já com mensagem pronta pra mostrar pra gente."""


# ------------------------------------------------------------------ config

def carregar_config(exigir=("lauren",)):
    """Lê config.json e RECUSA rodar com placeholder não preenchido."""
    if not os.path.exists(CONFIG):
        raise ErroLauren(f"{CONFIG} não existe. Copie o modelo da seção 7 do documento.")
    with open(CONFIG) as f:
        cfg = json.load(f)

    pendentes = []
    for secao in exigir:
        for chave, valor in cfg.get(secao, {}).items():
            if isinstance(valor, str) and any(p in valor for p in PLACEHOLDERS):
                pendentes.append(f"{secao}.{chave} = {valor!r}")
    if pendentes:
        raise ErroLauren(
            "config.json ainda tem placeholder:\n  " + "\n  ".join(pendentes) +
            "\nPreencha antes de rodar. Nada aqui inventa valor."
        )

    # a chave pode vir do ambiente, como manda a lista de conferência da API
    if os.environ.get("LAUREN_API_KEY"):
        cfg.setdefault("lauren", {})["api_key"] = os.environ["LAUREN_API_KEY"]
    return cfg


# ------------------------------------------------------- as 8 ferramentas
# O parâmetro `quem` NÃO aparece aqui de propósito: quem falou vem do JID da
# mensagem, não da opinião do modelo. Ele não pode dizer que foi a esposa.

ESQUEMA_FERRAMENTAS = [
    {"type": "function", "function": {
        "name": "salvar_conta",
        "description": "Cria ou atualiza a REGRA de uma conta que se repete "
                       "(aluguel, luz, internet). Não registra pagamento.",
        "parameters": {"type": "object", "properties": {
            "nome": {"type": "string", "description": "Nome curto da conta, ex: 'aluguel'"},
            "dia": {"type": "integer", "description": "Dia do vencimento, 1 a 31"},
            "valor": {"type": "number", "description": "Valor em reais. 2300 é dois mil e trezentos."},
        }, "required": ["nome", "dia", "valor"]}}},

    {"type": "function", "function": {
        "name": "marcar_pago",
        "description": "Registra que uma conta foi paga em UM mês específico. "
                       "Sem competencia, assume o mês atual.",
        "parameters": {"type": "object", "properties": {
            "nome": {"type": "string"},
            "competencia": {"type": "string", "description": "Mês no formato AAAA-MM. Omita para o mês atual."},
            "valor_pago": {"type": "number", "description": "Só se foi diferente do valor da conta."},
            "banco": {"type": "string", "description": "De onde saiu o dinheiro. Passe "
                                                        "sempre que disserem — assim o saldo também cai."},
        }, "required": ["nome"]}}},

    {"type": "function", "function": {
        "name": "consultar_contas",
        "description": "Lista as contas com dia de vencimento, valor, quantos dias faltam "
                       "e se o MÊS ATUAL já foi pago. Use sempre que perguntarem sobre "
                       "conta, vencimento ou pagamento — nunca responda de memória.",
        "parameters": {"type": "object", "properties": {
            "nome": {"type": "string", "description": "Omita para listar todas."},
        }, "required": []}}},

    {"type": "function", "function": {
        "name": "lista_add",
        "description": "Põe um item na lista de compras da casa. Um item por chamada.",
        "parameters": {"type": "object", "properties": {
            "item": {"type": "string"},
        }, "required": ["item"]}}},

    {"type": "function", "function": {
        "name": "lista_marcar_comprado",
        "description": "Marca um item da lista como comprado. Use quando disserem "
                       "que já compraram algo.",
        "parameters": {"type": "object", "properties": {
            "item": {"type": "string"},
        }, "required": ["item"]}}},

    {"type": "function", "function": {
        "name": "lista_ver",
        "description": "O que ainda falta comprar. Use sempre que perguntarem da lista.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},

    {"type": "function", "function": {
        "name": "lembrar_fato",
        "description": "Guarda um fato solto da casa: senha do wifi, data da revisão "
                       "do carro, aniversário. Mesmo assunto de novo atualiza o valor.",
        "parameters": {"type": "object", "properties": {
            "assunto": {"type": "string", "description": "Chave curta, ex: 'wifi'"},
            "valor": {"type": "string", "description": "O conteúdo do fato"},
        }, "required": ["assunto", "valor"]}}},

    {"type": "function", "function": {
        "name": "buscar_fato",
        "description": "Procura nos fatos guardados. Use antes de dizer que não sabe.",
        "parameters": {"type": "object", "properties": {
            "termo": {"type": "string"},
        }, "required": ["termo"]}}},
    {"type": "function", "function": {
        "name": "lista_corrigir",
        "description": "Troca o NOME de um item da lista quando falarem errado ou "
                       "corrigirem. NUNCA use lista_marcar_comprado pra consertar nome.",
        "parameters": {"type": "object", "properties": {
            "item": {"type": "string", "description": "Como está hoje"},
            "novo_nome": {"type": "string", "description": "Como deve ficar"},
        }, "required": ["item", "novo_nome"]}}},

    {"type": "function", "function": {
        "name": "banco_salvar",
        "description": "Cadastra um banco, cartão ou o dinheiro do bolso. Use quando "
                       "disserem quanto têm em algum lugar.",
        "parameters": {"type": "object", "properties": {
            "nome": {"type": "string", "description": "Ex: 'inter', 'nubank'"},
            "tipo": {"type": "string", "enum": ["conta", "cartao", "dinheiro"]},
            "saldo_inicial": {"type": "number", "description": "Quanto tem hoje, se disserem"},
        }, "required": ["nome"]}}},

    {"type": "function", "function": {
        "name": "gasto_registrar",
        "description": "Registra dinheiro que SAIU. 'uber 27' é valor 27, descrição uber. "
                       "Sem banco dito, usa o único que houver.",
        "parameters": {"type": "object", "properties": {
            "valor": {"type": "number", "description": "Sempre positivo. 27 é R$ 27,00."},
            "descricao": {"type": "string", "description": "O que foi, curto"},
            "banco": {"type": "string", "description": "De onde saiu, se disserem"},
            "categoria": {"type": "string", "enum": ferramentas.CATEGORIAS},
            "quando": {"type": "string", "description": "AAAA-MM-DD. Só se não for hoje."},
        }, "required": ["valor"]}}},

    {"type": "function", "function": {
        "name": "entrada_registrar",
        "description": "Registra dinheiro que ENTROU: salário, pix recebido, devolução.",
        "parameters": {"type": "object", "properties": {
            "valor": {"type": "number", "description": "Sempre positivo"},
            "descricao": {"type": "string"},
            "banco": {"type": "string"},
            "quando": {"type": "string", "description": "AAAA-MM-DD. Só se não for hoje."},
        }, "required": ["valor"]}}},

    {"type": "function", "function": {
        "name": "saldo_ver",
        "description": "Quanto tem. Sem nome, lista todos os bancos e o total. "
                       "Use sempre que perguntarem de saldo — nunca calcule de cabeça.",
        "parameters": {"type": "object", "properties": {
            "banco": {"type": "string"},
        }, "required": []}}},

    {"type": "function", "function": {
        "name": "extrato",
        "description": "Os lançamentos do mês, um a um — onde o dinheiro foi.",
        "parameters": {"type": "object", "properties": {
            "banco": {"type": "string"},
            "competencia": {"type": "string", "description": "AAAA-MM. Omita para o mês atual."},
            "categoria": {"type": "string", "enum": ferramentas.CATEGORIAS},
        }, "required": []}}},

    {"type": "function", "function": {
        "name": "resumo",
        "description": "Total gasto por categoria no mês, com o limite ao lado. "
                       "Use pra 'quanto gastei', 'resumo do mês', 'em que gastei mais'.",
        "parameters": {"type": "object", "properties": {
            "competencia": {"type": "string", "description": "AAAA-MM. Omita para o mês atual."},
            "comparar_com": {"type": "string", "description": "AAAA-MM de outro mês, pra "
                                                              "'gastei mais que mês passado?'"},
        }, "required": []}}},

    {"type": "function", "function": {
        "name": "quanto_sobra",
        "description": "Saldo dos bancos menos as contas fixas ainda não pagas do mês. "
                       "Use pra 'quanto sobra', 'posso gastar quanto', 'tá apertado?'.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},

    {"type": "function", "function": {
        "name": "estornar",
        "description": "Desfaz um lançamento errado pelo número dele. Peça o número "
                       "se não souber — ele aparece na confirmação e no extrato.",
        "parameters": {"type": "object", "properties": {
            "id": {"type": "integer", "description": "O número do lançamento"},
        }, "required": ["id"]}}},

    {"type": "function", "function": {
        "name": "limite_definir",
        "description": "Teto de gasto de uma categoria por mês.",
        "parameters": {"type": "object", "properties": {
            "categoria": {"type": "string", "enum": ferramentas.CATEGORIAS},
            "valor_mes": {"type": "number"},
        }, "required": ["categoria", "valor_mes"]}}},
    {"type": "function", "function": {
        "name": "tarefa_add",
        "description": "Algo a fazer: 'revisar o carro dia 12', 'levar o cachorro no vet'. "
                       "Com data entra no aviso diário. Sem data, é só uma pendência.",
        "parameters": {"type": "object", "properties": {
            "tarefa": {"type": "string", "description": "O que precisa ser feito"},
            "quando": {"type": "string", "description": "AAAA-MM-DD. Converta 'quinta', "
                                                        "'amanhã', 'outubro' a partir da data de hoje. Omita se não houver prazo."},
            "de_quem": {"type": "string", "description": "De quem é a tarefa, se disserem"},
            "repete": {"type": "string", "enum": ["diaria", "semanal", "mensal"],
                       "description": "Só se for coisa que se repete: 'toda segunda', "
                                      "'todo dia 5', 'todo dia'. Quando marcada feita, "
                                      "a próxima nasce sozinha."},
        }, "required": ["tarefa"]}}},

    {"type": "function", "function": {
        "name": "tarefas_ver",
        "description": "O que falta fazer, com quantos dias faltam (negativo = atrasada). "
                       "Use pra 'o que tenho pra fazer', 'minhas tarefas'.",
        "parameters": {"type": "object", "properties": {
            "incluir_feitas": {"type": "boolean", "description": "true pra ver o que já foi feito"},
            "ate": {"type": "string", "description": "AAAA-MM-DD: só o que vence até essa data"},
        }, "required": []}}},

    {"type": "function", "function": {
        "name": "tarefa_feita",
        "description": "Marca uma tarefa como feita. Use quando disserem que "
                       "fizeram, concluíram, terminaram ou resolveram algo — "
                       "mesmo que a frase tenha a palavra 'conta' dentro. "
                       "SE O RESULTADO TRAZ 'proxima', mostre essa data na sua "
                       "resposta: é uma tarefa que se repete e a pessoa precisa "
                       "saber que a próxima já está agendada.",
        "parameters": {"type": "object", "properties": {
            "tarefa": {"type": "string"},
        }, "required": ["tarefa"]}}},

    {"type": "function", "function": {
        "name": "tarefa_corrigir",
        "description": "Muda o texto e/ou a data de uma tarefa. NUNCA use tarefa_feita "
                       "pra consertar texto errado.",
        "parameters": {"type": "object", "properties": {
            "tarefa": {"type": "string", "description": "Como está hoje"},
            "novo_texto": {"type": "string"},
            "novo_quando": {"type": "string", "description": "AAAA-MM-DD, ou vazio pra tirar o prazo"},
        }, "required": ["tarefa"]}}},
    {"type": "function", "function": {
        "name": "desmarcar_pago",
        "description": "Desfaz o pagamento de um mês: 'na verdade não paguei o aluguel'. "
                       "Não estorna o gasto no banco — isso é estornar().",
        "parameters": {"type": "object", "properties": {
            "nome": {"type": "string"},
            "competencia": {"type": "string", "description": "AAAA-MM. Omita para o mês atual."},
        }, "required": ["nome"]}}},

    {"type": "function", "function": {
        "name": "conta_desativar",
        "description": "Aposenta uma conta que não se paga mais ('cancelei a academia'). "
                       "Ela para de aparecer e de ser cobrada, mas o histórico fica.",
        "parameters": {"type": "object", "properties": {
            "nome": {"type": "string"},
        }, "required": ["nome"]}}},

    {"type": "function", "function": {
        "name": "esquecer_fato",
        "description": "Apaga um fato que não vale mais (senha velha, por exemplo).",
        "parameters": {"type": "object", "properties": {
            "assunto": {"type": "string"},
        }, "required": ["assunto"]}}},
    {"type": "function", "function": {
        "name": "compra_parcelada",
        "description": "Compra dividida em vezes: 'geladeira em 10x de 300', "
                       "'3000 em 12 vezes'. Cria um lançamento por mês. As "
                       "parcelas futuras não mexem no saldo de hoje.",
        "parameters": {"type": "object", "properties": {
            "descricao": {"type": "string", "description": "O que foi comprado"},
            "parcelas": {"type": "integer", "description": "Em quantas vezes"},
            "valor_parcela": {"type": "number", "description": "Quanto cada uma, se "
                                                               "disserem '10x de 300'"},
            "valor_total": {"type": "number", "description": "O total, se disserem "
                                                             "'3000 em 10x'"},
            "banco": {"type": "string"},
            "categoria": {"type": "string", "enum": ferramentas.CATEGORIAS},
            "quando": {"type": "string", "description": "AAAA-MM-DD da primeira. Só se não for hoje."},
        }, "required": ["descricao", "parcelas"]}}},

    {"type": "function", "function": {
        "name": "parcelas_cancelar",
        "description": "Cancela as parcelas que ainda NÃO venceram de uma compra "
                       "parcelada (devolveu o produto, por exemplo). As já pagas ficam.",
        "parameters": {"type": "object", "properties": {
            "descricao": {"type": "string", "description": "O que foi comprado"},
        }, "required": ["descricao"]}}},

    {"type": "function", "function": {
        "name": "historico_ver",
        "description": "O que VOCÊ registrou nos últimos dias, com as ferramentas que "
                       "usou. Use pra 'o que você anotou hoje', 'o que mudou essa "
                       "semana', 'o que eu registrei ontem'.",
        "parameters": {"type": "object", "properties": {
            "dias": {"type": "integer", "description": "Quantos dias pra trás. 1 = hoje."},
        }, "required": []}}},
]


INSTRUCOES = """Você é a Lauren, assistente de um casal, num grupo de WhatsApp da casa deles.
Cuida de três coisas: contas a pagar, lista de compras e fatos soltos da casa.

ANTES DE MAIS NADA — "CONTA" TEM DOIS SENTIDOS

O que decide NÃO é a palavra "conta", é o VERBO da frase:

  "fiz" / "concluí" / "terminei" / "resolvi" / "já foi" / "acabei"
      → é TAREFA. Chame tarefas_ver e depois tarefa_feita.
  "paguei" / "vence" / "já foi paga" / "quanto devo"
      → é CONTA A PAGAR. Chame consultar_contas ou marcar_pago.

"Foi concluída a troca da conta de água e luz" é TAREFA. A palavra "conta"
está ali porque é o texto da tarefa, não porque é conta a pagar.

Se VOCÊ acabou de perguntar qual tarefa foi feita, a próxima mensagem que
nomear uma tarefa é a RESPOSTA da sua pergunta — marque como feita. Não crie
outra igual.

NUNCA responda "não achei" sobre algo que disseram ter concluído sem ter
chamado tarefas_ver antes. Se não achar num lugar, olhe no outro — as duas
buscas são baratas.

A SUA MEMÓRIA É O BANCO, NÃO A CONVERSA.
Nunca responda de cabeça sobre conta, vencimento, pagamento, lista ou fato guardado.
Chame a ferramenta e responda com o que ela devolveu. Se não chamou, você não sabe.

NUNCA diga que anotou, marcou ou guardou sem ter chamado a ferramenta e recebido
"ok": true. Dizer "marquei!" sem gravar é o pior erro possível aqui.

NÃO afirme nada da casa que a ferramenta não tenha devolvido no resultado. Se a
informação não veio no retorno, diga que não achou no banco — não complete com o
que você lembra da conversa. O que está no histórico não é prova; o banco é.

Se a ferramenta devolver "ok": false, diga o que deu errado, sem enfeitar.

COMO ESCREVER A RESPOSTA

Isto é WhatsApp, numa tela estreita: texto longo quebra em duas linhas
sozinho. Use *negrito* com um asterisco de cada lado (não use ** nem # nem
tabela — o WhatsApp não entende).

TRÊS REGRAS QUE VALEM SEMPRE:

1. UM item = recibo, uma informação por linha. VÁRIOS itens = uma linha por
   item, com tudo junto na mesma linha, separado por travessão. Nunca quebre
   um item em título numa linha e data em outra dentro de uma lista: como o
   texto já quebra sozinho na tela, vira um monte de linha sem começo nem fim.

2. Linha em branco entre o título e a lista, e entre blocos diferentes. É o
   que separa uma coisa da outra quando tudo quebra.

3. Se todos os itens tiverem a mesma data, diga uma vez no título e não
   repita em cada linha.

RUIM (foi o que saiu no grupo e ficou ilegível):
📌 Fazer a troca da conta de água e luz da casa nova
📅 08/09/2026 — amanhã
📌 Arrumar a porta nova na casa antiga
📅 08/09/2026 — amanhã

BOM:
✅ *Anotei 3 tarefas para amanhã (08/09)*

📌 Fazer a troca da conta de água e luz da casa nova
📌 Arrumar a porta nova na casa antiga
📌 Mexer no rack da Neutralink

UM item gravado, com os dados embaixo:
✅ *Conta salva!*
📄 Aluguel
📅 Todo dia 23
💸 R$ 2.300,00

VÁRIOS itens, um por linha:
🛒 *Falta comprar*

• chuveiro
• cortina blackout

NÃO mostre quem pediu, quem comprou ou quem gravou, a não ser que perguntem
("quem pediu a cortina?"). A ferramenta devolve essa informação, mas ela polui
a lista e ninguém precisa dela no dia a dia.

📌 *Suas tarefas*

🔴 Pagar o IPVA — atrasada há 2 dias
📅 Revisar o carro — 12/10
⚪ Comprar presente da vó — sem prazo

🏦 *Bancos*

🏦 Inter — R$ 4.053,61
🏦 C6 — R$ 0,00
💰 Total: R$ 4.053,61

Uma conta consultada (um item, então recibo):
📄 *Aluguel*
📅 Vence dia 23 — daqui a 16 dias
💸 R$ 2.300,00
❌ Ainda não foi paga em setembro

Quando não achar, ou der erro, diga também o CAMINHO — não deixe a pessoa no
vazio:
⚠️ Não achei "arroz" na sua lista.
Quer que eu adicione?

⚠️ Nenhum banco cadastrado ainda.
Me diz quanto tem e onde: "Lauren, tenho 1200 no Inter"

NUNCA comece a SUA mensagem com "Lauren," — esse é o seu nome, você é quem
está falando.

O "Lauren," só aparece dentro do exemplo que você dá pra pessoa responder.
Você vive num grupo e só recebe mensagem que começa com "Lauren" — quem
responder só "inter" não chega até você e acha que foi ignorado. Então quando
fizer uma pergunta, mostre a resposta pronta. Em vez de "De qual banco foi?":

De qual banco foi? Responda assim: *Lauren, foi do Inter*

DINHEIRO

"uber 27" é gasto de R$ 27,00 com uber. "gastei 250 num tênis pelo nubank" é
250 no Nubank. Valor sempre positivo — quem põe o sinal é a ferramenta.
Escolha a categoria da lista; na dúvida, Outros.

banco_salvar é só pra CRIAR um banco que não existe, com o saldo que ele tem
naquele momento. Ela nunca mexe no saldo de banco já cadastrado. Dinheiro
entrando é entrada_registrar; dinheiro saindo é gasto_registrar. É sempre por
esses dois que o saldo muda.

Se não estiver claro se o dinheiro ENTROU ou SAIU, PERGUNTE antes de gravar.
Com dinheiro não se chuta. "Adiciona 50 no Inter" é ambíguo — pode ser depósito
ou gasto que a pessoa quer lançar. Uma pergunta curta custa menos que um saldo
errado.

Vários valores numa mensagem são vários lançamentos, um por valor.

"Em 10x", "parcelado em 12", "dividido em 3" é compra_parcelada, não gasto
solto. Diga o valor de cada parcela E o total na confirmação:

✅ *Compra parcelada!*
🛒 Geladeira
💸 10x de R$ 300,00 — total R$ 3.000,00
🏦 Inter — 1ª parcela hoje, última em 09/06/2027
💰 Saldo: R$ 3.753,61

O saldo cai só a primeira parcela. As outras já estão gravadas e entram no mês
delas — se perguntarem o saldo hoje, ele NÃO leva o tombo das 10.

Quando disserem que pagaram uma conta E de onde saiu ("paguei o aluguel pelo
Inter"), passe o banco no marcar_pago: o pagamento fica registrado e o saldo
cai junto.

Se NÃO disserem o banco, registre assim mesmo e avise numa linha que o saldo
não mudou. NÃO fique perguntando de qual banco foi — registrar o pagamento já
é o principal, e o dinheiro pode ter saído de qualquer lugar. Perguntar só
quando a pessoa disse um valor de gasto sem dizer se entrou ou saiu.

DESFAZER

Errou? desmarcar_pago desfaz pagamento de conta, estornar desfaz lançamento de
dinheiro, tarefa_corrigir e lista_corrigir arrumam texto, conta_desativar
aposenta conta que não se paga mais, esquecer_fato apaga fato vencido.
Nunca use a ferramenta de CONCLUIR (marcar comprado, marcar feita, marcar
pago) pra consertar erro — só quando a coisa aconteceu de verdade.

Se a pessoa disser que comprou algo que está na lista E disser o valor, faça as
duas coisas: marque na lista e registre o gasto.

Ao registrar gasto:
✅ *Gasto registrado!*
🛒 Chuveiro
🏷️ Moradia
💸 R$ 50,00
🏦 Inter — saldo: R$ 1.150,00
🔢 #12

Sempre mostre o número (#) do lançamento: é por ele que se estorna depois.

Ao registrar entrada (dinheiro que chegou — use ⬆️, nunca 💸, que é saída):
✅ *Entrada registrada!*
⬆️ R$ 118,76 — pix da mãe
🏦 Inter — saldo: R$ 4.172,37
🔢 #2

Ao mostrar saldo:
🏦 *Inter*
💰 R$ 1.150,00

Ao resumir o mês:
📊 *Setembro*
🏷️ Transporte — R$ 427,00 ⚠️ estourou o limite de R$ 300,00
🏷️ Alimentação — R$ 19,00
💸 Total: R$ 446,00
💰 Entrou: R$ 3.200,00

Quanto sobra (saldo menos as contas fixas que ainda vão vencer):
💰 Você tem R$ 4.053,61
📄 Contas a pagar: R$ 2.300,00
🟢 Sobra: R$ 1.753,61

Comparando meses:
📊 *Setembro x Agosto*

🏷️ Mercado — R$ 620,00 ↑ 24%
🏷️ Transporte — R$ 180,00 ↓ 40%
💸 Total: R$ 1.240,00 ↑ 5%

Nunca some saldo de cabeça e nunca converta moeda. O número vem da ferramenta.

Quando for só conversa, sem nada do banco, responda em uma frase simples, sem
emoji e sem formatação. O formato de recibo é pra dado — não para bate-papo.

Nunca invente linha que a ferramenta não devolveu. Melhor três linhas certas
que seis bonitas.

Hoje é {hoje}. A competência do mês atual é {competencia}."""


class Cerebro:
    def __init__(self, config):
        lauren = config["lauren"]
        self.url = lauren["base_url"].rstrip("/") + "/chat/completions"
        self.chave = lauren["api_key"]
        self.modelo = lauren.get("modelo") or "lauren-4"

    # -------------------------------------------------------- rede
    def _chamar(self, mensagens):
        corpo = json.dumps({
            "model": self.modelo,
            "messages": mensagens,
            "tools": ESQUEMA_FERRAMENTAS,
            "tool_choice": "auto",
        }, ensure_ascii=False).encode("utf-8")

        pedido = urllib.request.Request(self.url, data=corpo, headers={
            "Authorization": f"Bearer {self.chave}",
            "Content-Type": "application/json",
        })

        ultima = None
        for tentativa in range(3):
            try:
                with urllib.request.urlopen(pedido, timeout=TIMEOUT) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                bruto = e.read().decode("utf-8", "replace")
                try:
                    msg = json.loads(bruto)["error"]["message"]
                except Exception:
                    msg = bruto.strip()[:400] or e.reason

                if e.code in (401, 403):
                    raise ErroLauren(f"[{e.code}] {msg}") from None
                if e.code == 429:
                    # a mensagem já vem escrita pra gente ler: mostrar inteira
                    raise ErroLauren(f"Cota estourada: {msg}") from None
                if e.code in (502, 503) and tentativa < 2:
                    ultima = f"[{e.code}] {msg}"
                    time.sleep(2 ** tentativa)      # recuo exponencial
                    continue
                raise ErroLauren(f"[{e.code}] {msg}") from None
            except urllib.error.URLError as e:
                if tentativa < 2:
                    ultima = f"rede: {e.reason}"
                    time.sleep(2 ** tentativa)
                    continue
                raise ErroLauren(f"Não consegui falar com a Lauren: {e.reason}") from None
        raise ErroLauren(f"A Lauren não respondeu depois de 3 tentativas. Último: {ultima}")

    # -------------------------------------------------------- o laço
    def responder(self, mensagem, quem=None, historico=None):
        """Devolve dict: resposta, ferramentas, tokens_in, tokens_out, modelo.

        `historico` é a lista de turnos anteriores (sem o system). A rota não
        guarda nada — o histórico inteiro vai junto em toda chamada.
        """
        agora = ferramentas.competencia_atual()
        hoje = time.strftime("%d/%m/%Y")
        sistema = INSTRUCOES.format(hoje=hoje, competencia=agora)

        mensagens = [{"role": "system", "content": sistema}]
        mensagens += _limpar(historico or [])
        mensagens.append({"role": "user", "content": mensagem})

        usadas, tin, tout, modelo_real = [], 0, 0, self.modelo

        for _ in range(TETO_VOLTAS):
            try:
                bruto = self._chamar(mensagens)
            except ErroLauren as e:
                # quem for reprocessar precisa saber: se já gravou, repetir grava 2x
                e.ferramentas_ja_rodadas = list(usadas)
                raise
            uso = bruto.get("usage") or {}
            tin += uso.get("prompt_tokens", 0)
            tout += uso.get("completion_tokens", 0)
            modelo_real = bruto.get("model", modelo_real)   # o da RESPOSTA

            msg = bruto["choices"][0]["message"]
            mensagens.append(msg)                            # o turno INTEIRO volta

            chamadas = msg.get("tool_calls")
            if not chamadas:
                texto = (msg.get("content") or "").strip()
                if not texto:
                    # turno vazio envenena o histórico: não guardar, não devolver
                    mensagens.pop()
                    texto = "Não consegui formular a resposta. Repete?"
                return {"resposta": texto, "ferramentas": usadas,
                        "tokens_in": tin, "tokens_out": tout,
                        "modelo": modelo_real, "mensagens": mensagens[1:]}

            for chamada in chamadas:
                nome = chamada["function"]["name"]
                try:
                    args = json.loads(chamada["function"]["arguments"] or "{}")  # STRING JSON
                except json.JSONDecodeError as e:
                    resultado = {"ok": False, "erro": f"argumentos ilegíveis: {e}"}
                else:
                    funcao = ferramentas.FERRAMENTAS.get(nome)
                    if funcao is None:
                        resultado = {"ok": False, "erro": f"ferramenta '{nome}' não existe"}
                    else:
                        if quem is not None and nome != "consultar_contas" \
                                and "quem" in funcao.__code__.co_varnames:
                            args["quem"] = quem      # quem falou vem do JID, não do modelo
                        try:
                            resultado = funcao(**args)
                        except TypeError as e:
                            resultado = {"ok": False, "erro": f"argumentos errados: {e}"}
                        except Exception as e:
                            resultado = {"ok": False, "erro": f"{type(e).__name__}: {e}"}

                usadas.append({"nome": nome, "args": args, "resultado": resultado})
                mensagens.append({
                    "role": "tool",
                    "tool_call_id": chamada["id"],
                    "content": json.dumps(resultado, ensure_ascii=False, default=str),
                })

        return {"resposta": "Me embananei toda nessa. Pergunta de novo, mais simples?",
                "ferramentas": usadas, "tokens_in": tin, "tokens_out": tout,
                "modelo": modelo_real, "mensagens": mensagens[1:]}


def _limpar(historico):
    """Tira turnos de assistente vazios — reenviá-los trava a conversa."""
    limpo = []
    for m in historico:
        if m.get("role") == "assistant" and not m.get("tool_calls") \
                and not (m.get("content") or "").strip():
            continue
        limpo.append(m)
    return limpo
